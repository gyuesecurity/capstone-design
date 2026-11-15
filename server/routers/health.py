"""서버 상태 확인 API: GET /api/health"""

from __future__ import annotations

from fastapi import APIRouter

from .. import config

router = APIRouter()


# 프론트가 서버 연결 여부와 업로드 제한(최대 크기, 허용 확장자)을 확인할 때 부릅니다.
# 값은 요청마다 config에서 읽습니다. (기존 코드도 실행 중에 값을 읽었습니다)
@router.get("/api/health")
def health():
    return {"backend": True, "maxFileSize": config.MAX_FILE_SIZE, "extensions": sorted(config.ALLOWED_EXTENSIONS)}
