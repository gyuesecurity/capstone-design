"""증거 패키지 API (8단계). A 화면 report-packages.js가 부르는 주소 그대로

K1 POST /api/packages                         패키지 만들기 {reportId} → 201 + K3 모양
K2 GET  /api/packages?reportId=               내가 만든 패키지 목록 (최근 순). reportId가 있으면 그 보고서 것만
K3 GET  /api/packages/{package_id}            패키지 상세
K4 GET  /api/packages/{package_id}/download   ZIP 다운로드

- 패키지는 만든 사람(= 보고서 작성자)만 볼 수 있습니다. 남의 패키지는 404
- 삭제·수정 API는 없습니다. (패키지는 반출 기록)
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..db import Storage, get_storage
from ..packages import (
    create_package,
    download_package,
    load_package,
    package_dict,
    package_filename,
)
from ..reports import load_report
from ..schemas import PackageCreate
from ..security import current_user
from ..stored_evidence import content_disposition

router = APIRouter()


# K1: 패키지 만들기. 원본을 복호화·확인하고 ZIP을 만들어서 몇 초 걸릴 수 있습니다.
@router.post("/api/packages", status_code=201)
def create(payload: PackageCreate, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    return create_package(storage, user, payload.reportId)


# K2: 내 패키지 목록 (최근 순, 읽기만 함). 남의 보고서 ID를 넣으면 404
@router.get("/api/packages")
def list_packages(reportId: str | None = None, user: sqlite3.Row = Depends(current_user),
                  storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        if reportId is None:
            rows = connection.execute(
                "SELECT * FROM evidence_packages WHERE created_by = ? ORDER BY created_at DESC, rowid DESC",
                (user["id"],)).fetchall()
        else:
            load_report(connection, user, reportId)
            rows = connection.execute(
                "SELECT * FROM evidence_packages WHERE created_by = ? AND report_id = ? "
                "ORDER BY created_at DESC, rowid DESC", (user["id"], reportId)).fetchall()
        return [package_dict(connection, row) for row in rows]


# K3: 패키지 상세 (읽기만 함)
@router.get("/api/packages/{package_id}")
def get_package(package_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    with closing(storage.connect()) as connection:
        return package_dict(connection, load_package(connection, user, package_id))


# K4: ZIP 다운로드. 증거마다 다운로드 권한이 남아 있고, 저장된 파일의 크기·SHA-256이 기록과 같을 때만 보냅니다.
#     관리 이력(PACKAGE_DOWNLOADED)과 활동 기록(package_download)을 먼저 저장합니다.
@router.get("/api/packages/{package_id}/download")
def download(package_id: str, user: sqlite3.Row = Depends(current_user), storage: Storage = Depends(get_storage)):
    data, row = download_package(storage, user, package_id)  # 확인한 바이트를 그대로 보냄
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": content_disposition(package_filename(row["id"]))})
