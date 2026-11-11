"""저장 위치(데이터 폴더·증거 파일 폴더·SQLite DB)와 DB 연결.

기존 create_app() 안에 있던 저장 경로 계산, connect(), 테이블 생성 코드를 그대로 옮겨 왔습니다.
나중에 쓰기 트랜잭션(write_transaction)과 자동 마이그레이션이 이 파일에 들어갈 예정입니다.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from .config import ROOT


@dataclass(frozen=True)
class Storage:
    """서버 하나가 쓰는 저장 위치 묶음. create_app()이 만들어 app.state.storage에 넣어 둡니다."""

    root: Path       # 데이터 폴더 (EVIDENCE_DATA_DIR 또는 ROOT/data)
    files_dir: Path  # 증거 원본 파일 폴더 (root/files)
    db_path: Path    # SQLite DB 파일 (root/evidence.sqlite3)

    def connect(self) -> sqlite3.Connection:
        """요청마다 새 DB 연결을 엽니다. (기존 connect()와 같은 설정)"""
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def open_storage(data_dir: str | Path | None = None) -> Storage:
    """저장 폴더를 정하고, 폴더와 테이블이 없으면 만듭니다.

    폴더 결정 순서는 기존과 같습니다: create_app(data_dir) 인자 → 환경변수 EVIDENCE_DATA_DIR → ROOT/data
    """
    root = Path(data_dir or os.environ.get("EVIDENCE_DATA_DIR", ROOT / "data")).resolve()
    files_dir = root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    storage = Storage(root=root, files_dir=files_dir, db_path=root / "evidence.sqlite3")

    # 테이블 생성 SQL은 기존 그대로입니다. (2단계는 동작 변경 없음)
    # 통합 설계의 "executescript 사용 안 함, BEGIN IMMEDIATE 트랜잭션"은 3단계 이후에 바꿉니다.
    with closing(storage.connect()) as connection, connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
              password_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence (
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
              filename TEXT NOT NULL, stored_name TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
              mime TEXT NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
              description TEXT NOT NULL, memo TEXT NOT NULL, registered_at TEXT NOT NULL,
              sha256 TEXT NOT NULL, verification_status TEXT NOT NULL DEFAULT 'NOT_VERIFIED'
            );
            CREATE INDEX IF NOT EXISTS evidence_owner_date ON evidence(owner_id, registered_at DESC);
            CREATE TABLE IF NOT EXISTS verifications (
              id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id), user_id TEXT NOT NULL REFERENCES users(id),
              started_at TEXT NOT NULL, completed_at TEXT NOT NULL, expected_hash TEXT NOT NULL,
              current_hash TEXT, status TEXT NOT NULL, reason TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS verification_evidence_date ON verifications(evidence_id, completed_at DESC);
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
              action TEXT NOT NULL, evidence_id TEXT, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS audit_user_date ON audit(user_id, created_at DESC);
        """)
    return storage


def get_storage(request: Request) -> Storage:
    """FastAPI 의존성: 이 요청을 처리하는 서버(app)의 저장 위치를 꺼냅니다.

    기존에는 create_app() 안의 지역 변수였던 값을, 파일을 나눈 뒤에도 같이 쓰기 위해
    app.state에 넣어 두고 여기서 꺼내 씁니다. (API 주소·응답에는 영향 없음)
    """
    return request.app.state.storage
