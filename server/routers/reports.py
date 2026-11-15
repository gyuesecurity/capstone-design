"""보고서 API (7단계). A API 명세 4장 그대로

R1 POST /api/reports                       보고서 만들기 (서버 PDF, D 렌더러) → 201 + R3 모양
R2 GET  /api/reports                       내가 만든 보고서 목록 (최근 순)
R3 GET  /api/reports/{report_id}           보고서 상세
R4 GET  /api/reports/{report_id}/download  PDF 다운로드

- 보고서는 만든 사람만 볼 수 있습니다. 남의 보고서는 404 (존재 여부를 드러내지 않음)
- 삭제·수정 API는 없습니다. (보고서는 증거 기록)
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..audit import append_audit
from ..db import Storage, get_storage, write_transaction
from ..reports import create_report, load_report, pdf_filename, read_report_pdf, report_dict, report_list_item
from ..schemas import ReportCreate
from ..security import current_user
from ..stored_evidence import content_disposition

router = APIRouter()


# R1: 보고서 만들기. PDF를 서버에서 만들어서 몇 초 걸릴 수 있습니다. (진행률 없이 끝나면 한 번에 응답)
@router.post("/api/reports", status_code=201)
def create(payload: ReportCreate, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    return create_report(storage, user, payload.title, payload.project, payload.description,
                         payload.selected, payload.options)


# R2: 내 보고서 목록 (최근 순, 읽기만 함)
@router.get("/api/reports")
def list_reports(user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        rows = connection.execute("SELECT * FROM reports WHERE created_by = ? ORDER BY created_at DESC, rowid DESC",
                                  (user["id"],)).fetchall()
        return [report_list_item(connection, row) for row in rows]


# R3: 보고서 상세 (읽기만 함)
@router.get("/api/reports/{report_id}")
def get_report(report_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        return report_dict(connection, load_report(connection, user, report_id))


# R4: PDF 다운로드. 저장된 파일의 크기·SHA-256이 기록과 같을 때만 보내고, 활동 기록(report_download)을 먼저 저장합니다.
@router.get("/api/reports/{report_id}/download")
def download_report(report_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        row = load_report(connection, user, report_id)
    data = read_report_pdf(storage, row)  # 확인한 바이트를 그대로 보냄 (확인과 전송 사이에 바뀔 틈 없음)
    with closing(storage.connect()) as connection, write_transaction(connection):
        append_audit(connection, storage, user["id"], "report_download")
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition(pdf_filename(row["title"]))})
