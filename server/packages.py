"""증거 패키지 만들기·보여 주기·내려받기 (8단계). 설계 문서 STEP 3·8, A 화면 report-packages.js, D 명세 PackageBuildBundle v1

패키지 = 보고서 1개를 기준으로 "보고서 PDF + 증거 원본 + 검증 기록 + 관리 이력"을 묶은 ZIP 1개

흐름 (K1)
1) 내 보고서인지 확인 (아니면 404)
2) 저장된 보고서 PDF가 기록과 같은지 확인 (다르면 409. 증거 원본을 열기 전에 멈춤)
3) 보고서의 증거마다 다운로드 권한(DOWNLOAD) 확인 (없으면 ACCESS_DENIED 기록 후 403)
4) 원본 크기 합계 200 MB 상한 (넘으면 422, 11/16 결정)
5) 읽기 (잠금 없이): 증거마다 관리 이력 마지막 번호·해시(= 기준점), 검증 기록 전체
6) 원본 복호화 + SHA-256 = 기준 해시 확인 (B 모듈. 인증 태그가 맞아야만 평문을 받음)
   다르거나 변조면 409 + PACKAGE_EXPORTED FAILURE (원인 증거: 원인 코드, 이미 처리한 증거: ABORTED. 11/16 결정)
   키·메타데이터·내부 오류는 500, 기록하지 않음 (11/15 B 결정과 같은 규칙)
7) manifest.json · verification.json · custody.json을 C가 바이트로 만듦 (키 정렬, 구분자 , :, 한글 그대로)
8) D 함수(build_package_zip)로 .zip.part에 ZIP 작성
9) C가 ZIP 검사: 열림, 암호화 항목 없음, 항목 이름·순서·크기·SHA-256이 넘긴 그대로
10) fsync → 최종 이름 → 읽기 전용
11) 기록 (쓰기 잠금 안): 권한과 기준점을 다시 확인 (바뀌었으면 같은 패키지 ID로 처음부터, 최대 3번)
    패키지 + 증거 목록 + 증거마다 PACKAGE_EXPORTED SUCCESS + 활동 기록 package_create를 한 트랜잭션에

D는 DB·권한·관리 이력을 다루지 않고, C가 넘긴 바이트를 ZIP에 그대로 씁니다.
ZIP 파일은 지금은 평문으로 저장하고 SHA-256으로 위조를 확인합니다. (암호화는 B와 AAD를 정한 뒤 추가. 11/16 결정)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import unicodedata
import uuid
import zipfile
from contextlib import closing

from fastapi import HTTPException

from d_package.package_builder import (
    PackageBuildError,
    PackageBundleError,
    build_package_zip,
)

from .access import access_dict
from .audit import append_audit
from .config import CHUNK_SIZE
from .crypto import (
    AuthenticationFailedError,
    CryptoInternalError,
    InvalidCryptoInputError,
    KeyUnavailableError,
    Sha256Hasher,
)
from .custody import append_event, canonical_json
from .db import Storage, write_transaction
from .reports import kst, load_report, read_report_pdf
from .routers.evidence import stored_file
from .security import utc_now
from .stored_evidence import verified_plaintext

MAX_PACKAGE_ORIGINAL_SIZE = 200 * 1024 * 1024  # 원본 크기 합계 상한 (11/16 결정)
MAX_ATTEMPTS = 3                                # 보고서와 같은 재시도 상한
MANIFEST_VERSION = "package-manifest-v1"
BUNDLE_VERSION = "package-v1"                   # D 명세 PackageBuildBundle v1
ABORTED = "ABORTED"                             # 다른 증거 문제로 중단된 증거의 실패 코드

NO_DOWNLOAD = "다운로드 권한이 없는 증거가 포함되어 있습니다."
REVOKED_DOWNLOAD = "다운로드 권한이 회수된 증거가 포함되어 있습니다."
REPORT_FILE_PROBLEM = "보고서 파일 문제가 해결되지 않아 패키지를 만들 수 없습니다."
TOO_LARGE = "패키지에 넣을 원본 파일 크기의 합계가 200 MB를 넘어 패키지를 만들 수 없습니다."
HASH_PROBLEM = "원본 해시가 기준 해시와 일치하지 않는 증거가 있어 패키지를 만들 수 없습니다."
FILE_PROBLEM = "원본 파일을 읽을 수 없는 증거가 있어 패키지를 만들 수 없습니다."
CREATE_FAILED = "패키지를 만들지 못했습니다."
CHANGED_DURING_CREATE = "패키지를 만드는 동안 증거 이력이 변경되었습니다. 다시 시도해 주세요."
PACKAGE_NOT_FOUND = "패키지를 찾을 수 없습니다."
PACKAGE_FILE_NOT_FOUND = "패키지 파일을 찾을 수 없습니다."
PACKAGE_INTEGRITY_PROBLEM = "파일 무결성 문제로 복구가 필요한 패키지입니다. 관리자에게 문의해 주세요."

# ZIP 안 파일 이름에 쓰지 않는 글자: Windows 금지 글자 + 제어 문자 → _
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f-\x9f]')
_KEY = re.compile(r"[0-9a-f]{32}")


class _EvidenceProblem(Exception):
    """증거 1건의 원본 문제 (409 + PACKAGE_EXPORTED FAILURE)"""

    def __init__(self, index: int, failure_code: str, message: str):
        super().__init__(failure_code)
        self.index = index
        self.failure_code = failure_code
        self.message = message


# ---------------------------------------------------------------- 이름·경로

def safe_filename(filename: str) -> str:
    """ZIP 안 파일 이름. 금지 글자는 _로, 앞뒤 공백·점은 지우고, 너무 길면 확장자를 남기고 자름"""
    name = unicodedata.normalize("NFC", filename)
    name = _UNSAFE_NAME.sub("_", name).strip().strip(".")
    if not name:
        name = "file"
    if len(name) > 120:
        stem, dot, extension = name.rpartition(".")
        if dot and 0 < len(extension) <= 10:
            name = stem[: 120 - len(extension) - 1] + "." + extension
        else:
            name = name[:120]
    return name


def evidence_path(order: int, evidence_id: str, filename: str) -> str:
    """패키지 안 증거 경로 (패키지 폴더 기준). 순번·증거 ID가 앞에 있어 이름이 겹치지 않음"""
    return f"evidence/{order:02d}_{evidence_id}_{safe_filename(filename)}"


def package_filename(package_id: str) -> str:
    return f"{package_id}.zip"


# ---------------------------------------------------------------- 권한

def _lacking_download(connection: sqlite3.Connection, user, evidence_ids: list[str]) -> list[tuple[str, bool]]:
    """다운로드 권한이 없는 증거 목록 [(증거 ID, 볼 권한은 있는지)]"""
    lacking = []
    for evidence_id in evidence_ids:
        row = connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        access = access_dict(connection, row, user) if row is not None else None
        if access is None:
            lacking.append((evidence_id, False))
        elif not access["canDownload"]:
            lacking.append((evidence_id, True))
    return lacking


def _deny_all(storage: Storage, user, lacking: list[tuple[str, bool]], message: str) -> None:
    """403 거부. 볼 권한은 남아 있는 증거에만 ACCESS_DENIED를 새 트랜잭션에 기록합니다.

    볼 권한까지 회수된 증거는 그 사람이 더 이상 볼 수 없는 이력이라 기록하지 않습니다. (access.deny와 같은 기준)
    """
    visible = [evidence_id for evidence_id, can_view in lacking if can_view]
    if visible:
        with closing(storage.connect()) as connection, write_transaction(connection):
            for evidence_id in visible:
                append_event(connection, evidence_id, "ACCESS_DENIED", user, "FAILURE", {"requiredPermission": "DOWNLOAD"})
    raise HTTPException(403, message)


# ---------------------------------------------------------------- 읽기 (스냅샷)

def _report_evidence(connection: sqlite3.Connection, report_id: str) -> list[sqlite3.Row]:
    """보고서에 넣은 증거 (보고서 순서대로) + 그때의 검증 ID"""
    return connection.execute("""SELECT report_evidence.verification_id AS report_verification_id, evidence.*
        FROM report_evidence JOIN evidence ON evidence.id = report_evidence.evidence_id
        WHERE report_evidence.report_id = ? ORDER BY report_evidence.item_order""", (report_id,)).fetchall()


def _read_snapshot(connection: sqlite3.Connection, report_id: str) -> list[dict]:
    """증거마다 지금 모습: 행, 관리 이력(기준점까지 전부), 검증 기록 전체"""
    items = []
    for row in _report_evidence(connection, report_id):
        events = connection.execute(
            "SELECT * FROM chain_of_custody WHERE evidence_id = ? ORDER BY sequence_no", (row["id"],)).fetchall()
        verifications = connection.execute(
            "SELECT * FROM verifications WHERE evidence_id = ? ORDER BY completed_at, rowid", (row["id"],)).fetchall()
        items.append({"row": row, "events": events, "verifications": verifications,
                      "latest": verifications[-1] if verifications else None,
                      "snapshot": (events[-1]["sequence_no"], events[-1]["event_hash"])})
    return items


def _current_snapshot(connection: sqlite3.Connection, evidence_id: str) -> tuple | None:
    last = connection.execute(
        "SELECT sequence_no, event_hash FROM chain_of_custody WHERE evidence_id = ? ORDER BY sequence_no DESC LIMIT 1",
        (evidence_id,)).fetchone()
    return (last["sequence_no"], last["event_hash"]) if last else None


# ---------------------------------------------------------------- 원본 읽기

def _read_original(storage: Storage, index: int, row: sqlite3.Row) -> bytes:
    """원본 평문을 읽고 SHA-256이 기준 해시와 같은지 확인합니다. 문제가 있으면 _EvidenceProblem

    키·메타데이터·내부 오류는 그대로 올려 보냅니다. (부른 쪽이 500, 기록 없음)
    """
    target = stored_file(storage, row)
    if not target.is_file():
        raise _EvidenceProblem(index, "FILE_UNAVAILABLE", FILE_PROBLEM)
    try:
        plain = verified_plaintext(storage.crypto, target, row)
        try:
            digest = Sha256Hasher()
            parts = []
            while chunk := plain.read(CHUNK_SIZE):
                digest.update(chunk)
                parts.append(chunk)
        finally:
            plain.close()
    except AuthenticationFailedError:
        raise _EvidenceProblem(index, "AUTHENTICATION_FAILED", HASH_PROBLEM) from None
    except OSError:
        raise _EvidenceProblem(index, "FILE_UNAVAILABLE", FILE_PROBLEM) from None
    data = b"".join(parts)
    if len(data) != row["size"] or not hmac.compare_digest(digest.hexdigest(), row["sha256"]):
        raise _EvidenceProblem(index, "HASH_MISMATCH", HASH_PROBLEM)
    return data


def _record_export_failure(storage: Storage, user, report_id: str, package_id: str,
                           items: list[dict], problem: _EvidenceProblem) -> None:
    """패키지를 못 만든 사실을 관리 이력에 남깁니다. (별도 트랜잭션, 11/16 결정)

    원인 증거: 원인 코드 (HASH_MISMATCH / AUTHENTICATION_FAILED / FILE_UNAVAILABLE)
    그 앞에서 이미 원본을 꺼낸 증거: ABORTED   그 뒤 증거: 원본을 열지 않았으므로 기록 없음
    """
    with closing(storage.connect()) as connection, write_transaction(connection):
        for index, item in enumerate(items[: problem.index + 1]):
            code = problem.failure_code if index == problem.index else ABORTED
            append_event(connection, item["row"]["id"], "PACKAGE_EXPORTED", user, "FAILURE",
                         {"reportId": report_id, "packageId": package_id, "failureCode": code})


# ---------------------------------------------------------------- 패키지 안 JSON 3종

def _json_bytes(value) -> bytes:
    """C가 만드는 JSON 바이트 규칙: 키 정렬, 구분자 , :, 한글 그대로, UTF-8, BOM·끝 줄바꿈 없음"""
    return canonical_json(value).encode("utf-8")


def _verification_record(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "status": row["status"], "failureCode": row["failure_code"] or None,
            "startedAt": row["started_at"], "completedAt": row["completed_at"],
            "performedBy": row["performed_by_name"], "performedById": row["user_id"],
            "expectedHash": row["expected_hash"], "currentHash": row["current_hash"], "reason": row["reason"]}


def _custody_record(row: sqlite3.Row) -> dict:
    """관리 이력 1건을 DB에 저장된 모습 그대로 (details는 문자열 그대로) → 받은 사람이 event_hash를 다시 계산할 수 있음"""
    return {key: row[key] for key in ("evidence_id", "sequence_no", "event_type", "actor_user_id", "event_time",
                                      "result", "details", "previous_event_hash", "event_hash")}


def build_json_files(package_id: str, report: sqlite3.Row, report_pdf: bytes, created_at: str, user,
                     items: list[dict], packaged: list[dict]) -> dict[str, bytes]:
    """manifest.json · verification.json · custody.json 바이트"""
    verification = {
        "packageId": package_id,
        "evidence": [{"evidenceId": item["row"]["id"],
                      "records": [_verification_record(row) for row in item["verifications"]]} for item in items],
    }
    custody = {
        "packageId": package_id,
        "hashRule": "event_hash = SHA-256(canonical JSON of evidence_id, sequence_no, event_type, actor_user_id, "
                    "event_time, result, details, previous_event_hash; sort_keys, separators ',' ':', UTF-8)",
        "evidence": [{"evidenceId": item["row"]["id"],
                      "snapshot": {"sequenceNo": item["snapshot"][0], "eventHash": item["snapshot"][1]},
                      "events": [_custody_record(row) for row in item["events"]]} for item in items],
    }
    verification_bytes, custody_bytes = _json_bytes(verification), _json_bytes(custody)
    manifest = {
        "manifestVersion": MANIFEST_VERSION,
        "packageId": package_id, "reportId": report["id"],
        "created": kst(created_at), "createdAt": created_at, "timeZone": "KST",
        "creator": user["name"], "creatorId": user["id"],
        "report": {"path": "report/report.pdf", "title": report["title"],
                   "sha256": hashlib.sha256(report_pdf).hexdigest(), "size": len(report_pdf)},
        "files": [
            {"path": "verification.json", "sha256": hashlib.sha256(verification_bytes).hexdigest(),
             "size": len(verification_bytes)},
            {"path": "custody.json", "sha256": hashlib.sha256(custody_bytes).hexdigest(), "size": len(custody_bytes)},
        ],
        "items": [{
            "evidenceId": item["row"]["id"], "name": item["row"]["name"], "filename": item["row"]["filename"],
            "path": entry["path"], "size": item["row"]["size"],
            "evidenceSha256": item["row"]["sha256"], "packagedFileSha256": entry["sha256"],
            "verificationId": item["latest"]["id"] if item["latest"] else None,
            "verificationStatus": item["latest"]["status"] if item["latest"] else "NOT_VERIFIED",
            "reportVerificationId": item["row"]["report_verification_id"],
            "custodySnapshot": {"sequenceNo": item["snapshot"][0], "eventHash": item["snapshot"][1]},
        } for item, entry in zip(items, packaged, strict=True)],
    }
    return {"manifest.json": _json_bytes(manifest), "verification.json": verification_bytes,
            "custody.json": custody_bytes}


def build_entries(package_id: str, json_files: dict[str, bytes], report_pdf: bytes,
                  packaged: list[dict]) -> list[dict]:
    """D에게 넘기는 entries (순서 = ZIP 안 순서). 경로는 모두 PKG-…/ 아래"""
    def entry(path, kind, data, evidence_id=None):
        return {"path": f"{package_id}/{path}", "kind": kind, "data": data, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "evidenceId": evidence_id}

    entries = [entry("manifest.json", "manifest", json_files["manifest.json"]),
               entry("verification.json", "verification", json_files["verification.json"]),
               entry("custody.json", "custody", json_files["custody.json"]),
               entry("report/report.pdf", "report", report_pdf)]
    entries += [entry(item["path"], "evidence", item["data"], item["evidenceId"]) for item in packaged]
    return entries


# ---------------------------------------------------------------- C의 ZIP 검사

def check_package_zip(path, entries: list[dict]) -> None:
    """D가 만든 ZIP을 저장하기 전에 확인합니다. 문제가 있으면 ValueError

    열림 · 항목 이름과 순서가 넘긴 그대로 · 암호화·폴더 항목 없음 · 압축 방식(저장/deflate) ·
    크기와 SHA-256이 넘긴 그대로 (끝까지 읽어서 CRC도 zipfile이 확인)
    """
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if [info.filename for info in infos] != [item["path"] for item in entries]:
            raise ValueError("ZIP 항목 목록이 예상과 다릅니다.")
        for info, item in zip(infos, entries, strict=True):
            if info.flag_bits & 0x1:
                raise ValueError("암호화된 ZIP 항목이 있습니다.")
            if info.is_dir() or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError("ZIP 항목 형식이 올바르지 않습니다.")
            if info.file_size != item["size"]:
                raise ValueError("ZIP 항목 크기가 예상과 다릅니다.")
            digest, total = hashlib.sha256(), 0
            with archive.open(info) as source:
                while chunk := source.read(CHUNK_SIZE):
                    total += len(chunk)
                    if total > item["size"]:
                        raise ValueError("ZIP 항목 크기가 예상보다 큽니다.")
                    digest.update(chunk)
            if total != item["size"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("ZIP 항목 내용이 예상과 다릅니다.")


# ---------------------------------------------------------------- 파일 저장

def _package_path(storage: Storage, key: str):
    return storage.packages_dir / f"{key}.zip"


def _write_package_file(storage: Storage, bundle: dict, entries: list[dict]) -> tuple[str, str, int]:
    """D가 .zip.part에 쓰고 → C가 검사 → fsync → 최종 이름 → 읽기 전용. (저장 이름, SHA-256, 크기)"""
    key = uuid.uuid4().hex
    final = _package_path(storage, key)
    part = storage.packages_dir / f"{key}.zip.part"
    descriptor = os.open(part, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w+b") as output:
            try:
                build_package_zip(bundle, output)
            except PackageBundleError as error:  # 백엔드가 만든 묶음이 명세와 다름 = C의 버그
                raise HTTPException(500, "패키지 데이터 형식 오류로 ZIP을 만들지 못했습니다.") from error
            except PackageBuildError as error:
                raise HTTPException(500, "ZIP 파일을 만들지 못했습니다.") from error
            output.flush()
            os.fsync(output.fileno())
        try:
            check_package_zip(part, entries)
        except Exception as error:
            raise HTTPException(500, "생성된 패키지가 검사를 통과하지 못했습니다.") from error
        digest, size = hashlib.sha256(), 0
        with part.open("rb") as source:
            while chunk := source.read(CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
        os.replace(part, final)
        final.chmod(0o400)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return key, digest.hexdigest(), size


def _discard_package_file(storage: Storage, key: str) -> None:
    """방금 만든(DB에 기록되지 않은) 파일만 지웁니다."""
    path = _package_path(storage, key)
    if path.exists():
        path.chmod(0o600)
        path.unlink()


# ---------------------------------------------------------------- K1

def create_package(storage: Storage, user, report_id: str) -> dict:
    package_id = f"PKG-{uuid.uuid4().hex}"
    with closing(storage.connect()) as connection:
        report = load_report(connection, user, report_id)                       # 1) 내 보고서 (아니면 404)
    try:
        report_pdf = read_report_pdf(storage, report)                           # 2) 보고서 PDF 먼저
    except HTTPException as error:
        if error.status_code == 409:
            raise HTTPException(409, REPORT_FILE_PROBLEM) from None
        raise

    for _attempt in range(MAX_ATTEMPTS):
        created_at = utc_now()
        with closing(storage.connect()) as connection:
            items = _read_snapshot(connection, report_id)
            lacking = _lacking_download(connection, user, [item["row"]["id"] for item in items])
        if lacking:                                                             # 3) 다운로드 권한
            _deny_all(storage, user, lacking, NO_DOWNLOAD)
        if sum(item["row"]["size"] for item in items) > MAX_PACKAGE_ORIGINAL_SIZE:
            raise HTTPException(422, TOO_LARGE)                                 # 4) 크기 상한

        packaged = []                                                           # 6) 원본 복호화·해시 확인
        try:
            for index, item in enumerate(items):
                row = item["row"]
                data = _read_original(storage, index, row)
                packaged.append({"evidenceId": row["id"], "data": data,
                                 "path": evidence_path(index + 1, row["id"], row["filename"]),
                                 "sha256": hashlib.sha256(data).hexdigest()})
        except _EvidenceProblem as problem:
            _record_export_failure(storage, user, report_id, package_id, items, problem)
            raise HTTPException(409, problem.message) from None
        except (KeyUnavailableError, InvalidCryptoInputError, CryptoInternalError) as error:
            raise HTTPException(500, CREATE_FAILED) from error

        json_files = build_json_files(package_id, report, report_pdf, created_at, user, items, packaged)  # 7)
        entries = build_entries(package_id, json_files, report_pdf, packaged)
        bundle = {"bundleVersion": BUNDLE_VERSION, "packageId": package_id, "reportId": report_id,
                  "created": kst(created_at), "creator": user["name"], "entries": entries}
        key, file_sha256, file_size = _write_package_file(storage, bundle, entries)  # 8)~10)
        # ZIP을 쓴 뒤에는 원본 바이트가 필요 없으므로 메모리에서 놓아 줍니다. (경로·해시는 남김)
        del bundle, entries
        for entry in packaged:
            entry.pop("data", None)

        denied = None
        try:
            with closing(storage.connect()) as connection, write_transaction(connection):  # 11)
                lacking = _lacking_download(connection, user, [item["row"]["id"] for item in items])
                if lacking:
                    denied = lacking
                elif all(_current_snapshot(connection, item["row"]["id"]) == item["snapshot"] for item in items):
                    _insert_package(connection, storage, user, package_id, report_id, created_at,
                                    key, file_sha256, file_size, items, packaged)
                    return package_dict(connection, load_package(connection, user, package_id))
        except BaseException:
            _discard_package_file(storage, key)
            raise
        _discard_package_file(storage, key)  # 기록하지 않은 파일
        if denied:
            _deny_all(storage, user, denied, REVOKED_DOWNLOAD)
    raise HTTPException(409, CHANGED_DURING_CREATE)


def _insert_package(connection, storage, user, package_id, report_id, created_at, key, file_sha256, file_size,
                    items, packaged) -> None:
    connection.execute("""INSERT INTO evidence_packages (
            id, report_id, created_by, creator_name, created_at, file_storage_key, file_sha256, file_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (package_id, report_id, user["id"], user["name"], created_at, key, file_sha256, file_size))
    for order, (item, entry) in enumerate(zip(items, packaged, strict=True), start=1):
        latest = item["latest"]
        sequence_no, event_hash = item["snapshot"]
        connection.execute("""INSERT INTO evidence_package_items (
                package_id, evidence_id, item_order, evidence_sha256, packaged_file_sha256,
                verification_id, verification_status, path_in_package,
                custody_snapshot_sequence_no, custody_snapshot_event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (package_id, item["row"]["id"], order, item["row"]["sha256"], entry["sha256"],
                            latest["id"] if latest else None, latest["status"] if latest else "NOT_VERIFIED",
                            entry["path"], sequence_no, event_hash))
        # 반출 이벤트는 기준점 바로 다음 번호가 됨. 넣은 원본 해시를 함께 남김
        append_event(connection, item["row"]["id"], "PACKAGE_EXPORTED", user, "SUCCESS", {
            "reportId": report_id, "packageId": package_id, "packagedFileSha256": entry["sha256"],
            "snapshotSequenceNo": sequence_no, "snapshotEventHash": event_hash,
        })
    append_audit(connection, storage, user["id"], "package_create", None, created_at)


# ---------------------------------------------------------------- K2·K3 응답

def load_package(connection: sqlite3.Connection, user, package_id: str) -> sqlite3.Row:
    """만든 사람만 볼 수 있음. 없거나 남의 패키지면 404"""
    row = connection.execute("SELECT * FROM evidence_packages WHERE id = ? AND created_by = ?",
                             (package_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, PACKAGE_NOT_FOUND)
    return row


def _package_items(connection: sqlite3.Connection, package_id: str) -> list[sqlite3.Row]:
    return connection.execute("SELECT * FROM evidence_package_items WHERE package_id = ? ORDER BY item_order",
                              (package_id,)).fetchall()


def package_dict(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """K3 모양. K1 응답과 K2 목록의 한 줄도 같은 모양"""
    return {
        "id": row["id"], "reportId": row["report_id"],
        "creator": row["creator_name"], "creatorId": row["created_by"],
        "created": kst(row["created_at"]), "createdAt": row["created_at"],
        "file": {"filename": package_filename(row["id"]), "sha256": row["file_sha256"], "size": row["file_size"]},
        "items": [{
            "evidenceId": item["evidence_id"], "evidenceSha256": item["evidence_sha256"],
            "packagedFileSha256": item["packaged_file_sha256"],
            "verificationId": item["verification_id"], "verificationStatus": item["verification_status"],
            "pathInPackage": item["path_in_package"],
            "custodySnapshot": {"sequenceNo": item["custody_snapshot_sequence_no"],
                                "eventHash": item["custody_snapshot_event_hash"]},
        } for item in _package_items(connection, row["id"])],
    }


# ---------------------------------------------------------------- K4

def _record_download_failure(storage: Storage, user, row: sqlite3.Row, evidence_ids: list[str], code: str) -> None:
    """패키지를 못 내려받은 사실을 관리 이력에 남깁니다. (원본 다운로드 E4 FAILURE와 같은 규칙, 별도 트랜잭션)"""
    with closing(storage.connect()) as connection, write_transaction(connection):
        for evidence_id in evidence_ids:
            append_event(connection, evidence_id, "PACKAGE_DOWNLOADED", user, "FAILURE",
                         {"packageId": row["id"], "failureCode": code})


def download_package(storage: Storage, user, package_id: str) -> tuple[bytes, sqlite3.Row]:
    """K4: 권한 확인 → 파일 크기·SHA-256 확인 → (쓰기 잠금 안에서 권한 재확인) 기록 → 바이트를 돌려줌"""
    with closing(storage.connect()) as connection:
        row = load_package(connection, user, package_id)
        evidence_ids = [item["evidence_id"] for item in _package_items(connection, package_id)]
        lacking = _lacking_download(connection, user, evidence_ids)
    if lacking:
        _deny_all(storage, user, lacking, REVOKED_DOWNLOAD)
    if not _KEY.fullmatch(row["file_storage_key"]):
        raise HTTPException(500, "패키지 파일 경로가 유효하지 않습니다.")
    path = _package_path(storage, row["file_storage_key"])
    if not path.is_file():
        _record_download_failure(storage, user, row, evidence_ids, "FILE_UNAVAILABLE")
        raise HTTPException(404, PACKAGE_FILE_NOT_FOUND)
    try:
        data = path.read_bytes()
    except OSError:
        _record_download_failure(storage, user, row, evidence_ids, "FILE_UNAVAILABLE")
        raise HTTPException(404, PACKAGE_FILE_NOT_FOUND) from None
    if len(data) != row["file_size"] or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), row["file_sha256"]):
        _record_download_failure(storage, user, row, evidence_ids, "HASH_MISMATCH")
        raise HTTPException(409, PACKAGE_INTEGRITY_PROBLEM)

    with closing(storage.connect()) as connection, write_transaction(connection):
        # 파일을 확인하는 사이에 권한이 회수됐을 수 있으므로 쓰기 잠금 안에서 다시 확인합니다.
        lacking = _lacking_download(connection, user, evidence_ids)
        if not lacking:
            for evidence_id in evidence_ids:
                append_event(connection, evidence_id, "PACKAGE_DOWNLOADED", user, "SUCCESS", {"packageId": package_id})
            append_audit(connection, storage, user["id"], "package_download")
    if lacking:
        _deny_all(storage, user, lacking, REVOKED_DOWNLOAD)
    return data, row
