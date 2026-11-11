"""API server for the EVIDENCE capstone prototype (serves /api only; no frontend files)."""

# 이 파일은 서버를 "조립"만 합니다. 실제 기능은 아래 모듈에 나뉘어 있습니다.
#   config.py       설정 값 (최대 크기, 허용 확장자, 상태 이름 등)
#   db.py           저장 위치와 DB 연결, 테이블 생성
#   security.py     시각, 비밀번호 해시, 로그인 세션, 현재 사용자 확인
#   schemas.py      요청 본문 모양
#   serializers.py  응답 모양
#   custody.py      Chain of Custody 이벤트 추가와 사슬 검사 (4단계 추가)
#   routers/        기능별 API (health, auth, account, evidence, custody)
#
# 바깥에서 쓰는 이름은 그대로입니다.
#   - create_app(data_dir): 테스트가 임시 폴더로 서버를 만들 때 사용
#   - app: `uvicorn server.main:app`으로 실행할 때 사용

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .db import open_storage
from .routers import account, auth, custody, evidence, health


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    # 1) 저장 폴더·DB 준비 (폴더 결정 순서: data_dir 인자 → EVIDENCE_DATA_DIR → ROOT/data)
    storage = open_storage(data_dir)

    # 2) FastAPI 앱 생성. 각 API는 app.state.storage에서 저장 위치를 꺼내 씁니다.
    app = FastAPI(title="EVIDENCE API", version="0.1.0")
    app.state.storage = storage

    # 3) 기능별 API 연결 (주소는 각 라우터 파일에 기존 그대로 적혀 있습니다)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(evidence.router)
    app.include_router(custody.router)  # 4단계 추가: Chain of Custody 조회·검사 (C1, C2)
    return app


app = create_app()
