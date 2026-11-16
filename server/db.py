"""저장 위치(데이터 폴더·증거 파일 폴더·SQLite DB), DB 연결, 쓰기 트랜잭션, 자동 마이그레이션.

3단계(feature/immutable-records)에서 바뀐 점
- 쓰기는 모두 write_transaction()으로 감쌉니다. BEGIN IMMEDIATE로 쓰기 잠금을 먼저 잡고 시작합니다.
- 테이블 생성은 executescript 대신 "버전별 마이그레이션"으로 합니다.
  서버가 시작할 때(요청을 받기 전) 한 트랜잭션으로 실행하고, 끝나면 PRAGMA user_version을 올립니다.
  기존 데이터가 있으면 먼저 DB 파일을 backups/ 폴더에 백업합니다. 실패하면 서버가 시작하지 않습니다.
- 증거·검증 결과·활동 기록의 수정·삭제를 막는 트리거와, 작업 당시 이름 컬럼을 추가합니다. (마이그레이션 2)

4단계(feature/chain-of-custody)에서 바뀐 점
- Chain of Custody 표(chain_of_custody)와 추가만 허용하는 트리거를 만듭니다. (마이그레이션 3)

5단계(feature/evidence-sharing)에서 바뀐 점
- 사용자 역할(users.role)과 증거별 공유 권한 표(evidence_permissions)를 만듭니다. (마이그레이션 4)

6단계(feature/evidence-encryption)에서 바뀐 점
- 증거 파일 암호화 정보, 검증 실패 원인 코드, 활동 기록 서명 체인 컬럼을 추가합니다. (마이그레이션 5)
- 서버 시작 때 암호화 키를 먼저 읽습니다. 키가 없으면 DB를 건드리기 전에 시작을 멈춥니다. (B 결정: fail-closed)

7단계(feature/reports)에서 바뀐 점
- 보고서와 보고서에 넣은 증거 표를 만듭니다. (마이그레이션 6) PDF는 root/reports/에 저장

8단계(feature/packages)에서 바뀐 점
- 증거 패키지와 패키지에 넣은 증거 표를 만듭니다. (마이그레이션 7) ZIP은 root/packages/에 저장
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request

from .config import ROOT
from .crypto import EvidenceCrypto


@dataclass(frozen=True)
class Storage:
    """서버 하나가 쓰는 저장 위치 묶음. create_app()이 만들어 app.state.storage에 넣어 둡니다."""

    root: Path       # 데이터 폴더 (EVIDENCE_DATA_DIR 또는 ROOT/data)
    files_dir: Path  # 증거 원본 파일 폴더 (root/files). 6단계부터 새 파일은 암호문으로 저장
    db_path: Path    # SQLite DB 파일 (root/evidence.sqlite3)
    crypto: EvidenceCrypto | None = None  # 6단계 추가: B의 암호화 모듈 (키를 들고 있음, 응답·로그에 내보내지 않음)

    @property
    def reports_dir(self) -> Path:
        """보고서 PDF 폴더 (root/reports). 7단계 추가"""
        return self.root / "reports"

    @property
    def packages_dir(self) -> Path:
        """증거 패키지 ZIP 폴더 (root/packages). 8단계 추가"""
        return self.root / "packages"

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


# 공유 권한 종류 (통합 설계 문서 7장). VIEW는 공유하면 항상 포함되고, DOWNLOAD·VERIFY는 선택입니다.
# server/access.py의 PERMISSIONS와 같은 목록이어야 합니다. (테스트가 둘이 같은지 확인)
PERMISSION_TYPES = ("VIEW", "DOWNLOAD", "VERIFY")
# 사용자 역할. ADMIN이 무엇을 할 수 있는지는 아직 [결정 필요]라서 지금은 칸과 값만 둡니다. (11/15 결정)
USER_ROLES = ("ADMIN", "USER", "SYSTEM")


def _migration_4_sharing(connection: sqlite3.Connection) -> None:
    """마이그레이션 4: 사용자 역할(role) + 증거별 공유 권한 표(evidence_permissions)

    - users.role: 기본값 USER. 기존 계정도 모두 USER가 됩니다. (기본값이 있는 NOT NULL 컬럼은 ALTER로 추가 가능)
    - evidence_permissions: 권한 1개당 1행. 회수할 때 행을 지우지 않고 revoked_at만 채웁니다. (기록이 남음)
      같은 사람·같은 권한의 "살아 있는 행"(revoked_at IS NULL)은 1개만 있을 수 있습니다. (부분 UNIQUE 인덱스)
      다시 공유하면 새 행이 생깁니다.
    - 트리거가 막는 것: 삭제, revoked_at 말고 다른 칸 수정, 회수 취소, 소유자 본인에게 공유,
      소유자가 아닌 사람이 부여(재공유 금지), VIEW 없이 DOWNLOAD·VERIFY만 있는 상태
    """
    roles = ", ".join(f"'{name}'" for name in USER_ROLES)  # 코드에 적힌 고정 값만 사용
    permissions = ", ".join(f"'{name}'" for name in PERMISSION_TYPES)
    _add_column_if_missing(connection, "users", "role", "TEXT NOT NULL DEFAULT 'USER'")

    connection.execute("""CREATE TABLE IF NOT EXISTS evidence_permissions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          evidence_id TEXT NOT NULL REFERENCES evidence(id),
          user_id TEXT NOT NULL REFERENCES users(id),
          permission TEXT NOT NULL,
          granted_by TEXT NOT NULL REFERENCES users(id),
          granted_at TEXT NOT NULL,
          revoked_at TEXT
        )""")
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS evidence_permissions_active
        ON evidence_permissions(evidence_id, user_id, permission) WHERE revoked_at IS NULL""")
    # "공유받은 증거" 목록(scope=shared)을 빨리 찾기 위한 인덱스
    connection.execute("""CREATE INDEX IF NOT EXISTS evidence_permissions_user_active
        ON evidence_permissions(user_id, evidence_id) WHERE revoked_at IS NULL""")

    triggers = {
        # 역할 값 검사
        "users_check_role_insert": f"""
            CREATE TRIGGER users_check_role_insert BEFORE INSERT ON users
            WHEN NEW.role NOT IN ({roles})
            BEGIN SELECT RAISE(ABORT, 'INVALID: users.role 값이 허용 목록에 없습니다'); END""",
        "users_check_role_update": f"""
            CREATE TRIGGER users_check_role_update BEFORE UPDATE OF role ON users
            WHEN NEW.role NOT IN ({roles})
            BEGIN SELECT RAISE(ABORT, 'INVALID: users.role 값이 허용 목록에 없습니다'); END""",
        # 권한 기록: 삭제 금지
        "permissions_block_delete": """
            CREATE TRIGGER permissions_block_delete BEFORE DELETE ON evidence_permissions
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 권한 기록은 삭제할 수 없습니다 (revoked_at으로 회수)'); END""",
        # 권한 기록: "빈 revoked_at에 회수 시각을 한 번 채우기"만 허용
        "permissions_block_update": """
            CREATE TRIGGER permissions_block_update BEFORE UPDATE ON evidence_permissions
            WHEN NEW.id IS NOT OLD.id OR NEW.evidence_id IS NOT OLD.evidence_id OR NEW.user_id IS NOT OLD.user_id
              OR NEW.permission IS NOT OLD.permission OR NEW.granted_by IS NOT OLD.granted_by
              OR NEW.granted_at IS NOT OLD.granted_at OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 권한 기록은 회수 시각(revoked_at)을 한 번 채우는 것만 가능합니다'); END""",
        # 권한 값 검사
        "permissions_check_value": f"""
            CREATE TRIGGER permissions_check_value BEFORE INSERT ON evidence_permissions
            WHEN NEW.permission NOT IN ({permissions})
            BEGIN SELECT RAISE(ABORT, 'INVALID: evidence_permissions.permission 값이 허용 목록에 없습니다'); END""",
        # 새 행은 살아 있는 권한으로만 추가 (회수된 행을 직접 넣지 않음)
        "permissions_insert_active": """
            CREATE TRIGGER permissions_insert_active BEFORE INSERT ON evidence_permissions
            WHEN NEW.revoked_at IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'INVALID: 새 권한은 revoked_at 없이 추가해야 합니다'); END""",
        # 소유자 본인에게 공유 금지
        "permissions_block_owner": """
            CREATE TRIGGER permissions_block_owner BEFORE INSERT ON evidence_permissions
            WHEN NEW.user_id IS (SELECT owner_id FROM evidence WHERE id = NEW.evidence_id)
            BEGIN SELECT RAISE(ABORT, 'INVALID: 증거 소유자에게는 공유할 수 없습니다'); END""",
        # 부여는 소유자만 (공유받은 사람의 재공유 금지)
        "permissions_granted_by_owner": """
            CREATE TRIGGER permissions_granted_by_owner BEFORE INSERT ON evidence_permissions
            WHEN NEW.granted_by IS NOT (SELECT owner_id FROM evidence WHERE id = NEW.evidence_id)
            BEGIN SELECT RAISE(ABORT, 'INVALID: 권한은 증거 소유자만 부여할 수 있습니다'); END""",
        # DOWNLOAD·VERIFY는 살아 있는 VIEW가 있을 때만 부여
        "permissions_require_view_insert": """
            CREATE TRIGGER permissions_require_view_insert BEFORE INSERT ON evidence_permissions
            WHEN NEW.permission IS NOT 'VIEW' AND NOT EXISTS (
                SELECT 1 FROM evidence_permissions WHERE evidence_id = NEW.evidence_id AND user_id = NEW.user_id
                  AND permission = 'VIEW' AND revoked_at IS NULL)
            BEGIN SELECT RAISE(ABORT, 'INVALID: DOWNLOAD·VERIFY는 VIEW 권한이 있어야 부여할 수 있습니다'); END""",
        # VIEW는 DOWNLOAD·VERIFY를 먼저 회수한 뒤에만 회수
        "permissions_require_view_revoke": """
            CREATE TRIGGER permissions_require_view_revoke BEFORE UPDATE OF revoked_at ON evidence_permissions
            WHEN OLD.permission = 'VIEW' AND NEW.revoked_at IS NOT NULL AND EXISTS (
                SELECT 1 FROM evidence_permissions WHERE evidence_id = OLD.evidence_id AND user_id = OLD.user_id
                  AND permission != 'VIEW' AND revoked_at IS NULL)
            BEGIN SELECT RAISE(ABORT, 'INVALID: VIEW는 DOWNLOAD·VERIFY를 먼저 회수해야 회수할 수 있습니다'); END""",
    }
    for name, statement in triggers.items():
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute(statement)


