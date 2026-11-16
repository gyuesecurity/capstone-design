"""활동 기록(audit) 쓰기와 서명 체인 검사. B의 append_audit / audit_integrity를 옮겨 왔습니다. (6단계)

역할 구분 (11/15 B와 확정)
- audit: 사용자의 서비스 활동 기록 (마이페이지 표시, 운영 추적). 사용자별 HMAC 서명 체인으로 위조를 막습니다.
- chain_of_custody: 증거별 공식 관리 이력 (SHA-256 해시 체인). 별도 모듈(custody.py)
- verifications: 검증 상세 결과

서명 규칙 (B 코드 그대로)
- 서명 대상: {userId, action, evidenceId, createdAt, previousHash}를 정규화한 JSON
  (키 정렬, 공백 없는 구분자, 한글 그대로)
- previousHash: 그 사용자의 바로 앞 "서명된" 기록의 entry_hash. 첫 기록은 0을 64개 이어 붙인 값
- entry_hash: B 모듈 crypto.sign("audit-log", 서명 대상)의 HMAC-SHA256 값, key_version과 함께 저장
- 6단계 이전 기록은 서명이 없어서(entry_hash = '') LEGACY로 표시하고 체인 계산에서 건너뜁니다.
"""

from __future__ import annotations

import json
import sqlite3

from .crypto import KeyUnavailableError
from .db import Storage
from .security import utc_now

AUDIT_EMPTY_HASH = "0" * 64


def audit_payload(user_id: str, action: str, evidence_id: str | None, created_at: str, previous_hash: str) -> bytes:
    """서명 대상 바이트 (B 코드와 같은 모양)"""
    return json.dumps({
        "userId": user_id,
        "action": action,
        "evidenceId": evidence_id,
        "createdAt": created_at,
        "previousHash": previous_hash,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def append_audit(connection: sqlite3.Connection, storage: Storage, user_id: str, action: str,
                 evidence_id: str | None = None, created_at: str | None = None) -> None:
    """활동 기록 1건을 서명해서 추가합니다.

    반드시 이미 열린 write_transaction(BEGIN IMMEDIATE) 안에서 부릅니다.
    그래야 같은 사용자의 기록이 동시에 들어와도 "바로 앞 서명"이 꼬이지 않습니다.
    """
    if not connection.in_transaction:
        raise RuntimeError("append_audit는 write_transaction 안에서만 호출할 수 있습니다.")
    created_at = created_at or utc_now()
    previous = connection.execute(
        "SELECT entry_hash FROM audit WHERE user_id = ? AND entry_hash != '' ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    previous_hash = previous["entry_hash"] if previous else AUDIT_EMPTY_HASH
    key_version, entry_hash = storage.crypto.sign(
        "audit-log", audit_payload(user_id, action, evidence_id, created_at, previous_hash)
    )
    connection.execute(
        """INSERT INTO audit(user_id, action, evidence_id, created_at, previous_hash, entry_hash, key_version)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, action, evidence_id, created_at, previous_hash, entry_hash, key_version),
    )


def audit_integrity(connection: sqlite3.Connection, storage: Storage, user_id: str) -> dict[int, str]:
    """사용자의 활동 기록마다 서명 체인 검사 결과: VALID / INVALID / LEGACY / KEY_UNAVAILABLE (B 코드 그대로)"""
    statuses: dict[int, str] = {}
    expected_previous = AUDIT_EMPTY_HASH
    rows = connection.execute("SELECT * FROM audit WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    for row in rows:
        if not row["entry_hash"]:
            statuses[row["id"]] = "LEGACY"
            continue
        try:
            valid = row["previous_hash"] == expected_previous and storage.crypto.signature_matches(
                "audit-log",
                audit_payload(row["user_id"], row["action"], row["evidence_id"], row["created_at"], row["previous_hash"]),
                row["key_version"],
                row["entry_hash"],
            )
        except KeyUnavailableError:
            statuses[row["id"]] = "KEY_UNAVAILABLE"
        else:
            statuses[row["id"]] = "VALID" if valid else "INVALID"
        expected_previous = row["entry_hash"]
    return statuses
