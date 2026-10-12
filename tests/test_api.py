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


if __name__ == "__main__":
    unittest.main()
