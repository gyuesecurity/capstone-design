"""7단계 테스트: 보고서 API (R1~R4), D 렌더러 연결, C의 PDF 검사, 낙관적 동시성 검사

대부분은 "가짜 PDF 함수"로 D 렌더러를 대신해서 Pango가 없는 PC에서도 돌아갑니다.
실제 D 렌더러 테스트(RealRendererTests)는 WeasyPrint·Pango가 없으면 건너뜁니다.
실제 비밀번호·키는 쓰지 않습니다. 모든 데이터는 .test-data/ 아래 임시 폴더에 만들고 끝나면 지웁니다.
"""

import os
# 키가 없으면 서버가 시작하지 않으므로, 테스트에서는 임시 데이터 폴더에 개발용 키를 자동 생성합니다. (키 값은 저장소에 없음)
os.environ.setdefault("EVIDENCE_ALLOW_DEV_KEY_GENERATION", "true")

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import httpx

from d_package.pdf_renderer import PdfRenderError, ReportBundleError
from d_package.report_bundle import validate_report_bundle
from server import custody, db, main, reports

TEST_PASSWORD = "Strong!123"  # 테스트 전용 값


def tiny_pdf(text: str, catalog_extra: bytes = b"") -> bytes:
    """글자 한 줄짜리 PDF (pypdf로 글자를 뽑을 수 있음). catalog_extra로 위험한 항목을 일부러 넣어 볼 수 있음"""
    content = b"BT /F1 12 Tf 50 700 Td (" + text.encode("ascii") + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b">>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    return out


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.app = main.create_app(self.root)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.owner, self.owner_id = await self.register("owner@example.com", "소유자")
        self.friend, self.friend_id = await self.register("friend@example.com", "친구")
        self.stranger, _ = await self.register("stranger@example.com", "모르는 사람")
        self.ev1 = await self.upload("채팅 캡처.txt", b"first evidence")
        self.ev2 = await self.upload("메모.txt", b"second evidence")
        self.verification = (await self.client.post(f"/api/evidence/{self.ev1}/verify", headers=self.owner)).json()["result"]
        # D 렌더러 대신 가짜 PDF 함수 (받은 묶음을 기록해 두고 reportId가 적힌 PDF를 돌려줌)
        self.bundles = []
        self.renderer = mock.patch("server.reports.render_report_pdf", side_effect=self.fake_render)
        self.renderer.start()

    async def asyncTearDown(self):
        self.renderer.stop()
        await self.client.aclose()
        self.temporary.cleanup()

    def fake_render(self, bundle):
        self.bundles.append(bundle)
        return tiny_pdf(bundle["reportId"])

    def raw(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.root / "evidence.sqlite3")
        connection.row_factory = sqlite3.Row
        return connection

    async def register(self, email, name):
        response = await self.client.post("/api/auth/register", json={"email": email, "name": name, "password": TEST_PASSWORD})
        body = response.json()
        return {"Authorization": f"Bearer {body['token']}"}, body["user"]["id"]

    async def upload(self, filename, content):
        response = await self.client.post("/api/evidence", headers=self.owner, files={"file": (filename, content, "text/plain")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    async def create(self, headers=None, **change):
        body = {"title": "디지털 증거 보고서", "project": "사건-1", "description": "", "selected": [self.ev1, self.ev2],
                "options": ["custody", "basic", "hash", "results"], "author": "보내도 무시됨"}
        body.update(change)
        return await self.client.post("/api/reports", headers=headers or self.owner, json=body)

    def counts(self):
        with closing(self.raw()) as connection:
            result = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("reports", "report_evidence", "chain_of_custody", "audit")}
        result["files"] = len(list((self.root / "reports").iterdir()))
        return result

    async def events(self, evidence_id):
        return (await self.client.get(f"/api/evidence/{evidence_id}/custody", headers=self.owner)).json()["events"]

    # ---------------------------------------------------------------- 정상 흐름

    async def test_create_list_detail_download(self):
        before_ev1 = (await self.events(self.ev1))[-1]
        response = await self.create()
        self.assertEqual(response.status_code, 201, response.text)
        report = response.json()

        # 응답 (A 명세 4장, report-server.js checkedReport가 확인하는 필드)
        self.assertRegex(report["id"], r"^RPT-[0-9a-f]{32}$")
        self.assertEqual((report["author"], report["authorId"]), ("소유자", self.owner_id))
        self.assertEqual(report["selected"], [self.ev1, self.ev2])                    # 고른 순서 그대로
        self.assertEqual(report["options"], ["basic", "hash", "results", "custody"])  # 정해진 순서로 저장
        self.assertEqual(report["summary"], {"MATCH": 1, "MISMATCH": 0, "NOT_VERIFIED": 1, "UNAVAILABLE": 0})
        self.assertRegex(report["created"], r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(report["date"], report["created"][:10])
        first, second = report["evidenceSnapshot"]
        self.assertEqual((first["statusCode"], first["status"], first["lastVerification"]["id"]),
                         ("MATCH", "일치", self.verification["id"]))
        self.assertEqual((second["statusCode"], second["lastVerification"]), ("NOT_VERIFIED", None))
        self.assertEqual(first["custodySnapshot"], {"sequenceNo": before_ev1["sequenceNo"], "eventHash": before_ev1["eventHash"]})

        # D에게 넘긴 묶음은 D의 형식 검사(명세보다 엄격한 D-1 포함)를 통과해야 함
        bundle = self.bundles[-1]
        validate_report_bundle(bundle)
        self.assertEqual(bundle["created"], report["created"])  # PDF 인쇄 시각 = R3 created (글자까지 같음)
        self.assertEqual((bundle["report"]["author"], bundle["report"]["project"], bundle["report"]["description"]),
                         ("소유자", "사건-1", None))
        self.assertEqual(bundle["evidence"][0]["verification"]["performedBy"], "소유자")

        # R2 목록
        listing = (await self.client.get("/api/reports", headers=self.owner)).json()
        self.assertEqual(listing[0]["listStatus"], {"code": "NOT_VERIFIED", "label": "미검증 1건"})
        self.assertEqual((listing[0]["id"], listing[0]["evidenceCount"], listing[0]["author"]), (report["id"], 2, "소유자"))
        # R3 상세 = R1 응답
        self.assertEqual((await self.client.get(f"/api/reports/{report['id']}", headers=self.owner)).json(), report)
        # R4 다운로드
        response = await self.client.get(f"/api/reports/{report['id']}/download", headers=self.owner)
        self.assertEqual((response.status_code, response.headers["content-type"]), (200, "application/pdf"))
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), report["file"]["sha256"])
        self.assertEqual(len(response.content), report["file"]["size"])

        # 관리 이력: 증거마다 기준점 바로 다음 번호에 REPORT_INCLUDED, 사슬은 정상
        included = (await self.events(self.ev1))[-1]
        self.assertEqual((included["eventType"], included["sequenceNo"]), ("REPORT_INCLUDED", before_ev1["sequenceNo"] + 1))
        self.assertEqual(included["details"], {"actorName": "소유자", "reportId": report["id"],
                                               "snapshotSequenceNo": before_ev1["sequenceNo"],
                                               "snapshotEventHash": before_ev1["eventHash"],
                                               "verificationId": self.verification["id"]})
        self.assertIsNone((await self.events(self.ev2))[-1]["details"]["verificationId"])
        self.assertTrue((await self.client.get(f"/api/evidence/{self.ev1}/custody/verify", headers=self.owner)).json()["valid"])
        audit = [item["action"] for item in (await self.client.get("/api/audit", headers=self.owner)).json()]
        self.assertEqual(audit[:2], ["report_download", "report_create"])

    async def test_report_is_a_snapshot(self):
        report = (await self.create()).json()
        # 보고서를 만든 뒤에 다시 검증해도 보고서 내용은 그대로
        await self.client.post(f"/api/evidence/{self.ev2}/verify", headers=self.owner)
        again = (await self.client.get(f"/api/reports/{report['id']}", headers=self.owner)).json()
        self.assertEqual(again["evidenceSnapshot"], report["evidenceSnapshot"])
        self.assertEqual(again["summary"]["NOT_VERIFIED"], 1)

    async def test_filename_rule(self):
        report = (await self.create(title='a/b:c*?"<>|')).json()
        self.assertEqual(report["file"]["filename"], "a_b_c______.pdf")
        response = await self.client.get(f"/api/reports/{report['id']}/download", headers=self.owner)
        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="a_b_c______.pdf"')

    # ---------------------------------------------------------------- 입력·권한

    async def test_input_errors(self):
        cases = [
            ({"selected": []}, "보고서에 포함할 증거를 한 개 이상 선택해 주세요."),
            ({"selected": [self.ev1, self.ev1]}, "선택한 증거 목록을 확인해 주세요. (중복 불가)"),
            ({"selected": [f"EV-{i}" for i in range(101)]}, "보고서에는 증거를 최대 100개까지 넣을 수 있습니다."),
            ({"title": "  "}, "보고서 제목은 1~150자로 입력해 주세요."),
            ({"title": "가" * 151}, "보고서 제목은 1~150자로 입력해 주세요."),
            ({"project": "가" * 201}, "사건명은 200자, 설명은 4000자 이내로 입력해 주세요."),
            ({"options": []}, "보고서에 포함할 내용을 확인해 주세요."),
            ({"options": ["basic", "preview"]}, "보고서에 포함할 내용을 확인해 주세요."),
            ({"options": ["basic", "basic"]}, "보고서에 포함할 내용을 확인해 주세요."),
        ]
        for change, detail in cases:
            response = await self.create(**change)
            self.assertEqual((response.status_code, response.json()["detail"]), (422, detail), change)
        self.assertEqual(self.bundles, [])
        self.assertEqual(self.counts()["reports"], 0)

    async def test_permissions(self):
        before = self.counts()
        # 볼 권한이 없는 증거 → 404, 아무것도 남지 않음
        self.assertEqual((await self.create(headers=self.stranger)).status_code, 404)
        self.assertEqual(self.counts(), {**before, "audit": before["audit"]})
        # 보기 권한(VIEW)만 공유받아도 보고서에 넣을 수 있음 (11/15 결정)
        await self.client.put(f"/api/evidence/{self.ev1}/shares", headers=self.owner,
                              json={"email": "friend@example.com", "download": False, "verify": False})
        response = await self.create(headers=self.friend, selected=[self.ev1])
        self.assertEqual(response.status_code, 201, response.text)
        report = response.json()
        self.assertEqual(report["author"], "친구")
        # 보고서는 만든 사람만: 증거 소유자도 남의 보고서는 404
        for path in (f"/api/reports/{report['id']}", f"/api/reports/{report['id']}/download"):
            self.assertEqual((await self.client.get(path, headers=self.owner)).status_code, 404)
        self.assertEqual((await self.client.get("/api/reports", headers=self.owner)).json(), [])
        self.assertEqual((await self.client.get("/api/reports/RPT-none", headers=self.friend)).status_code, 404)

    async def test_evidence_without_custody_history_is_rejected(self):
        # 4단계 이전에 등록되어 관리 이력이 없는 증거 (LEGACY)
        with closing(self.raw()) as connection, connection:
            connection.execute("DROP TRIGGER evidence_require_encryption")
            connection.execute("""INSERT INTO evidence (id, owner_id, name, filename, stored_name, type, mime, size, kind,
                source, description, memo, registered_at, sha256, registered_by_name) VALUES ('EV-old', ?, '예전 증거',
                'old.txt', ?, 'TXT', 'text/plain', 5, '파일', '직접 수집', '', '', '2026-11-01T00:00:00.000+00:00', ?, '소유자')""",
                               (self.owner_id, "0" * 32, "a" * 64))
        response = await self.create(selected=[self.ev1, "EV-old"])
        self.assertEqual((response.status_code, response.json()["detail"]), (409, "관리 이력이 없는 증거는 보고서에 넣을 수 없습니다."))

    # ---------------------------------------------------------------- 낙관적 동시성 검사

    def add_event_during_render(self, times):
        """PDF를 만드는 사이에 ev1에 다른 이벤트(다운로드)가 생기는 상황 (times번까지)"""
        storage = self.app.state.storage
        calls = {"n": 0}

        def render(bundle):
            calls["n"] += 1
            if calls["n"] <= times:
                with closing(storage.connect()) as connection, db.write_transaction(connection):
                    user = connection.execute("SELECT * FROM users WHERE id = ?", (self.owner_id,)).fetchone()
                    custody.append_event(connection, self.ev1, "EVIDENCE_DOWNLOADED", user, "SUCCESS")
            return self.fake_render(bundle)
        return render, calls

    async def test_retry_when_history_changes(self):
        render, calls = self.add_event_during_render(times=1)
        with mock.patch("server.reports.render_report_pdf", side_effect=render):
            response = await self.create()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(calls["n"], 2)  # 한 번 다시 만듦
        self.assertEqual(self.bundles[0]["reportId"], self.bundles[1]["reportId"])  # 같은 보고서 ID 유지
        events = await self.events(self.ev1)
        self.assertEqual([e["eventType"] for e in events[-2:]], ["EVIDENCE_DOWNLOADED", "REPORT_INCLUDED"])
        self.assertEqual(response.json()["evidenceSnapshot"][0]["custodySnapshot"]["sequenceNo"], events[-2]["sequenceNo"])
        self.assertEqual(self.counts()["files"], 1)  # 버린 시도의 파일은 남지 않음

    async def test_gives_up_after_three_attempts(self):
        render, calls = self.add_event_during_render(times=99)
        before = self.counts()
        with mock.patch("server.reports.render_report_pdf", side_effect=render):
            response = await self.create()
        self.assertEqual((response.status_code, response.json()["detail"]),
                         (409, "보고서를 만드는 동안 증거 이력이 변경되었습니다. 다시 시도해 주세요."))
        self.assertEqual(calls["n"], 3)
        after = self.counts()
        self.assertEqual((after["reports"], after["report_evidence"], after["files"]), (0, 0, 0))
        self.assertEqual(after["chain_of_custody"], before["chain_of_custody"] + 3)  # 끼어든 다운로드 이벤트만

    async def test_view_revoked_during_render_saves_nothing(self):
        await self.client.put(f"/api/evidence/{self.ev1}/shares", headers=self.owner,
                              json={"email": "friend@example.com", "download": False, "verify": False})
        before = self.counts()

        def revoke_then_render(bundle):
            with closing(self.raw()) as connection, connection:
                connection.execute("UPDATE evidence_permissions SET revoked_at = 't' WHERE permission = 'VIEW'")
            return self.fake_render(bundle)

        with mock.patch("server.reports.render_report_pdf", side_effect=revoke_then_render):
            response = await self.create(headers=self.friend, selected=[self.ev1])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.counts(), before)

    # ---------------------------------------------------------------- D 결과 검사·오류

    async def test_renderer_and_pdf_check_failures_save_nothing(self):
        before = self.counts()
        cases = [
            (PdfRenderError("x"), "PDF 생성기를 사용할 수 없어 보고서를 만들지 못했습니다."),
            (ReportBundleError("FIELD_TYPE", "bundle"), "보고서 데이터 형식 오류로 PDF를 만들지 못했습니다."),
            (lambda bundle: b"not a pdf", "생성된 PDF가 검사를 통과하지 못했습니다."),
            (lambda bundle: tiny_pdf("RPT-other"), "생성된 PDF가 검사를 통과하지 못했습니다."),     # reportId 없음
            (lambda bundle: tiny_pdf(bundle["reportId"], b"/OpenAction 3 0 R "), "생성된 PDF가 검사를 통과하지 못했습니다."),
            (lambda bundle: tiny_pdf(bundle["reportId"], b"/Names << /JavaScript 5 0 R >> "), "생성된 PDF가 검사를 통과하지 못했습니다."),
        ]
        for effect, detail in cases:
            with mock.patch("server.reports.render_report_pdf", side_effect=effect):
                response = await self.create()
            self.assertEqual((response.status_code, response.json()["detail"]), (500, detail))
        self.assertEqual(self.counts(), before)

    async def test_download_detects_file_problems(self):
        report = (await self.create()).json()
        with closing(self.raw()) as connection:
            key = connection.execute("SELECT file_storage_key FROM reports").fetchone()[0]
        path = self.root / "reports" / f"{key}.pdf"
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b" ")  # 파일이 바뀜
        message = "파일 무결성 문제로 복구가 필요한 보고서입니다. 관리자에게 문의해 주세요."
        response = await self.client.get(f"/api/reports/{report['id']}/download", headers=self.owner)
        self.assertEqual((response.status_code, response.json()["detail"]), (409, message))
        path.unlink()  # 파일이 없어짐
        response = await self.client.get(f"/api/reports/{report['id']}/download", headers=self.owner)
        self.assertEqual((response.status_code, response.json()["detail"]), (409, message))
        audit = [item["action"] for item in (await self.client.get("/api/audit", headers=self.owner)).json()]
        self.assertNotIn("report_download", audit)

    # ---------------------------------------------------------------- DB 트리거

    async def test_report_triggers(self):
        await self.create()
        with closing(self.raw()) as connection:
            def blocked(sql, params=(), word="INVALID"):
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    connection.execute(sql, params)
                self.assertIn(word, str(caught.exception))
            report = connection.execute("SELECT * FROM reports").fetchone()
            blocked("UPDATE reports SET title = 'x'", word="IMMUTABLE")
            blocked("DELETE FROM reports", word="IMMUTABLE")
            blocked("UPDATE report_evidence SET item_order = 9", word="IMMUTABLE")
            blocked("DELETE FROM report_evidence", word="IMMUTABLE")
            insert = """INSERT INTO reports (id, created_by, author_name, title, case_name, description, options_json,
                        created_at, file_storage_key, file_sha256, file_size) VALUES (?, ?, ?, 't', '', '', '[]', 't', ?, ?, ?)"""
            blocked(insert, ("RPT-123", self.owner_id, "이름", "k1", "a" * 64, 1))                 # ID 형식
            blocked(insert, ("RPT-" + "b" * 32, self.owner_id, " ", "k2", "a" * 64, 1))           # 작성자 이름
            blocked(insert, ("RPT-" + "b" * 32, self.owner_id, "이름", "k3", "A" * 64, 1))        # 해시 대문자
            item = """INSERT INTO report_evidence (report_id, evidence_id, item_order, verification_id,
                      custody_snapshot_sequence_no, custody_snapshot_event_hash) VALUES (?, ?, 9, ?, ?, ?)"""
            last = connection.execute("SELECT sequence_no, event_hash FROM chain_of_custody WHERE evidence_id = ? "
                                      "ORDER BY sequence_no DESC LIMIT 1", (self.ev2,)).fetchone()
            blocked(item, (report["id"], self.ev2, None, last[0], "c" * 64))                        # 없는 기준점
            blocked(item, (report["id"], self.ev2, self.verification["id"], last[0], last[1]))     # 다른 증거의 검증

    def test_constants_match_d(self):
        from d_package.report_bundle import OPTIONS, STATUSES
        self.assertEqual(set(reports.REPORT_OPTIONS), OPTIONS)
        self.assertEqual(set(db.FAILURE_CODES) | {""}, {"", "HASH_MISMATCH", "AUTHENTICATION_FAILED", "FILE_UNAVAILABLE"})
        from server.config import STATUS_LABELS
        self.assertEqual(set(STATUS_LABELS.values()), STATUSES)


