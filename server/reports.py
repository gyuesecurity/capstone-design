"""보고서 만들기·보여 주기 (7단계). 설계 문서 6장, A API 명세 4장, D 명세 ReportRenderBundle v1

흐름 (R1)
1) 읽기 (잠금 없이): 증거마다 보기 권한, 관리 이력 마지막 번호·해시(= 기준점), 최신 검증 결과
2) D 함수 호출 (잠금 밖): ReportRenderBundle v1 → PDF 바이트
3) C가 PDF 검사: PDF로 열림, 암호화·실행 코드 없음, 본문에 이 reportId가 글자로 있음, 크기 상한
4) 파일 저장: .part → fsync → 최종 이름 → (그다음) DB 커밋. "DB 행은 있는데 파일이 없는" 상태가 생기지 않음
5) 기록 (쓰기 잠금 안): 권한과 "관리 이력이 그사이 안 바뀌었는지" 다시 확인 (낙관적 동시성 검사)
   바뀌었으면 같은 보고서 ID로 처음부터 다시 (최대 3번, 넘으면 409)
   확인되면 보고서 + 증거 목록 + 증거마다 REPORT_INCLUDED + 활동 기록 report_create를 한 트랜잭션에

D는 DB·권한·관리 이력을 다루지 않고, C가 넘긴 묶음으로 PDF만 만듭니다.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from d_package.pdf_renderer import PdfRenderError, ReportBundleError, render_report_pdf

from .access import viewable_evidence
from .audit import append_audit
from .config import STATUS_LABELS
from .custody import append_event
from .db import Storage, write_transaction
from .security import utc_now

REPORT_OPTIONS = ("basic", "metadata", "hash", "results", "custody")  # A 화면 서버 모드·D 명세와 같은 5개
MAX_REPORT_EVIDENCE = 100            # 11/15 결정 (D 측정: 100건 3.65초, 61쪽)
MAX_REPORT_PDF_SIZE = 20 * 1024 * 1024
MAX_ATTEMPTS = 3                     # 설계 문서 6장: 재시도 상한 3회
KST = timezone(timedelta(hours=9))
CHANGED_DURING_CREATE = "보고서를 만드는 동안 증거 이력이 변경되었습니다. 다시 시도해 주세요."
FILE_INTEGRITY_PROBLEM = "파일 무결성 문제로 복구가 필요한 보고서입니다. 관리자에게 문의해 주세요."
_FORBIDDEN_FILENAME = str.maketrans({char: "_" for char in '\\/:*?"<>|'})


# ---------------------------------------------------------------- 시각·이름 표시

def kst(value: str, seconds: bool = True) -> str:
    """UTC ISO 문자열 → 한국 시간 표시 글자 (YYYY/MM/DD HH:mm:ss). PDF와 R3에 같은 글자가 나가게 한 곳에서만 만듭니다."""
    moment = datetime.fromisoformat(value).astimezone(KST)
    return moment.strftime("%Y/%m/%d %H:%M:%S" if seconds else "%Y/%m/%d %H:%M")


def pdf_filename(title: str) -> str:
    """다운로드 파일 이름. report.js와 같은 규칙: \\ / : * ? " < > | → _"""
    return (title.translate(_FORBIDDEN_FILENAME) or "증거 보고서") + ".pdf"


# ---------------------------------------------------------------- 입력 검사

def validate_request(title: str, project: str, description: str, selected: list, options: list) -> tuple:
    """R1 입력 검사. 문제가 있으면 422 (A 명세 4장 문구)"""
    title, project, description = title.strip(), project.strip(), description.strip()
    if not selected:
        raise HTTPException(422, "보고서에 포함할 증거를 한 개 이상 선택해 주세요.")
    if len(selected) > MAX_REPORT_EVIDENCE:
        raise HTTPException(422, f"보고서에는 증거를 최대 {MAX_REPORT_EVIDENCE}개까지 넣을 수 있습니다.")
    if any(not isinstance(item, str) or not item for item in selected) or len(set(selected)) != len(selected):
        raise HTTPException(422, "선택한 증거 목록을 확인해 주세요. (중복 불가)")
    if not title or len(title) > 150:
        raise HTTPException(422, "보고서 제목은 1~150자로 입력해 주세요.")
    if len(project) > 200 or len(description) > 4000:
        raise HTTPException(422, "사건명은 200자, 설명은 4000자 이내로 입력해 주세요.")
    if not options or any(option not in REPORT_OPTIONS for option in options) or len(set(options)) != len(options):
        raise HTTPException(422, "보고서에 포함할 내용을 확인해 주세요.")
    # 포함 항목은 화면 순서와 상관없이 정해진 순서로 저장 (같은 선택 → 같은 값)
    return title, project, description, [option for option in REPORT_OPTIONS if option in options]


