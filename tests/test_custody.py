"""4단계 테스트: Chain of Custody (이벤트 기록, 추가만 허용하는 트리거, C1 조회, C2 사슬 검사)

실제 비밀번호·키는 쓰지 않습니다. 모든 데이터는 .test-data/ 아래 임시 폴더에 만들고 끝나면 지웁니다.
"""

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import httpx

from server import custody, db, main

ZERO = "0" * 64
TEST_PASSWORD = "Strong!123"  # 테스트 전용 값


def independent_hash(row) -> str:
    """서버 코드(custody.compute_event_hash)를 쓰지 않고 설계 문서 규칙대로 직접 계산한 event_hash"""
    material = {key: row[key] for key in ("evidence_id", "sequence_no", "event_type", "actor_user_id",
                                          "event_time", "result", "details", "previous_event_hash")}
    text = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CustodyApiTests(unittest.IsolatedAsyncioTestCase):
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
        connection = sqlite3.connect(self.root / "evidence.sqlite3")
        connection.row_factory = sqlite3.Row
        return connection

    async def register(self, email, name):
        response = await self.client.post("/api/auth/register", json={"email": email, "name": name, "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    async def upload(self, headers, content=b"evidence"):
        response = await self.client.post("/api/evidence", headers=headers, files={"file": ("a.txt", content, "text/plain")})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def custody(self, headers, evidence_id):
        response = await self.client.get(f"/api/evidence/{evidence_id}/custody", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def check(self, headers, evidence_id):
        response = await self.client.get(f"/api/evidence/{evidence_id}/custody/verify", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_register_download_verify_build_valid_chain(self):
        headers = await self.register("owner@example.com", "소유자")
        evidence = await self.upload(headers)
        self.assertEqual((await self.client.get(f"/api/evidence/{evidence['id']}/download", headers=headers)).status_code, 200)
        verified = (await self.client.post(f"/api/evidence/{evidence['id']}/verify", headers=headers)).json()["result"]

        body = await self.custody(headers, evidence["id"])
        events = body["events"]
        self.assertEqual(body["evidenceId"], evidence["id"])
        self.assertEqual([e["sequenceNo"] for e in events], [1, 2, 3])
        self.assertEqual([e["eventType"] for e in events], ["EVIDENCE_REGISTERED", "EVIDENCE_DOWNLOADED", "INTEGRITY_VERIFIED"])
        self.assertEqual([e["result"] for e in events], ["SUCCESS"] * 3)
        self.assertEqual({e["actor"]["name"] for e in events}, {"소유자"})
        self.assertEqual(events[0]["details"], {"actorName": "소유자", "sha256": evidence["sha256"], "filename": "a.txt", "size": 8})
        self.assertEqual(events[2]["details"], {"actorName": "소유자", "status": "MATCH", "verificationId": verified["id"]})
        # 앞뒤 연결
        self.assertEqual(events[0]["previousEventHash"], ZERO)
        self.assertEqual(events[1]["previousEventHash"], events[0]["eventHash"])
        self.assertEqual(events[2]["previousEventHash"], events[1]["eventHash"])
        # 서버 코드와 별개로 직접 계산한 해시와 같음
        with closing(self.raw()) as connection:
            rows = connection.execute("SELECT * FROM chain_of_custody ORDER BY sequence_no").fetchall()
        self.assertEqual([independent_hash(row) for row in rows], [e["eventHash"] for e in events])

        result = await self.check(headers, evidence["id"])
        self.assertEqual(result, {"evidenceId": evidence["id"], "valid": True, "eventCount": 3, "lastSequenceNo": 3,
                                  "lastEventHash": events[2]["eventHash"], "failures": []})

    async def test_unavailable_verification_is_failure_event(self):
        headers = await self.register("owner@example.com", "소유자")
        evidence = await self.upload(headers)
        with closing(self.raw()) as connection:
            stored = connection.execute("SELECT stored_name FROM evidence").fetchone()[0]
        target = self.root / "files" / stored
        target.chmod(0o600)
        target.unlink()
        response = await self.client.post(f"/api/evidence/{evidence['id']}/verify", headers=headers)
        self.assertEqual(response.json()["result"]["status"], "UNAVAILABLE")
        last = (await self.custody(headers, evidence["id"]))["events"][-1]
        self.assertEqual((last["eventType"], last["result"], last["details"]["status"]), ("INTEGRITY_VERIFIED", "FAILURE", "UNAVAILABLE"))
        self.assertTrue((await self.check(headers, evidence["id"]))["valid"])

    async def test_mismatch_is_success_event(self):
        headers = await self.register("owner@example.com", "소유자")
        evidence = await self.upload(headers)
        with closing(self.raw()) as connection:
            stored = connection.execute("SELECT stored_name FROM evidence").fetchone()[0]
        target = self.root / "files" / stored
        target.chmod(0o600)
        target.write_bytes(b"changed!")
        await self.client.post(f"/api/evidence/{evidence['id']}/verify", headers=headers)
        last = (await self.custody(headers, evidence["id"]))["events"][-1]
        self.assertEqual((last["result"], last["details"]["status"]), ("SUCCESS", "MISMATCH"))

    async def test_only_owner_can_read(self):
        owner = await self.register("owner@example.com", "소유자")
        other = await self.register("other@example.com", "다른 사람")
        evidence = await self.upload(owner)
        for path in (f"/api/evidence/{evidence['id']}/custody", f"/api/evidence/{evidence['id']}/custody/verify"):
            self.assertEqual((await self.client.get(path, headers=other)).status_code, 404)
            self.assertEqual((await self.client.get(path)).status_code, 401)
        self.assertEqual((await self.client.get("/api/evidence/EV-none/custody", headers=owner)).status_code, 404)

    async def test_name_change_keeps_old_actor_name(self):
        headers = await self.register("owner@example.com", "처음 이름")
        evidence = await self.upload(headers)
        await self.client.put("/api/me", headers=headers, json={"name": "바뀐 이름"})
        await self.client.post(f"/api/evidence/{evidence['id']}/verify", headers=headers)
        events = (await self.custody(headers, evidence["id"]))["events"]
        self.assertEqual([e["actor"]["name"] for e in events], ["처음 이름", "바뀐 이름"])

    async def test_failed_request_leaves_no_event(self):
        headers = await self.register("owner@example.com", "소유자")
        # 등록: 이벤트 기록이 실패하면 증거 행·파일·이벤트가 모두 남지 않음
        with mock.patch("server.routers.evidence.append_event", side_effect=RuntimeError("테스트용 실패")):
            with self.assertRaises(RuntimeError):
                await self.client.post("/api/evidence", headers=headers, files={"file": ("a.txt", b"x", "text/plain")})
        with closing(self.raw()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chain_of_custody").fetchone()[0], 0)
        self.assertEqual(list((self.root / "files").iterdir()), [])

        # 검증: 이벤트 기록이 실패하면 검증 결과·상태 변경도 되돌려짐
        evidence = await self.upload(headers)
        with mock.patch("server.routers.evidence.append_event", side_effect=RuntimeError("테스트용 실패")):
            with self.assertRaises(RuntimeError):
                await self.client.post(f"/api/evidence/{evidence['id']}/verify", headers=headers)
        with closing(self.raw()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT verification_status FROM evidence").fetchone()[0], "NOT_VERIFIED")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chain_of_custody").fetchone()[0], 1)

    async def test_append_event_requires_transaction(self):
        with closing(db.Storage(self.root, self.root / "files", self.root / "evidence.sqlite3").connect()) as connection:
            with self.assertRaises(RuntimeError):
                custody.append_event(connection, "EV-x", "EVIDENCE_REGISTERED", {"id": "u", "name": "n"})

    async def test_triggers_keep_chain_append_only(self):
        headers = await self.register("owner@example.com", "소유자")
        evidence = await self.upload(headers)
        with closing(self.raw()) as connection:
            first = connection.execute("SELECT * FROM chain_of_custody").fetchone()

            def blocked(sql, params=(), word="INVALID"):
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    connection.execute(sql, params)
                self.assertIn(word, str(caught.exception))

            blocked("UPDATE chain_of_custody SET details = '{}'", word="IMMUTABLE")
            blocked("DELETE FROM chain_of_custody", word="IMMUTABLE")

            insert = """INSERT INTO chain_of_custody (evidence_id, sequence_no, event_type, actor_user_id, event_time,
                        result, details, previous_event_hash, event_hash) VALUES (?, ?, ?, ?, 't', ?, ?, ?, ?)"""
            good = dict(evidence_id=evidence["id"], sequence_no=2, event_type="EVIDENCE_DOWNLOADED",
                        actor_user_id=first["actor_user_id"], result="SUCCESS", details='{"actorName":"소유자"}',
                        previous_event_hash=first["event_hash"], event_hash="b" * 64)

            def values(**change):
                row = {**good, **change}
                return (row["evidence_id"], row["sequence_no"], row["event_type"], row["actor_user_id"], row["result"],
                        row["details"], row["previous_event_hash"], row["event_hash"])

            blocked(insert, values(event_type="VIEWED"))                       # 허용 목록 밖
            blocked(insert, values(result="OK"))                               # result 값
            blocked(insert, values(details='{"status":"MATCH"}'))              # actorName 없음
            blocked(insert, values(details='{"actorName":"  "}'))              # actorName 빈 값
            blocked(insert, values(details='{"actorName":1}'))                 # actorName 문자열 아님
            blocked(insert, values(details="not json"))                        # JSON 아님
            blocked(insert, values(details='["actorName"]'))                   # JSON 객체 아님
            blocked(insert, values(sequence_no=3))                             # 번호 건너뜀
            blocked(insert, values(sequence_no=1))                             # 번호 되돌림
            blocked(insert, values(previous_event_hash=ZERO))                  # 앞 해시 불일치
            blocked(insert, values(event_hash="B" * 64))                       # 대문자
            blocked(insert, values(event_hash="b" * 63))                       # 길이
            connection.execute(insert, values())                               # 규칙을 지키면 들어감 (해시 값 자체는 C2가 검사)

    async def test_c2_detects_tampering(self):
        headers = await self.register("owner@example.com", "소유자")
        evidence = await self.upload(headers)
        for _ in range(2):
            await self.client.post(f"/api/evidence/{evidence['id']}/verify", headers=headers)
        self.assertTrue((await self.check(headers, evidence["id"]))["valid"])

        def tamper(*statements):
            # 누군가 DB 파일을 직접 열어 트리거를 지우고 고친 상황
            with closing(self.raw()) as connection, connection:
                connection.execute("DROP TRIGGER custody_block_update")
                connection.execute("DROP TRIGGER custody_block_delete")
                for sql, params in statements:
                    connection.execute(sql, params)

        # (1) 2번 이벤트 내용을 바꿈 → 2번 해시 재계산 불일치
        tamper(("UPDATE chain_of_custody SET details = ? WHERE sequence_no = 2",
                ('{"actorName":"다른 사람","status":"MATCH","verificationId":"x"}',)))
        result = await self.check(headers, evidence["id"])
        self.assertFalse(result["valid"])
        self.assertEqual([(f["code"], f["sequenceNo"]) for f in result["failures"]], [("HASH_MISMATCH", 2)])

        # (2) 해시까지 다시 계산해서 맞춤 → 이번엔 3번과의 연결이 끊어짐
        with closing(self.raw()) as connection, connection:
            row = connection.execute("SELECT * FROM chain_of_custody WHERE sequence_no = 2").fetchone()
            connection.execute("UPDATE chain_of_custody SET event_hash = ? WHERE id = ?", (independent_hash(row), row["id"]))
        result = await self.check(headers, evidence["id"])
        self.assertEqual([(f["code"], f["sequenceNo"]) for f in result["failures"]], [("LINK_BROKEN", 3)])

        # (3) 2번 이벤트를 지움 → 번호 빈곳 + 연결 끊김
        with closing(self.raw()) as connection, connection:
            connection.execute("DELETE FROM chain_of_custody WHERE sequence_no = 2")
        codes = {(f["code"], f["sequenceNo"]) for f in (await self.check(headers, evidence["id"]))["failures"]}
        self.assertEqual(codes, {("SEQUENCE_GAP", 2), ("LINK_BROKEN", 3)})

    async def test_concurrent_requests_keep_sequence(self):
        headers = await self.register("owner@example.com", "소유자")
        evidence = await self.upload(headers)
        requests = [self.client.post(f"/api/evidence/{evidence['id']}/verify", headers=headers) for _ in range(15)]
        requests += [self.client.get(f"/api/evidence/{evidence['id']}/download", headers=headers) for _ in range(15)]
        responses = await asyncio.gather(*requests)
        self.assertEqual({response.status_code for response in responses}, {200})
        result = await self.check(headers, evidence["id"])
        self.assertTrue(result["valid"], result["failures"])
        self.assertEqual(result["eventCount"], 31)


class VerifyChainTests(unittest.TestCase):
    """C2 검사 함수 단위 테스트. DB 없이 이벤트 목록을 직접 만들어 검사합니다."""

    def chain(self, *specs):
        """(event_type, details 문자열) 목록으로 올바르게 연결된 이벤트 목록을 만듭니다."""
        rows, previous = [], ZERO
        for number, (event_type, details) in enumerate(specs, start=1):
            row = {"id": number, "evidence_id": "EV-1", "sequence_no": number, "event_type": event_type,
                   "actor_user_id": "u1", "event_time": f"2026-11-12T00:00:0{number}.000+00:00", "result": "SUCCESS",
                   "details": details, "previous_event_hash": previous}
            row["event_hash"] = independent_hash(row)
            previous = row["event_hash"]
            rows.append(row)
        return rows

    def codes(self, rows):
        return [(f["code"], f["sequenceNo"]) for f in custody.verify_chain(rows)]

    def test_valid_and_empty(self):
        self.assertEqual(self.codes(self.chain(("EVIDENCE_REGISTERED", '{"actorName":"a"}'))), [])
        self.assertEqual(self.codes([]), [("CHAIN_EMPTY", None)])

    def test_genesis_invalid(self):
        self.assertEqual(self.codes(self.chain(("EVIDENCE_DOWNLOADED", '{"actorName":"a"}'))), [("GENESIS_INVALID", 1)])

    def test_duplicate_sequence(self):
        rows = self.chain(("EVIDENCE_REGISTERED", '{"actorName":"a"}'), ("EVIDENCE_DOWNLOADED", '{"actorName":"a"}'))
        copy = dict(rows[1], id=3)
        self.assertIn(("SEQUENCE_DUPLICATE", 2), self.codes(rows + [copy]))

    def test_actor_name_rules(self):
        # actorName이 없는 이벤트: C2는 정상, C1은 actor.name = null
        rows = self.chain(("EVIDENCE_REGISTERED", "{}"))
        self.assertEqual(self.codes(rows), [])
        self.assertIsNone(custody.event_dict(rows[0])["actor"]["name"])
        # 있는데 빈 값·문자열 아님 → 형식 오류
        self.assertEqual(self.codes(self.chain(("EVIDENCE_REGISTERED", '{"actorName":""}'))), [("EVENT_FORMAT_INVALID", 1)])
        self.assertEqual(self.codes(self.chain(("EVIDENCE_REGISTERED", '{"actorName":7}'))), [("EVENT_FORMAT_INVALID", 1)])
        self.assertEqual(self.codes(self.chain(("EVIDENCE_REGISTERED", "not json"))), [("EVENT_FORMAT_INVALID", 1)])

    def test_event_types_match_db_trigger_list(self):
        self.assertEqual(custody.EVENT_TYPES, db.CUSTODY_EVENT_TYPES)
        self.assertTrue(set(custody.GENESIS_EVENT_TYPES) <= set(custody.EVENT_TYPES))


class CustodyMigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "evidence.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    async def test_stage3_db_is_migrated_and_old_evidence_has_empty_chain(self):
        # 3단계 구조(user_version 2) DB + 기존 증거 1건
        token = "stage3-test-token"
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="milliseconds")
        with closing(sqlite3.connect(self.db_path, isolation_level=None)) as connection:
            connection.row_factory = sqlite3.Row
            with db.write_transaction(connection):
                db._migration_1_base_schema(connection)
                db._migration_2_immutable_records(connection)
                connection.execute("PRAGMA user_version = 2")
                connection.execute("INSERT INTO users VALUES ('u1', 'old@example.com', '예전 사용자', 'scrypt$x', 't0')")
                connection.execute("INSERT INTO sessions VALUES (?, 'u1', ?)", (hashlib.sha256(token.encode()).hexdigest(), expires))
                connection.execute("""INSERT INTO evidence (id, owner_id, name, filename, stored_name, type, mime, size, kind,
                    source, description, memo, registered_at, sha256, registered_by_name) VALUES ('EV-old', 'u1', '예전 증거',
                    'old.txt', ?, 'TXT', 'text/plain', 3, '파일', '직접 수집', '', '', 't', ?, '예전 사용자')""", ("0" * 32, "a" * 64))

        app = main.create_app(self.root)

        self.assertEqual(len(list((self.root / "backups").glob("evidence-v2-*.sqlite3"))), 1)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chain_of_custody").fetchone()[0], 0)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {token}"}
            self.assertEqual((await client.get("/api/evidence/EV-old/custody", headers=headers)).json()["events"], [])
            result = (await client.get("/api/evidence/EV-old/custody/verify", headers=headers)).json()
        self.assertFalse(result["valid"])
        self.assertEqual([f["code"] for f in result["failures"]], ["CHAIN_EMPTY"])


if __name__ == "__main__":
    unittest.main()