def _weasyprint_ready() -> bool:
    try:
        import weasyprint  # noqa: F401  (Pango가 없으면 OSError)
        return True
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_weasyprint_ready(), "WeasyPrint·Pango가 없어 실제 PDF 테스트를 건너뜁니다")
class RealRendererTests(unittest.IsolatedAsyncioTestCase):
    """D의 실제 렌더러로 보고서를 끝까지 만들어 봅니다."""

    async def test_real_pdf(self):
        from io import BytesIO

        from pypdf import PdfReader
        Path(".test-data").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".test-data") as name:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.create_app(name)), base_url="http://test") as client:
                token = (await client.post("/api/auth/register", json={"email": "a@example.com", "name": "임경영",
                                                                       "password": TEST_PASSWORD})).json()["token"]
                headers = {"Authorization": f"Bearer {token}"}
                evidence_id = (await client.post("/api/evidence", headers=headers,
                                                 files={"file": ("증거.txt", b"real", "text/plain")})).json()["id"]
                await client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
                response = await client.post("/api/reports", headers=headers, json={
                    "title": "실제 렌더러 보고서 <b>", "project": "", "description": "", "selected": [evidence_id],
                    "options": ["basic", "metadata", "hash", "results", "custody"]})
                self.assertEqual(response.status_code, 201, response.text)
                report = response.json()
                pdf = (await client.get(f"/api/reports/{report['id']}/download", headers=headers)).content
        text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        for expected in (report["id"], "실제 렌더러 보고서 <b>", "임경영", report["created"], "일치"):
            self.assertIn(expected, text)


class ReportMigrationTests(unittest.TestCase):
    def test_stage6_db_is_migrated(self):
        Path(".test-data").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".test-data") as name:
            root = Path(name)
            with closing(sqlite3.connect(root / "evidence.sqlite3", isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                with db.write_transaction(connection):
                    for _, apply in db.MIGRATIONS[:5]:
                        apply(connection)
                    connection.execute("PRAGMA user_version = 5")
                    connection.execute("INSERT INTO users (id, email, name, password_hash, created_at) "
                                       "VALUES ('u1', 'old@example.com', '예전 사용자', 'scrypt$x', 't0')")
            main.create_app(root)
            self.assertEqual(len(list((root / "backups").glob("evidence-v5-*.sqlite3"))), 1)
            self.assertTrue((root / "reports").is_dir())
            with closing(sqlite3.connect(root / "evidence.sqlite3")) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