# 검증 실패 원인 코드 (B 인터페이스 v1, 11/15 확정). 빈 문자열은 일치(MATCH)
FAILURE_CODES = ("HASH_MISMATCH", "AUTHENTICATION_FAILED", "FILE_UNAVAILABLE")


def _migration_5_encryption(connection: sqlite3.Connection) -> None:
    """마이그레이션 5: 증거 파일 암호화(B 모듈) 정보 + 검증 실패 원인 코드 + 활동 기록 서명 체인

    - evidence: 암호화 방식·키 버전·salt·nonce·태그·AAD 버전·저장 크기 (B의 DB 매핑 그대로)
      이전 증거는 encryption_algorithm = 'LEGACY'(평문 파일)로 남고, 그대로 읽고 검증할 수 있습니다.
      새 증거는 반드시 AES-256-GCM 암호화 정보가 있어야 들어갑니다. (트리거)
      수정 금지 트리거는 새 컬럼까지 포함해서 다시 만듭니다. (2단계에 적어 둔 계획)
    - verifications.failure_code: '' / HASH_MISMATCH / AUTHENTICATION_FAILED / FILE_UNAVAILABLE
      상태와 원인 코드가 서로 맞아야 들어갑니다. (MATCH↔'', MISMATCH↔HASH_MISMATCH·AUTHENTICATION_FAILED,
      UNAVAILABLE↔FILE_UNAVAILABLE). 기존 행은 ''로 남습니다.
    - audit: B의 사용자별 HMAC 서명 체인 컬럼(previous_hash, entry_hash, key_version). 기존 행은 ''(LEGACY)
      새 기록은 서명이 있어야 하고, previous_hash가 그 사용자의 마지막 서명과 이어져야 들어갑니다.
    - 컬럼 추가(ALTER TABLE ADD COLUMN)는 UPDATE가 아니라서 수정 금지 트리거가 실행되지 않습니다.
    """
    for column, definition in {
        "stored_size": "INTEGER",
        "encryption_algorithm": "TEXT NOT NULL DEFAULT 'LEGACY'",
        "encryption_key_version": "TEXT",
        "encryption_salt": "TEXT",
        "encryption_nonce": "TEXT",
        "encryption_tag": "TEXT",
        "encryption_aad_version": "TEXT NOT NULL DEFAULT ''",
    }.items():
        _add_column_if_missing(connection, "evidence", column, definition)
    _add_column_if_missing(connection, "verifications", "failure_code", "TEXT NOT NULL DEFAULT ''")
    for column in ("previous_hash", "entry_hash", "key_version"):
        _add_column_if_missing(connection, "audit", column, "TEXT NOT NULL DEFAULT ''")

    codes = ", ".join(f"'{code}'" for code in ("",) + FAILURE_CODES)  # 코드에 적힌 고정 값만 사용
    zero_hash = "0" * 64
    triggers = {
        # evidence 수정 금지 (2단계 트리거를 새 컬럼까지 포함해서 다시 만듦). 바꿀 수 있는 것은 여전히 verification_status 하나
        "evidence_block_update": """
            CREATE TRIGGER evidence_block_update BEFORE UPDATE ON evidence
            WHEN NEW.id IS NOT OLD.id OR NEW.owner_id IS NOT OLD.owner_id OR NEW.name IS NOT OLD.name
              OR NEW.filename IS NOT OLD.filename OR NEW.stored_name IS NOT OLD.stored_name
              OR NEW.type IS NOT OLD.type OR NEW.mime IS NOT OLD.mime OR NEW.size IS NOT OLD.size
              OR NEW.kind IS NOT OLD.kind OR NEW.source IS NOT OLD.source
              OR NEW.description IS NOT OLD.description OR NEW.memo IS NOT OLD.memo
              OR NEW.registered_at IS NOT OLD.registered_at OR NEW.sha256 IS NOT OLD.sha256
              OR NEW.registered_by_name IS NOT OLD.registered_by_name
              OR NEW.stored_size IS NOT OLD.stored_size OR NEW.encryption_algorithm IS NOT OLD.encryption_algorithm
              OR NEW.encryption_key_version IS NOT OLD.encryption_key_version
              OR NEW.encryption_salt IS NOT OLD.encryption_salt OR NEW.encryption_nonce IS NOT OLD.encryption_nonce
              OR NEW.encryption_tag IS NOT OLD.encryption_tag OR NEW.encryption_aad_version IS NOT OLD.encryption_aad_version
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: evidence 정보는 수정할 수 없습니다 (verification_status만 변경 가능)'); END""",
        # 새 증거는 암호화 정보가 모두 있어야 함 (평문 저장 금지)
        "evidence_require_encryption": """
            CREATE TRIGGER evidence_require_encryption BEFORE INSERT ON evidence
            WHEN NEW.encryption_algorithm IS NOT 'AES-256-GCM'
              OR coalesce(NEW.encryption_key_version, '') = '' OR coalesce(NEW.encryption_salt, '') = ''
              OR coalesce(NEW.encryption_nonce, '') = '' OR coalesce(NEW.encryption_tag, '') = ''
              OR NEW.encryption_aad_version IS NOT 'v1' OR NEW.stored_size IS NULL
            BEGIN SELECT RAISE(ABORT, 'INVALID: 새 증거는 AES-256-GCM 암호화 정보가 있어야 합니다'); END""",
        # 검증 실패 원인 코드: 허용 목록 + 상태와 짝이 맞는지
        "verifications_check_failure_code": f"""
            CREATE TRIGGER verifications_check_failure_code BEFORE INSERT ON verifications
            WHEN NEW.failure_code NOT IN ({codes})
              OR (NEW.status = 'MATCH' AND NEW.failure_code != '')
              OR (NEW.status = 'MISMATCH' AND NEW.failure_code NOT IN ('HASH_MISMATCH', 'AUTHENTICATION_FAILED'))
              OR (NEW.status = 'UNAVAILABLE' AND NEW.failure_code != 'FILE_UNAVAILABLE')
            BEGIN SELECT RAISE(ABORT, 'INVALID: verifications.failure_code가 허용 목록에 없거나 상태와 맞지 않습니다'); END""",
        # 활동 기록: 새 기록은 서명이 있어야 함
        "audit_require_signature": """
            CREATE TRIGGER audit_require_signature BEFORE INSERT ON audit
            WHEN length(NEW.entry_hash) != 64 OR NEW.entry_hash GLOB '*[^0-9a-f]*' OR NEW.key_version = ''
            BEGIN SELECT RAISE(ABORT, 'INVALID: 새 활동 기록에는 서명(entry_hash)과 키 버전이 필요합니다'); END""",
        # 활동 기록: 사용자별 체인 연결 (그 사용자의 마지막 서명된 기록과 이어져야 함. 첫 기록은 0 64개)
        "audit_check_link": f"""
            CREATE TRIGGER audit_check_link BEFORE INSERT ON audit
            WHEN NEW.previous_hash IS NOT COALESCE((
                SELECT entry_hash FROM audit WHERE user_id = NEW.user_id AND entry_hash != ''
                ORDER BY id DESC LIMIT 1), '{zero_hash}')
            BEGIN SELECT RAISE(ABORT, 'INVALID: audit.previous_hash가 그 사용자의 마지막 서명과 다릅니다'); END""",
    }
    for name, statement in triggers.items():
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute(statement)


