"""저장 위치(데이터 폴더·증거 파일 폴더·SQLite DB), DB 연결, 쓰기 트랜잭션, 자동 마이그레이션.

3단계(feature/immutable-records)에서 바뀐 점
- 쓰기는 모두 write_transaction()으로 감쌉니다. BEGIN IMMEDIATE로 쓰기 잠금을 먼저 잡고 시작합니다.
- 테이블 생성은 executescript 대신 "버전별 마이그레이션"으로 합니다.
  서버가 시작할 때(요청을 받기 전) 한 트랜잭션으로 실행하고, 끝나면 PRAGMA user_version을 올립니다.
  기존 데이터가 있으면 먼저 DB 파일을 backups/ 폴더에 백업합니다. 실패하면 서버가 시작하지 않습니다.
- 증거·검증 결과·활동 기록의 수정·삭제를 막는 트리거와, 작업 당시 이름 컬럼을 추가합니다. (마이그레이션 2)

4단계(feature/chain-of-custody)에서 바뀐 점
- Chain of Custody 표(chain_of_custody)와 추가만 허용하는 트리거를 만듭니다. (마이그레이션 3)
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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
        """요청마다 새 DB 연결을 엽니다.

        isolation_level=None: 파이썬이 트랜잭션을 몰래 시작하지 않게 합니다.
        트랜잭션은 write_transaction()에서만 직접 시작합니다. (읽기는 트랜잭션 없이 바로 실행)
        """
        connection = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


@contextmanager
def write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """쓰기 트랜잭션. 안에서 오류가 나면 전부 되돌리고(ROLLBACK), 아니면 저장(COMMIT)합니다.

    BEGIN IMMEDIATE: 시작할 때 쓰기 잠금을 먼저 잡습니다. 동시에 들어온 쓰기 요청끼리
    중간에 잠금을 다투다 실패하는 일을 줄이고, Chain of Custody 순번 계산을 안전하게 하기 위해서입니다.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


# ---------------------------------------------------------------------------
# 마이그레이션
# 각 함수는 이미 열린 트랜잭션 안에서 문장을 "하나씩" 실행합니다. (executescript 사용 안 함)
# 한 번 배포한 마이그레이션은 고치지 않고, 바꿀 것이 생기면 새 번호를 추가합니다.
# ---------------------------------------------------------------------------

