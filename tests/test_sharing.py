"""5단계 테스트: 증거 공유와 권한(RBAC), 사용자 역할(role)

등장인물: 소유자(owner) / 공유받는 사람(friend) / 관계없는 사람(stranger)
실제 비밀번호·키는 쓰지 않습니다. 모든 데이터는 .test-data/ 아래 임시 폴더에 만들고 끝나면 지웁니다.
"""

import hmac
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import httpx

from server import access, db, main

TEST_PASSWORD = "Strong!123"  # 테스트 전용 값


class SharingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.create_app(self.root)), base_url="http://test")
        self.owner, self.owner_id = await self.register("owner@example.com", "소유자")
        self.friend, self.friend_id = await self.register("friend@example.com", "친구")
        self.stranger, _ = await self.register("stranger@example.com", "모르는 사람")
        response = await self.client.post("/api/evidence", headers=self.owner, files={"file": ("a.txt", b"evidence", "text/plain")})
        self.assertEqual(response.status_code, 201, response.text)
        self.evidence_id = response.json()["id"]
        self.base = f"/api/evidence/{self.evidence_id}"

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temporary.cleanup()

    def raw(self) -> sqlite3.Connection:
        """API를 거치지 않고 DB 파일을 직접 여는 연결"""
        connection = sqlite3.connect(self.root / "evidence.sqlite3")
        connection.row_factory = sqlite3.Row
        return connection

    async def register(self, email, name):
        response = await self.client.post("/api/auth/register", json={"email": email, "name": name, "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        return {"Authorization": f"Bearer {body['token']}"}, body["user"]["id"]

    async def share(self, download=False, verify=False, email="friend@example.com", headers=None):
        return await self.client.put(f"{self.base}/shares", headers=headers or self.owner,
                                     json={"email": email, "download": download, "verify": verify})

    async def events(self, headers=None):
        response = await self.client.get(f"{self.base}/custody", headers=headers or self.owner)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["events"]

    def audit_actions(self, user_id):
        with closing(self.raw()) as connection:
            return [row[0] for row in connection.execute("SELECT action FROM audit WHERE user_id = ? ORDER BY id", (user_id,))]

    # ---------------------------------------------------------------- 기본 흐름

    async def test_owner_access_field_and_role(self):
        evidence = (await self.client.get(self.base, headers=self.owner)).json()
        self.assertEqual(evidence["access"], {"isOwner": True, "owner": {"id": self.owner_id, "name": "소유자"},
                                              "permissions": ["VIEW", "DOWNLOAD", "VERIFY"],
                                              "canView": True, "canDownload": True, "canVerify": True})
        # 로그인·내 정보 응답의 role (기존 계정·새 계정 모두 USER)
        login = (await self.client.post("/api/auth/login", json={"email": "owner@example.com", "password": TEST_PASSWORD})).json()
        self.assertEqual(login["user"]["role"], "USER")
        self.assertEqual((await self.client.get("/api/me", headers=self.owner)).json()["role"], "USER")

    async def test_view_only_share(self):
        # 공유 전: 친구에게는 증거가 없는 것처럼 보임
        self.assertEqual((await self.client.get(self.base, headers=self.friend)).status_code, 404)
        self.assertEqual((await self.client.get("/api/evidence?scope=shared", headers=self.friend)).json(), [])

        response = await self.share()
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        self.assertEqual({key: row[key] for key in ("id", "email", "name", "download", "verify")},
                         {"id": self.friend_id, "email": "friend@example.com", "name": "친구", "download": False, "verify": False})
        self.assertIn("grantedAt", row)
        self.assertEqual((await self.client.get(f"{self.base}/shares", headers=self.owner)).json(), [row])

        # 친구: 상세는 보임, 권한은 VIEW만
        evidence = (await self.client.get(self.base, headers=self.friend)).json()
        self.assertEqual(evidence["access"], {"isOwner": False, "owner": {"id": self.owner_id, "name": "소유자"},
                                              "permissions": ["VIEW"], "canView": True, "canDownload": False, "canVerify": False})
        # 목록: 기본은 내 증거만(기존 동작), shared·all에서만 보임
        self.assertEqual((await self.client.get("/api/evidence", headers=self.friend)).json(), [])
        self.assertEqual([e["id"] for e in (await self.client.get("/api/evidence?scope=shared", headers=self.friend)).json()], [self.evidence_id])
        self.assertEqual([e["id"] for e in (await self.client.get("/api/evidence?scope=all", headers=self.friend)).json()], [self.evidence_id])
        self.assertEqual((await self.client.get("/api/evidence?scope=shared", headers=self.owner)).json(), [])
        response = await self.client.get("/api/evidence?scope=mine", headers=self.owner)
        self.assertEqual((response.status_code, response.json()["detail"]), (422, "scope 값은 owned, shared, all 중 하나여야 합니다."))

        # 친구도 관리 이력은 볼 수 있음 (11/15 결정)
        self.assertTrue((await self.client.get(f"{self.base}/custody/verify", headers=self.friend)).json()["valid"])
        # 관계없는 사람은 여전히 404
        for path in (self.base, f"{self.base}/custody", f"{self.base}/download", f"{self.base}/shares"):
            self.assertEqual((await self.client.get(path, headers=self.stranger)).status_code, 404, path)

    async def test_missing_permission_is_403_with_access_denied_event(self):
        await self.share()
        response = await self.client.get(f"{self.base}/download", headers=self.friend)
        self.assertEqual((response.status_code, response.json()["detail"]), (403, "다운로드 권한이 없습니다."))
        response = await self.client.post(f"{self.base}/verify", headers=self.friend)
        self.assertEqual((response.status_code, response.json()["detail"]), (403, "검증 권한이 없습니다."))
        response = await self.client.get(f"{self.base}/shares", headers=self.friend)
        self.assertEqual((response.status_code, response.json()["detail"]), (403, "공유 관리는 증거 소유자만 할 수 있습니다."))

        denied = [e for e in await self.events() if e["eventType"] == "ACCESS_DENIED"]
        self.assertEqual([(e["result"], e["actor"]["name"], e["details"]) for e in denied], [
            ("FAILURE", "친구", {"actorName": "친구", "requiredPermission": "DOWNLOAD"}),
            ("FAILURE", "친구", {"actorName": "친구", "requiredPermission": "VERIFY"}),
            ("FAILURE", "친구", {"actorName": "친구", "requiredPermission": "OWNER"}),
        ])
        # 거부된 작업은 기록되지 않음 (다운로드·검증 기록 없음)
        self.assertEqual(self.audit_actions(self.friend_id), ["register", "login"])
        with closing(self.raw()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0], 0)

    async def test_grant_change_and_revoke(self):
        # 다운로드·검증까지 허락 → 친구가 둘 다 할 수 있음
        await self.share(download=True, verify=True)
        self.assertEqual((await self.client.get(f"{self.base}/download", headers=self.friend)).content, b"evidence")
        result = (await self.client.post(f"{self.base}/verify", headers=self.friend)).json()["result"]
        self.assertEqual((result["status"], result["performedBy"]), ("MATCH", "친구"))

        # 다운로드 체크만 끔 → DOWNLOAD만 회수
        row = (await self.share(download=False, verify=True)).json()
        self.assertEqual((row["download"], row["verify"]), (False, True))
        self.assertEqual((await self.client.get(f"{self.base}/download", headers=self.friend)).status_code, 403)

        # 같은 값으로 다시 저장 → 아무것도 기록하지 않음
        before = len(await self.events())
        await self.share(download=False, verify=True)
        self.assertEqual(len(await self.events()), before)

        # 공유 해제 → 모든 권한 회수, 친구에게는 다시 404
        response = await self.client.delete(f"{self.base}/shares/{self.friend_id}", headers=self.owner)
        self.assertEqual(response.json(), {"id": self.friend_id, "revoked": True})
        self.assertEqual((await self.client.get(self.base, headers=self.friend)).status_code, 404)
        self.assertEqual((await self.client.get(f"{self.base}/shares", headers=self.owner)).json(), [])
        response = await self.client.delete(f"{self.base}/shares/{self.friend_id}", headers=self.owner)
        self.assertEqual((response.status_code, response.json()["detail"]), (404, "공유 정보를 찾을 수 없습니다."))

        permission_events = [(e["eventType"], e["details"]["permission"]) for e in await self.events()
                             if e["eventType"].startswith("PERMISSION_")]
        self.assertEqual(permission_events, [
            ("PERMISSION_GRANTED", "VIEW"), ("PERMISSION_GRANTED", "DOWNLOAD"), ("PERMISSION_GRANTED", "VERIFY"),
            ("PERMISSION_REVOKED", "DOWNLOAD"),
            ("PERMISSION_REVOKED", "VERIFY"), ("PERMISSION_REVOKED", "VIEW"),
        ])
        granted = next(e for e in await self.events() if e["eventType"] == "PERMISSION_GRANTED")
        self.assertEqual(granted["details"], {"actorName": "소유자", "targetUserId": self.friend_id,
                                              "targetName": "친구", "permission": "VIEW"})
        self.assertEqual(self.audit_actions(self.owner_id)[-3:], ["share_update", "share_update", "share_revoke"])
        # 회수한 행은 지워지지 않고 revoked_at만 채워짐
        with closing(self.raw()) as connection:
            rows = connection.execute("SELECT permission, revoked_at FROM evidence_permissions ORDER BY id").fetchall()
        self.assertEqual([row["permission"] for row in rows], ["VIEW", "DOWNLOAD", "VERIFY"])
        self.assertTrue(all(row["revoked_at"] for row in rows))
        # 다시 공유하면 새 행이 생김
        await self.share()
        with closing(self.raw()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence_permissions").fetchone()[0], 4)
        self.assertTrue((await self.client.get(f"{self.base}/custody/verify", headers=self.owner)).json()["valid"])

    async def test_share_input_errors(self):
        cases = [
            ("nobody@example.com", 404, "사용자를 찾을 수 없습니다."),
            ("owner@example.com", 422, "증거 소유자에게는 공유할 수 없습니다."),
            ("not-an-email", 422, "이메일 형식을 확인해 주세요."),
        ]
        for email, code, detail in cases:
            response = await self.share(email=email)
            self.assertEqual((response.status_code, response.json()["detail"]), (code, detail), email)
        # 이메일은 대소문자·공백 무시
        self.assertEqual((await self.share(email="  Friend@Example.com ")).status_code, 200)
        # 공유받은 사람은 다른 사람에게 다시 공유할 수 없음
        response = await self.share(email="stranger@example.com", headers=self.friend)
        self.assertEqual(response.status_code, 403)
        # 실패한 요청은 권한 표에 아무것도 남기지 않음
        with closing(self.raw()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence_permissions").fetchone()[0], 1)

    async def test_revoked_during_verification_saves_nothing(self):
        await self.share(verify=True)
        real_compare = hmac.compare_digest

        def revoke_then_compare(*args):
            # 해시 계산이 끝나고 기록하기 전에 소유자가 VERIFY 권한을 회수한 상황
            with closing(self.raw()) as connection, connection:
                connection.execute("UPDATE evidence_permissions SET revoked_at = 't' WHERE permission = 'VERIFY'")
            return real_compare(*args)

        with mock.patch("server.routers.evidence.hmac.compare_digest", side_effect=revoke_then_compare):
            response = await self.client.post(f"{self.base}/verify", headers=self.friend)
        self.assertEqual(response.status_code, 403)
        with closing(self.raw()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT verification_status FROM evidence").fetchone()[0], "NOT_VERIFIED")
        self.assertEqual((await self.events())[-1]["eventType"], "ACCESS_DENIED")

    # ---------------------------------------------------------------- DB 트리거

    async def test_permission_triggers(self):
        await self.share(download=True)
        with closing(self.raw()) as connection:
            def blocked(sql, params=(), word="INVALID"):
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    connection.execute(sql, params)
                self.assertIn(word, str(caught.exception))

            insert = """INSERT INTO evidence_permissions (evidence_id, user_id, permission, granted_by, granted_at, revoked_at)
                        VALUES (?, ?, ?, ?, 't', ?)"""
            stranger_id = connection.execute("SELECT id FROM users WHERE email = 'stranger@example.com'").fetchone()[0]
            blocked("DELETE FROM evidence_permissions", word="IMMUTABLE")
            blocked("UPDATE evidence_permissions SET permission = 'VERIFY' WHERE permission = 'DOWNLOAD'", word="IMMUTABLE")
            blocked("UPDATE evidence_permissions SET granted_at = 'x'", word="IMMUTABLE")
            blocked(insert, (self.evidence_id, self.friend_id, "EDIT", self.owner_id, None))       # 권한 값 (VIEW 있는 사람)
            blocked(insert, (self.evidence_id, stranger_id, "VIEW", self.owner_id, "t"))           # 회수된 행 추가
            blocked(insert, (self.evidence_id, self.owner_id, "VIEW", self.owner_id, None))        # 소유자 본인
            blocked(insert, (self.evidence_id, stranger_id, "VIEW", self.friend_id, None))         # 재공유
            blocked(insert, (self.evidence_id, stranger_id, "DOWNLOAD", self.owner_id, None))      # VIEW 없이
            blocked("UPDATE evidence_permissions SET revoked_at = 't' WHERE permission = 'VIEW'")  # VIEW 먼저 회수
            with self.assertRaises(sqlite3.IntegrityError):                                         # 살아 있는 행 중복
                connection.execute(insert, (self.evidence_id, self.friend_id, "VIEW", self.owner_id, None))
            # 회수는 한 번만
            connection.execute("UPDATE evidence_permissions SET revoked_at = 't' WHERE permission = 'DOWNLOAD'")
            blocked("UPDATE evidence_permissions SET revoked_at = 't2' WHERE permission = 'DOWNLOAD'", word="IMMUTABLE")
            # 역할 값
            blocked("UPDATE users SET role = 'ROOT'")
            blocked("INSERT INTO users (id, email, name, password_hash, created_at, role) VALUES ('u9', 'r@example.com', 'r', 'x', 't', 'ROOT')")
            connection.execute("UPDATE users SET role = 'ADMIN' WHERE id = ?", (self.owner_id,))

    def test_permission_lists_match(self):
        self.assertEqual(access.PERMISSIONS, db.PERMISSION_TYPES)


class SharingMigrationTests(unittest.TestCase):
    def setUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "evidence.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def test_stage4_db_gets_role_and_permission_table(self):
        # 4단계 구조(user_version 3) DB + 기존 사용자 1명
        with closing(sqlite3.connect(self.db_path, isolation_level=None)) as connection:
            connection.row_factory = sqlite3.Row
            with db.write_transaction(connection):
                for _, apply in db.MIGRATIONS[:3]:
                    apply(connection)
                connection.execute("PRAGMA user_version = 3")
                connection.execute("INSERT INTO users VALUES ('u1', 'old@example.com', '예전 사용자', 'scrypt$x', 't0')")

        main.create_app(self.root)

        self.assertEqual(len(list((self.root / "backups").glob("evidence-v3-*.sqlite3"))), 1)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
            self.assertEqual(connection.execute("SELECT role FROM users").fetchone()[0], "USER")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence_permissions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