# ---------------------------------------------------------------- 읽기 (스냅샷)

def _last_event(connection: sqlite3.Connection, evidence_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT sequence_no, event_hash FROM chain_of_custody WHERE evidence_id = ? ORDER BY sequence_no DESC LIMIT 1",
        (evidence_id,),
    ).fetchone()


def _latest_verification(connection: sqlite3.Connection, evidence_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM verifications WHERE evidence_id = ? ORDER BY completed_at DESC, rowid DESC LIMIT 1",
        (evidence_id,),
    ).fetchone()


def read_items(connection: sqlite3.Connection, user, selected: list[str]) -> list[dict]:
    """고른 증거마다 지금 모습을 읽습니다. 볼 권한이 없으면 404, 관리 이력이 없으면 409"""
    items = []
    for evidence_id in selected:
        row, _access = viewable_evidence(connection, user, evidence_id)
        last = _last_event(connection, evidence_id)
        if last is None:
            raise HTTPException(409, "관리 이력이 없는 증거는 보고서에 넣을 수 없습니다.")
        items.append({"row": row, "verification": _latest_verification(connection, evidence_id),
                      "snapshot": (last["sequence_no"], last["event_hash"])})
    return items


# ---------------------------------------------------------------- D에게 넘기는 묶음

def build_bundle(report_id: str, created_at: str, author: str, title: str, project: str,
                 description: str, options: list[str], items: list[dict]) -> dict:
    """ReportRenderBundle v1 (D 명세). 빈 값은 null, 키는 항상 넣음 (D-1 검사)"""
    evidence = []
    for item in items:
        row, verification = item["row"], item["verification"]
        status = verification["status"] if verification else "NOT_VERIFIED"
        evidence.append({
            "id": row["id"],
            "name": row["name"],
            "filename": row["filename"],
            "size": row["size"],
            "registeredAt": kst(row["registered_at"]),
            "registeredBy": row["registered_by_name"],
            "source": row["source"] or None,
            "mime": row["mime"] or None,
            "sha256": row["sha256"],
            "status": STATUS_LABELS[status],
            "verification": None if verification is None else {
                "completedAt": kst(verification["completed_at"]),
                "performedBy": verification["performed_by_name"],
                "currentHash": verification["current_hash"],
                "reason": verification["reason"],
            },
            "custodySnapshot": {"sequenceNo": item["snapshot"][0], "eventHash": item["snapshot"][1]},
        })
    return {
        "bundleVersion": "report-v1",
        "reportId": report_id,
        "created": kst(created_at),
        "timeZone": "KST",
        "report": {"title": title, "project": project or None, "description": description or None,
                   "author": author, "options": options},
        "evidence": evidence,
    }


# ---------------------------------------------------------------- C의 PDF 검사 (설계 문서 6장)

_ACTIVE_ACTIONS = {"/JavaScript", "/Launch", "/ImportData", "/SubmitForm", "/ResetForm", "/GoToR", "/GoToE"}


def check_report_pdf(data: bytes, report_id: str) -> None:
    """D가 만든 PDF를 저장하기 전에 확인합니다. 문제가 있으면 ValueError

    크기 · PDF로 열리는지 · 암호화 없음 · 자동 실행/스크립트/첨부 파일 없음 · 1쪽 본문에 reportId가 글자로 있음
    """
    from pypdf import PdfReader  # 보고서를 만들 때만 필요

    if not data or len(data) > MAX_REPORT_PDF_SIZE:
        raise ValueError("PDF 크기가 허용 범위를 벗어났습니다.")
    if not data.startswith(b"%PDF-"):
        raise ValueError("PDF 형식이 아닙니다.")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ValueError("암호화된 PDF입니다.")
    root = reader.trailer["/Root"]
    if "/OpenAction" in root or "/AA" in root or "/AcroForm" in root:
        raise ValueError("PDF에 자동 실행 또는 입력 양식이 있습니다.")
    names = root.get("/Names")
    if names is not None and ("/JavaScript" in names or "/EmbeddedFiles" in names):
        raise ValueError("PDF에 스크립트 또는 첨부 파일이 있습니다.")
    for page in reader.pages:
        if "/AA" in page:
            raise ValueError("PDF 쪽에 자동 실행이 있습니다.")
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A")
            if action is not None and action.get_object().get("/S") in _ACTIVE_ACTIONS:
                raise ValueError("PDF에 실행 동작이 있습니다.")
    if report_id not in (reader.pages[0].extract_text() or ""):
        raise ValueError("PDF 본문에서 보고서 ID를 찾지 못했습니다.")


# ---------------------------------------------------------------- 파일 저장

