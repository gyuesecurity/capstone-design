"""8단계 테스트: 증거 패키지 API (K1~K4), D의 build_package_zip 연결, C의 ZIP 검사, 실패 기록, 낙관적 동시성 검사

보고서 PDF는 test_reports.py와 같은 "가짜 PDF 함수"로 만들어서 Pango가 없는 PC에서도 돌아갑니다.
ZIP은 D의 실제 build_package_zip으로 만듭니다.
실제 비밀번호·키는 쓰지 않습니다. 모든 데이터는 .test-data/ 아래 임시 폴더에 만들고 끝나면 지웁니다.
"""

import os
# 키가 없으면 서버가 시작하지 않으므로, 테스트에서는 임시 데이터 폴더에 개발용 키를 자동 생성합니다. (키 값은 저장소에 없음)
os.environ.setdefault("EVIDENCE_ALLOW_DEV_KEY_GENERATION", "true")

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest import mock

import httpx

from server import custody, main, packages
from server.crypto import KeyUnavailableError
from tests.test_reports import TEST_PASSWORD, tiny_pdf


class PackageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.app = main.create_app(self.root)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.owner, self.owner_id = await self.register("owner@example.com", "소유자")
        self.friend, self.friend_id = await self.register("friend@example.com", "친구")
        self.ev1 = await self.upload("채팅 캡처.txt", b"first evidence")
        self.ev2 = await self.upload("메모:2|.txt", b"second evidence")
        self.verification = (await self.client.post(f"/api/evidence/{self.ev1}/verify", headers=self.owner)).json()["result"]
        self.renderer = mock.patch("server.reports.render_report_pdf", side_effect=lambda bundle: tiny_pdf(bundle["reportId"]))
        self.renderer.start()
        self.report = await self.make_report([self.ev1, self.ev2])

    async def asyncTearDown(self):
        self.renderer.stop()
        await self.client.aclose()
        self.temporary.cleanup()

    def raw(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.root / "evidence.sqlite3")
        connection.row_factory = sqlite3.Row
        return connection

    async def register(self, email, name):
        response = await self.client.post("/api/auth/register", json={"email": email, "name": name, "password": TEST_PASSWORD})
        body = response.json()
        return {"Authorization": f"Bearer {body['token']}"}, body["user"]["id"]

    async def upload(self, filename, content, headers=None):
        response = await self.client.post("/api/evidence", headers=headers or self.owner,
                                          files={"file": (filename, content, "text/plain")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    async def make_report(self, selected, headers=None):
        response = await self.client.post("/api/reports", headers=headers or self.owner, json={
            "title": "디지털 증거 보고서", "selected": selected, "options": ["basic", "hash", "results", "custody"]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def create(self, report_id=None, headers=None):
        return await self.client.post("/api/packages", headers=headers or self.owner,
                                      json={"reportId": report_id or self.report["id"]})

    async def share(self, evidence_id, download):
        response = await self.client.put(f"/api/evidence/{evidence_id}/shares", headers=self.owner,
                                         json={"email": "friend@example.com", "download": download, "verify": False})
        self.assertEqual(response.status_code, 200, response.text)

    def events(self, evidence_id):
        with closing(self.raw()) as connection:
            return connection.execute("SELECT * FROM chain_of_custody WHERE evidence_id = ? ORDER BY sequence_no",
                                      (evidence_id,)).fetchall()

    def counts(self):
        with closing(self.raw()) as connection:
            result = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("evidence_packages", "evidence_package_items", "chain_of_custody", "audit")}
        result["files"] = sorted(path.name for path in (self.root / "packages").iterdir())
        return result

    def stored_path(self, evidence_id):
        with closing(self.raw()) as connection:
            name = connection.execute("SELECT stored_name FROM evidence WHERE id = ?", (evidence_id,)).fetchone()[0]
        return self.root / "files" / name

    def package_file(self, package_id):
        with closing(self.raw()) as connection:
            key = connection.execute("SELECT file_storage_key FROM evidence_packages WHERE id = ?", (package_id,)).fetchone()[0]
        return self.root / "packages" / f"{key}.zip"

    def audit_actions(self):
        with closing(self.raw()) as connection:
            return [row[0] for row in connection.execute("SELECT action FROM audit ORDER BY id")]

    # ---------------------------------------------------------------- 정상 흐름

    async def test_create_list_detail_download(self):
        before = {evidence_id: self.events(evidence_id)[-1] for evidence_id in (self.ev1, self.ev2)}
        response = await self.create()
        self.assertEqual(response.status_code, 201, response.text)
        package = response.json()

        # 응답 (report-packages.js가 확인하는 필드: id, reportId, created, createdAt, file.filename)
        self.assertRegex(package["id"], r"^PKG-[0-9a-f]{32}$")
        self.assertEqual(package["reportId"], self.report["id"])
        self.assertEqual((package["creator"], package["creatorId"]), ("소유자", self.owner_id))
        self.assertRegex(package["created"], r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(package["file"]["filename"], package["id"] + ".zip")
        first, second = package["items"]
        self.assertEqual(first["pathInPackage"], f"evidence/01_{self.ev1}_채팅 캡처.txt")
        self.assertEqual(second["pathInPackage"], f"evidence/02_{self.ev2}_메모_2_.txt")  # : | → _
        self.assertEqual(first["packagedFileSha256"], hashlib.sha256(b"first evidence").hexdigest())
        self.assertEqual(first["evidenceSha256"], first["packagedFileSha256"])
        self.assertEqual((first["verificationId"], first["verificationStatus"]), (self.verification["id"], "MATCH"))
        self.assertEqual((second["verificationId"], second["verificationStatus"]), (None, "NOT_VERIFIED"))
        self.assertEqual(first["custodySnapshot"], {"sequenceNo": before[self.ev1]["sequence_no"],
                                                    "eventHash": before[self.ev1]["event_hash"]})

        # 관리 이력: 기준점 바로 다음에 PACKAGE_EXPORTED SUCCESS, 활동 기록 package_create
        exported = self.events(self.ev1)[-1]
        self.assertEqual((exported["event_type"], exported["result"], exported["sequence_no"]),
                         ("PACKAGE_EXPORTED", "SUCCESS", before[self.ev1]["sequence_no"] + 1))
        self.assertEqual(json.loads(exported["details"]), {
            "actorName": "소유자", "reportId": self.report["id"], "packageId": package["id"],
            "packagedFileSha256": first["packagedFileSha256"],
            "snapshotSequenceNo": before[self.ev1]["sequence_no"], "snapshotEventHash": before[self.ev1]["event_hash"]})
        self.assertEqual(self.audit_actions()[-1], "package_create")

        # K2 목록 (보고서별, 전체), K3 상세
        listing = await self.client.get(f"/api/packages?reportId={self.report['id']}", headers=self.owner)
        self.assertEqual(listing.json(), [package])
        self.assertEqual((await self.client.get("/api/packages", headers=self.owner)).json(), [package])
        self.assertEqual((await self.client.get(f"/api/packages/{package['id']}", headers=self.owner)).json(), package)

        # K4 다운로드
        download = await self.client.get(f"/api/packages/{package['id']}/download", headers=self.owner)
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(download.headers["content-type"], "application/zip")
        self.assertEqual(download.headers["content-disposition"], f'attachment; filename="{package["id"]}.zip"')
        self.assertEqual(hashlib.sha256(download.content).hexdigest(), package["file"]["sha256"])
        self.assertEqual(len(download.content), package["file"]["size"])
        for evidence_id in (self.ev1, self.ev2):
            last = self.events(evidence_id)[-1]
            self.assertEqual((last["event_type"], last["result"], json.loads(last["details"])),
                             ("PACKAGE_DOWNLOADED", "SUCCESS", {"actorName": "소유자", "packageId": package["id"]}))
        self.assertEqual(self.audit_actions()[-1], "package_download")

        # ZIP 안: 순서·내용
        prefix = package["id"] + "/"
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            self.assertEqual(archive.namelist(), [prefix + name for name in (
                "manifest.json", "verification.json", "custody.json", "report/report.pdf",
                first["pathInPackage"], second["pathInPackage"])])
            files = {name[len(prefix):]: archive.read(name) for name in archive.namelist()}
        self.assertEqual(files[first["pathInPackage"]], b"first evidence")
        self.assertEqual(files[second["pathInPackage"]], b"second evidence")
        report_pdf = (await self.client.get(f"/api/reports/{self.report['id']}/download", headers=self.owner)).content
        self.assertEqual(files["report/report.pdf"], report_pdf)

        manifest = json.loads(files["manifest.json"])
        self.assertEqual(files["manifest.json"], custody.canonical_json(manifest).encode())  # 키 정렬·공백 없음
        self.assertEqual((manifest["packageId"], manifest["reportId"], manifest["creatorId"], manifest["created"]),
                         (package["id"], self.report["id"], self.owner_id, package["created"]))
        self.assertEqual(manifest["report"]["sha256"], hashlib.sha256(report_pdf).hexdigest())
        for entry in manifest["files"]:
            self.assertEqual(entry["sha256"], hashlib.sha256(files[entry["path"]]).hexdigest())
        for item, expected in zip(manifest["items"], package["items"]):
            self.assertEqual(hashlib.sha256(files[item["path"]]).hexdigest(), item["packagedFileSha256"])
            self.assertEqual((item["path"], item["custodySnapshot"]), (expected["pathInPackage"], expected["custodySnapshot"]))
        self.assertEqual(manifest["items"][0]["reportVerificationId"], self.verification["id"])

        # verification.json: 증거마다 검증 기록 전체
        verification = json.loads(files["verification.json"])
        self.assertEqual([len(item["records"]) for item in verification["evidence"]], [1, 0])
        self.assertEqual(verification["evidence"][0]["records"][0]["id"], self.verification["id"])

        # custody.json: 기준점까지의 이벤트를 받은 사람이 다시 계산해서 검사할 수 있음
        chains = json.loads(files["custody.json"])["evidence"]
        for chain in chains:
            self.assertEqual(custody.verify_chain(chain["events"]), [])
            self.assertEqual(chain["events"][-1]["event_hash"], chain["snapshot"]["eventHash"])
            self.assertEqual(len(chain["events"]), chain["snapshot"]["sequenceNo"])

        # 저장 파일은 읽기 전용, .part 없음
        self.assertEqual(self.package_file(package["id"]).stat().st_mode & 0o777, 0o400)
        self.assertFalse(any(name.endswith(".part") for name in self.counts()["files"]))

    async def test_list_is_newest_first_and_owner_only(self):
        older = (await self.create()).json()
        newer = (await self.create()).json()
        listing = (await self.client.get(f"/api/packages?reportId={self.report['id']}", headers=self.owner)).json()
        self.assertEqual([item["id"] for item in listing], [newer["id"], older["id"]])
        # 남의 보고서·패키지는 존재 여부를 드러내지 않음 (404)
        self.assertEqual((await self.create(headers=self.friend)).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/packages?reportId={self.report['id']}", headers=self.friend)).status_code, 404)
        self.assertEqual((await self.client.get("/api/packages", headers=self.friend)).json(), [])
        for path in (f"/api/packages/{older['id']}", f"/api/packages/{older['id']}/download"):
            self.assertEqual((await self.client.get(path, headers=self.friend)).status_code, 404)
        self.assertEqual((await self.create("RPT-" + "0" * 32)).status_code, 404)
        self.assertEqual((await self.client.post("/api/packages", headers=self.owner, json={})).status_code, 422)
        self.assertEqual((await self.client.post("/api/packages", json={"reportId": self.report["id"]})).status_code, 401)

    # ---------------------------------------------------------------- 권한

    async def test_shared_evidence_needs_download(self):
        await self.share(self.ev1, download=False)
        report = await self.make_report([self.ev1], headers=self.friend)
        before = self.counts()
        response = await self.create(report["id"], headers=self.friend)
        self.assertEqual((response.status_code, response.json()["detail"]), (403, packages.NO_DOWNLOAD))
        last = self.events(self.ev1)[-1]
        self.assertEqual((last["event_type"], last["result"], json.loads(last["details"])),
                         ("ACCESS_DENIED", "FAILURE", {"actorName": "친구", "requiredPermission": "DOWNLOAD"}))
        after = self.counts()
        self.assertEqual((after["evidence_packages"], after["files"], after["chain_of_custody"]),
                         (before["evidence_packages"], before["files"], before["chain_of_custody"] + 1))

        # 다운로드 권한을 주면 공유받은 사람도 자기 보고서로 패키지를 만들 수 있음
        await self.share(self.ev1, download=True)
        response = await self.create(report["id"], headers=self.friend)
        self.assertEqual(response.status_code, 201, response.text)
        package = response.json()

        # 그 뒤 다운로드 권한을 회수하면 K4는 403 + ACCESS_DENIED, 다운로드 기록 없음
        await self.share(self.ev1, download=False)
        response = await self.client.get(f"/api/packages/{package['id']}/download", headers=self.friend)
        self.assertEqual((response.status_code, response.json()["detail"]), (403, packages.REVOKED_DOWNLOAD))
        self.assertEqual(self.events(self.ev1)[-1]["event_type"], "ACCESS_DENIED")
        self.assertNotIn("package_download", self.audit_actions())

        # 보기 권한까지 회수되면 403이지만, 볼 수 없는 이력에는 ACCESS_DENIED를 남기지 않음
        await self.client.delete(f"/api/evidence/{self.ev1}/shares/{self.friend_id}", headers=self.owner)
        count = len(self.events(self.ev1))
        response = await self.client.get(f"/api/packages/{package['id']}/download", headers=self.friend)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.events(self.ev1)), count)

    async def test_download_revoked_while_building_saves_nothing(self):
        await self.share(self.ev1, download=True)
        report = await self.make_report([self.ev1], headers=self.friend)
        original = packages.build_package_zip

        def revoke_then_build(bundle, output):
            with closing(self.raw()) as connection:
                connection.execute("UPDATE evidence_permissions SET revoked_at = 't' WHERE permission = 'DOWNLOAD'")
                connection.commit()
            original(bundle, output)

        before = self.counts()
        with mock.patch("server.packages.build_package_zip", side_effect=revoke_then_build):
            response = await self.create(report["id"], headers=self.friend)
        self.assertEqual((response.status_code, response.json()["detail"]), (403, packages.REVOKED_DOWNLOAD))
        after = self.counts()
        self.assertEqual((after["evidence_packages"], after["files"]), (before["evidence_packages"], before["files"]))
        self.assertEqual(self.events(self.ev1)[-1]["event_type"], "ACCESS_DENIED")

    # ---------------------------------------------------------------- 원본·보고서 문제

    async def test_tampered_original_records_failure(self):
        path = self.stored_path(self.ev2)
        data = bytearray(path.read_bytes())
        data[-1] ^= 0x01
        path.write_bytes(bytes(data))
        before = self.counts()
        response = await self.create()
        self.assertEqual((response.status_code, response.json()["detail"]), (409, packages.HASH_PROBLEM))
        # 원인 증거(ev2)는 원인 코드, 그 앞에서 이미 처리한 증거(ev1)는 ABORTED
        for evidence_id, code in ((self.ev1, "ABORTED"), (self.ev2, "AUTHENTICATION_FAILED")):
            last = self.events(evidence_id)[-1]
            details = json.loads(last["details"])
            self.assertEqual((last["event_type"], last["result"], details["failureCode"], details["reportId"]),
                             ("PACKAGE_EXPORTED", "FAILURE", code, self.report["id"]))
            self.assertRegex(details["packageId"], r"^PKG-[0-9a-f]{32}$")
        after = self.counts()
        self.assertEqual((after["evidence_packages"], after["evidence_package_items"], after["files"], after["audit"]),
                         (before["evidence_packages"], before["evidence_package_items"], before["files"], before["audit"]))

    async def test_missing_first_original_stops_before_others(self):
        self.stored_path(self.ev1).unlink()
        ev2_count = len(self.events(self.ev2))
        response = await self.create()
        self.assertEqual((response.status_code, response.json()["detail"]), (409, packages.FILE_PROBLEM))
        self.assertEqual(json.loads(self.events(self.ev1)[-1]["details"])["failureCode"], "FILE_UNAVAILABLE")
        self.assertEqual(len(self.events(self.ev2)), ev2_count)  # 원본을 열지 않은 증거에는 기록 없음

    async def test_hash_mismatch_with_valid_decryption(self):
        # 복호화는 됐지만 내용이 기준 해시와 다른 경우 (예: LEGACY 평문 파일이 바뀜)
        def wrong_plain(_crypto, _target, _row):
            return io.BytesIO(b"other content!")
        with mock.patch("server.packages.verified_plaintext", side_effect=wrong_plain):
            response = await self.create()
        self.assertEqual((response.status_code, response.json()["detail"]), (409, packages.HASH_PROBLEM))
        self.assertEqual(json.loads(self.events(self.ev1)[-1]["details"])["failureCode"], "HASH_MISMATCH")

    async def test_key_error_records_nothing(self):
        before = self.counts()
        with mock.patch("server.packages.verified_plaintext", side_effect=KeyUnavailableError("test")):
            response = await self.create()
        self.assertEqual((response.status_code, response.json()["detail"]), (500, packages.CREATE_FAILED))
        self.assertEqual(self.counts(), before)

    async def test_report_file_problem_stops_first(self):
        with closing(self.raw()) as connection:
            key = connection.execute("SELECT file_storage_key FROM reports").fetchone()[0]
        path = self.root / "reports" / f"{key}.pdf"
        path.chmod(0o600)
        path.write_bytes(b"%PDF-1.4 changed")
        before = self.counts()
        response = await self.create()
        self.assertEqual((response.status_code, response.json()["detail"]), (409, packages.REPORT_FILE_PROBLEM))
        self.assertEqual(self.counts(), before)  # 증거 원본을 열기 전에 멈춤 → 관리 이력 기록 없음

    async def test_size_limit(self):
        before = self.counts()
        with mock.patch("server.packages.MAX_PACKAGE_ORIGINAL_SIZE", 20):
            response = await self.create()
        self.assertEqual((response.status_code, response.json()["detail"]), (422, packages.TOO_LARGE))
        self.assertEqual(self.counts(), before)

    # ---------------------------------------------------------------- D 결과 검사·동시성

    async def test_bad_zip_is_rejected(self):
        def wrong_zip(bundle, output):
            with zipfile.ZipFile(output, "w") as archive:
                for entry in bundle["entries"][:-1]:  # 마지막 항목을 빠뜨림
                    archive.writestr(entry["path"], entry["data"])
        before = self.counts()
        with mock.patch("server.packages.build_package_zip", side_effect=wrong_zip):
            response = await self.create()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.counts(), before)

    async def test_check_package_zip_rules(self):
        entries = packages.build_entries("PKG-" + "a" * 32, {"manifest.json": b"{}", "verification.json": b"{}",
                                                             "custody.json": b"{}"}, b"%PDF", [])
        target = self.root / "check.zip"

        def write(change):
            with zipfile.ZipFile(target, "w") as archive:
                for entry in entries:
                    path, data = change(entry["path"], entry["data"])
                    archive.writestr(path, data)

        write(lambda path, data: (path, data))
        packages.check_package_zip(target, entries)  # 그대로면 통과
        for change in (lambda path, data: (path, data + b"x"),                       # 내용 다름
                       lambda path, data: (path.replace("custody", "other"), data)):  # 이름 다름
            write(change)
            with self.assertRaises(ValueError):
                packages.check_package_zip(target, entries)

    async def test_custody_change_retries_then_succeeds(self):
        original = packages.build_package_zip
        calls = []

        def verify_once_then_build(bundle, output):
            calls.append(bundle["packageId"])
            if len(calls) == 1:  # 첫 시도 도중 다른 사람이 검증 → 관리 이력이 바뀜
                with closing(self.raw()) as connection:
                    row = connection.execute("SELECT * FROM users WHERE id = ?", (self.owner_id,)).fetchone()
                    connection.execute("BEGIN IMMEDIATE")
                    custody.append_event(connection, self.ev1, "ACCESS_DENIED", row, "FAILURE", {"requiredPermission": "VERIFY"})
                    connection.execute("COMMIT")
            original(bundle, output)

        with mock.patch("server.packages.build_package_zip", side_effect=verify_once_then_build):
            response = await self.create()
        self.assertEqual(response.status_code, 201, response.text)
        package = response.json()
        self.assertEqual(calls, [package["id"], package["id"]])  # 같은 패키지 ID로 다시 시도
        self.assertEqual(package["items"][0]["custodySnapshot"]["sequenceNo"], self.events(self.ev1)[-2]["sequence_no"])
        self.assertEqual(len(self.counts()["files"]), 1)  # 첫 시도 파일은 지워짐

    async def test_custody_keeps_changing_gives_409(self):
        original = packages.build_package_zip

        def always_change(bundle, output):
            with closing(self.raw()) as connection:
                row = connection.execute("SELECT * FROM users WHERE id = ?", (self.owner_id,)).fetchone()
                connection.execute("BEGIN IMMEDIATE")
                custody.append_event(connection, self.ev2, "ACCESS_DENIED", row, "FAILURE", {"requiredPermission": "VERIFY"})
                connection.execute("COMMIT")
            original(bundle, output)

        with mock.patch("server.packages.build_package_zip", side_effect=always_change):
            response = await self.create()
        self.assertEqual((response.status_code, response.json()["detail"]), (409, packages.CHANGED_DURING_CREATE))
        counts = self.counts()
        self.assertEqual((counts["evidence_packages"], counts["files"]), (0, []))

    # ---------------------------------------------------------------- K4 파일 문제

    async def test_download_file_problems(self):
        package = (await self.create()).json()
        path = self.package_file(package["id"])
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b"x")
        response = await self.client.get(f"/api/packages/{package['id']}/download", headers=self.owner)
        self.assertEqual((response.status_code, response.json()["detail"]), (409, packages.PACKAGE_INTEGRITY_PROBLEM))
        last = self.events(self.ev1)[-1]
        self.assertEqual((last["event_type"], last["result"], json.loads(last["details"])["failureCode"]),
                         ("PACKAGE_DOWNLOADED", "FAILURE", "HASH_MISMATCH"))
        path.unlink()
        response = await self.client.get(f"/api/packages/{package['id']}/download", headers=self.owner)
        self.assertEqual((response.status_code, response.json()["detail"]), (404, packages.PACKAGE_FILE_NOT_FOUND))
        self.assertEqual(json.loads(self.events(self.ev2)[-1]["details"])["failureCode"], "FILE_UNAVAILABLE")
        self.assertNotIn("package_download", self.audit_actions())

    # ---------------------------------------------------------------- DB 트리거

    async def test_database_rejects_changes(self):
        package = (await self.create()).json()
        with closing(self.raw()) as connection:
            for statement in ("UPDATE evidence_packages SET file_size = 1", "DELETE FROM evidence_packages",
                              "UPDATE evidence_package_items SET item_order = 9", "DELETE FROM evidence_package_items"):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "IMMUTABLE"):
                    connection.execute(statement)
            item = connection.execute("SELECT * FROM evidence_package_items WHERE evidence_id = ?", (self.ev1,)).fetchone()
            values = dict(item)

            def insert_item(**change):
                row = dict(values, **change)
                connection.execute(f"INSERT INTO evidence_package_items ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                                   tuple(row.values()))

            # 같은 패키지에 다른 해시·없는 기준점·남의 검증 ID를 넣으려 하면 거부 (PK 충돌 전에 트리거가 막음)
            for change in ({"packaged_file_sha256": "0" * 64}, {"packaged_file_sha256": "0" * 64, "evidence_sha256": "0" * 64},
                           {"custody_snapshot_event_hash": "1" * 64},
                           {"verification_id": None}, {"verification_status": "MISMATCH"}):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "INVALID"):
                    insert_item(item_order=9, path_in_package="evidence/x", **change)
            base_row = dict(connection.execute("SELECT * FROM evidence_packages").fetchone())

            def insert_package(**change):
                row = dict(base_row, id="PKG-" + "b" * 32, file_storage_key="c" * 32)
                row.update(change)
                connection.execute(f"INSERT INTO evidence_packages ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                                   tuple(row.values()))

            # 보고서 작성자가 아닌 사람 이름 / 잘못된 ID·해시·이름·크기로 패키지 행 넣기 거부
            for change in ({"created_by": self.friend_id}, {"id": "PKG-" + "B" * 32}, {"id": "RPT-" + "b" * 32},
                           {"file_sha256": "A" * 64}, {"file_sha256": "a" * 63}, {"creator_name": " "}, {"file_size": 0}):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "INVALID"):
                    insert_package(**change)
        # 보고서에 없던 증거는 해시·기준점·검증 상태가 모두 맞아도 거부
        ev3 = await self.upload("다른 증거.txt", b"third evidence")
        with closing(self.raw()) as connection:
            last = connection.execute("SELECT * FROM chain_of_custody WHERE evidence_id = ? ORDER BY sequence_no DESC LIMIT 1",
                                      (ev3,)).fetchone()
            row = dict(values, evidence_id=ev3, item_order=9, path_in_package="evidence/y",
                       evidence_sha256=hashlib.sha256(b"third evidence").hexdigest(),
                       packaged_file_sha256=hashlib.sha256(b"third evidence").hexdigest(),
                       verification_id=None, verification_status="NOT_VERIFIED",
                       custody_snapshot_sequence_no=last["sequence_no"], custody_snapshot_event_hash=last["event_hash"])
            with self.assertRaisesRegex(sqlite3.DatabaseError, "INVALID"):
                connection.execute(f"INSERT INTO evidence_package_items ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                                   tuple(row.values()))
        self.assertEqual(package["items"][0]["evidenceId"], self.ev1)

    def test_safe_filename(self):
        self.assertEqual(packages.safe_filename('a/b\\c:d*e?f"g<h>i|j.txt'), "a_b_c_d_e_f_g_h_i_j.txt")
        self.assertEqual(packages.safe_filename("..."), "file")
        self.assertEqual(packages.safe_filename(" 줄\n바꿈.txt "), "줄_바꿈.txt")
        long = packages.safe_filename("가" * 300 + ".pdf")
        self.assertEqual((len(long), long[-4:]), (120, ".pdf"))


if __name__ == "__main__":
    unittest.main()
