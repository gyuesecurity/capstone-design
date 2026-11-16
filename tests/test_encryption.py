"""6단계 테스트: 증거 파일 암호화(B 모듈 연결), 검증 실패 원인 코드, 활동 기록 서명 체인

실제 키는 쓰지 않습니다. 테스트 키는 실행할 때마다 임시 폴더에 새로 만들거나 os.urandom으로 만듭니다.
모든 데이터는 .test-data/ 아래 임시 폴더에 만들고 끝나면 지웁니다.
"""

import os

# 키가 없으면 서버가 시작하지 않으므로, 테스트에서는 임시 데이터 폴더에 개발용 키를 자동 생성합니다. (키 값은 저장소에 없음)
os.environ.setdefault("EVIDENCE_ALLOW_DEV_KEY_GENERATION", "true")

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import httpx

from server import crypto, db, main

TEST_PASSWORD = "Strong!123"  # 테스트 전용 값
CONTENT = "기밀 증거 내용 secret evidence content".encode() * 50


class EncryptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.client = self.client_for(main.create_app(self.root))
        response = await self.client.post("/api/auth/register",
                                          json={"email": "owner@example.com", "name": "소유자", "password": TEST_PASSWORD})
        self.headers = {"Authorization": f"Bearer {response.json()['token']}"}
        response = await self.client.post("/api/evidence", headers=self.headers,
                                          files={"file": ("증거 파일.txt", CONTENT, "text/plain")})
        self.assertEqual(response.status_code, 201, response.text)
        self.evidence = response.json()
        self.base = f"/api/evidence/{self.evidence['id']}"

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temporary.cleanup()

    def client_for(self, app):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    def raw(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.root / "evidence.sqlite3")
        connection.row_factory = sqlite3.Row
        return connection

    def row(self):
        with closing(self.raw()) as connection:
            return connection.execute("SELECT * FROM evidence").fetchone()

    def stored_path(self) -> Path:
        return self.root / "files" / self.row()["stored_name"]

    def tamper_file(self, change):
        path = self.stored_path()
        path.chmod(0o600)
        data = bytearray(path.read_bytes())
        change(data)
        path.write_bytes(bytes(data))

    async def events(self):
        return (await self.client.get(f"{self.base}/custody", headers=self.headers)).json()["events"]

    def counts(self):
        with closing(self.raw()) as connection:
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("verifications", "audit", "chain_of_custody")}

    # ---------------------------------------------------------------- 정상 흐름

    async def test_file_is_stored_encrypted_and_round_trips(self):
        stored = self.stored_path().read_bytes()
        self.assertNotEqual(stored, CONTENT)
        self.assertEqual(len(stored), len(CONTENT))          # 태그를 DB에 따로 두므로 크기가 같음
        self.assertNotIn("기밀".encode(), stored)
        row = self.row()
        self.assertEqual((row["encryption_algorithm"], row["encryption_key_version"], row["encryption_aad_version"]),
                         ("AES-256-GCM", "v1", "v1"))
        self.assertEqual((row["stored_size"], row["size"]), (len(CONTENT), len(CONTENT)))
        self.assertEqual(self.evidence["sha256"], hashlib.sha256(CONTENT).hexdigest())  # 기준 해시는 원본 기준
        # 응답에는 알고리즘·키 버전만 (salt·nonce·태그는 내보내지 않음, 11/15 B 결정)
        self.assertEqual(self.evidence["encryption"], {"algorithm": "AES-256-GCM", "keyVersion": "v1"})

        response = await self.client.get(f"{self.base}/download", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, CONTENT)
        # 헤더는 5단계까지의 FileResponse와 같은 규칙
        self.assertEqual(response.headers["content-disposition"],
                         "attachment; filename*=utf-8''%EC%A6%9D%EA%B1%B0%20%ED%8C%8C%EC%9D%BC.txt")
        self.assertEqual(response.headers["content-length"], str(len(CONTENT)))

        result = (await self.client.post(f"{self.base}/verify", headers=self.headers)).json()["result"]
        self.assertEqual((result["status"], result["failureCode"]), ("MATCH", None))
        self.assertEqual((await self.events())[-1]["details"],
                         {"actorName": "소유자", "status": "MATCH", "verificationId": result["id"]})

    # ---------------------------------------------------------------- 변조·파일 없음

    async def test_tampered_ciphertext_is_mismatch_authentication_failed(self):
        self.tamper_file(lambda data: data.__setitem__(10, data[10] ^ 1))
        result = (await self.client.post(f"{self.base}/verify", headers=self.headers)).json()["result"]
        self.assertEqual((result["status"], result["failureCode"], result["currentHash"]),
                         ("MISMATCH", "AUTHENTICATION_FAILED", None))
        last = (await self.events())[-1]
        self.assertEqual((last["eventType"], last["result"], last["details"]["status"], last["details"]["failureCode"]),
                         ("INTEGRITY_VERIFIED", "SUCCESS", "MISMATCH", "AUTHENTICATION_FAILED"))
        audit = (await self.client.get("/api/audit", headers=self.headers)).json()
        self.assertEqual(audit[0]["action"], "verify:MISMATCH")

        # 다운로드: 409 + 관리 이력에 EVIDENCE_DOWNLOADED FAILURE, 활동 기록(download)은 없음
        response = await self.client.get(f"{self.base}/download", headers=self.headers)
        self.assertEqual((response.status_code, response.json()["detail"]),
                         (409, "암호화 파일의 인증 정보가 일치하지 않아 다운로드할 수 없습니다."))
        last = (await self.events())[-1]
        self.assertEqual((last["eventType"], last["result"], last["details"]["failureCode"]),
                         ("EVIDENCE_DOWNLOADED", "FAILURE", "AUTHENTICATION_FAILED"))
        audit = (await self.client.get("/api/audit", headers=self.headers)).json()
        self.assertNotIn("download", [item["action"] for item in audit])
        self.assertTrue((await self.client.get(f"{self.base}/custody/verify", headers=self.headers)).json()["valid"])

    async def test_missing_file(self):
        path = self.stored_path()
        path.chmod(0o600)
        path.unlink()
        response = await self.client.get(f"{self.base}/download", headers=self.headers)
        self.assertEqual(response.status_code, 404)
        last = (await self.events())[-1]
        self.assertEqual((last["eventType"], last["result"], last["details"]["failureCode"]),
                         ("EVIDENCE_DOWNLOADED", "FAILURE", "FILE_UNAVAILABLE"))
        result = (await self.client.post(f"{self.base}/verify", headers=self.headers)).json()["result"]
        self.assertEqual((result["status"], result["failureCode"]), ("UNAVAILABLE", "FILE_UNAVAILABLE"))
        last = (await self.events())[-1]
        self.assertEqual((last["eventType"], last["result"]), ("INTEGRITY_VERIFIED", "FAILURE"))

    # ---------------------------------------------------------------- 키·메타데이터 오류는 기록하지 않음

    async def test_key_unavailable_saves_nothing(self):
        # 같은 데이터 폴더를, v1 키가 없는 키링(v2만)으로 다시 시작한 상황
        keyring = json.dumps({"v2": os.urandom(32).hex()})
        with mock.patch.dict(os.environ, {"EVIDENCE_MASTER_KEYS": keyring, "EVIDENCE_KEY_VERSION": "v2"}):
            other = self.client_for(main.create_app(self.root))
        try:
            before = self.counts()
            response = await other.post(f"{self.base}/verify", headers=self.headers)
            self.assertEqual((response.status_code, response.json()["detail"]),
                             (500, "암호화 키를 사용할 수 없어 무결성을 검증하지 못했습니다."))
            response = await other.get(f"{self.base}/download", headers=self.headers)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(self.counts(), before)
            self.assertEqual(self.row()["verification_status"], "NOT_VERIFIED")
            # v1 키로 서명한 활동 기록은 지금 키링으로 검사할 수 없음
            audit = (await other.get("/api/audit", headers=self.headers)).json()
            self.assertEqual({item["chainStatus"] for item in audit}, {"KEY_UNAVAILABLE"})
        finally:
            await other.aclose()

    async def test_invalid_metadata_saves_nothing(self):
        with closing(self.raw()) as connection, connection:
            connection.execute("DROP TRIGGER evidence_block_update")
            connection.execute("UPDATE evidence SET encryption_nonce = '!!'")
        before = self.counts()
        response = await self.client.post(f"{self.base}/verify", headers=self.headers)
        self.assertEqual((response.status_code, response.json()["detail"]),
                         (500, "암호화 메타데이터가 올바르지 않아 무결성을 검증하지 못했습니다."))
        self.assertEqual((await self.client.get(f"{self.base}/download", headers=self.headers)).status_code, 500)
        self.assertEqual(self.counts(), before)

    def test_server_refuses_to_start_without_key(self):
        empty = self.root / "no-key"
        with mock.patch.dict(os.environ, {"EVIDENCE_ALLOW_DEV_KEY_GENERATION": "false"}):
            for name in ("EVIDENCE_MASTER_KEYS", "EVIDENCE_MASTER_KEYS_FILE", "EVIDENCE_MASTER_KEY"):
                os.environ.pop(name, None)
            with self.assertRaises(crypto.KeyConfigurationError):
                main.create_app(empty)
        self.assertFalse((empty / "evidence.sqlite3").exists())  # DB를 건드리기 전에 멈춤

    def test_key_file_from_environment(self):
        # 시연 방식: 데이터 폴더 밖의 키링 파일(Docker secret 위치와 같은 형식)
        key_file = self.root / "secret-keys.json"
        key_file.write_text(json.dumps({"v1": os.urandom(32).hex()}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"EVIDENCE_MASTER_KEYS_FILE": str(key_file), "EVIDENCE_ALLOW_DEV_KEY_GENERATION": "false"}):
            app = main.create_app(self.root / "with-file")
        self.assertEqual(app.state.storage.crypto.key_version, "v1")
        self.assertFalse((self.root / "with-file" / "keys").exists())  # 데이터 폴더에 키를 만들지 않음

    # ---------------------------------------------------------------- 활동 기록 서명 체인

    async def test_audit_chain(self):
        audit = (await self.client.get("/api/audit", headers=self.headers)).json()
        self.assertEqual([item["action"] for item in audit], ["upload", "login", "register"])
        self.assertEqual({item["chainStatus"] for item in audit}, {"VALID"})
        with closing(self.raw()) as connection, connection:
            def blocked(sql, params=()):
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    connection.execute(sql, params)
                self.assertIn("INVALID", str(caught.exception))
            user_id = connection.execute("SELECT id FROM users").fetchone()[0]
            last = connection.execute("SELECT entry_hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()[0]
            insert = """INSERT INTO audit(user_id, action, created_at, previous_hash, entry_hash, key_version)
                        VALUES (?, 'x', 't', ?, ?, 'v1')"""
            blocked(insert, (user_id, last, ""))                 # 서명 없음
            blocked(insert, (user_id, "0" * 64, "a" * 64))       # 앞 기록과 안 이어짐
            # 누군가 트리거를 지우고 기록을 고침 → 그 기록은 INVALID
            connection.execute("DROP TRIGGER audit_block_update")
            connection.execute("UPDATE audit SET action = 'login' WHERE action = 'upload'")
        audit = (await self.client.get("/api/audit", headers=self.headers)).json()
        self.assertEqual([item["chainStatus"] for item in audit], ["INVALID", "VALID", "VALID"])

    # ---------------------------------------------------------------- DB 트리거

    async def test_encryption_triggers(self):
        with closing(self.raw()) as connection:
            def blocked(sql, params=(), word="INVALID"):
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    connection.execute(sql, params)
                self.assertIn(word, str(caught.exception))
            row = self.row()
            # 암호화 정보는 수정 불가
            blocked("UPDATE evidence SET encryption_tag = 'x'", word="IMMUTABLE")
            blocked("UPDATE evidence SET encryption_algorithm = 'LEGACY'", word="IMMUTABLE")
            # 새 증거는 암호화 정보 필수 (평문 저장 금지)
            blocked("""INSERT INTO evidence (id, owner_id, name, filename, stored_name, type, mime, size, kind, source,
                       description, memo, registered_at, sha256, registered_by_name)
                       VALUES ('EV-x', ?, 'n', 'f', 's', 'TXT', 'text/plain', 1, 'k', 's', '', '', 't', ?, '이름')""",
                    (row["owner_id"], "a" * 64))
            # 검증 원인 코드는 상태와 짝이 맞아야 함
            insert = """INSERT INTO verifications (id, evidence_id, user_id, started_at, completed_at, expected_hash,
                        status, reason, performed_by_name, failure_code) VALUES (?, ?, ?, 't', 't', ?, ?, 'r', '이름', ?)"""
            for number, (status, code) in enumerate([("MATCH", "HASH_MISMATCH"), ("MISMATCH", ""),
                                                      ("MISMATCH", "FILE_UNAVAILABLE"), ("UNAVAILABLE", ""),
                                                      ("MISMATCH", "OTHER")]):
                blocked(insert, (f"V-{number}", row["id"], row["owner_id"], "a" * 64, status, code))


class LegacyEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """5단계까지 평문으로 저장된 증거(LEGACY)가 마이그레이션 후에도 그대로 동작하는지"""

    async def test_stage5_plaintext_evidence_still_works(self):
        Path(".test-data").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".test-data") as name:
            root = Path(name)
            (root / "files").mkdir()
            stored_name = "0" * 32
            (root / "files" / stored_name).write_bytes(b"plain")
            token = "legacy-stage5-token"
            expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat(timespec="milliseconds")
            with closing(sqlite3.connect(root / "evidence.sqlite3", isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                with db.write_transaction(connection):
                    for _, apply in db.MIGRATIONS[:4]:
                        apply(connection)
                    connection.execute("PRAGMA user_version = 4")
                    connection.execute("INSERT INTO users (id, email, name, password_hash, created_at) VALUES ('u1', 'old@example.com', '예전 사용자', 'scrypt$x', 't0')")
                    connection.execute("INSERT INTO sessions VALUES (?, 'u1', ?)", (hashlib.sha256(token.encode()).hexdigest(), expires))
                    connection.execute("""INSERT INTO evidence (id, owner_id, name, filename, stored_name, type, mime, size, kind,
                        source, description, memo, registered_at, sha256, registered_by_name) VALUES ('EV-old', 'u1', '예전 증거',
                        'old.txt', ?, 'TXT', 'text/plain', 5, '파일', '직접 수집', '', '', 't', ?, '예전 사용자')""",
                                       (stored_name, hashlib.sha256(b"plain").hexdigest()))
                    connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES ('u1', 'upload', 'EV-old', 't')")

            app = main.create_app(root)

            self.assertEqual(len(list((root / "backups").glob("evidence-v4-*.sqlite3"))), 1)
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                evidence = (await client.get("/api/evidence/EV-old", headers=headers)).json()
                self.assertEqual((await client.get("/api/evidence/EV-old/download", headers=headers)).content, b"plain")
                result = (await client.post("/api/evidence/EV-old/verify", headers=headers)).json()["result"]
                audit = (await client.get("/api/audit", headers=headers)).json()
            self.assertEqual(result["status"], "MATCH")
            self.assertEqual(evidence["encryption"], {"algorithm": "LEGACY", "keyVersion": None})
            with closing(sqlite3.connect(root / "evidence.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT encryption_algorithm FROM evidence").fetchone()[0], "LEGACY")
            # 서명 없는 예전 기록은 LEGACY, 새 기록은 VALID (서명 체인은 새 기록부터 0 64개로 시작)
            self.assertEqual([(item["action"], item["chainStatus"]) for item in audit],
                             [("verify:MATCH", "VALID"), ("download", "VALID"), ("upload", "LEGACY")])


if __name__ == "__main__":
    unittest.main()
