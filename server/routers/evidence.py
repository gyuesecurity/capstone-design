"""증거 API: 등록, 목록, 상세, 원본 다운로드, 무결성 검증

3단계에서 바뀐 점
- 쓰기는 write_transaction(BEGIN IMMEDIATE)으로 감쌉니다.
- 새 증거에는 등록자 이름(registered_by_name), 새 검증 결과에는 검증자 이름(performed_by_name)을 저장합니다.
  이름은 프론트가 보낸 값이 아니라, 로그인한 사용자의 지금 이름을 서버가 넣습니다.
- INSERT는 컬럼 위치가 아니라 컬럼 이름을 적어서 넣습니다. (컬럼이 추가돼도 깨지지 않게)
- 검증할 때 파일 해시 계산은 쓰기 잠금 밖에서 하고, 잠금 안에서는 다시 확인한 뒤 기록만 합니다.

4단계에서 바뀐 점 (응답 모양은 그대로)
- 등록·다운로드·검증 때 Chain of Custody 이벤트를 같은 트랜잭션 안에서 함께 기록합니다.
  EVIDENCE_REGISTERED / EVIDENCE_DOWNLOADED / INTEGRITY_VERIFIED

5단계에서 바뀐 점 (기존 필드는 그대로, access 필드만 추가)
- 공유받은 사람도 권한만큼 쓸 수 있습니다. 보기(VIEW)=목록·상세, DOWNLOAD=원본 다운로드, VERIFY=무결성 검증
- VIEW가 없으면 404, VIEW는 있지만 필요한 권한이 없으면 403 + 관리 이력에 ACCESS_DENIED
- 목록에 scope(owned/shared/all) 추가. 기본값은 내 증거만(기존 동작)

6단계에서 바뀐 점 (B 암호화 모듈 연결, 응답은 검증 결과의 failureCode만 추가)
- 등록: 원본 SHA-256을 계산하면서 같은 조각을 AES-256-GCM으로 암호화해 저장 (디스크에는 암호문만)
- 다운로드·검증: 쓰기 잠금 밖에서 복호화. 인증 태그가 맞아야만 평문을 씀
- 6단계 이전에 평문으로 저장된 증거(LEGACY)도 그대로 다운로드·검증 가능
"""

from __future__ import annotations

import hmac
import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .. import config
from ..access import access_dict, deny, viewable_evidence
from ..audit import append_audit
from ..config import ALLOWED_EXTENSIONS, CHUNK_SIZE
from ..crypto import (
    AuthenticationFailedError,
    CryptoInternalError,
    InvalidCryptoInputError,
    InvalidEncryptionMetadataError,
    KeyUnavailableError,
    Sha256Hasher,
)
from ..custody import append_event
from ..db import Storage, get_storage, write_transaction
from ..security import current_user, utc_now
from ..serializers import evidence_dict, verification_dict
from ..stored_evidence import (
    AAD_VERSION,
    content_disposition,
    evidence_aad_v1,
    verified_plaintext,
)

router = APIRouter()