def _migration_1_base_schema(connection: sqlite3.Connection) -> None:
    """마이그레이션 1: 기존 테이블 5개와 인덱스 (2단계까지의 구조와 같음)

    IF NOT EXISTS를 쓰므로, 버전 기록이 없던 예전 DB(user_version 0)에서도 그대로 통과합니다.
    """
    statements = [
        """CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
              password_hash TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
        """CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL
            )""",
        """CREATE TABLE IF NOT EXISTS evidence (
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
              filename TEXT NOT NULL, stored_name TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
              mime TEXT NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
              description TEXT NOT NULL, memo TEXT NOT NULL, registered_at TEXT NOT NULL,
              sha256 TEXT NOT NULL, verification_status TEXT NOT NULL DEFAULT 'NOT_VERIFIED'
            )""",
        "CREATE INDEX IF NOT EXISTS evidence_owner_date ON evidence(owner_id, registered_at DESC)",
        """CREATE TABLE IF NOT EXISTS verifications (
              id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id), user_id TEXT NOT NULL REFERENCES users(id),
              started_at TEXT NOT NULL, completed_at TEXT NOT NULL, expected_hash TEXT NOT NULL,
              current_hash TEXT, status TEXT NOT NULL, reason TEXT NOT NULL
            )""",
        "CREATE INDEX IF NOT EXISTS verification_evidence_date ON verifications(evidence_id, completed_at DESC)",
        """CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
              action TEXT NOT NULL, evidence_id TEXT, created_at TEXT NOT NULL
            )""",
        "CREATE INDEX IF NOT EXISTS audit_user_date ON audit(user_id, created_at DESC)",
    ]
    for statement in statements:
        connection.execute(statement)


def _add_column_if_missing(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """컬럼이 없을 때만 추가합니다. (table·column은 코드에 적힌 고정 값만 넘깁니다)"""
    existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migration_2_immutable_records(connection: sqlite3.Connection) -> None:
    """마이그레이션 2: 작업 당시 이름 컬럼 + 수정·삭제 금지 트리거 + 값 목록 검사 트리거

    - 이름 컬럼은 NULL 허용으로 추가합니다. (행이 있는 테이블에 기본값 없는 NOT NULL 컬럼은 추가할 수 없음)
      기존 행은 NULL 그대로 두고(UPDATE 없음), 새 행은 입력 전 검사 트리거가 NULL·빈 문자열을 거부합니다.
    - 트리거는 DROP → CREATE로 만듭니다. 나중에 목록을 바꿀 때도 새 마이그레이션에서 같은 방식으로 다시 만듭니다.
    - 늘어날 수 있는 값 목록(상태 값)은 CHECK가 아니라 트리거로 검사합니다. (CHECK를 바꾸려면 테이블을 다시 만들어야 함)
    """
    _add_column_if_missing(connection, "evidence", "registered_by_name", "TEXT")
    _add_column_if_missing(connection, "verifications", "performed_by_name", "TEXT")

    triggers = {
        # evidence 수정 금지 트리거는 하나로 합칩니다. 바꿀 수 있는 컬럼은 verification_status 하나뿐입니다.
        # (6단계 암호화 저장에서 재암호화 예외가 생기면 이 트리거를 새 목록으로 다시 만듭니다)
        "evidence_block_update": """
            CREATE TRIGGER evidence_block_update BEFORE UPDATE ON evidence
            WHEN NEW.id IS NOT OLD.id OR NEW.owner_id IS NOT OLD.owner_id OR NEW.name IS NOT OLD.name
              OR NEW.filename IS NOT OLD.filename OR NEW.stored_name IS NOT OLD.stored_name
              OR NEW.type IS NOT OLD.type OR NEW.mime IS NOT OLD.mime OR NEW.size IS NOT OLD.size
              OR NEW.kind IS NOT OLD.kind OR NEW.source IS NOT OLD.source
              OR NEW.description IS NOT OLD.description OR NEW.memo IS NOT OLD.memo
              OR NEW.registered_at IS NOT OLD.registered_at OR NEW.sha256 IS NOT OLD.sha256
              OR NEW.registered_by_name IS NOT OLD.registered_by_name
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: evidence 정보는 수정할 수 없습니다 (verification_status만 변경 가능)'); END""",
        "evidence_block_delete": """
            CREATE TRIGGER evidence_block_delete BEFORE DELETE ON evidence
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: evidence는 삭제할 수 없습니다'); END""",
        "evidence_check_status_insert": """
            CREATE TRIGGER evidence_check_status_insert BEFORE INSERT ON evidence
            WHEN NEW.verification_status NOT IN ('NOT_VERIFIED', 'MATCH', 'MISMATCH', 'UNAVAILABLE')
            BEGIN SELECT RAISE(ABORT, 'INVALID: evidence.verification_status 값이 허용 목록에 없습니다'); END""",
        "evidence_check_status_update": """
            CREATE TRIGGER evidence_check_status_update BEFORE UPDATE OF verification_status ON evidence
            WHEN NEW.verification_status NOT IN ('NOT_VERIFIED', 'MATCH', 'MISMATCH', 'UNAVAILABLE')
            BEGIN SELECT RAISE(ABORT, 'INVALID: evidence.verification_status 값이 허용 목록에 없습니다'); END""",
        "evidence_require_registered_by_name": """
            CREATE TRIGGER evidence_require_registered_by_name BEFORE INSERT ON evidence
            WHEN NEW.registered_by_name IS NULL OR trim(NEW.registered_by_name) = ''
            BEGIN SELECT RAISE(ABORT, 'INVALID: 새 증거에는 registered_by_name이 필요합니다'); END""",
        # 검증 결과: 행 전체 수정·삭제 금지
        "verifications_block_update": """
            CREATE TRIGGER verifications_block_update BEFORE UPDATE ON verifications
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 검증 결과는 수정할 수 없습니다'); END""",
        "verifications_block_delete": """
            CREATE TRIGGER verifications_block_delete BEFORE DELETE ON verifications
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 검증 결과는 삭제할 수 없습니다'); END""",
        "verifications_check_status": """
            CREATE TRIGGER verifications_check_status BEFORE INSERT ON verifications
            WHEN NEW.status NOT IN ('MATCH', 'MISMATCH', 'UNAVAILABLE')
            BEGIN SELECT RAISE(ABORT, 'INVALID: verifications.status 값이 허용 목록에 없습니다'); END""",
        "verifications_require_performed_by_name": """
            CREATE TRIGGER verifications_require_performed_by_name BEFORE INSERT ON verifications
            WHEN NEW.performed_by_name IS NULL OR trim(NEW.performed_by_name) = ''
            BEGIN SELECT RAISE(ABORT, 'INVALID: 새 검증 결과에는 performed_by_name이 필요합니다'); END""",
        # 활동 기록: 행 전체 수정·삭제 금지
        "audit_block_update": """
            CREATE TRIGGER audit_block_update BEFORE UPDATE ON audit
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 활동 기록은 수정할 수 없습니다'); END""",
        "audit_block_delete": """
            CREATE TRIGGER audit_block_delete BEFORE DELETE ON audit
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 활동 기록은 삭제할 수 없습니다'); END""",
    }
    for name, statement in triggers.items():
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute(statement)