def _write_report_file(storage: Storage, data: bytes) -> str:
    """.part로 쓰고 fsync한 뒤 최종 이름으로 바꿉니다. 저장 이름(32자리 16진수)을 돌려줌"""
    key = uuid.uuid4().hex
    final = storage.reports_dir / f"{key}.pdf"
    part = storage.reports_dir / f"{key}.pdf.part"
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(part, final)
        final.chmod(0o400)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return key


def _discard_report_file(storage: Storage, key: str) -> None:
    """방금 만든(DB에 기록되지 않은) 파일만 지웁니다. 읽기 전용 표시를 먼저 풀어야 Windows에서도 지워짐"""
    path = storage.reports_dir / f"{key}.pdf"
    if path.exists():
        path.chmod(0o600)
        path.unlink()


# ---------------------------------------------------------------- R1

def create_report(storage: Storage, user, title: str, project: str, description: str,
                  selected: list[str], options: list[str]) -> dict:
    title, project, description, options = validate_request(title, project, description, selected, options)
    report_id = f"RPT-{uuid.uuid4().hex}"
    for _attempt in range(MAX_ATTEMPTS):
        created_at = utc_now()  # 설계 문서 6장: 마지막 성공 시도의 읽기 시작 시각
        with closing(storage.connect()) as connection:
            items = read_items(connection, user, selected)
        bundle = build_bundle(report_id, created_at, user["name"], title, project, description, options, items)
        try:
            pdf = render_report_pdf(bundle)
        except ReportBundleError as error:  # 백엔드가 만든 묶음이 명세와 다름 = C의 버그
            raise HTTPException(500, "보고서 데이터 형식 오류로 PDF를 만들지 못했습니다.") from error
        except PdfRenderError as error:     # PDF 도구(WeasyPrint·Pango) 문제
            raise HTTPException(500, "PDF 생성기를 사용할 수 없어 보고서를 만들지 못했습니다.") from error
        try:
            check_report_pdf(pdf, report_id)
        except Exception as error:  # noqa: BLE001 - pypdf 파싱 오류 포함, 모두 "D 결과 불합격"
            raise HTTPException(500, "생성된 PDF가 검사를 통과하지 못했습니다.") from error

        key = _write_report_file(storage, pdf)
        try:
            with closing(storage.connect()) as connection, write_transaction(connection):
                # 쓰기 잠금 안에서 권한과 기준점을 다시 확인 (그사이 권한 회수·새 이벤트가 있었는지)
                changed = False
                for item in items:
                    viewable_evidence(connection, user, item["row"]["id"])
                    last = _last_event(connection, item["row"]["id"])
                    if (last["sequence_no"], last["event_hash"]) != item["snapshot"]:
                        changed = True
                        break
                if not changed:
                    _insert_report(connection, storage, user, report_id, created_at, title, project,
                                   description, options, items, key, pdf)
                    return report_dict(connection, load_report(connection, user, report_id))
        except BaseException:
            _discard_report_file(storage, key)
            raise
        _discard_report_file(storage, key)  # 기준점이 바뀌어 기록하지 않은 파일
    raise HTTPException(409, CHANGED_DURING_CREATE)


