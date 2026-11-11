"""3단계 테스트: 수정·삭제 금지 트리거, 작업 당시 이름 저장, 자동 마이그레이션

실제 비밀번호·키는 쓰지 않습니다. 모든 데이터는 .test-data/ 아래 임시 폴더에 만들고 끝나면 지웁니다.
"""

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import httpx

from server import db, main

# 2단계까지 쓰던 테이블 구조 (마이그레이션 전 DB를 그대로 흉내 내기 위해 코드와 따로 적어 둠)
STAGE2_SCHEMA = [
    """CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
       password_hash TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
       expires_at TEXT NOT NULL)""",
    """CREATE TABLE evidence (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
       filename TEXT NOT NULL, stored_name TEXT UNIQUE NOT NULL, type TEXT NOT NULL, mime TEXT NOT NULL,
       size INTEGER NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, description TEXT NOT NULL, memo TEXT NOT NULL,
       registered_at TEXT NOT NULL, sha256 TEXT NOT NULL, verification_status TEXT NOT NULL DEFAULT 'NOT_VERIFIED')""",
    """CREATE TABLE verifications (id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id),
       user_id TEXT NOT NULL REFERENCES users(id), started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
       expected_hash TEXT NOT NULL, current_hash TEXT, status TEXT NOT NULL, reason TEXT NOT NULL)""",
    """CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
       action TEXT NOT NULL, evidence_id TEXT, created_at TEXT NOT NULL)""",
]
LEGACY_HASH = "a" * 64
LEGACY_TOKEN = "legacy-test-token"


class ImmutableRecordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.create_app(self.root)), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temporary.cleanup()

    def raw(self) -> sqlite3.Connection:
        """API를 거치지 않고 DB 파일을 직접 여는 연결 (누군가 DB를 직접 고치려는 상황을 흉내 냄)"""
        return sqlite3.connect(self.root / "evidence.sqlite3")

    async def register(self, email, name):
        response = await self.client.post("/api/auth/register", json={"email": email, "name": name, "password": "Strong!123"})
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    async def upload_and_verify(self, headers):
        response = await self.client.post("/api/evidence", headers=headers, files={"file": ("a.txt", b"evidence", "text/plain")})
        self.assertEqual(response.status_code, 201, response.text)
        evidence_id = response.json()["id"]
        response = await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return evidence_id

    def assertBlocked(self, connection, sql, params=(), word="IMMUTABLE"):
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            connection.execute(sql, params)
        self.assertIn(word, str(caught.exception))

    async def test_triggers_block_update_and_delete(self):
        headers = await self.register("owner@example.com", "소유자")
        evidence_id = await self.upload_and_verify(headers)
        with closing(self.raw()) as connection:
            # 증거: verification_status 말고는 수정 불가, 삭제 불가
            self.assertBlocked(connection, "UPDATE evidence SET name = 'x' WHERE id = ?", (evidence_id,))
            self.assertBlocked(connection, "UPDATE evidence SET sha256 = ? WHERE id = ?", ("b" * 64, evidence_id))
            self.assertBlocked(connection, "UPDATE evidence SET registered_by_name = 'x' WHERE id = ?", (evidence_id,))
            self.assertBlocked(connection, "DELETE FROM evidence WHERE id = ?", (evidence_id,))
            connection.execute("UPDATE evidence SET verification_status = 'MISMATCH' WHERE id = ?", (evidence_id,))
            self.assertBlocked(connection, "UPDATE evidence SET verification_status = 'OK' WHERE id = ?", (evidence_id,), "INVALID")
            # 검증 결과·활동 기록: 행 전체 수정·삭제 불가
            self.assertBlocked(connection, "UPDATE verifications SET status = 'MISMATCH'")
            self.assertBlocked(connection, "UPDATE verifications SET performed_by_name = 'x'")
            self.assertBlocked(connection, "DELETE FROM verifications")
            self.assertBlocked(connection, "UPDATE audit SET action = 'x'")
            self.assertBlocked(connection, "DELETE FROM audit")
            # 새 행은 이름과 허용된 상태 값이 있어야 함
            owner_id = connection.execute("SELECT owner_id FROM evidence").fetchone()[0]
            self.assertBlocked(connection, """INSERT INTO evidence (id, owner_id, name, filename, stored_name, type, mime, size,
                kind, source, description, memo, registered_at, sha256) VALUES
                ('EV-x', ?, 'n', 'f', 's', 'TXT', 'text/plain', 1, 'k', 's', '', '', 't', ?)""", (owner_id, LEGACY_HASH), "INVALID")
            self.assertBlocked(connection, """INSERT INTO evidence (id, owner_id, name, filename, stored_name, type, mime, size,
                kind, source, description, memo, registered_at, sha256, verification_status, registered_by_name) VALUES
                ('EV-y', ?, 'n', 'f', 's2', 'TXT', 'text/plain', 1, 'k', 's', '', '', 't', ?, 'OK', '등록자')""",
                               (owner_id, LEGACY_HASH), "INVALID")
            self.assertBlocked(connection, """INSERT INTO verifications (id, evidence_id, user_id, started_at, completed_at,
                expected_hash, status, reason, performed_by_name) VALUES ('V-x', ?, ?, 't', 't', ?, 'MATCH', 'r', '  ')""",
                               (evidence_id, owner_id, LEGACY_HASH), "INVALID")
            self.assertBlocked(connection, """INSERT INTO verifications (id, evidence_id, user_id, started_at, completed_at,
                expected_hash, status, reason, performed_by_name) VALUES ('V-x', ?, ?, 't', 't', ?, 'OK', 'r', '검증자')""",
                               (evidence_id, owner_id, LEGACY_HASH), "INVALID")

    async def test_names_are_kept_after_profile_change(self):
        headers = await self.register("owner@example.com", "처음 이름")
        evidence_id = await self.upload_and_verify(headers)
        response = await self.client.put("/api/me", headers=headers, json={"name": "바뀐 이름"})
        self.assertEqual(response.status_code, 200, response.text)
        await self.client.post(f"/api/evidence/{evidence_id}/verify", headers=headers)
        evidence = (await self.client.get(f"/api/evidence/{evidence_id}", headers=headers)).json()
        # 등록자와 첫 검증자는 그때 이름, 이름을 바꾼 뒤의 검증자는 새 이름
        self.assertEqual(evidence["registeredBy"], "처음 이름")
        self.assertEqual([item["performedBy"] for item in evidence["verifications"]], ["바뀐 이름", "처음 이름"])
        self.assertEqual(evidence["lastVerification"]["performedBy"], "바뀐 이름")


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "evidence.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def make_stage2_db(self):
        """2단계 구조 + 데이터 1세트(사용자·세션·증거·검증·활동)가 들어 있는 DB를 만듭니다. user_version은 0"""
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="milliseconds")
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            for statement in STAGE2_SCHEMA:
                connection.execute(statement)
            connection.execute("INSERT INTO users VALUES ('u1', 'old@example.com', '예전 사용자', 'scrypt$x', 't0')")
            connection.execute("INSERT INTO sessions VALUES (?, 'u1', ?)", (hashlib.sha256(LEGACY_TOKEN.encode()).hexdigest(), expires))
            connection.execute("""INSERT INTO evidence VALUES ('EV-old', 'u1', '예전 증거', 'old.txt', ?, 'TXT', 'text/plain', 3,
                '파일', '직접 수집', '', '', '2026-11-01T00:00:00.000+00:00', ?, 'MATCH')""", ("0" * 32, LEGACY_HASH))
            connection.execute("""INSERT INTO verifications VALUES ('VERIFY-old', 'EV-old', 'u1', 't1', '2026-11-02T00:00:00.000+00:00',
                ?, ?, 'MATCH', '일치')""", (LEGACY_HASH, LEGACY_HASH))
            connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES ('u1', 'upload', 'EV-old', 't2')")

    def snapshot_rows(self, connection):
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("users", "evidence", "verifications", "audit")}

    async def test_stage2_db_is_migrated_with_backup(self):
        self.make_stage2_db()
        with closing(sqlite3.connect(self.db_path)) as connection:
            before = self.snapshot_rows(connection)

        app = main.create_app(self.root)

        backups = list((self.root / "backups").glob("evidence-v0-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(self.snapshot_rows(backup), before)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
            after = self.snapshot_rows(connection)
            # ALTER TABLE ADD COLUMN은 수정 금지 트리거를 실행하지 않고, 기존 값은 그대로이며 새 컬럼만 NULL로 붙음
            self.assertEqual([tuple(row[:-1]) for row in after["evidence"]], [tuple(row) for row in before["evidence"]])
            self.assertEqual([tuple(row[:-1]) for row in after["verifications"]], [tuple(row) for row in before["verifications"]])
            self.assertIsNone(after["evidence"][0][-1])
            self.assertIsNone(after["verifications"][0][-1])
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE evidence SET name = 'x'")

        # API에서는 기존 기록의 이름이 null (화면에서 "미기록")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            evidence = (await client.get("/api/evidence/EV-old", headers={"Authorization": f"Bearer {LEGACY_TOKEN}"})).json()
        self.assertIsNone(evidence["registeredBy"])
        self.assertIsNone(evidence["lastVerification"]["performedBy"])

        # 이미 최신이면 다시 시작해도 백업을 새로 만들지 않음
        main.create_app(self.root)
        self.assertEqual(len(list((self.root / "backups").iterdir())), 1)

    def test_failed_migration_rolls_back_everything(self):
        self.make_stage2_db()

        def failing(connection):
            db._migration_2_immutable_records(connection)
            raise RuntimeError("테스트용 실패")

        with mock.patch.object(db, "MIGRATIONS", [(1, db._migration_1_base_schema), (2, failing)]):
            with self.assertRaises(RuntimeError):
                main.create_app(self.root)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(evidence)")}
            self.assertNotIn("registered_by_name", columns)
            triggers = connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
            self.assertEqual(triggers, [])

    def test_newer_db_version_stops_server(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        with self.assertRaises(RuntimeError):
            main.create_app(self.root)


if __name__ == "__main__":
    unittest.main()
