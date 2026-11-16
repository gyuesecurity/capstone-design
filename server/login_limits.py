"""로그인 실패 제한 (B 코드 feature/evidence-crypto의 login_identifier·reject_if_login_blocked·record_login_failure를 옮겨 옴)

규칙 (B 코드와 같음)
- "이메일 + 접속 주소"마다 실패 횟수를 셉니다. (식별자는 SHA-256으로 바꿔 저장해서 이메일·주소가 그대로 남지 않음)
- 첫 실패부터 10분 안에 5번 틀리면 15분 동안 로그인을 막습니다. 막힌 동안은 비밀번호가 맞아도 429입니다.
- 10분이 지나면 횟수를 처음부터 다시 셉니다. 로그인에 성공하면 기록을 지웁니다.

참고: 개발용 프록시를 거치면 접속 주소가 모두 127.0.0.1이라 사실상 "이메일마다" 세게 됩니다.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request

MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=10)
BLOCK_DURATION = timedelta(minutes=15)
TOO_MANY_ATTEMPTS = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."


def _now() -> datetime:
    return datetime.now(UTC)


def login_identifier(request: Request, email: str) -> str:
    client = request.client.host if request.client else "unknown"
    return hashlib.sha256(f"{email}|{client}".encode()).hexdigest()


def reject_if_login_blocked(connection: sqlite3.Connection, identifier: str) -> None:
    """막힌 상태면 429 (비밀번호 확인 전에 부름)"""
    row = connection.execute("SELECT blocked_until FROM login_attempts WHERE identifier = ?", (identifier,)).fetchone()
    if row and row["blocked_until"] and row["blocked_until"] > _now().isoformat(timespec="milliseconds"):
        raise HTTPException(429, TOO_MANY_ATTEMPTS)


def record_login_failure(connection: sqlite3.Connection, identifier: str) -> None:
    """실패 1회 기록. write_transaction 안에서 부릅니다."""
    now = _now()
    row = connection.execute("SELECT * FROM login_attempts WHERE identifier = ?", (identifier,)).fetchone()
    if row is None or datetime.fromisoformat(row["first_failed_at"]) < now - FAILURE_WINDOW:
        failures = 1
        first_failed_at = now.isoformat(timespec="milliseconds")
    else:
        failures = row["failures"] + 1
        first_failed_at = row["first_failed_at"]
    blocked_until = (now + BLOCK_DURATION).isoformat(timespec="milliseconds") if failures >= MAX_FAILURES else None
    connection.execute(
        """INSERT INTO login_attempts(identifier, failures, first_failed_at, blocked_until)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(identifier) DO UPDATE SET failures=excluded.failures,
           first_failed_at=excluded.first_failed_at, blocked_until=excluded.blocked_until""",
        (identifier, failures, first_failed_at, blocked_until),
    )


def clear_login_failures(connection: sqlite3.Connection, identifier: str) -> None:
    """로그인 성공 시 기록 삭제. write_transaction 안에서 부릅니다."""
    connection.execute("DELETE FROM login_attempts WHERE identifier = ?", (identifier,))
