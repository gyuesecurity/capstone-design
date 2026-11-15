import base64
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import httpx

os.environ.setdefault("EVIDENCE_MASTER_KEYS", json.dumps({
    "v1": base64.urlsafe_b64encode(b"K" * 32).decode("ascii"),
    "v2": base64.urlsafe_b64encode(b"L" * 32).decode("ascii"),
}))
os.environ.setdefault("EVIDENCE_KEY_VERSION", "v1")

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

    async def test_aad_v1_schema_is_canonical_and_does_not_include_display_user_name(self):
        aad = main.evidence_aad_v1(
            evidence_id="EV-1", owner_id="USER-1", name="증거", filename="a.txt",
            kind="파일", source="직접 수집", registered_at="2026-11-12T00:00:00.000+00:00",
            key_version="v1",
        )
        self.assertEqual(
            aad,
            (
                '{"evidenceId":"EV-1","filename":"a.txt","keyVersion":"v1","kind":"파일",'
                '"name":"증거","ownerId":"USER-1",'
                '"registeredAt":"2026-11-12T00:00:00.000+00:00","schema":"v1",'
                '"source":"직접 수집"}'
            ).encode(),
        )

    async def test_upload_verify_access_and_audit(self):
        owner = await self.register("owner@example.com")
        other = await self.register("other@example.com")
        headers = {"Authorization": f"Bearer {owner}"}
        self.assertEqual((await self.client.get("/api/evidence")).status_code, 401)
        upload = await self.client.post("/api/evidence", headers=headers,
                                        data={"name": "제출 자료", "source": "직접 수집",
                                              "client_selected_at": "2026-11-03T10:20:30+09:00"},
                                        files={"file": ("증거.txt", b"original bytes", "text/plain")})
        self.assertEqual(upload.status_code, 201, upload.text)
        record = upload.json()
        self.assertEqual(record["filename"], "증거.txt")
        self.assertEqual(record["size"], 14)
        self.assertEqual(record["storedSize"], 14)
        self.assertEqual(record["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(len(record["sha256"]), 64)
        self.assertEqual(record["encryption"]["algorithm"], "AES-256-GCM")
        self.assertEqual(record["encryption"]["keyVersion"], "v1")
        self.assertEqual(record["encryption"]["keyDerivation"], "HKDF-SHA256")
        self.assertEqual(record["encryption"]["aadVersion"], "v1")
        self.assertTrue(record["encryption"]["salt"])
        self.assertEqual(record["selectedAt"], "2026-11-03T01:20:30.000+00:00")
        self.assertNotEqual(record["selectedAt"], record["registeredAt"])
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
            stored_name, algorithm, nonce, tag = connection.execute(
                "SELECT stored_name, encryption_algorithm, encryption_nonce, encryption_tag FROM evidence WHERE id = ?",
                (evidence_id,),
            ).fetchone()
        self.assertEqual(algorithm, "AES-256-GCM")
        self.assertTrue(nonce)
        self.assertTrue(tag)
        file_path = self.root / "files" / stored_name
        self.assertNotEqual(file_path.read_bytes(), b"original bytes")
        self.assertNotIn(b"original bytes", file_path.read_bytes())
        self.assertFalse(bool(file_path.stat().st_mode & 0o222))
        file_path.chmod(0o600)
        file_path.write_bytes(b"changed bytes")
        mismatched = await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
        self.assertEqual(mismatched.json()["result"]["status"], "MISMATCH")
        self.assertEqual(mismatched.json()["result"]["failureCode"], "AUTHENTICATION_FAILED")
        self.assertIsNone(mismatched.json()["result"]["currentHash"])
        self.assertEqual((await self.client.get(f"/api/evidence/{evidence_id}/download", headers=headers)).status_code, 409)
        details = (await self.client.get(f"/api/evidence/{evidence_id}", headers=headers)).json()
        self.assertEqual(details["verificationStatus"], "MISMATCH")
        self.assertEqual(len(details["verifications"]), 2)
        audit = (await self.client.get("/api/audit", headers=headers)).json()
        self.assertTrue({"upload", "download", "verify:MATCH", "verify:MISMATCH"}.issubset({item["action"] for item in audit}))
        self.assertEqual({item["chainStatus"] for item in audit}, {"VALID"})

    async def test_auth_validation_and_upload_limits(self):
        owner = await self.register("owner@example.com")
        headers = {"Authorization": f"Bearer {owner}"}
        self.assertEqual((await self.client.post("/api/auth/register", json={"email": "owner@example.com", "name": "Tester", "password": "Strong!123"})).status_code, 409)
        self.assertEqual((await self.client.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong"})).status_code, 401)
        for _ in range(4):
            await self.client.post("/api/auth/login", json={"email": "blocked@example.com", "password": "wrong"})
        fifth = await self.client.post("/api/auth/login", json={"email": "blocked@example.com", "password": "wrong"})
        self.assertEqual(fifth.status_code, 401)
        self.assertEqual(
            (await self.client.post("/api/auth/login", json={"email": "blocked@example.com", "password": "wrong"})).status_code,
            429,
        )
        self.assertEqual((await self.client.post("/api/evidence", headers=headers, files={"file": ("bad.exe", b"x", "application/octet-stream")})).status_code, 415)
        self.assertEqual((await self.client.post("/api/evidence", headers=headers, files={"file": ("fake.png", b"not a png", "image/png")})).status_code, 415)
        self.assertEqual((await self.client.post("/api/evidence", headers=headers, files={"file": ("empty.txt", b"", "text/plain")})).status_code, 422)
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
        secondary = await self.client.post(
            "/api/auth/login", json={"email": "owner@example.com", "password": "Strong!123"}
        )
        secondary_headers = {"Authorization": f"Bearer {secondary.json()['token']}"}
        self.assertEqual((await self.client.put("/api/auth/password", headers=headers, json={"current_password": "wrong", "new_password": "NewStrong!123"})).status_code, 403)
        self.assertEqual((await self.client.put("/api/auth/password", headers=headers, json={"current_password": "Strong!123", "new_password": "NewStrong!123"})).status_code, 200)
        self.assertEqual((await self.client.get("/api/me", headers=secondary_headers)).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={"email": "owner@example.com", "password": "Strong!123"})).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={"email": "owner@example.com", "password": "NewStrong!123"})).status_code, 200)
        self.assertEqual((await self.client.post("/api/auth/logout", headers=headers)).status_code, 200)
        self.assertEqual((await self.client.get("/api/me", headers=headers)).status_code, 401)

    async def test_encrypted_evidence_survives_app_restart(self):
        token = await self.register("restart@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        uploaded = await self.client.post(
            "/api/evidence", headers=headers, files={"file": ("restart.txt", b"persistent secret", "text/plain")}
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        evidence_id = uploaded.json()["id"]
        self.assertFalse((self.root / "keys").exists())

        await self.client.aclose()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.create_app(self.root)), base_url="http://test"
        )
        login = await self.client.post(
            "/api/auth/login", json={"email": "restart@example.com", "password": "Strong!123"}
        )
        restarted_headers = {"Authorization": f"Bearer {login.json()['token']}"}
        download = await self.client.get(f"/api/evidence/{evidence_id}/download", headers=restarted_headers)
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(download.content, b"persistent secret")
        verified = await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=restarted_headers)
        self.assertEqual(verified.json()["result"]["status"], "MATCH")

    async def test_key_rotation_keeps_old_evidence_readable(self):
        token = await self.register("rotation@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        first = await self.client.post(
            "/api/evidence", headers=headers, files={"file": ("v1.txt", b"version one", "text/plain")}
        )
        self.assertEqual(first.status_code, 201, first.text)
        first_id = first.json()["id"]
        await self.client.aclose()

        with mock.patch.dict("os.environ", {"EVIDENCE_KEY_VERSION": "v2"}):
            self.client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.create_app(self.root)), base_url="http://test"
            )
        login = await self.client.post(
            "/api/auth/login", json={"email": "rotation@example.com", "password": "Strong!123"}
        )
        rotated_headers = {"Authorization": f"Bearer {login.json()['token']}"}
        old_download = await self.client.get(f"/api/evidence/{first_id}/download", headers=rotated_headers)
        self.assertEqual(old_download.content, b"version one")
        second = await self.client.post(
            "/api/evidence", headers=rotated_headers, files={"file": ("v2.txt", b"version two", "text/plain")}
        )
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["encryption"]["keyVersion"], "v2")
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection:
            versions = {row[0] for row in connection.execute("SELECT encryption_key_version FROM evidence")}
        self.assertEqual(versions, {"v1", "v2"})
        self.assertEqual(
            (await self.client.post(f"/api/evidence/{first_id}/verify", headers=rotated_headers)).json()["result"]["status"],
            "MATCH",
        )

    async def test_missing_key_is_not_recorded_as_evidence_tampering(self):
        token = await self.register("missing-key@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        uploaded = await self.client.post(
            "/api/evidence", headers=headers, files={"file": ("key.txt", b"key protected", "text/plain")}
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        evidence_id = uploaded.json()["id"]
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection, connection:
            connection.execute(
                "UPDATE evidence SET encryption_key_version = 'missing' WHERE id = ?", (evidence_id,)
            )

        verified = await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
        self.assertEqual(verified.status_code, 500, verified.text)
        self.assertIn("암호화 키", verified.json()["detail"])
        downloaded = await self.client.get(f"/api/evidence/{evidence_id}/download", headers=headers)
        self.assertEqual(downloaded.status_code, 500, downloaded.text)
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM verifications WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()[0]
            status = connection.execute(
                "SELECT verification_status FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(status, "NOT_VERIFIED")

    async def test_invalid_encryption_metadata_is_not_saved_as_a_verification_result(self):
        token = await self.register("invalid-metadata@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        uploaded = await self.client.post(
            "/api/evidence", headers=headers, files={"file": ("metadata.txt", b"metadata", "text/plain")}
        )
        evidence_id = uploaded.json()["id"]
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection, connection:
            connection.execute(
                "UPDATE evidence SET encryption_nonce = 'invalid!' WHERE id = ?", (evidence_id,)
            )

        verified = await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
        self.assertEqual(verified.status_code, 500, verified.text)
        self.assertIn("메타데이터", verified.json()["detail"])
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM verifications WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()[0]
            status = connection.execute(
                "SELECT verification_status FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(status, "NOT_VERIFIED")

    async def test_aad_and_audit_chain_detect_metadata_tampering(self):
        token = await self.register("tamper@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        upload = await self.client.post(
            "/api/evidence", headers=headers, data={"name": "원본 이름"},
            files={"file": ("bound.txt", b"bound content", "text/plain")},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        evidence_id = upload.json()["id"]
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection, connection:
            connection.execute("UPDATE evidence SET name = '변조된 이름' WHERE id = ?", (evidence_id,))
        verified = await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
        self.assertEqual(verified.json()["result"]["failureCode"], "AUTHENTICATION_FAILED")
        with closing(sqlite3.connect(self.root / "evidence.sqlite3")) as connection, connection:
            connection.execute("UPDATE evidence SET name = '원본 이름' WHERE id = ?", (evidence_id,))
            first_audit_id = connection.execute(
                "SELECT id FROM audit WHERE user_id = (SELECT id FROM users WHERE email = ?) ORDER BY id LIMIT 1",
                ("tamper@example.com",),
            ).fetchone()[0]
            connection.execute("UPDATE audit SET action = 'tampered' WHERE id = ?", (first_audit_id,))
        audit = (await self.client.get("/api/audit", headers=headers)).json()
        self.assertIn("INVALID", {item["chainStatus"] for item in audit})

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
