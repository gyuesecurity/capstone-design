"""보안 보완 테스트: B 코드에서 옮겨 온 4가지 + 빈 파일 거부

1) 로그인 실패 제한  2) 비밀번호 변경 시 다른 세션 끊기  3) 파일 형식(서명) 확인 + 빈 파일 거부  4) 보안 응답 헤더
실제 비밀번호·키는 쓰지 않습니다. 모든 데이터는 .test-data/ 아래 임시 폴더에 만들고 끝나면 지웁니다.
"""

import os

# 키가 없으면 서버가 시작하지 않으므로, 테스트에서는 임시 데이터 폴더에 개발용 키를 자동 생성합니다. (키 값은 저장소에 없음)
os.environ.setdefault("EVIDENCE_ALLOW_DEV_KEY_GENERATION", "true")

import io
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from server import db, login_limits, main
from server.upload_checks import SNIFF_SIZE, file_signature_matches

TEST_PASSWORD = "Strong!123"  # 테스트 전용 값


def zip_bytes(names) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"x")
    return buffer.getvalue()


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.app = main.create_app(self.root)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        response = await self.client.post("/api/auth/register",
                                          json={"email": "owner@example.com", "name": "소유자", "password": TEST_PASSWORD})
        self.headers = {"Authorization": f"Bearer {response.json()['token']}"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temporary.cleanup()

    def raw(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.root / "evidence.sqlite3")
        connection.row_factory = sqlite3.Row
        return connection

    async def login(self, password, email="owner@example.com"):
        return await self.client.post("/api/auth/login", json={"email": email, "password": password})

    async def upload(self, filename, content):
        return await self.client.post("/api/evidence", headers=self.headers,
                                      files={"file": (filename, content, "application/octet-stream")})

    # ---------------------------------------------------------------- 1) 로그인 실패 제한

    async def test_login_blocked_after_five_failures(self):
        for _ in range(login_limits.MAX_FAILURES - 1):
            self.assertEqual((await self.login("wrong-password")).status_code, 401)
        # 4번 틀린 뒤 맞으면 성공하고 기록이 지워짐
        self.assertEqual((await self.login(TEST_PASSWORD)).status_code, 200)
        with closing(self.raw()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0], 0)

        for _ in range(login_limits.MAX_FAILURES):
            self.assertEqual((await self.login("wrong-password")).status_code, 401)
        # 5번 틀리면 비밀번호가 맞아도 429
        response = await self.login(TEST_PASSWORD)
        self.assertEqual((response.status_code, response.json()["detail"]), (429, login_limits.TOO_MANY_ATTEMPTS))
        # 다른 이메일(없는 계정 포함)은 따로 셈
        self.assertEqual((await self.login("wrong-password", "other@example.com")).status_code, 401)

        # 막힌 시각이 지나면 다시 로그인 가능
        with closing(self.raw()) as connection:
            past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="milliseconds")
            connection.execute("UPDATE login_attempts SET blocked_until = ?", (past,))
            connection.commit()
        self.assertEqual((await self.login(TEST_PASSWORD)).status_code, 200)

    async def test_failures_reset_after_window(self):
        for _ in range(login_limits.MAX_FAILURES - 1):
            await self.login("wrong-password")
        # 첫 실패가 10분보다 오래전이면 횟수를 처음부터 다시 셈
        with closing(self.raw()) as connection:
            old = (datetime.now(UTC) - login_limits.FAILURE_WINDOW - timedelta(seconds=1)).isoformat(timespec="milliseconds")
            connection.execute("UPDATE login_attempts SET first_failed_at = ?", (old,))
            connection.commit()
        self.assertEqual((await self.login("wrong-password")).status_code, 401)
        self.assertEqual((await self.login(TEST_PASSWORD)).status_code, 200)

    async def test_identifier_hides_email(self):
        await self.login("wrong-password")
        with closing(self.raw()) as connection:
            identifier = connection.execute("SELECT identifier FROM login_attempts").fetchone()[0]
        self.assertRegex(identifier, r"^[0-9a-f]{64}$")
        self.assertNotIn("owner", identifier)

    # ---------------------------------------------------------------- 2) 비밀번호 변경 시 다른 세션 끊기

    async def test_password_change_keeps_only_current_session(self):
        other = {"Authorization": f"Bearer {(await self.login(TEST_PASSWORD)).json()['token']}"}
        self.assertEqual((await self.client.get("/api/me", headers=other)).status_code, 200)
        response = await self.client.put("/api/auth/password", headers=self.headers,
                                         json={"current_password": TEST_PASSWORD, "new_password": "NewStrong!123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.get("/api/me", headers=self.headers)).status_code, 200)  # 지금 기기는 유지
        self.assertEqual((await self.client.get("/api/me", headers=other)).status_code, 401)          # 다른 기기는 끊김
        # 틀린 현재 비밀번호면 아무 세션도 지우지 않음
        third = {"Authorization": f"Bearer {(await self.login('NewStrong!123')).json()['token']}"}
        response = await self.client.put("/api/auth/password", headers=self.headers,
                                         json={"current_password": "wrong", "new_password": "Another!123"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.client.get("/api/me", headers=third)).status_code, 200)

    # ---------------------------------------------------------------- 3) 빈 파일·파일 형식

    async def test_upload_rejects_empty_and_disguised_files(self):
        response = await self.upload("빈 파일.txt", b"")
        self.assertEqual((response.status_code, response.json()["detail"]), (422, "빈 파일은 등록할 수 없습니다."))
        response = await self.upload("사진.png", b"MZ\x90\x00 this is an exe")
        self.assertEqual((response.status_code, response.json()["detail"]),
                         (415, "파일 확장자와 실제 파일 형식이 일치하지 않습니다."))
        self.assertEqual((await self.upload("문서.docx", zip_bytes(["a.txt"]))).status_code, 415)
        # 거부된 파일은 저장 폴더·DB에 남지 않음
        self.assertEqual(list((self.root / "files").iterdir()), [])
        with closing(self.raw()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)

        # 진짜 형식이면 등록됨 (원본 해시는 그대로)
        accepted = {
            "사진.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
            "문서.pdf": b"%PDF-1.4\n%test\n",
            "문서.docx": zip_bytes(["[Content_Types].xml", "word/document.xml"]),
            "표.xlsx": zip_bytes(["[Content_Types].xml", "xl/workbook.xml"]),
            "묶음.zip": zip_bytes(["a.txt"]),
            "기록.csv": "이름,값\n가,1\n".encode("cp949"),
        }
        for filename, content in accepted.items():
            response = await self.upload(filename, content)
            self.assertEqual(response.status_code, 201, (filename, response.text))
            download = await self.client.get(f"/api/evidence/{response.json()['id']}/download", headers=self.headers)
            self.assertEqual(download.content, content, filename)

    async def test_long_korean_text_is_accepted(self):
        # 8 KiB 경계가 한글 글자 중간에 걸리는 경우 3가지 모두 (B 코드는 이 중 하나를 거부했음)
        for pad in range(3):
            content = ("a" * pad + "한글 증거 기록입니다. " * 1000).encode("utf-8")
            response = await self.upload(f"긴 한글 {pad}.txt", content)
            self.assertEqual(response.status_code, 201, (pad, response.text))

    def test_text_check_rules(self):
        long_text = "가" * 5000
        utf8 = long_text.encode("utf-8")
        cp949 = long_text.encode("cp949")
        # 잘라 낸 앞부분: 끝에서 잘린 글자는 허용
        self.assertTrue(file_signature_matches(".txt", utf8[:SNIFF_SIZE], None, truncated=True))
        self.assertTrue(file_signature_matches(".txt", cp949[:SNIFF_SIZE - 1], None, truncated=True))
        # 파일 전체인데 끝이 잘린 글자거나, 중간에 잘못된 바이트·NUL이 있으면 거부
        self.assertFalse(file_signature_matches(".txt", b"abc\xff", None, truncated=False))
        self.assertFalse(file_signature_matches(".txt", b"abc\xff\xfe\xfd" + b"a" * 100, None, truncated=True))
        self.assertFalse(file_signature_matches(".txt", b"abc\x00def", None, truncated=False))
        self.assertFalse(file_signature_matches(".exe", b"MZ", None))

    # ---------------------------------------------------------------- 4) 보안 응답 헤더

    async def test_security_headers(self):
        response = await self.client.get("/api/health")
        for name, value in (("x-content-type-options", "nosniff"), ("x-frame-options", "DENY"),
                            ("referrer-policy", "no-referrer"), ("cache-control", "no-store")):
            self.assertEqual(response.headers[name], value)
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertNotIn("strict-transport-security", response.headers)  # HTTP라서 없음
        # 파일 다운로드(스트리밍)에도 붙음
        evidence = (await self.upload("메모.txt", b"hello")).json()
        download = await self.client.get(f"/api/evidence/{evidence['id']}/download", headers=self.headers)
        self.assertEqual((download.content, download.headers["x-content-type-options"]), (b"hello", "nosniff"))
        self.assertEqual(len(download.headers.get_list("x-content-type-options")), 1)  # 중복 없음
        # API 문서 화면은 외부 스크립트를 써서 CSP를 붙이지 않음
        docs = await self.client.get("/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertNotIn("content-security-policy", docs.headers)
        self.assertEqual(docs.headers["x-frame-options"], "DENY")
        # HTTPS로 들어오면 HSTS
        secure = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://test")
        async with secure:
            self.assertIn("max-age=", (await secure.get("/api/health")).headers["strict-transport-security"])

    def test_migration_creates_login_attempts(self):
        self.assertEqual(db.SCHEMA_VERSION, 8)
        with closing(self.raw()) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(login_attempts)")]
        self.assertEqual(columns, ["identifier", "failures", "first_failed_at", "blocked_until"])


if __name__ == "__main__":
    unittest.main()
