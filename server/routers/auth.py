"""계정 인증 API: 회원가입, 로그인, 로그아웃, 비밀번호 변경

3단계: 쓰기는 모두 write_transaction(BEGIN IMMEDIATE)으로 감쌉니다. 응답과 오류 문구는 그대로입니다.
6단계: 활동 기록은 append_audit(서명 체인)로만 씁니다.
보안 보완: B 코드의 로그인 실패 제한(10분 안에 5번 → 15분 429)과
           비밀번호 변경 시 "지금 기기 말고 다른 로그인 세션 모두 끊기"를 옮겨 왔습니다.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import closing

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..audit import append_audit
from ..config import EMAIL_PATTERN
from ..db import Storage, get_storage, write_transaction
from ..login_limits import (
    clear_login_failures,
    login_identifier,
    record_login_failure,
    reject_if_login_blocked,
)
from ..schemas import Credentials, PasswordChange, Registration
from ..security import (
    current_user,
    issue_session,
    password_hash,
    password_matches,
    utc_now,
)

router = APIRouter()


# 회원가입: 계정을 만들고 바로 로그인 토큰을 돌려줍니다. 활동 기록에 register, login 2건을 남깁니다.
@router.post("/api/auth/register", status_code=201)
def register(payload: Registration, storage: Storage = Depends(get_storage)):
    email = payload.email.strip().lower()
    name = payload.name.strip()
    if not EMAIL_PATTERN.fullmatch(email) or not name or len(name) > 100 or not 8 <= len(payload.password) <= 128:
        raise HTTPException(422, "이름·이메일·비밀번호를 확인해 주세요.")
    user_id = uuid.uuid4().hex
    hashed = password_hash(payload.password)  # 시간이 걸리는 계산은 쓰기 잠금 밖에서
    with closing(storage.connect()) as connection, write_transaction(connection):
        try:
            connection.execute("INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                               (user_id, email, name, hashed, utc_now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "이미 등록된 이메일입니다.") from None
        token = issue_session(connection, user_id)
        append_audit(connection, storage, user_id, "register")
        append_audit(connection, storage, user_id, "login")
    return {"token": token, "user": {"id": user_id, "email": email, "name": name}}


# 로그인: 이메일·비밀번호가 맞으면 새 토큰을 돌려줍니다.
# 없는 이메일이어도 비밀번호 해시 계산을 한 번 해서, 응답 시간으로 계정 존재 여부를 알 수 없게 합니다.
# 비밀번호 확인(시간이 걸리는 계산)은 쓰기 잠금 밖에서 하고, 잠금 안에서는 기록만 합니다.
# 로그인 실패 제한: 막힌 동안은 비밀번호를 확인하지 않고 바로 429. 틀리면 실패 1회 기록, 맞으면 기록 삭제
@router.post("/api/auth/login")
def login(payload: Credentials, request: Request, storage: Storage = Depends(get_storage)):
    email = payload.email.strip().lower()
    identifier = login_identifier(request, email)
    with closing(storage.connect()) as connection:
        reject_if_login_blocked(connection, identifier)
        user = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        valid = password_matches(payload.password, user["password_hash"] if user else password_hash("invalid-password"))
        if not user or not valid:
            with write_transaction(connection):
                record_login_failure(connection, identifier)
            raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")
        with write_transaction(connection):
            # 비밀번호를 확인하는 사이에 다른 요청이 막아 놨을 수 있으므로 잠금 안에서 다시 확인합니다.
            reject_if_login_blocked(connection, identifier)
            clear_login_failures(connection, identifier)
            token = issue_session(connection, user["id"])
            append_audit(connection, storage, user["id"], "login")
    # role: 5단계 추가 (설계 문서 3장 "로그인·내 정보 응답에 role 추가"). ADMIN 특권은 아직 없음
    return {"token": token, "user": {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}}


# 로그아웃: 지금 쓰고 있는 토큰만 지웁니다. (세션은 수정·삭제 금지 대상이 아님)
@router.post("/api/auth/logout")
def logout(
    authorization: str | None = Header(default=None),
    user: sqlite3.Row = Depends(current_user),
    storage: Storage = Depends(get_storage),
):
    token = authorization[7:].strip()
    with closing(storage.connect()) as connection, write_transaction(connection):
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
        append_audit(connection, storage, user["id"], "logout")
    return {"ok": True}


# 비밀번호 변경: 현재 비밀번호를 확인한 뒤 새 비밀번호로 바꿉니다.
# 바꾸면 지금 쓰는 기기(이 요청의 토큰)만 남기고 다른 로그인 세션은 모두 지웁니다. (B 코드와 같음)
@router.put("/api/auth/password")
def change_password(
    payload: PasswordChange,
    authorization: str | None = Header(default=None),
    user: sqlite3.Row = Depends(current_user),
    storage: Storage = Depends(get_storage),
):
    if not password_matches(payload.current_password, user["password_hash"]):
        raise HTTPException(403, "현재 비밀번호가 올바르지 않습니다.")
    if not 8 <= len(payload.new_password) <= 128:
        raise HTTPException(422, "새 비밀번호는 8자 이상이어야 합니다.")
    new_hash = password_hash(payload.new_password)  # 시간이 걸리는 계산은 쓰기 잠금 밖에서
    with closing(storage.connect()) as connection, write_transaction(connection):
        connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
        current_token_hash = hashlib.sha256(authorization[7:].strip().encode()).hexdigest()
        connection.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?", (user["id"], current_token_hash))
        append_audit(connection, storage, user["id"], "password_change")
    return {"ok": True}
