import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import httpx

from server import main


class EvidenceApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path('.test-data').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir='.test-data')
        self.root = Path(self.temporary.name)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.create_app(self.root)), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temporary.cleanup()

    async def register(self, email, name="Tester"):
        response = await self.client.post("/api/auth/register", json={"email": email, "name": name, "password": "Strong!123"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["token"]

    async def test_upload_verify_access_and_audit(self):
        owner = await self.register("owner@example.com")
        other = await self.register("other@example.com")
        headers = {"Authorization": f"Bearer {owner}"}
        self.assertEqual((await self.client.get("/api/evidence")).status_code, 401)
        upload = await self.client.post("/api/evidence", headers=headers,
                                        data={"name": "제출 자료", "source": "직접 수집"},
                                        files={"file": ("증거.txt", b"original bytes", "text/plain")})
        self.assertEqual(upload.status_code, 201, upload.text)
        record = upload.json()
        self.assertEqual(record["filename"], "증거.txt")
        self.assertIsNone(record["selectedAt"])
        self.assertTrue(record["registeredAt"])
        self.assertEqual(record["size"], 14)
        self.assertEqual(record["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(len(record["sha256"]), 64)
        evidence_id = record["id"]
        self.assertEqual(len((await self.client.get("/api/evidence", headers=headers)).json()), 1)
        self.assertEqual((await self.client.get(f"/api/evidence/{evidence_id}/download", headers=headers)).content, b"original bytes")
        self.assertEqual((await self.client.get(f"/api/evidence/{evidence_id}", headers={"Authorization": f"Bearer {other}"})).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/evidence/{evidence_id}/download", headers={"Authorization": f"Bearer {other}"})).status_code, 404)
        self.assertEqual((await self.client.post(f"/api/evidence/{evidence_id}/verify", headers={"Authorization": f"Bearer {other}"})).status_code, 404)
        matched = await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
        self.assertEqual(matched.status_code, 200, matched.text)
        self.assertEqual(matched.json()["result"]["status"], "MATCH")
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection:
            stored_name = connection.execute("SELECT stored_name FROM evidence WHERE id = ?", (evidence_id,)).fetchone()[0]
        file_path = self.root / "files" / stored_name
        self.assertFalse(bool(file_path.stat().st_mode & 0o222))
        file_path.chmod(0o600)
        file_path.write_bytes(b"changed bytes")
        mismatched = await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
        self.assertEqual(mismatched.json()["result"]["status"], "MISMATCH")
        details = (await self.client.get(f"/api/evidence/{evidence_id}", headers=headers)).json()
        self.assertEqual(details["verificationStatus"], "MISMATCH")
        self.assertEqual(len(details["verifications"]), 2)
        audit = (await self.client.get("/api/audit", headers=headers)).json()
        self.assertTrue({"upload", "download", "verify:MATCH", "verify:MISMATCH"}.issubset({item["action"] for item in audit}))

    async def test_shared_permissions_and_selection_time(self):
        owner = {"Authorization": "Bearer " + await self.register("owner@example.com", "등록 당시 이름")}
        reader = {"Authorization": "Bearer " + await self.register("reader@example.com", "검증 당시 이름")}
        upload = await self.client.post("/api/evidence", headers=owner,
            files={"file": ("shared.txt", b"bytes", "text/plain")},
            data={"selectedAt": "2026-11-07T17:45:00+09:00"})
        self.assertEqual(upload.status_code, 201, upload.text)
        record = upload.json()
        self.assertEqual(record["selectedAt"], "2026-11-07T08:45:00.000+00:00")
        self.assertEqual(record["registeredBy"], "등록 당시 이름")
        url = "/api/evidence/" + record["id"]
        settings = {"email": "reader@example.com"}
        for download, verify in [(False, False), (True, False), (False, True)]:
            settings.update(download=download, verify=verify)
            self.assertEqual((await self.client.put(url + "/shares", headers=owner, json=settings)).status_code, 200)
            listing = (await self.client.get("/api/evidence", headers=reader)).json()
            self.assertEqual(len(listing), 1)
            access = listing[0]["access"]
            self.assertFalse(access["isOwner"])
            self.assertIn("VIEW", access["permissions"])
            self.assertEqual("DOWNLOAD" in access["permissions"], download)
            self.assertEqual("VERIFY" in access["permissions"], verify)
            self.assertEqual((await self.client.get(url + "/download", headers=reader)).status_code, 200 if download else 403)
            self.assertEqual((await self.client.post(url + "/verify", headers=reader)).status_code, 200 if verify else 403)
        for actor in (owner, reader):
            rename = await self.client.put("/api/me", headers=actor, json={"name": "변경된 이름"})
            self.assertEqual(rename.status_code, 200)
        details = (await self.client.get(url, headers=owner)).json()
        self.assertEqual(details["registeredBy"], "등록 당시 이름")
        self.assertEqual(details["lastVerification"]["performedBy"], "검증 당시 이름")
        self.assertEqual((await self.client.get("/api/me", headers=reader)).json()["verificationCount"], 1)
        self.assertEqual((await self.client.get("/api/me", headers=owner)).json()["verificationCount"], 0)
        shares = (await self.client.get(url + "/shares", headers=owner)).json()
        self.assertEqual(len(shares), 1)
        revoke = url + "/shares/" + shares[0]["id"]
        self.assertEqual((await self.client.delete(revoke, headers=reader)).status_code, 404)
        self.assertEqual((await self.client.put(url + "/shares", headers=reader, json=settings)).status_code, 404)
        self.assertEqual((await self.client.delete(revoke, headers=owner)).status_code, 200)
        self.assertEqual((await self.client.get("/api/evidence", headers=reader)).json(), [])
        self.assertEqual((await self.client.get(url, headers=reader)).status_code, 404)
        self.assertEqual((await self.client.post(url + "/verify", headers=reader)).status_code, 404)

    async def test_actor_migration_preserves_unknown_names(self):
        headers = {"Authorization": "Bearer " + await self.register("legacy@example.com")}
        upload = await self.client.post("/api/evidence", headers=headers,
                                        files={"file": ("legacy.txt", b"legacy", "text/plain")})
        url = "/api/evidence/" + upload.json()["id"]
        self.assertEqual((await self.client.post(url + "/verify", headers=headers)).status_code, 200)
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection, connection:
            connection.execute("ALTER TABLE evidence DROP COLUMN registered_by")
            connection.execute("ALTER TABLE verifications DROP COLUMN performed_by")
        transport = httpx.ASGITransport(app=main.create_app(self.root))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as restarted:
            details = (await restarted.get(url, headers=headers)).json()
            self.assertIsNone(details["registeredBy"])
            self.assertIsNone(details["lastVerification"]["performedBy"])
            new_result = (await restarted.post(url + "/verify", headers=headers)).json()["result"]
            self.assertEqual(new_result["performedBy"], "Tester")

    async def test_auth_validation_and_upload_limits(self):
        owner = await self.register("owner@example.com")
        headers = {"Authorization": f"Bearer {owner}"}
        self.assertEqual((await self.client.post("/api/auth/register", json={"email": "owner@example.com", "name": "Tester", "password": "Strong!123"})).status_code, 409)
        self.assertEqual((await self.client.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong"})).status_code, 401)
        self.assertEqual((await self.client.post("/api/evidence", headers=headers, files={"file": ("bad.exe", b"x", "application/octet-stream")})).status_code, 415)
        self.assertEqual((await self.client.post("/api/evidence", headers=headers, files={"file": ("empty.txt", b"data", "text/plain")}, data={"name": "x" * 201})).status_code, 422)
        old_limit = main.MAX_FILE_SIZE
        main.MAX_FILE_SIZE = 3
        try:
            response = await self.client.post("/api/evidence", headers=headers, files={"file": ("large.txt", b"1234", "text/plain")})
            self.assertEqual(response.status_code, 413, response.text)
            self.assertEqual(list((self.root / "files").iterdir()), [])
        finally:
            main.MAX_FILE_SIZE = old_limit
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection:
            password = connection.execute("SELECT password_hash FROM users WHERE email = ?", ("owner@example.com",)).fetchone()[0]
        self.assertTrue(password.startswith("scrypt$"))
        self.assertNotIn("Strong!123", password)
        self.assertEqual((await self.client.put("/api/auth/password", headers=headers, json={"current_password": "wrong", "new_password": "NewStrong!123"})).status_code, 403)
        self.assertEqual((await self.client.put("/api/auth/password", headers=headers, json={"current_password": "Strong!123", "new_password": "NewStrong!123"})).status_code, 200)
        self.assertEqual((await self.client.post("/api/auth/login", json={"email": "owner@example.com", "password": "Strong!123"})).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={"email": "owner@example.com", "password": "NewStrong!123"})).status_code, 200)
        self.assertEqual((await self.client.post("/api/auth/logout", headers=headers)).status_code, 200)
        self.assertEqual((await self.client.get("/api/me", headers=headers)).status_code, 401)

    async def test_profile_and_activity_are_scoped_to_current_user(self):
        owner = await self.register("owner@example.com", "가입 이름")
        other = await self.register("other@example.com", "다른 사용자")
        headers = {"Authorization": f"Bearer {owner}"}
        other_headers = {"Authorization": f"Bearer {other}"}
        account = (await self.client.get("/api/me", headers=headers)).json()
        self.assertEqual((account["name"], account["email"]), ("가입 이름", "owner@example.com"))
        self.assertTrue(account["createdAt"])
        self.assertEqual((await self.client.put("/api/me", headers=headers, json={"name": "  "})).status_code, 422)
        updated = await self.client.put("/api/me", headers=headers, json={"name": "수정한 이름"})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual((await self.client.get("/api/me", headers=headers)).json()["name"], "수정한 이름")
        self.assertEqual((await self.client.get("/api/me", headers=other_headers)).json()["name"], "다른 사용자")
        uploaded = await self.client.post("/api/evidence", headers=headers,
                                          files={"file": ("활동.txt", b"bytes", "text/plain")})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        activities = (await self.client.get("/api/audit", headers=headers)).json()
        self.assertTrue({"register", "login", "profile_update", "upload"}.issubset({item["action"] for item in activities}))
        upload_activity = next(item for item in activities if item["action"] == "upload")
        self.assertEqual(upload_activity["evidenceFilename"], "활동.txt")
        self.assertEqual(upload_activity["evidenceId"], uploaded.json()["id"])
        self.assertFalse(any(item["evidenceId"] == upload_activity["evidenceId"] for item in (await self.client.get("/api/audit", headers=other_headers)).json()))


if __name__ == "__main__":
    unittest.main()