def _insert_report(connection, storage, user, report_id, created_at, title, project, description,
                   options, items, key, pdf) -> None:
    connection.execute("""INSERT INTO reports (
            id, created_by, author_name, title, case_name, description, options_json, created_at,
            file_storage_key, file_sha256, file_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (report_id, user["id"], user["name"], title, project, description,
                        json.dumps(options), created_at, key, hashlib.sha256(pdf).hexdigest(), len(pdf)))
    for order, item in enumerate(items, start=1):
        verification = item["verification"]
        sequence_no, event_hash = item["snapshot"]
        connection.execute("""INSERT INTO report_evidence (
                report_id, evidence_id, item_order, verification_id,
                custody_snapshot_sequence_no, custody_snapshot_event_hash
            ) VALUES (?, ?, ?, ?, ?, ?)""",
                           (report_id, item["row"]["id"], order, verification["id"] if verification else None,
                            sequence_no, event_hash))
        # 포함 이벤트는 기준점 바로 다음 번호가 됨 (설계 문서 4장 REPORT_INCLUDED 필수 필드)
        append_event(connection, item["row"]["id"], "REPORT_INCLUDED", user, "SUCCESS", {
            "reportId": report_id, "snapshotSequenceNo": sequence_no, "snapshotEventHash": event_hash,
            "verificationId": verification["id"] if verification else None,
        })
    append_audit(connection, storage, user["id"], "report_create", None, created_at)


# ---------------------------------------------------------------- R2·R3 응답

def load_report(connection: sqlite3.Connection, user, report_id: str) -> sqlite3.Row:
    """만든 사람만 볼 수 있음. 없거나 남의 보고서면 404"""
    row = connection.execute("SELECT * FROM reports WHERE id = ? AND created_by = ?", (report_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "보고서를 찾을 수 없습니다.")
    return row


def _snapshot_items(connection: sqlite3.Connection, report_id: str) -> list[dict]:
    """보고서를 만든 그 순간의 증거 모습 (고칠 수 없는 증거·검증 기록으로 다시 만듦)"""
    rows = connection.execute("""SELECT report_evidence.*, evidence.* FROM report_evidence
        JOIN evidence ON evidence.id = report_evidence.evidence_id
        WHERE report_evidence.report_id = ? ORDER BY report_evidence.item_order""", (report_id,)).fetchall()
    items = []
    for row in rows:
        verification = None
        if row["verification_id"]:
            verification = connection.execute("SELECT * FROM verifications WHERE id = ?", (row["verification_id"],)).fetchone()
        status = verification["status"] if verification else "NOT_VERIFIED"
        items.append({
            "id": row["evidence_id"], "name": row["name"], "filename": row["filename"],
            "type": row["kind"], "fileType": row["type"],
            "date": kst(row["registered_at"], seconds=False), "registeredAt": row["registered_at"],
            "registeredBy": row["registered_by_name"],
            "statusCode": status, "status": STATUS_LABELS[status],
            "size": row["size"], "mime": row["mime"], "sha256": row["sha256"], "source": row["source"],
            "lastVerification": None if verification is None else {
                "id": verification["id"], "status": verification["status"],
                "completedAt": verification["completed_at"], "performedBy": verification["performed_by_name"],
                "currentHash": verification["current_hash"], "reason": verification["reason"],
                "failureCode": verification["failure_code"] or None,
            },
            "custodySnapshot": {"sequenceNo": row["custody_snapshot_sequence_no"],
                                "eventHash": row["custody_snapshot_event_hash"]},
        })
    return items


def _summary(items: list[dict]) -> dict:
    counts = {"MATCH": 0, "MISMATCH": 0, "NOT_VERIFIED": 0, "UNAVAILABLE": 0}
    for item in items:
        counts[item["statusCode"]] += 1
    return counts


def list_status(summary: dict) -> dict:
    """목록 상태 표시. report.js listStatus와 같은 규칙: 불일치 → 검증 불가 → 미검증 → 모두 일치"""
    if summary["MISMATCH"]:
        return {"code": "MISMATCH", "label": f"불일치 {summary['MISMATCH']}건"}
    if summary["UNAVAILABLE"]:
        return {"code": "UNAVAILABLE", "label": f"검증 불가 {summary['UNAVAILABLE']}건"}
    if summary["NOT_VERIFIED"]:
        return {"code": "NOT_VERIFIED", "label": f"미검증 {summary['NOT_VERIFIED']}건"}
    if summary["MATCH"]:
        return {"code": "MATCH", "label": "✓ 모두 일치"}
    return {"code": "NOT_VERIFIED", "label": "미검증"}


def report_dict(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """R3 상세 (A API 명세 4장 모양). R1 응답도 같은 모양"""
    items = _snapshot_items(connection, row["id"])
    return {
        "id": row["id"], "title": row["title"], "project": row["case_name"], "description": row["description"],
        "author": row["author_name"], "authorId": row["created_by"],
        "date": kst(row["created_at"])[:10], "created": kst(row["created_at"]), "createdAt": row["created_at"],
        "selected": [item["id"] for item in items], "options": json.loads(row["options_json"]),
        "evidenceSnapshot": items, "summary": _summary(items),
        "file": {"filename": pdf_filename(row["title"]), "sha256": row["file_sha256"], "size": row["file_size"]},
    }


def report_list_item(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """R2 목록 한 줄"""
    summary = _summary(_snapshot_items(connection, row["id"]))
    return {"id": row["id"], "title": row["title"], "author": row["author_name"], "created": kst(row["created_at"]),
            "createdAt": row["created_at"], "evidenceCount": sum(summary.values()), "summary": summary,
            "listStatus": list_status(summary)}


# ---------------------------------------------------------------- R4

def read_report_pdf(storage: Storage, row: sqlite3.Row) -> bytes:
    """저장된 PDF를 읽고 크기·SHA-256이 기록과 같은지 확인합니다. 다르거나 없으면 409"""
    key = row["file_storage_key"]
    if len(key) != 32 or any(char not in "0123456789abcdef" for char in key):
        raise HTTPException(500, "보고서 파일 경로가 유효하지 않습니다.")
    try:
        data = (storage.reports_dir / f"{key}.pdf").read_bytes()
    except OSError:
        raise HTTPException(409, FILE_INTEGRITY_PROBLEM) from None
    if len(data) != row["file_size"] or hashlib.sha256(data).hexdigest() != row["file_sha256"]:
        raise HTTPException(409, FILE_INTEGRITY_PROBLEM)
    return data