def _migration_6_reports(connection: sqlite3.Connection) -> None:
    """마이그레이션 6: 보고서(reports)와 보고서에 넣은 증거(report_evidence)

    - 보고서는 "그 순간의 증거 상태를 찍은 사진"이라 만든 뒤에는 수정·삭제할 수 없습니다. (트리거)
    - 증거·검증 결과는 원래 고칠 수 없는 기록이므로, 보고서에는 그 기록의 ID(검증 ID)와
      관리 이력 기준점(번호·해시)만 저장하고 화면용 모습은 그 기록으로 다시 만듭니다.
    - PDF 파일은 데이터 폴더의 reports/에 저장하고, DB에는 저장 이름·SHA-256·크기만 둡니다.
      (보고서 PDF 암호화는 B와 AAD 구성을 정한 뒤 추가. 11/15 결정: 지금은 해시로 위조 확인)
    """
    connection.execute("""CREATE TABLE IF NOT EXISTS reports (
          id TEXT PRIMARY KEY,
          created_by TEXT NOT NULL REFERENCES users(id),
          author_name TEXT NOT NULL,
          title TEXT NOT NULL,
          case_name TEXT NOT NULL,
          description TEXT NOT NULL,
          options_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          file_storage_key TEXT NOT NULL UNIQUE,
          file_sha256 TEXT NOT NULL,
          file_size INTEGER NOT NULL
        )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS report_evidence (
          report_id TEXT NOT NULL REFERENCES reports(id),
          evidence_id TEXT NOT NULL REFERENCES evidence(id),
          item_order INTEGER NOT NULL,
          verification_id TEXT REFERENCES verifications(id),
          custody_snapshot_sequence_no INTEGER NOT NULL,
          custody_snapshot_event_hash TEXT NOT NULL,
          PRIMARY KEY (report_id, evidence_id),
          UNIQUE (report_id, item_order)
        )""")
    connection.execute("CREATE INDEX IF NOT EXISTS reports_created_by ON reports(created_by, created_at)")

    triggers = {
        "reports_block_update": """
            CREATE TRIGGER reports_block_update BEFORE UPDATE ON reports
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 보고서는 수정할 수 없습니다'); END""",
        "reports_block_delete": """
            CREATE TRIGGER reports_block_delete BEFORE DELETE ON reports
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 보고서는 삭제할 수 없습니다'); END""",
        "report_evidence_block_update": """
            CREATE TRIGGER report_evidence_block_update BEFORE UPDATE ON report_evidence
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 보고서에 넣은 증거 목록은 수정할 수 없습니다'); END""",
        "report_evidence_block_delete": """
            CREATE TRIGGER report_evidence_block_delete BEFORE DELETE ON report_evidence
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 보고서에 넣은 증거 목록은 삭제할 수 없습니다'); END""",
        # 보고서 ID·파일 해시 형식, 작성자 이름
        "reports_check_values": """
            CREATE TRIGGER reports_check_values BEFORE INSERT ON reports
            WHEN length(NEW.id) != 36 OR substr(NEW.id, 1, 4) != 'RPT-' OR substr(NEW.id, 5) GLOB '*[^0-9a-f]*'
              OR length(NEW.file_sha256) != 64 OR NEW.file_sha256 GLOB '*[^0-9a-f]*'
              OR trim(NEW.author_name) = '' OR NEW.file_size <= 0
            BEGIN SELECT RAISE(ABORT, 'INVALID: 보고서 ID·파일 해시·작성자 이름 형식이 올바르지 않습니다'); END""",
        # 기준점은 그 증거의 관리 이력에 실제로 있는 이벤트(번호·해시)여야 함
        "report_evidence_check_snapshot": """
            CREATE TRIGGER report_evidence_check_snapshot BEFORE INSERT ON report_evidence
            WHEN NOT EXISTS (SELECT 1 FROM chain_of_custody WHERE evidence_id = NEW.evidence_id
                             AND sequence_no = NEW.custody_snapshot_sequence_no
                             AND event_hash = NEW.custody_snapshot_event_hash)
            BEGIN SELECT RAISE(ABORT, 'INVALID: 관리 이력 기준점이 그 증거의 이벤트와 맞지 않습니다'); END""",
        # 검증 ID는 그 증거의 검증 결과여야 함 (없으면 NULL = 검증한 적 없음)
        "report_evidence_check_verification": """
            CREATE TRIGGER report_evidence_check_verification BEFORE INSERT ON report_evidence
            WHEN NEW.verification_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM verifications WHERE id = NEW.verification_id AND evidence_id = NEW.evidence_id)
            BEGIN SELECT RAISE(ABORT, 'INVALID: 검증 ID가 그 증거의 검증 결과가 아닙니다'); END""",
    }
    for name, statement in triggers.items():
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute(statement)