# Chain of Custody 이벤트 허용 목록 (통합 설계 문서 4장).
# EVIDENCE_REENCRYPTED는 [결정 필요]라서 아직 넣지 않습니다. 정해지면 새 마이그레이션에서 트리거를 다시 만듭니다.
# server/custody.py의 EVENT_TYPES와 같은 목록이어야 합니다. (테스트가 둘이 같은지 확인)
CUSTODY_EVENT_TYPES = (
    "EVIDENCE_REGISTERED", "EVIDENCE_DOWNLOADED", "INTEGRITY_VERIFIED",
    "PERMISSION_GRANTED", "PERMISSION_REVOKED", "ACCESS_DENIED",
    "REPORT_INCLUDED", "PACKAGE_EXPORTED", "PACKAGE_DOWNLOADED", "LEGACY_IMPORTED",
)


def _migration_3_chain_of_custody(connection: sqlite3.Connection) -> None:
    """마이그레이션 3: Chain of Custody 표 + 추가만 허용하는 트리거 + 사슬 연결 검사 트리거

    - 증거마다 sequence_no 1, 2, 3 ... 순서로 이벤트가 쌓입니다. 각 이벤트는 바로 앞 이벤트의 해시를 품습니다.
    - 수정·삭제는 트리거가 막습니다. (IMMUTABLE)
    - 새 이벤트는 "마지막 번호 + 1"이고 "마지막 이벤트 해시"를 이어받아야 들어갑니다. (INVALID)
      → 코드에 실수가 있어도 DB가 끊긴 사슬을 받아들이지 않습니다.
    - event_hash 값이 정말 SHA-256 계산 결과인지는 SQLite가 계산할 수 없어서, C2 검사(custody.verify_chain)가 확인합니다.
    - 이 마이그레이션 이전에 등록된 증거에는 이벤트를 거슬러 만들지 않습니다. (C2에서 CHAIN_EMPTY. 11/12 결정)
    """
    connection.execute("""CREATE TABLE IF NOT EXISTS chain_of_custody (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          evidence_id TEXT NOT NULL REFERENCES evidence(id),
          sequence_no INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          actor_user_id TEXT NOT NULL REFERENCES users(id),
          event_time TEXT NOT NULL,
          result TEXT NOT NULL,
          details TEXT NOT NULL,
          previous_event_hash TEXT NOT NULL,
          event_hash TEXT NOT NULL UNIQUE,
          UNIQUE (evidence_id, sequence_no)
        )""")

    event_types = ", ".join(f"'{name}'" for name in CUSTODY_EVENT_TYPES)  # 코드에 적힌 고정 값만 사용
    zero_hash = "0" * 64
    triggers = {
        # 추가만 허용: 수정·삭제 금지
        "custody_block_update": """
            CREATE TRIGGER custody_block_update BEFORE UPDATE ON chain_of_custody
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: Chain of Custody 이벤트는 수정할 수 없습니다'); END""",
        "custody_block_delete": """
            CREATE TRIGGER custody_block_delete BEFORE DELETE ON chain_of_custody
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: Chain of Custody 이벤트는 삭제할 수 없습니다'); END""",
        # 값 목록 검사 (늘어날 수 있으므로 CHECK 대신 트리거)
        "custody_check_event_type": f"""
            CREATE TRIGGER custody_check_event_type BEFORE INSERT ON chain_of_custody
            WHEN NEW.event_type NOT IN ({event_types})
            BEGIN SELECT RAISE(ABORT, 'INVALID: chain_of_custody.event_type 값이 허용 목록에 없습니다'); END""",
        "custody_check_result": """
            CREATE TRIGGER custody_check_result BEFORE INSERT ON chain_of_custody
            WHEN NEW.result NOT IN ('SUCCESS', 'FAILURE')
            BEGIN SELECT RAISE(ABORT, 'INVALID: chain_of_custody.result 값이 허용 목록에 없습니다'); END""",
        # details는 JSON 객체이고, actorName(작업 당시 이름)은 빈 값이 아닌 문자열이어야 함
        "custody_require_actor_name": """
            CREATE TRIGGER custody_require_actor_name BEFORE INSERT ON chain_of_custody
            WHEN CASE WHEN json_valid(NEW.details) THEN (
                     json_type(NEW.details) IS NOT 'object'
                     OR json_type(NEW.details, '$.actorName') IS NOT 'text'
                     OR trim(json_extract(NEW.details, '$.actorName')) = '')
                 ELSE 1 END  -- JSON이 아니면 json_type을 부르지 않고 바로 거부 (CASE는 필요한 쪽만 계산)
            BEGIN SELECT RAISE(ABORT, 'INVALID: 새 이벤트의 details에는 actorName이 필요합니다'); END""",
        # 해시 2개는 소문자 16진수 64자리
        "custody_check_hash_format": """
            CREATE TRIGGER custody_check_hash_format BEFORE INSERT ON chain_of_custody
            WHEN length(NEW.event_hash) != 64 OR NEW.event_hash GLOB '*[^0-9a-f]*'
              OR length(NEW.previous_event_hash) != 64 OR NEW.previous_event_hash GLOB '*[^0-9a-f]*'
            BEGIN SELECT RAISE(ABORT, 'INVALID: 해시는 소문자 16진수 64자리여야 합니다'); END""",
        # 번호는 그 증거의 마지막 번호 + 1 (첫 이벤트는 1)
        "custody_check_sequence": """
            CREATE TRIGGER custody_check_sequence BEFORE INSERT ON chain_of_custody
            WHEN NEW.sequence_no IS NOT (
                SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM chain_of_custody WHERE evidence_id = NEW.evidence_id)
            BEGIN SELECT RAISE(ABORT, 'INVALID: sequence_no는 마지막 번호 + 1이어야 합니다'); END""",
        # 앞 이벤트 해시는 그 증거의 마지막 이벤트 해시 (첫 이벤트는 0 64개)
        "custody_check_link": f"""
            CREATE TRIGGER custody_check_link BEFORE INSERT ON chain_of_custody
            WHEN NEW.previous_event_hash IS NOT COALESCE((
                SELECT event_hash FROM chain_of_custody WHERE evidence_id = NEW.evidence_id
                ORDER BY sequence_no DESC LIMIT 1), '{zero_hash}')
            BEGIN SELECT RAISE(ABORT, 'INVALID: previous_event_hash가 마지막 이벤트 해시와 다릅니다'); END""",
    }
    for name, statement in triggers.items():
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute(statement)


