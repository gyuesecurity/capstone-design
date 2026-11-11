"""Chain of Custody: 이벤트 추가(append_event)와 사슬 검사(verify_chain).

규칙은 통합 설계 문서 4장을 따릅니다.
- 증거마다 sequence_no는 1부터 빈곳 없이 이어집니다. 첫 이벤트의 previous_event_hash는 0을 64개 이어 붙인 값입니다.
- event_hash = SHA-256(정규화한 JSON). 대상 항목 8개:
  evidence_id, sequence_no, event_type, actor_user_id, event_time, result, details, previous_event_hash
  정규화: 키 정렬, 구분자 "," ":"(공백 없음), 한글은 그대로(ensure_ascii=False)
  details는 DB에 저장된 "문자열 그대로" 넣습니다. (다시 파싱·정렬하지 않음)
- 추가만 합니다. 수정·삭제는 코드에 없고 DB 트리거가 거부합니다.
- 상태 변경과 이벤트는 같은 트랜잭션(BEGIN IMMEDIATE)에 기록하고, 잠금 안에서 다음 번호를 계산합니다.
- details.actorName(작업 당시 이름)은 모든 새 이벤트에 서버가 users.name에서 복사해 넣습니다.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence

from .db import CUSTODY_EVENT_TYPES
from .security import utc_now

# 허용 이벤트 목록은 DB 트리거와 같은 목록을 씁니다. (한 곳에서만 관리)
EVENT_TYPES = CUSTODY_EVENT_TYPES
# 사슬의 첫 이벤트가 될 수 있는 종류
GENESIS_EVENT_TYPES = ("EVIDENCE_REGISTERED", "LEGACY_IMPORTED")
RESULTS = ("SUCCESS", "FAILURE")
ZERO_HASH = "0" * 64
_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def canonical_json(value) -> str:
    """정규화한 JSON 문자열: 키 정렬, 공백 없는 구분자, 한글 그대로"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_event_hash(event: Mapping) -> str:
    """이벤트 1건의 event_hash를 계산합니다. event는 DB 행(sqlite3.Row)이나 같은 키를 가진 dict입니다."""
    material = {
        "evidence_id": event["evidence_id"],
        "sequence_no": event["sequence_no"],
        "event_type": event["event_type"],
        "actor_user_id": event["actor_user_id"],
        "event_time": event["event_time"],
        "result": event["result"],
        "details": event["details"],  # 저장된 문자열 그대로
        "previous_event_hash": event["previous_event_hash"],
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def append_event(
    connection: sqlite3.Connection,
    evidence_id: str,
    event_type: str,
    actor: Mapping,
    result: str = "SUCCESS",
    details: Mapping | None = None,
) -> dict:
    """이벤트 1건을 사슬 끝에 붙입니다.

    반드시 이미 열린 write_transaction(BEGIN IMMEDIATE) 안에서 불러야 합니다.
    그래야 (1) 상태 변경과 이벤트가 같이 저장되거나 같이 취소되고,
    (2) 동시에 들어온 요청이 같은 번호를 만들지 않습니다. (잠금을 잡은 뒤 마지막 번호를 읽음)

    actor는 로그인한 사용자 행(users)입니다. actorName은 호출한 쪽이 넣은 값이 있어도 actor["name"]으로 덮어씁니다.
    """
    if not connection.in_transaction:
        raise RuntimeError("append_event는 write_transaction 안에서만 호출할 수 있습니다.")
    if event_type not in EVENT_TYPES:
        raise ValueError(f"허용되지 않은 이벤트 종류입니다: {event_type}")
    if result not in RESULTS:
        raise ValueError(f"허용되지 않은 result 값입니다: {result}")

    body = dict(details or {})
    body["actorName"] = actor["name"]

    last = connection.execute(
        "SELECT sequence_no, event_hash FROM chain_of_custody WHERE evidence_id = ? ORDER BY sequence_no DESC LIMIT 1",
        (evidence_id,),
    ).fetchone()
    event = {
        "evidence_id": evidence_id,
        "sequence_no": (last["sequence_no"] + 1) if last else 1,
        "event_type": event_type,
        "actor_user_id": actor["id"],
        "event_time": utc_now(),
        "result": result,
        "details": canonical_json(body),
        "previous_event_hash": last["event_hash"] if last else ZERO_HASH,
    }
    event["event_hash"] = compute_event_hash(event)
    connection.execute(
        """INSERT INTO chain_of_custody (
            evidence_id, sequence_no, event_type, actor_user_id, event_time,
            result, details, previous_event_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event["evidence_id"], event["sequence_no"], event["event_type"], event["actor_user_id"],
         event["event_time"], event["result"], event["details"], event["previous_event_hash"], event["event_hash"]),
    )
    return event


def load_events(connection: sqlite3.Connection, evidence_id: str) -> list[sqlite3.Row]:
    """증거 1건의 이벤트 전체 (번호순, 같은 번호면 저장순)"""
    return connection.execute(
        "SELECT * FROM chain_of_custody WHERE evidence_id = ? ORDER BY sequence_no, id", (evidence_id,)
    ).fetchall()


def _parse_details(text) -> dict | None:
    """details 문자열을 dict로. JSON 객체가 아니면 None"""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def event_dict(row: Mapping) -> dict:
    """C1 응답의 이벤트 1건. actorName이 없거나 문자열이 아니면 actor.name은 null"""
    details = _parse_details(row["details"])
    actor_name = details.get("actorName") if details else None
    return {
        "sequenceNo": row["sequence_no"],
        "eventType": row["event_type"],
        "result": row["result"],
        "eventTime": row["event_time"],
        "actor": {"id": row["actor_user_id"], "name": actor_name if isinstance(actor_name, str) and actor_name else None},
        "details": details,
        "previousEventHash": row["previous_event_hash"],
        "eventHash": row["event_hash"],
    }


def _format_problems(row: Mapping) -> list[str]:
    """이벤트 1건의 형식 문제 목록 (비어 있으면 형식은 정상)"""
    problems = []
    if not isinstance(row["sequence_no"], int) or row["sequence_no"] < 1:
        problems.append("sequence_no가 1 이상의 정수가 아닙니다")
    if row["event_type"] not in EVENT_TYPES:
        problems.append("event_type이 허용 목록에 없습니다")
    if row["result"] not in RESULTS:
        problems.append("result가 SUCCESS·FAILURE가 아닙니다")
    for column in ("previous_event_hash", "event_hash"):
        if not isinstance(row[column], str) or not _HASH_PATTERN.fullmatch(row[column]):
            problems.append(f"{column}이 소문자 16진수 64자리가 아닙니다")
    details = _parse_details(row["details"])
    if details is None:
        problems.append("details가 JSON 객체가 아닙니다")
    elif "actorName" in details and (not isinstance(details["actorName"], str) or not details["actorName"].strip()):
        # actorName이 아예 없는 것은 C1에서 null로 보여 주고, "있는데 잘못된 값"만 형식 오류로 봅니다.
        problems.append("details.actorName이 빈 값이 아닌 문자열이 아닙니다")
    return problems


def verify_chain(rows: Sequence[Mapping]) -> list[dict]:
    """C2 사슬 검사. 문제를 모두 찾아 [{code, sequenceNo, message}] 목록으로 돌려줍니다. 빈 목록이면 정상.

    1단계(체인 전체)만 검사합니다. 보고서·패키지 기준점 검사(ANCHOR_*, INCLUSION_*, PACKAGE_*)는
    보고서·패키지 기능을 만들 때 추가합니다.
    """
    if not rows:
        return [{"code": "CHAIN_EMPTY", "sequenceNo": None, "message": "이벤트가 하나도 없습니다."}]

    failures: list[dict] = []

    def fail(code: str, sequence_no, message: str) -> None:
        failures.append({"code": code, "sequenceNo": sequence_no, "message": message})

    ordered = sorted(rows, key=lambda row: (row["sequence_no"] if isinstance(row["sequence_no"], int) else 0,
                                            row["id"] if "id" in row.keys() else 0))

    # 형식
    for row in ordered:
        for problem in _format_problems(row):
            fail("EVENT_FORMAT_INVALID", row["sequence_no"], problem)

    # 번호 중복·빈곳 (1부터 마지막 번호까지 하나씩 있어야 함)
    numbers = [row["sequence_no"] for row in ordered if isinstance(row["sequence_no"], int) and row["sequence_no"] >= 1]
    for number, count in sorted(Counter(numbers).items()):
        if count > 1:
            fail("SEQUENCE_DUPLICATE", number, f"{number}번 이벤트가 {count}개 있습니다.")
    if numbers:
        for missing in sorted(set(range(1, max(numbers) + 1)) - set(numbers)):
            fail("SEQUENCE_GAP", missing, f"{missing}번 이벤트가 없습니다.")

    # 첫 이벤트
    first = ordered[0]
    if first["sequence_no"] == 1:
        if first["event_type"] not in GENESIS_EVENT_TYPES:
            fail("GENESIS_INVALID", 1, "첫 이벤트는 EVIDENCE_REGISTERED 또는 LEGACY_IMPORTED여야 합니다.")
        if first["previous_event_hash"] != ZERO_HASH:
            fail("GENESIS_INVALID", 1, "첫 이벤트의 previous_event_hash는 0 64개여야 합니다.")

    # 앞뒤 연결: 각 이벤트의 previous_event_hash = 바로 앞 이벤트의 event_hash
    for before, after in zip(ordered, ordered[1:]):
        if after["previous_event_hash"] != before["event_hash"]:
            fail("LINK_BROKEN", after["sequence_no"],
                 f"{after['sequence_no']}번 이벤트가 앞 이벤트({before['sequence_no']}번)의 해시와 이어지지 않습니다.")

    # 해시 재계산
    for row in ordered:
        try:
            recomputed = compute_event_hash(row)
        except (TypeError, ValueError):
            continue  # 형식 오류로 이미 기록됨
        if recomputed != row["event_hash"]:
            fail("HASH_MISMATCH", row["sequence_no"], f"{row['sequence_no']}번 이벤트의 해시를 다시 계산한 값이 저장된 값과 다릅니다.")

    return failures