def _migration_7_packages(connection: sqlite3.Connection) -> None:
    """마이그레이션 7: 증거 패키지(evidence_packages)와 패키지에 넣은 증거(evidence_package_items)

    - 패키지는 "보고서 PDF + 증거 원본 + 검증 기록 + 관리 이력"을 묶은 ZIP입니다. 만든 뒤에는 수정·삭제할 수 없습니다.
    - 패키지는 보고서를 만든 사람만 만들 수 있습니다. (트리거가 보고서 작성자와 같은지 확인)
    - 넣은 원본의 SHA-256(packaged_file_sha256)은 기준 해시(evidence.sha256)와 같아야만 기록됩니다.
      다르면 C 코드가 먼저 막지만, 코드에 실수가 있어도 DB가 받아들이지 않게 한 번 더 막습니다.
    - ZIP 파일은 데이터 폴더의 packages/에 저장하고, DB에는 저장 이름·SHA-256·크기만 둡니다.
      (ZIP 암호화는 B와 AAD 구성을 정한 뒤 추가. 11/16 결정: 지금은 해시로 위조 확인)
    """
    connection.execute("""CREATE TABLE IF NOT EXISTS evidence_packages (
          id TEXT PRIMARY KEY,
          report_id TEXT NOT NULL REFERENCES reports(id),
          created_by TEXT NOT NULL REFERENCES users(id),
          creator_name TEXT NOT NULL,
          created_at TEXT NOT NULL,
          file_storage_key TEXT NOT NULL UNIQUE,
          file_sha256 TEXT NOT NULL,
          file_size INTEGER NOT NULL
        )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS evidence_package_items (
          package_id TEXT NOT NULL REFERENCES evidence_packages(id),
          evidence_id TEXT NOT NULL REFERENCES evidence(id),
          item_order INTEGER NOT NULL,
          evidence_sha256 TEXT NOT NULL,
          packaged_file_sha256 TEXT NOT NULL,
          verification_id TEXT REFERENCES verifications(id),
          verification_status TEXT NOT NULL,
          path_in_package TEXT NOT NULL,
          custody_snapshot_sequence_no INTEGER NOT NULL,
          custody_snapshot_event_hash TEXT NOT NULL,
          PRIMARY KEY (package_id, evidence_id),
          UNIQUE (package_id, item_order),
          UNIQUE (package_id, path_in_package)
        )""")
    connection.execute("CREATE INDEX IF NOT EXISTS evidence_packages_report ON evidence_packages(report_id, created_at)")

    triggers = {
        "packages_block_update": """
            CREATE TRIGGER packages_block_update BEFORE UPDATE ON evidence_packages
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 증거 패키지는 수정할 수 없습니다'); END""",
        "packages_block_delete": """
            CREATE TRIGGER packages_block_delete BEFORE DELETE ON evidence_packages
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 증거 패키지는 삭제할 수 없습니다'); END""",
        "package_items_block_update": """
            CREATE TRIGGER package_items_block_update BEFORE UPDATE ON evidence_package_items
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 패키지에 넣은 증거 목록은 수정할 수 없습니다'); END""",
        "package_items_block_delete": """
            CREATE TRIGGER package_items_block_delete BEFORE DELETE ON evidence_package_items
            BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: 패키지에 넣은 증거 목록은 삭제할 수 없습니다'); END""",
        # 패키지 ID·파일 해시 형식, 만든 사람 이름, 크기
        "packages_check_values": """
            CREATE TRIGGER packages_check_values BEFORE INSERT ON evidence_packages
            WHEN length(NEW.id) != 36 OR substr(NEW.id, 1, 4) != 'PKG-' OR substr(NEW.id, 5) GLOB '*[^0-9a-f]*'
              OR length(NEW.file_sha256) != 64 OR NEW.file_sha256 GLOB '*[^0-9a-f]*'
              OR trim(NEW.creator_name) = '' OR NEW.file_size <= 0
            BEGIN SELECT RAISE(ABORT, 'INVALID: 패키지 ID·파일 해시·만든 사람 이름 형식이 올바르지 않습니다'); END""",
        # 패키지는 그 보고서를 만든 사람만 만들 수 있음
        "packages_check_owner": """
            CREATE TRIGGER packages_check_owner BEFORE INSERT ON evidence_packages
            WHEN NOT EXISTS (SELECT 1 FROM reports WHERE id = NEW.report_id AND created_by = NEW.created_by)
            BEGIN SELECT RAISE(ABORT, 'INVALID: 패키지는 보고서를 만든 사람만 만들 수 있습니다'); END""",
        # 넣은 원본 해시 = 기록한 기준 해시 = 증거의 기준 해시, 그리고 그 보고서에 들어 있던 증거여야 함
        "package_items_check_hash": """
            CREATE TRIGGER package_items_check_hash BEFORE INSERT ON evidence_package_items
            WHEN NEW.packaged_file_sha256 IS NOT NEW.evidence_sha256
              OR NEW.evidence_sha256 IS NOT (SELECT sha256 FROM evidence WHERE id = NEW.evidence_id)
              OR NOT EXISTS (SELECT 1 FROM report_evidence JOIN evidence_packages
                             ON evidence_packages.report_id = report_evidence.report_id
                             WHERE evidence_packages.id = NEW.package_id AND report_evidence.evidence_id = NEW.evidence_id)
            BEGIN SELECT RAISE(ABORT, 'INVALID: 패키지 원본 해시가 기준 해시와 다르거나 보고서에 없는 증거입니다'); END""",
        # 기준점은 그 증거의 관리 이력에 실제로 있는 이벤트(번호·해시)여야 함
        "package_items_check_snapshot": """
            CREATE TRIGGER package_items_check_snapshot BEFORE INSERT ON evidence_package_items
            WHEN NOT EXISTS (SELECT 1 FROM chain_of_custody WHERE evidence_id = NEW.evidence_id
                             AND sequence_no = NEW.custody_snapshot_sequence_no
                             AND event_hash = NEW.custody_snapshot_event_hash)
            BEGIN SELECT RAISE(ABORT, 'INVALID: 관리 이력 기준점이 그 증거의 이벤트와 맞지 않습니다'); END""",
        # 검증 ID는 그 증거의 검증 결과이고 상태도 같아야 함. 검증한 적 없으면 ID는 NULL, 상태는 NOT_VERIFIED
        "package_items_check_verification": """
            CREATE TRIGGER package_items_check_verification BEFORE INSERT ON evidence_package_items
            WHEN (NEW.verification_id IS NULL AND NEW.verification_status IS NOT 'NOT_VERIFIED')
              OR (NEW.verification_id IS NOT NULL AND NOT EXISTS (
                  SELECT 1 FROM verifications WHERE id = NEW.verification_id AND evidence_id = NEW.evidence_id
                  AND status = NEW.verification_status))
            BEGIN SELECT RAISE(ABORT, 'INVALID: 검증 ID·상태가 그 증거의 검증 결과와 맞지 않습니다'); END""",
    }
    for name, statement in triggers.items():
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute(statement)


