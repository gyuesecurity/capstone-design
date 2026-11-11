"""시각, 비밀번호 해시, 로그인 세션과 현재 사용자 확인.

기존 server/main.py의 utc_now, password_hash, password_matches와
create_app() 안에 있던 issue_session, current_user를 그대로 옮겨 왔습니다.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException

from .db import Storage, get_storage


def utc_now() -> str:
    """현재 시각(UTC)을 밀리초까지 ISO 8601 문자열로 돌려줍니다."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def password_hash(password: str, salt: bytes | None = None) -> str:
    """비밀번호를 scrypt로 해시해 "scrypt$n$r$p$salt$digest" 형태로 돌려줍니다."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    """입력한 비밀번호가 저장된 해시와 같은지 확인합니다. 형식이 이상하면 False."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        computed = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(computed, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def issue_session(connection: sqlite3.Connection, user_id: str) -> str:
    """로그인 토큰을 새로 만들고, DB에는 토큰의 SHA-256만 저장합니다. (유효 기간 8시간)"""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(timespec="milliseconds")
    connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
    return token


def current_user(
    authorization: str | None = Header(default=None),
    storage: Storage = Depends(get_storage),
) -> sqlite3.Row:
    """FastAPI 의존성: Authorization: Bearer <토큰>으로 로그인한 사용자를 찾습니다. 없으면 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "로그인이 필요합니다.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "로그인이 필요합니다.")
    with closing(storage.connect()) as connection, connection:
        user = connection.execute("""
            SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
        """, (hashlib.sha256(token.encode()).hexdigest(), utc_now())).fetchone()
    if user is None:
        raise HTTPException(401, "로그인이 만료되었습니다. 다시 로그인해 주세요.")
    return user
