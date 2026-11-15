"""저장된 증거 파일의 암호화 연결 부분 (6단계). B의 main.py에 있던 함수를 옮겨 왔습니다.

- evidence_aad_v1: 암호문에 묶는 증거 정보(AAD). 11/15 B 결정으로 v1 구성은 동결 (등록자 이름 추가 안 함)
  나중에 바꿀 일이 생기면 v1은 그대로 두고 v2를 새로 추가합니다.
- encryption_metadata / aad_for_row: DB 행에서 복호화에 필요한 값을 꺼냅니다.
- verified_plaintext: 인증 태그까지 확인된 평문만 돌려줍니다. (LEGACY 평문 파일은 그대로 복사)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from .config import CHUNK_SIZE
from .crypto import EncryptionMetadata, EvidenceCrypto, InvalidEncryptionMetadataError

AAD_VERSION = "v1"
LEGACY = "LEGACY"  # 6단계 이전에 평문으로 저장된 증거


def evidence_aad_v1(*, evidence_id: str, owner_id: str, name: str, filename: str,
                    kind: str, source: str, registered_at: str, key_version: str) -> bytes:
    """AAD v1 (B 코드 그대로). DB에 저장하는 값과 정확히 같은 값을 넣어야 나중에 복호화할 수 있습니다."""
    return json.dumps({
        "schema": "v1",
        "evidenceId": evidence_id,
        "ownerId": owner_id,
        "name": name,
        "filename": filename,
        "kind": kind,
        "source": source,
        "registeredAt": registered_at,
        "keyVersion": key_version,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encryption_metadata(row: sqlite3.Row) -> EncryptionMetadata | None:
    """DB 행의 암호화 정보. LEGACY(평문)면 None"""
    if row["encryption_algorithm"] == LEGACY:
        return None
    values = (row["encryption_key_version"], row["encryption_salt"], row["encryption_nonce"], row["encryption_tag"])
    if not all(values):
        raise InvalidEncryptionMetadataError("증거의 암호화 메타데이터가 누락되었습니다.")
    return EncryptionMetadata(row["encryption_algorithm"], *values, row["encryption_aad_version"])


def aad_for_row(row: sqlite3.Row) -> bytes:
    """DB 행으로 등록 때와 같은 AAD를 다시 만듭니다."""
    version = row["encryption_aad_version"]
    if not version:
        return b""
    if version != AAD_VERSION:
        raise InvalidEncryptionMetadataError(f"지원하지 않는 AAD 버전입니다: {version}")
    return evidence_aad_v1(
        evidence_id=row["id"], owner_id=row["owner_id"], name=row["name"],
        filename=row["filename"], kind=row["kind"], source=row["source"],
        registered_at=row["registered_at"], key_version=row["encryption_key_version"],
    )


def _file_chunks(target: Path):
    with target.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            yield chunk


def verified_plaintext(crypto: EvidenceCrypto, target: Path, row: sqlite3.Row) -> BinaryIO:
    """인증까지 확인된 평문 스트림 (부른 쪽이 반드시 닫아야 함)

    암호화된 증거: B의 decrypt_verified가 파일 전체의 인증 태그를 확인한 뒤에만 돌려줍니다.
    LEGACY 증거: 평문 파일을 임시 스트림으로 복사합니다.
    실패: AuthenticationFailedError(변조) / KeyUnavailableError / InvalidEncryptionMetadataError /
          InvalidCryptoInputError / CryptoInternalError / OSError(파일 읽기 실패)
    """
    metadata = encryption_metadata(row)
    if metadata is not None:
        return crypto.decrypt_verified(_file_chunks(target), metadata, aad_for_row(row))
    plain = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        for chunk in _file_chunks(target):
            plain.write(chunk)
        plain.seek(0)
        return plain
    except Exception:
        plain.close()
        raise


def content_disposition(filename: str) -> str:
    """다운로드 파일 이름 헤더. 5단계까지 쓰던 FileResponse와 같은 규칙이라 화면 쪽 동작이 그대로입니다."""
    quoted = quote(filename)
    if quoted != filename:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{filename}"'
