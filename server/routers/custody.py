"""Chain of Custody API (읽기만 함)

C1 GET /api/evidence/{evidence_id}/custody         증거 1건의 이력 (번호순)
C2 GET /api/evidence/{evidence_id}/custody/verify  이력 사슬 검사

지금은 증거 소유자만 볼 수 있습니다. 공유받은 사람의 접근은 공유·권한(RBAC) 단계에서 엽니다.
다른 사람 증거이거나 없는 증거면 기존 API와 같이 404입니다. (존재 여부를 드러내지 않음)
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends

from ..custody import event_dict, load_events, verify_chain
from ..db import Storage, get_storage
from ..security import current_user
from .evidence import owned_evidence

router = APIRouter()


# C1: 이력 목록
@router.get("/api/evidence/{evidence_id}/custody")
def custody_events(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        owned_evidence(connection, user["id"], evidence_id)
        rows = load_events(connection, evidence_id)
    return {"evidenceId": evidence_id, "events": [event_dict(row) for row in rows]}


# C2: 사슬 검사 (번호·첫 이벤트·앞뒤 연결·해시 재계산·형식)
@router.get("/api/evidence/{evidence_id}/custody/verify")
def custody_verify(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        owned_evidence(connection, user["id"], evidence_id)
        rows = load_events(connection, evidence_id)
    failures = verify_chain(rows)
    last = rows[-1] if rows else None
    return {
        "evidenceId": evidence_id,
        "valid": not failures,
        "eventCount": len(rows),
        "lastSequenceNo": last["sequence_no"] if last else None,
        "lastEventHash": last["event_hash"] if last else None,
        "failures": failures,
    }
