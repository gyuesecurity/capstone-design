"""DB 행(row)을 API 응답(JSON)으로 바꾸는 함수.

기존 create_app() 안에 있던 verification_dict, evidence_dict를 그대로 옮겨 왔습니다.
응답 필드 이름과 값은 바꾸지 않습니다. (프론트 A가 이 이름에 맞춰 화면을 그립니다)
"""

from __future__ import annotations

import sqlite3

from .config import STATUS_LABELS


def verification_dict(row: sqlite3.Row, filename: str = "") -> dict:
    """검증 결과 1건"""
    return {
        "id": row["id"], "evidenceId": row["evidence_id"], "startedAt": row["started_at"],
        "completedAt": row["completed_at"], "expectedHash": row["expected_hash"],
        "currentHash": row["current_hash"], "status": row["status"], "reason": row["reason"],
        "filename": filename,
    }


def evidence_dict(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """증거 1건 + 그 증거의 검증 기록(최신순)"""
    history = [verification_dict(item, row["filename"]) for item in connection.execute(
        "SELECT * FROM verifications WHERE evidence_id = ? ORDER BY completed_at DESC", (row["id"],)
    ).fetchall()]
    return {
        "id": row["id"], "name": row["name"], "filename": row["filename"],
        "type": row["type"], "mime": row["mime"], "size": row["size"],
        "kind": row["kind"], "source": row["source"], "description": row["description"], "memo": row["memo"],
        # selectedAt은 호환용 필드입니다. 실제 파일 선택 시각이 아니라 서버 등록 시각(registeredAt)과 같은 값입니다. (11/9 결정)
        "selectedAt": row["registered_at"], "registeredAt": row["registered_at"],
        "date": row["registered_at"][:10], "sha256": row["sha256"],
        "verificationStatus": row["verification_status"], "status": STATUS_LABELS[row["verification_status"]],
        "lastVerification": history[0] if history else None, "verifications": history, "storage": "server",
    }
