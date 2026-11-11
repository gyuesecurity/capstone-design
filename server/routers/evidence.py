"""증거 API: 등록, 목록, 상세, 원본 다운로드, 무결성 검증

기존 create_app() 안의 owned_evidence와 증거 API 5개를 그대로 옮겨 왔습니다.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config
from ..config import ALLOWED_EXTENSIONS, CHUNK_SIZE
from ..db import Storage, get_storage
from ..security import current_user, utc_now
from ..serializers import evidence_dict, verification_dict

router = APIRouter()


def owned_evidence(connection: sqlite3.Connection, user_id: str, evidence_id: str) -> sqlite3.Row:
    """내가 소유한 증거 1건. 없거나 다른 사람 것이면 404 (존재 여부를 드러내지 않음)"""
    row = connection.execute("SELECT * FROM evidence WHERE id = ? AND owner_id = ?", (evidence_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(404, "증거를 찾을 수 없습니다.")
    return row


# 증거 등록: 파일을 1 MiB씩 받으면서 SHA-256(기준 해시)을 계산하고, 임의 이름으로 저장합니다.
# 실패하면 저장하던 파일을 지웁니다.
@router.post("/api/evidence", status_code=201)
async def upload_evidence(
    file: UploadFile = File(...), name: str = Form(""), kind: str = Form("파일"),
    source: str = Form("직접 수집"), description: str = Form(""), memo: str = Form(""),
    user: sqlite3.Row = Depends(current_user),
    storage: Storage = Depends(get_storage),
):
    filename = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
    if not filename or any(ord(char) < 32 for char in filename) or len(filename) > 255:
        raise HTTPException(422, "파일명을 확인해 주세요.")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "허용되지 않은 파일 형식입니다.")
    if len(name) > 200 or len(kind) > 50 or len(source) > 200 or len(description) > 4000 or len(memo) > 4000:
        raise HTTPException(422, "입력 길이를 확인해 주세요.")
    evidence_id = f"EV-{uuid.uuid4().hex}"
    stored_name = uuid.uuid4().hex
    target = storage.files_dir / stored_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    size = 0
    digest = hashlib.sha256()
    try:
        descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                # 최대 크기는 요청마다 config에서 읽습니다. (기존 코드도 실행 중에 값을 읽었고, 테스트가 이 값을 바꿔서 413을 확인합니다)
                if size > config.MAX_FILE_SIZE:
                    raise HTTPException(413, "파일은 50 MB 이하만 등록할 수 있습니다.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        target.chmod(0o400)
        now = utc_now()
        with closing(storage.connect()) as connection, connection:
            connection.execute("""INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                               (evidence_id, user["id"], name.strip() or filename, filename, stored_name,
                                extension[1:].upper(), file.content_type or "application/octet-stream", size,
                                kind, source, description, memo, now, digest.hexdigest(), "NOT_VERIFIED"))
            connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                               (user["id"], "upload", evidence_id, now))
            row = owned_evidence(connection, user["id"], evidence_id)
            return evidence_dict(connection, row)
    except Exception:
        if target.exists():
            target.chmod(0o600)
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


# 내 증거 목록 (최신 등록순)
@router.get("/api/evidence")
def list_evidence(user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection, connection:
        rows = connection.execute("SELECT * FROM evidence WHERE owner_id = ? ORDER BY registered_at DESC", (user["id"],)).fetchall()
        return [evidence_dict(connection, row) for row in rows]


# 증거 상세
@router.get("/api/evidence/{evidence_id}")
def get_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection, connection:
        return evidence_dict(connection, owned_evidence(connection, user["id"], evidence_id))


# 원본 다운로드: 저장 이름이 32자리 16진수인지 확인해서, 저장 폴더 밖 경로를 열지 않게 합니다.
@router.get("/api/evidence/{evidence_id}/download")
def download_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection, connection:
        row = owned_evidence(connection, user["id"], evidence_id)
        if not re.fullmatch(r"[0-9a-f]{32}", row["stored_name"]):
            raise HTTPException(500, "저장 파일 경로가 유효하지 않습니다.")
        target = storage.files_dir / row["stored_name"]
        if not target.is_file():
            raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
        connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                           (user["id"], "download", evidence_id, utc_now()))
        return FileResponse(target, media_type="application/octet-stream", filename=row["filename"])


# 무결성 검증: 저장된 파일의 SHA-256을 다시 계산해 기준 해시와 비교합니다.
# 파일을 읽을 수 없으면 UNAVAILABLE. 결과는 검증 기록 1행으로 남깁니다.
@router.post("/api/evidence/{evidence_id}/verify")
def verify_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    started = utc_now()
    with closing(storage.connect()) as connection, connection:
        row = owned_evidence(connection, user["id"], evidence_id)
        if not re.fullmatch(r"[0-9a-f]{32}", row["stored_name"]):
            raise HTTPException(500, "저장 파일 경로가 유효하지 않습니다.")
        target = storage.files_dir / row["stored_name"]
        current_hash = None
        status = "UNAVAILABLE"
        reason = "저장된 파일을 읽을 수 없어 검증하지 못했습니다."
        try:
            digest = hashlib.sha256()
            with target.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    digest.update(chunk)
            current_hash = digest.hexdigest()
            status = "MATCH" if hmac.compare_digest(current_hash, row["sha256"]) else "MISMATCH"
            reason = "등록 당시 SHA-256과 일치합니다." if status == "MATCH" else "등록 당시 SHA-256과 일치하지 않습니다."
        except OSError:
            pass
        completed = utc_now()
        result_id = f"VERIFY-{uuid.uuid4().hex}"
        connection.execute("""INSERT INTO verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (result_id, evidence_id, user["id"], started, completed, row["sha256"], current_hash, status, reason))
        connection.execute("UPDATE evidence SET verification_status = ? WHERE id = ?", (status, evidence_id))
        connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                           (user["id"], "verify:" + status, evidence_id, completed))
        return {"result": verification_dict(connection.execute("SELECT * FROM verifications WHERE id = ?", (result_id,)).fetchone(), row["filename"]), "saved": True}
