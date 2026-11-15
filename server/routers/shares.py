"""증거 공유 API (5단계). A 화면(app.js api.shares, showShares)의 모양에 맞췄습니다. (A API 명세 3장)

P2 GET    /api/evidence/{evidence_id}/shares            공유 목록 (사람별 1줄)
P3 PUT    /api/evidence/{evidence_id}/shares            공유 추가·수정 {email, download, verify}
P4 DELETE /api/evidence/{evidence_id}/shares/{user_id}  공유 해제 (그 사람의 모든 권한 회수)

- 증거 소유자만 쓸 수 있습니다. 공유받은 사람이 시도하면 403 + 관리 이력에 ACCESS_DENIED(requiredPermission=OWNER)
- 권한은 1개당 1행으로 저장하고, 회수할 때는 행을 지우지 않고 revoked_at만 채웁니다.
- 바뀐 권한마다 관리 이력에 PERMISSION_GRANTED / PERMISSION_REVOKED를 남깁니다. (권한 변경과 같은 트랜잭션)
  details: {targetUserId, targetName, permission}  ← targetName은 그 시점의 받는 사람 이름
- 활동 기록(audit) 이름은 A가 정한 share_update / share_revoke입니다. 바뀐 게 없으면 아무것도 기록하지 않습니다.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException

from ..access import OWNER, PERMISSIONS, active_permissions, deny, viewable_evidence
from ..config import EMAIL_PATTERN
from ..custody import append_event
from ..db import Storage, get_storage, write_transaction
from ..schemas import ShareUpdate
from ..security import current_user, utc_now

router = APIRouter()

OWNER_ONLY = "공유 관리는 증거 소유자만 할 수 있습니다."


def _owner_evidence(storage: Storage, user: sqlite3.Row, evidence_id: str) -> sqlite3.Row:
    """공유 관리 대상 증거. 볼 권한이 없으면 404, 보기만 가능한 사람이면 403 + ACCESS_DENIED"""
    with closing(storage.connect()) as connection:
        row, access = viewable_evidence(connection, user, evidence_id)
    if not access["isOwner"]:
        deny(storage, evidence_id, user, OWNER, OWNER_ONLY)
    return row


def _share_rows(connection: sqlite3.Connection, evidence_id: str, user_id: str | None = None) -> list[dict]:
    """공유 목록 (사람별 1줄). 처음 공유한 순서(VIEW를 받은 시각)대로. user_id를 주면 그 사람만"""
    sql = """SELECT users.id, users.email, users.name, granted_view.granted_at,
                 EXISTS (SELECT 1 FROM evidence_permissions p WHERE p.evidence_id = granted_view.evidence_id
                         AND p.user_id = granted_view.user_id AND p.permission = 'DOWNLOAD' AND p.revoked_at IS NULL) AS download,
                 EXISTS (SELECT 1 FROM evidence_permissions p WHERE p.evidence_id = granted_view.evidence_id
                         AND p.user_id = granted_view.user_id AND p.permission = 'VERIFY' AND p.revoked_at IS NULL) AS verify
             FROM evidence_permissions granted_view JOIN users ON users.id = granted_view.user_id
             WHERE granted_view.evidence_id = ? AND granted_view.permission = 'VIEW' AND granted_view.revoked_at IS NULL"""
    params: tuple = (evidence_id,)
    if user_id is not None:
        sql += " AND granted_view.user_id = ?"
        params += (user_id,)
    rows = connection.execute(sql + " ORDER BY granted_view.granted_at, granted_view.id", params).fetchall()
    return [{"id": row["id"], "email": row["email"], "name": row["name"], "download": bool(row["download"]),
             "verify": bool(row["verify"]), "grantedAt": row["granted_at"]} for row in rows]


def _permission_event(connection, evidence_id, owner, target, permission, event_type) -> None:
    """권한 1개가 바뀐 것을 관리 이력에 남깁니다."""
    append_event(connection, evidence_id, event_type, owner, "SUCCESS",
                 {"targetUserId": target["id"], "targetName": target["name"], "permission": permission})


# P2: 공유 목록
@router.get("/api/evidence/{evidence_id}/shares")
def list_shares(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    _owner_evidence(storage, user, evidence_id)
    with closing(storage.connect()) as connection:
        return _share_rows(connection, evidence_id)


# P3: 공유 추가·수정. 처음이면 VIEW + 체크한 권한을 주고, 이미 공유 중이면 체크를 끈 권한만 회수합니다.
@router.put("/api/evidence/{evidence_id}/shares")
def update_share(evidence_id: str, payload: ShareUpdate, user: sqlite3.Row = Depends(current_user),
                 storage: Storage = Depends(get_storage)):
    _owner_evidence(storage, user, evidence_id)
    email = payload.email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(422, "이메일 형식을 확인해 주세요.")
    wanted = {"VIEW"} | ({"DOWNLOAD"} if payload.download else set()) | ({"VERIFY"} if payload.verify else set())

    with closing(storage.connect()) as connection, write_transaction(connection):
        target = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if target is None:
            raise HTTPException(404, "사용자를 찾을 수 없습니다.")
        if target["id"] == user["id"]:
            raise HTTPException(422, "증거 소유자에게는 공유할 수 없습니다.")
        current = active_permissions(connection, evidence_id, target["id"])
        now = utc_now()
        # 부여: VIEW를 먼저 (DOWNLOAD·VERIFY는 VIEW가 있어야 부여 가능)
        for permission in [name for name in PERMISSIONS if name in wanted - current]:
            connection.execute("""INSERT INTO evidence_permissions (evidence_id, user_id, permission, granted_by, granted_at)
                                  VALUES (?, ?, ?, ?, ?)""", (evidence_id, target["id"], permission, user["id"], now))
            _permission_event(connection, evidence_id, user, target, permission, "PERMISSION_GRANTED")
        # 회수: 체크를 끈 권한만 (VIEW는 wanted에 항상 있으므로 여기서 회수되지 않음)
        for permission in [name for name in PERMISSIONS if name in current - wanted]:
            connection.execute("""UPDATE evidence_permissions SET revoked_at = ?
                                  WHERE evidence_id = ? AND user_id = ? AND permission = ? AND revoked_at IS NULL""",
                               (now, evidence_id, target["id"], permission))
            _permission_event(connection, evidence_id, user, target, permission, "PERMISSION_REVOKED")
        if wanted != current:
            connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                               (user["id"], "share_update", evidence_id, now))
        return _share_rows(connection, evidence_id, target["id"])[0]


# P4: 공유 해제. DOWNLOAD·VERIFY를 먼저, VIEW를 마지막에 회수합니다.
@router.delete("/api/evidence/{evidence_id}/shares/{user_id}")
def revoke_share(evidence_id: str, user_id: str, user: sqlite3.Row = Depends(current_user),
                 storage: Storage = Depends(get_storage)):
    _owner_evidence(storage, user, evidence_id)
    with closing(storage.connect()) as connection, write_transaction(connection):
        current = active_permissions(connection, evidence_id, user_id)
        if "VIEW" not in current:
            raise HTTPException(404, "공유 정보를 찾을 수 없습니다.")
        target = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        now = utc_now()
        for permission in reversed([name for name in PERMISSIONS if name in current]):
            connection.execute("""UPDATE evidence_permissions SET revoked_at = ?
                                  WHERE evidence_id = ? AND user_id = ? AND permission = ? AND revoked_at IS NULL""",
                               (now, evidence_id, user_id, permission))
            _permission_event(connection, evidence_id, user, target, permission, "PERMISSION_REVOKED")
        connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                           (user["id"], "share_revoke", evidence_id, now))
    return {"id": user_id, "revoked": True}