# (버전 번호, 마이그레이션 함수). 번호는 1부터 빈칸 없이 올라갑니다.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_1_base_schema),
    (2, _migration_2_immutable_records),
    (3, _migration_3_chain_of_custody),
    (4, _migration_4_sharing),
    (5, _migration_5_encryption),
    (6, _migration_6_reports),
    (7, _migration_7_packages),
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
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
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
    (root / "reports").mkdir(exist_ok=True)  # 7단계: 보고서 PDF 폴더
    (root / "packages").mkdir(exist_ok=True)  # 8단계: 증거 패키지 ZIP 폴더
    # 6단계: 키를 먼저 읽습니다. 키가 없거나 설정이 틀리면 KeyConfigurationError로 여기서 멈추고 DB는 건드리지 않습니다.
    # 키 소스: EVIDENCE_MASTER_KEYS_FILE(시연·운영) / EVIDENCE_MASTER_KEYS / EVIDENCE_MASTER_KEY 중 하나,
    # 또는 개발 때만 EVIDENCE_ALLOW_DEV_KEY_GENERATION=true (root/keys/에 개발용 키 생성)
    crypto = EvidenceCrypto.from_storage(root)
    storage = Storage(root=root, files_dir=files_dir, db_path=root / "evidence.sqlite3", crypto=crypto)
    migrate(storage)
    return storage


def get_storage(request: Request) -> Storage:
    """FastAPI 의존성: 이 요청을 처리하는 서버(app)의 저장 위치를 꺼냅니다."""
    return request.app.state.storage