# (버전 번호, 마이그레이션 함수). 번호는 1부터 빈칸 없이 올라갑니다.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_1_base_schema),
    (2, _migration_2_immutable_records),
    (3, _migration_3_chain_of_custody),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def _has_data(connection: sqlite3.Connection) -> bool:
    """DB에 사용자 테이블이 하나라도 있으면 True (백업이 필요한 기존 DB인지 판단)"""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _backup(storage: Storage, connection: sqlite3.Connection, from_version: int) -> Path:
    """마이그레이션 전에 DB 파일 전체를 backups/ 폴더에 복사합니다. (SQLite 백업 기능 사용)"""
    backup_dir = storage.root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"evidence-v{from_version}-{stamp}.sqlite3"
    with closing(sqlite3.connect(target)) as backup_connection:
        connection.backup(backup_connection)
    return target


def migrate(storage: Storage) -> None:
    """서버 시작 시 DB를 최신 구조(SCHEMA_VERSION)로 맞춥니다.

    1) 이미 최신이면 아무것도 하지 않습니다.
    2) 더 새 버전 서버가 만든 DB면 시작을 멈춥니다. (모르는 구조를 건드리지 않기 위해)
    3) 기존 데이터가 있으면 먼저 백업합니다.
    4) 남은 마이그레이션을 "한 트랜잭션"으로 실행하고 user_version을 올립니다. 하나라도 실패하면 전부 되돌리고 오류를 냅니다.
    """
    with closing(storage.connect()) as connection:
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"DB 구조 버전({current})이 이 서버가 아는 버전({SCHEMA_VERSION})보다 높습니다. 서버 코드를 최신으로 받아 주세요."
            )
        if current == SCHEMA_VERSION:
            return
        if _has_data(connection):
            _backup(storage, connection, current)
        with write_transaction(connection):
            # 다른 프로세스가 그사이 먼저 마이그레이션했을 수 있으므로, 잠금을 잡은 뒤 버전을 다시 읽습니다.
            current = connection.execute("PRAGMA user_version").fetchone()[0]
            for version, apply in MIGRATIONS:
                if version > current:
                    apply(connection)
            connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")


def open_storage(data_dir: str | Path | None = None) -> Storage:
    """저장 폴더를 정하고, 폴더를 만들고, DB를 최신 구조로 맞춥니다.

    폴더 결정 순서는 기존과 같습니다: create_app(data_dir) 인자 → 환경변수 EVIDENCE_DATA_DIR → ROOT/data
    """
    root = Path(data_dir or os.environ.get("EVIDENCE_DATA_DIR", ROOT / "data")).resolve()
    files_dir = root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    storage = Storage(root=root, files_dir=files_dir, db_path=root / "evidence.sqlite3")
    migrate(storage)
    return storage


def get_storage(request: Request) -> Storage:
    """FastAPI 의존성: 이 요청을 처리하는 서버(app)의 저장 위치를 꺼냅니다."""
    return request.app.state.storage