def owned_evidence(connection: sqlite3.Connection, user_id: str, evidence_id: str) -> sqlite3.Row:
    """내가 소유한 증거 1건. 없거나 다른 사람 것이면 404 (존재 여부를 드러내지 않음)"""
    row = connection.execute("SELECT * FROM evidence WHERE id = ? AND owner_id = ?", (evidence_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(404, "증거를 찾을 수 없습니다.")
    return row


def stored_file(storage: Storage, row: sqlite3.Row) -> Path:
    """저장 파일 경로. 저장 이름이 32자리 16진수인지 확인해서, 저장 폴더 밖 경로를 열지 않게 합니다."""
    if not re.fullmatch(r"[0-9a-f]{32}", row["stored_name"]):
        raise HTTPException(500, "저장 파일 경로가 유효하지 않습니다.")
    return storage.files_dir / row["stored_name"]


# 증거 등록: 파일을 1 MiB씩 받으면서 원본의 SHA-256(기준 해시)을 계산하고,
# 같은 조각을 B 모듈로 암호화해서 임의 이름으로 저장합니다. (6단계: 디스크에는 암호문만)
# 파일을 다 쓴 뒤에 DB에 기록하고, 실패하면 저장하던 파일을 지웁니다.
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
    digest = Sha256Hasher()
    display_name = name.strip() or filename
    # 등록 시각은 AAD에 들어가므로 암호화를 시작하기 전에 정합니다. (DB에도 같은 값을 저장)
    now = utc_now()
    # AAD는 DB에 저장하는 값과 정확히 같은 값으로 만듭니다. (나중에 같은 행으로 다시 만들어 복호화)
    aad = evidence_aad_v1(evidence_id=evidence_id, owner_id=user["id"], name=display_name, filename=filename,
                          kind=kind, source=source, registered_at=now, key_version=storage.crypto.key_version)
    session = storage.crypto.start_encryption(aad)
    try:
        descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                # 최대 크기는 요청마다 config에서 읽습니다. (테스트가 이 값을 바꿔서 413을 확인합니다)
                if size > config.MAX_FILE_SIZE:
                    raise HTTPException(413, "파일은 50 MB 이하만 등록할 수 있습니다.")
                digest.update(chunk)                 # 기준 해시는 암호화 전 원본으로 계산
                output.write(session.update(chunk))  # 디스크에는 암호문만
            final, result = session.finalize(AAD_VERSION)
            output.write(final)
            output.flush()
            os.fsync(output.fileno())
        target.chmod(0o400)
        encryption = result.metadata
        with closing(storage.connect()) as connection, write_transaction(connection):
            connection.execute("""INSERT INTO evidence (
                    id, owner_id, name, filename, stored_name, type, mime, size, kind, source,
                    description, memo, registered_at, sha256, verification_status, registered_by_name,
                    stored_size, encryption_algorithm, encryption_key_version, encryption_salt,
                    encryption_nonce, encryption_tag, encryption_aad_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                               (evidence_id, user["id"], display_name, filename, stored_name,
                                extension[1:].upper(), file.content_type or "application/octet-stream", size,
                                kind, source, description, memo, now, digest.hexdigest(), "NOT_VERIFIED",
                                user["name"], result.stored_size, encryption.algorithm, encryption.key_version,
                                encryption.salt, encryption.nonce, encryption.tag, encryption.aad_version))
            append_audit(connection, storage, user["id"], "upload", evidence_id, now)
            # Chain of Custody 1번 이벤트. 등록 당시 기준 해시와 파일 정보를 함께 남깁니다.
            append_event(connection, evidence_id, "EVIDENCE_REGISTERED", user, "SUCCESS",
                         {"sha256": digest.hexdigest(), "filename": filename, "size": size})
            row = owned_evidence(connection, user["id"], evidence_id)
            return evidence_dict(connection, row, access_dict(connection, row, user))
    except Exception:
        if target.exists():
            target.chmod(0o600)
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


# 증거 목록 (최신 등록순, 읽기만 함)
# scope 없음·owned: 내 증거만 (기존 동작 그대로) / shared: 공유받은 증거만 / all: 둘 다
_SHARED_IDS = """SELECT evidence_id FROM evidence_permissions
                 WHERE user_id = ? AND permission = 'VIEW' AND revoked_at IS NULL"""
_SCOPE_QUERIES = {
    "owned": ("SELECT * FROM evidence WHERE owner_id = ? ORDER BY registered_at DESC", 1),
    "shared": (f"SELECT * FROM evidence WHERE id IN ({_SHARED_IDS}) ORDER BY registered_at DESC", 1),
    "all": (f"SELECT * FROM evidence WHERE owner_id = ? OR id IN ({_SHARED_IDS}) ORDER BY registered_at DESC", 2),
}


@router.get("/api/evidence")
def list_evidence(scope: str = "owned", user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    if scope not in _SCOPE_QUERIES:
        raise HTTPException(422, "scope 값은 owned, shared, all 중 하나여야 합니다.")
    sql, user_params = _SCOPE_QUERIES[scope]
    with closing(storage.connect()) as connection:
        rows = connection.execute(sql, (user["id"],) * user_params).fetchall()
        return [evidence_dict(connection, row, access_dict(connection, row, user)) for row in rows]


# 증거 상세 (읽기만 함). 소유자이거나 VIEW 권한이 있으면 볼 수 있습니다.
@router.get("/api/evidence/{evidence_id}")
def get_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        row, access = viewable_evidence(connection, user, evidence_id)
        return evidence_dict(connection, row, access)


def _download_failed(storage: Storage, evidence_id: str, user: sqlite3.Row, failure_code: str) -> None:
    """다운로드를 못 한 사실을 관리 이력에 남깁니다. (설계 문서 "E4 FAILURE", 별도 트랜잭션)"""
    with closing(storage.connect()) as connection, write_transaction(connection):
        append_event(connection, evidence_id, "EVIDENCE_DOWNLOADED", user, "FAILURE", {"failureCode": failure_code})


# 원본 다운로드 (6단계: 복호화해서 보냄)
# 1) 권한(DOWNLOAD) 확인  2) 쓰기 잠금 밖에서 복호화 (B 모듈이 파일 전체의 인증 태그를 확인한 뒤에만 평문을 줌)
# 3) 쓰기 잠금 안에서 권한을 다시 확인하고 활동 기록·관리 이력을 저장  4) 평문을 보냄
# 실패: 파일 없음 404 / 변조(인증 실패) 409 → 둘 다 관리 이력에 EVIDENCE_DOWNLOADED FAILURE
#       키·메타데이터·내부 오류 500 → 기록하지 않음 (11/15 B 결정)
@router.get("/api/evidence/{evidence_id}/download")
def download_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        row, access = viewable_evidence(connection, user, evidence_id)
    if not access["canDownload"]:
        deny(storage, evidence_id, user, "DOWNLOAD", "다운로드 권한이 없습니다.")
    target = stored_file(storage, row)
    if not target.is_file():
        _download_failed(storage, evidence_id, user, "FILE_UNAVAILABLE")
        raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
    try:
        plain = verified_plaintext(storage.crypto, target, row)
    except AuthenticationFailedError:
        _download_failed(storage, evidence_id, user, "AUTHENTICATION_FAILED")
        raise HTTPException(409, "암호화 파일의 인증 정보가 일치하지 않아 다운로드할 수 없습니다.") from None
    except KeyUnavailableError as error:
        raise HTTPException(500, "암호화 키를 사용할 수 없어 다운로드할 수 없습니다.") from error
    except InvalidEncryptionMetadataError as error:  # InvalidCryptoInputError보다 먼저 (하위 클래스)
        raise HTTPException(500, "암호화 메타데이터가 올바르지 않아 다운로드할 수 없습니다.") from error
    except InvalidCryptoInputError as error:
        raise HTTPException(500, "암호화 모듈 입력 오류로 다운로드할 수 없습니다.") from error
    except CryptoInternalError as error:
        raise HTTPException(500, "저장된 파일을 안전하게 복호화하지 못했습니다.") from error
    except OSError as error:
        _download_failed(storage, evidence_id, user, "FILE_UNAVAILABLE")
        raise HTTPException(500, "저장된 파일을 읽지 못했습니다.") from error

    try:
        with closing(storage.connect()) as connection, write_transaction(connection):
            # 복호화하는 사이에 권한이 회수됐을 수 있으므로 쓰기 잠금 안에서 다시 확인합니다.
            row, access = viewable_evidence(connection, user, evidence_id)
            denied = not access["canDownload"]
            if not denied:
                append_audit(connection, storage, user["id"], "download", evidence_id)
                # 파일을 보내기 시작하는 시점의 이벤트 (활동 기록과 같은 트랜잭션, "기록 없이는 반출 없음")
                append_event(connection, evidence_id, "EVIDENCE_DOWNLOADED", user, "SUCCESS")
    except BaseException:
        plain.close()
        raise
    if denied:
        plain.close()
        deny(storage, evidence_id, user, "DOWNLOAD", "다운로드 권한이 없습니다.")

    def chunks():
        while chunk := plain.read(CHUNK_SIZE):
            yield chunk

    # 헤더는 5단계까지의 FileResponse와 같게 맞춥니다. (파일 이름 규칙, 원본 크기)
    headers = {"Content-Disposition": content_disposition(row["filename"]), "Content-Length": str(row["size"])}
    return StreamingResponse(chunks(), media_type="application/octet-stream", headers=headers,
                             background=BackgroundTask(plain.close))


# 무결성 검증 (6단계: 복호화한 평문의 SHA-256을 기준 해시와 비교)
# 결과 규칙 (11/15 B와 확정)
#   일치            MATCH       failure_code ''                     관리 이력 SUCCESS
#   해시 불일치     MISMATCH    HASH_MISMATCH                       관리 이력 SUCCESS
#   암호문·태그 변조 MISMATCH    AUTHENTICATION_FAILED               관리 이력 SUCCESS
#   파일을 못 읽음  UNAVAILABLE FILE_UNAVAILABLE                    관리 이력 FAILURE
#   키·메타데이터·내부 오류 → 500, 검증 결과·활동 기록·관리 이력 모두 남기지 않음
@router.post("/api/evidence/{evidence_id}/verify")
def verify_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    started = utc_now()
    # 1) 대상과 권한(VERIFY) 확인 (읽기만)
    with closing(storage.connect()) as connection:
        row, access = viewable_evidence(connection, user, evidence_id)
    if not access["canVerify"]:
        deny(storage, evidence_id, user, "VERIFY", "검증 권한이 없습니다.")
    target = stored_file(storage, row)

    # 2) 복호화와 해시 계산은 쓰기 잠금 밖에서 합니다. 큰 파일을 읽는 동안 다른 요청이 멈추지 않게 하기 위해서입니다.
    current_hash = None
    status = "UNAVAILABLE"
    reason = "저장된 파일을 읽을 수 없어 검증하지 못했습니다."
    failure_code = "FILE_UNAVAILABLE"
    try:
        plain = verified_plaintext(storage.crypto, target, row)
        try:
            digest = Sha256Hasher()
            while chunk := plain.read(CHUNK_SIZE):
                digest.update(chunk)
            current_hash = digest.hexdigest()
        finally:
            plain.close()
        status = "MATCH" if hmac.compare_digest(current_hash, row["sha256"]) else "MISMATCH"
        reason = "등록 당시 SHA-256과 일치합니다." if status == "MATCH" else "등록 당시 SHA-256과 일치하지 않습니다."
        failure_code = "" if status == "MATCH" else "HASH_MISMATCH"
    except AuthenticationFailedError:
        status = "MISMATCH"
        reason = "암호화 파일 또는 인증 태그가 변경되어 AES-GCM 인증에 실패했습니다."
        failure_code = "AUTHENTICATION_FAILED"
    except KeyUnavailableError as error:
        raise HTTPException(500, "암호화 키를 사용할 수 없어 무결성을 검증하지 못했습니다.") from error
    except InvalidEncryptionMetadataError as error:  # InvalidCryptoInputError보다 먼저 (하위 클래스)
        raise HTTPException(500, "암호화 메타데이터가 올바르지 않아 무결성을 검증하지 못했습니다.") from error
    except InvalidCryptoInputError as error:
        raise HTTPException(500, "암호화 모듈 입력 오류로 무결성을 검증하지 못했습니다.") from error
    except CryptoInternalError as error:
        raise HTTPException(500, "암호화 모듈 내부 오류로 무결성을 검증하지 못했습니다.") from error
    except OSError:
        pass  # 파일 없음·읽기 실패 → UNAVAILABLE (기본값 그대로)
    completed = utc_now()
    result_id = f"VERIFY-{uuid.uuid4().hex}"

    # 3) 쓰기 잠금 안에서는 대상과 권한을 다시 확인한 뒤 기록만 합니다.
    #    해시를 계산하는 사이에 VERIFY 권한이 회수됐으면 아무것도 저장하지 않고 403입니다.
    with closing(storage.connect()) as connection, write_transaction(connection):
        row, access = viewable_evidence(connection, user, evidence_id)
        denied = not access["canVerify"]
        if not denied:
            connection.execute("""INSERT INTO verifications (
                    id, evidence_id, user_id, started_at, completed_at, expected_hash,
                    current_hash, status, reason, performed_by_name, failure_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                               (result_id, evidence_id, user["id"], started, completed, row["sha256"],
                                current_hash, status, reason, user["name"], failure_code))
            connection.execute("UPDATE evidence SET verification_status = ? WHERE id = ?", (status, evidence_id))
            append_audit(connection, storage, user["id"], "verify:" + status, evidence_id, completed)
            # MATCH·MISMATCH는 검증 작업 자체는 끝났으므로 SUCCESS, 파일을 못 읽은 UNAVAILABLE만 FAILURE
            details = {"status": status, "verificationId": result_id}
            if failure_code:
                details["failureCode"] = failure_code
            append_event(connection, evidence_id, "INTEGRITY_VERIFIED", user,
                         "FAILURE" if status == "UNAVAILABLE" else "SUCCESS", details)
            response = {"result": verification_dict(connection.execute("SELECT * FROM verifications WHERE id = ?", (result_id,)).fetchone(), row["filename"]), "saved": True}
    if denied:
        deny(storage, evidence_id, user, "VERIFY", "검증 권한이 없습니다.")
    return response
