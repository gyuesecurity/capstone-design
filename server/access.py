"""증거별 권한 확인 (RBAC): 누가 이 증거를 보고, 받고, 검증할 수 있는지.

규칙 (통합 설계 문서 7장, A API 명세 3장)
- 소유자: 자기 증거에 모든 권한. 권한 표에 행이 없어도 됩니다.
- 공유받은 사람: evidence_permissions의 살아 있는 행(revoked_at IS NULL)에 있는 권한만.
  VIEW는 공유하면 항상 포함되고, DOWNLOAD(원본 다운로드)·VERIFY(무결성 검증)는 선택입니다.
- VIEW가 없으면 404 (남의 증거가 있다는 것도 드러내지 않음)
- VIEW는 있지만 필요한 권한이 없으면 403 + Chain of Custody에 ACCESS_DENIED 기록
  ACCESS_DENIED는 거부된 작업과 섞이지 않게 "새 트랜잭션"에 따로 기록합니다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import closing

from fastapi import HTTPException

from .custody import append_event
from .db import PERMISSION_TYPES, Storage, write_transaction

# 권한 종류 (DB 트리거와 같은 목록). 응답의 permissions도 이 순서로 줍니다.
PERMISSIONS = PERMISSION_TYPES
# 소유자만 할 수 있는 일(공유 관리)을 거부할 때 ACCESS_DENIED에 적는 이름
OWNER = "OWNER"


def active_permissions(connection: sqlite3.Connection, evidence_id: str, user_id: str) -> set[str]:
    """공유받은 사람의 살아 있는 권한 목록"""
    rows = connection.execute(
        "SELECT permission FROM evidence_permissions WHERE evidence_id = ? AND user_id = ? AND revoked_at IS NULL",
        (evidence_id, user_id),
    ).fetchall()
    return {row["permission"] for row in rows}


def access_dict(connection: sqlite3.Connection, row: sqlite3.Row, user: Mapping) -> dict | None:
    """응답의 access 필드. 볼 권한(VIEW)이 없으면 None

    owner.name은 소유자의 "지금" 이름입니다. (화면의 "김OO님이 공유" 표시용. 기록이 아니라 표시라서 지금 이름을 씀)
    """
    is_owner = row["owner_id"] == user["id"]
    granted = set(PERMISSIONS) if is_owner else active_permissions(connection, row["id"], user["id"])
    if "VIEW" not in granted:
        return None
    owner = connection.execute("SELECT id, name FROM users WHERE id = ?", (row["owner_id"],)).fetchone()
    return {
        "isOwner": is_owner,
        "owner": {"id": owner["id"], "name": owner["name"]},
        "permissions": [name for name in PERMISSIONS if name in granted],
        "canView": True,
        "canDownload": "DOWNLOAD" in granted,
        "canVerify": "VERIFY" in granted,
    }


def viewable_evidence(connection: sqlite3.Connection, user: Mapping, evidence_id: str) -> tuple[sqlite3.Row, dict]:
    """볼 수 있는 증거 1건과 그 access. 없거나 VIEW 권한이 없으면 404"""
    row = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
    access = access_dict(connection, row, user) if row is not None else None
    if access is None:
        raise HTTPException(404, "증거를 찾을 수 없습니다.")
    return row, access


def deny(storage: Storage, evidence_id: str, user: Mapping, required: str, message: str) -> None:
    """403 거부: ACCESS_DENIED 이벤트를 새 트랜잭션에 기록한 뒤 403을 냅니다.

    반드시 다른 트랜잭션이 끝난 뒤(바깥)에서 부릅니다. details에는 거부된 권한 이름만 남깁니다. (11/15 결정)
    """
    with closing(storage.connect()) as connection, write_transaction(connection):
        append_event(connection, evidence_id, "ACCESS_DENIED", user, "FAILURE", {"requiredPermission": required})
    raise HTTPException(403, message)
