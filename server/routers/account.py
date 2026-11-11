"""내 계정 API: 내 정보 조회·이름 수정, 활동 기록

3단계: 쓰기는 write_transaction(BEGIN IMMEDIATE)으로 감쌉니다. 응답과 오류 문구는 그대로입니다.
이름을 바꿔도 과거 증거·검증 기록의 이름(registered_by_name, performed_by_name)은 바뀌지 않습니다.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException

from ..db import Storage, get_storage, write_transaction
from ..schemas import ProfileUpdate
from ..security import current_user, utc_now

router = APIRouter()


# 내 정보 조회
@router.get("/api/me")
def me(user: sqlite3.Row = Depends(current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "createdAt": user["created_at"]}


# 이름 수정 (1~100자)
@router.put("/api/me")
def update_profile(
    payload: ProfileUpdate,
    user: sqlite3.Row = Depends(current_user),
    storage: Storage = Depends(get_storage),
):
    name = payload.name.strip()
    if not name or len(name) > 100:
        raise HTTPException(422, "이름은 1~100자로 입력해 주세요.")
    with closing(storage.connect()) as connection, write_transaction(connection):
        connection.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
        connection.execute("INSERT INTO audit(user_id, action, created_at) VALUES (?, ?, ?)",
                           (user["id"], "profile_update", utc_now()))
    return {"id": user["id"], "email": user["email"], "name": name, "createdAt": user["created_at"]}


# 내 활동 기록 최근 100건 (마이페이지). 증거 이름은 내가 소유한 증거일 때만 붙입니다. (읽기만 함)
@router.get("/api/audit")
def audit_log(user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        rows = connection.execute("""SELECT audit.action, audit.evidence_id, audit.created_at,
            evidence.name AS evidence_name, evidence.filename AS evidence_filename
            FROM audit LEFT JOIN evidence ON evidence.id = audit.evidence_id AND evidence.owner_id = audit.user_id
            WHERE audit.user_id = ? ORDER BY audit.id DESC LIMIT 100""", (user["id"],)).fetchall()
        return [{"action": row["action"], "evidenceId": row["evidence_id"],
                 "evidenceName": row["evidence_name"], "evidenceFilename": row["evidence_filename"],
                 "createdAt": row["created_at"]} for row in rows]
