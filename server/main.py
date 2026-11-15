"""Same-origin API and static app for the EVIDENCE capstone prototype."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .crypto import (
    ALGORITHM,
    CryptoConfigurationError,
    EncryptionMetadata,
    EvidenceCrypto,
)

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_SIZE = 50 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
SNIFF_SIZE = 8192
AAD_VERSION = "v1"
AUDIT_EMPTY_HASH = "0" * 64
ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".txt", ".csv",
    ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".mp4", ".mov", ".webm", ".mp3", ".wav", ".m4a",
}
STATUS_LABELS = {
    "NOT_VERIFIED": "미검증", "MATCH": "일치", "MISMATCH": "불일치", "UNAVAILABLE": "검증 불가"
}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def normalized_client_time(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if len(value) > 64:
        raise HTTPException(422, "파일 선택 시각 형식이 올바르지 않습니다.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(422, "파일 선택 시각 형식이 올바르지 않습니다.") from error
    if parsed.tzinfo is None:
        raise HTTPException(422, "파일 선택 시각에는 시간대가 필요합니다.")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds")


def evidence_aad(fields: dict[str, str]) -> bytes:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def file_signature_matches(extension: str, header: bytes, file_object) -> bool:
    """Validate common evidence formats without trusting the browser MIME header."""
    if extension == ".pdf":
        return header.startswith(b"%PDF-")
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if extension == ".wav":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    if extension in {".mp4", ".mov", ".m4a"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if extension == ".webm":
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".mp3":
        return header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)
    if extension in {".doc", ".xls"}:
        return header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if extension in {".zip", ".docx", ".xlsx"}:
        try:
            file_object.seek(0)
            with zipfile.ZipFile(file_object) as archive:
                names = set(archive.namelist())
            if extension == ".docx":
                return "[Content_Types].xml" in names and any(name.startswith("word/") for name in names)
            if extension == ".xlsx":
                return "[Content_Types].xml" in names and any(name.startswith("xl/") for name in names)
            return True
        except (OSError, zipfile.BadZipFile):
            return False
        finally:
            file_object.seek(0)
    if extension in {".txt", ".csv", ".html", ".htm"}:
        if b"\x00" in header:
            return False
        try:
            header.decode("utf-8")
        except UnicodeDecodeError:
            try:
                header.decode("cp949")
            except UnicodeDecodeError:
                return False
        return True
    return False


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        computed = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(computed, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


class Credentials(BaseModel):
    email: str
    password: str


class Registration(Credentials):
    name: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class ProfileUpdate(BaseModel):
    name: str


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    storage = Path(data_dir or os.environ.get("EVIDENCE_DATA_DIR", ROOT / "data")).resolve()
    files_dir = storage / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    db_path = storage / "evidence.sqlite3"
    evidence_crypto = EvidenceCrypto.from_storage(storage)

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    with closing(connect()) as connection, connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
              password_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
              identifier TEXT PRIMARY KEY, failures INTEGER NOT NULL,
              first_failed_at TEXT NOT NULL, blocked_until TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence (
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
              filename TEXT NOT NULL, stored_name TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
              mime TEXT NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
              description TEXT NOT NULL, memo TEXT NOT NULL, client_selected_at TEXT, registered_at TEXT NOT NULL,
              sha256 TEXT NOT NULL, verification_status TEXT NOT NULL DEFAULT 'NOT_VERIFIED',
              encryption_algorithm TEXT NOT NULL DEFAULT 'LEGACY', encryption_key_version TEXT,
              encryption_salt TEXT, encryption_nonce TEXT, encryption_tag TEXT,
              encryption_aad_version TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS evidence_owner_date ON evidence(owner_id, registered_at DESC);
            CREATE TABLE IF NOT EXISTS verifications (
              id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id), user_id TEXT NOT NULL REFERENCES users(id),
              started_at TEXT NOT NULL, completed_at TEXT NOT NULL, expected_hash TEXT NOT NULL,
              current_hash TEXT, status TEXT NOT NULL, reason TEXT NOT NULL,
              failure_code TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS verification_evidence_date ON verifications(evidence_id, completed_at DESC);
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
              action TEXT NOT NULL, evidence_id TEXT, created_at TEXT NOT NULL,
              previous_hash TEXT NOT NULL DEFAULT '', entry_hash TEXT NOT NULL DEFAULT '', key_version TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS audit_user_date ON audit(user_id, created_at DESC);
        """)
        evidence_columns = {row[1] for row in connection.execute("PRAGMA table_info(evidence)")}
        for name, definition in {
            "client_selected_at": "TEXT",
            "encryption_algorithm": "TEXT NOT NULL DEFAULT 'LEGACY'",
            "encryption_key_version": "TEXT",
            "encryption_salt": "TEXT",
            "encryption_nonce": "TEXT",
            "encryption_tag": "TEXT",
            "encryption_aad_version": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in evidence_columns:
                connection.execute(f"ALTER TABLE evidence ADD COLUMN {name} {definition}")
        verification_columns = {row[1] for row in connection.execute("PRAGMA table_info(verifications)")}
        if "failure_code" not in verification_columns:
            connection.execute("ALTER TABLE verifications ADD COLUMN failure_code TEXT NOT NULL DEFAULT ''")
        audit_columns = {row[1] for row in connection.execute("PRAGMA table_info(audit)")}
        for name in ("previous_hash", "entry_hash", "key_version"):
            if name not in audit_columns:
                connection.execute(f"ALTER TABLE audit ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")

    app = FastAPI(title="EVIDENCE API", version="0.1.0")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def audit_payload(user_id: str, action: str, evidence_id: str | None, created_at: str, previous_hash: str) -> bytes:
        return json.dumps({
            "userId": user_id,
            "action": action,
            "evidenceId": evidence_id,
            "createdAt": created_at,
            "previousHash": previous_hash,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def append_audit(
        connection: sqlite3.Connection, user_id: str, action: str,
        evidence_id: str | None = None, created_at: str | None = None,
    ) -> None:
        created_at = created_at or utc_now()
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT entry_hash FROM audit WHERE user_id = ? AND entry_hash <> '' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        previous_hash = previous["entry_hash"] if previous else AUDIT_EMPTY_HASH
        key_version, entry_hash = evidence_crypto.sign(
            "audit-log", audit_payload(user_id, action, evidence_id, created_at, previous_hash)
        )
        connection.execute(
            """INSERT INTO audit(user_id, action, evidence_id, created_at, previous_hash, entry_hash, key_version)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, action, evidence_id, created_at, previous_hash, entry_hash, key_version),
        )

    def audit_integrity(connection: sqlite3.Connection, user_id: str) -> dict[int, str]:
        statuses: dict[int, str] = {}
        expected_previous = AUDIT_EMPTY_HASH
        rows = connection.execute("SELECT * FROM audit WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        for row in rows:
            if not row["entry_hash"]:
                statuses[row["id"]] = "LEGACY"
                continue
            try:
                valid = row["previous_hash"] == expected_previous and evidence_crypto.signature_matches(
                    "audit-log",
                    audit_payload(row["user_id"], row["action"], row["evidence_id"], row["created_at"], row["previous_hash"]),
                    row["key_version"],
                    row["entry_hash"],
                )
            except CryptoConfigurationError:
                statuses[row["id"]] = "KEY_UNAVAILABLE"
            else:
                statuses[row["id"]] = "VALID" if valid else "INVALID"
            expected_previous = row["entry_hash"]
        return statuses

    def issue_session(connection: sqlite3.Connection, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(UTC) + timedelta(hours=8)).isoformat(timespec="milliseconds")
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now(),))
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
        return token

    def login_identifier(request: Request, email: str) -> str:
        client = request.client.host if request.client else "unknown"
        return hashlib.sha256(f"{email}|{client}".encode()).hexdigest()

    def reject_if_login_blocked(connection: sqlite3.Connection, identifier: str) -> None:
        row = connection.execute("SELECT blocked_until FROM login_attempts WHERE identifier = ?", (identifier,)).fetchone()
        if row and row["blocked_until"] and row["blocked_until"] > utc_now():
            raise HTTPException(429, "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.")

    def record_login_failure(connection: sqlite3.Connection, identifier: str) -> None:
        now = datetime.now(UTC)
        row = connection.execute("SELECT * FROM login_attempts WHERE identifier = ?", (identifier,)).fetchone()
        if row is None or datetime.fromisoformat(row["first_failed_at"]) < now - timedelta(minutes=10):
            failures = 1
            first_failed_at = now.isoformat(timespec="milliseconds")
        else:
            failures = row["failures"] + 1
            first_failed_at = row["first_failed_at"]
        blocked_until = (now + timedelta(minutes=15)).isoformat(timespec="milliseconds") if failures >= 5 else None
        connection.execute(
            """INSERT INTO login_attempts(identifier, failures, first_failed_at, blocked_until)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(identifier) DO UPDATE SET failures=excluded.failures,
               first_failed_at=excluded.first_failed_at, blocked_until=excluded.blocked_until""",
            (identifier, failures, first_failed_at, blocked_until),
        )

    def current_user(authorization: str | None = Header(default=None)) -> sqlite3.Row:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "로그인이 필요합니다.")
        token = authorization[7:].strip()
        if not token:
            raise HTTPException(401, "로그인이 필요합니다.")
        with closing(connect()) as connection, connection:
            user = connection.execute("""
                SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """, (hashlib.sha256(token.encode()).hexdigest(), utc_now())).fetchone()
        if user is None:
            raise HTTPException(401, "로그인이 만료되었습니다. 다시 로그인해 주세요.")
        return user

    def owned_evidence(connection: sqlite3.Connection, user_id: str, evidence_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM evidence WHERE id = ? AND owner_id = ?", (evidence_id, user_id)).fetchone()
        if row is None:
            raise HTTPException(404, "증거를 찾을 수 없습니다.")
        return row

    def verification_dict(row: sqlite3.Row, filename: str = "") -> dict:
        return {
            "id": row["id"], "evidenceId": row["evidence_id"], "startedAt": row["started_at"],
            "completedAt": row["completed_at"], "expectedHash": row["expected_hash"],
            "currentHash": row["current_hash"], "status": row["status"], "reason": row["reason"],
            "failureCode": row["failure_code"],
            "filename": filename,
        }

    def encryption_metadata(row: sqlite3.Row) -> EncryptionMetadata | None:
        if row["encryption_algorithm"] == "LEGACY":
            return None
        values = (row["encryption_key_version"], row["encryption_salt"], row["encryption_nonce"], row["encryption_tag"])
        if not all(values):
            raise CryptoConfigurationError("증거의 암호화 메타데이터가 누락되었습니다.")
        return EncryptionMetadata(row["encryption_algorithm"], *values, row["encryption_aad_version"])

    def aad_for_row(row: sqlite3.Row) -> bytes:
        version = row["encryption_aad_version"]
        if not version:
            return b""
        if version != AAD_VERSION:
            raise CryptoConfigurationError(f"지원하지 않는 AAD 버전입니다: {version}")
        return evidence_aad({
            "schema": version,
            "evidenceId": row["id"],
            "ownerId": row["owner_id"],
            "name": row["name"],
            "filename": row["filename"],
            "kind": row["kind"],
            "source": row["source"],
            "registeredAt": row["registered_at"],
            "keyVersion": row["encryption_key_version"],
        })

    def decrypted_chunks(target: Path, row: sqlite3.Row):
        metadata = encryption_metadata(row)
        with target.open("rb") as source:
            if metadata is None:
                while chunk := source.read(CHUNK_SIZE):
                    yield chunk
            else:
                yield from evidence_crypto.iter_decrypted(source, metadata, CHUNK_SIZE, aad_for_row(row))

    def evidence_dict(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        history = [verification_dict(item, row["filename"]) for item in connection.execute(
            "SELECT * FROM verifications WHERE evidence_id = ? ORDER BY completed_at DESC", (row["id"],)
        ).fetchall()]
        return {
            "id": row["id"], "name": row["name"], "filename": row["filename"],
            "type": row["type"], "mime": row["mime"], "size": row["size"],
            "kind": row["kind"], "source": row["source"], "description": row["description"], "memo": row["memo"],
            "selectedAt": row["client_selected_at"], "registeredAt": row["registered_at"],
            "date": row["registered_at"][:10], "sha256": row["sha256"],
            "verificationStatus": row["verification_status"], "status": STATUS_LABELS[row["verification_status"]],
            "lastVerification": history[0] if history else None, "verifications": history, "storage": "server",
            "encryption": {
                "algorithm": row["encryption_algorithm"],
                "keyVersion": row["encryption_key_version"],
                "keyDerivation": "HKDF-SHA256" if row["encryption_algorithm"] != "LEGACY" else None,
                "salt": row["encryption_salt"],
                "nonce": row["encryption_nonce"],
                "authenticationTag": row["encryption_tag"],
                "aadVersion": row["encryption_aad_version"] or None,
            },
        }

    @app.get("/api/health")
    def health():
        return {"backend": True, "maxFileSize": MAX_FILE_SIZE, "extensions": sorted(ALLOWED_EXTENSIONS),
                "evidenceEncryption": ALGORITHM, "keyVersion": evidence_crypto.key_version,
                "availableKeyVersions": evidence_crypto.available_key_versions}

    @app.post("/api/auth/register", status_code=201)
    def register(payload: Registration):
        email = payload.email.strip().lower()
        name = payload.name.strip()
        if not EMAIL_PATTERN.fullmatch(email) or not name or len(name) > 100 or not 8 <= len(payload.password) <= 128:
            raise HTTPException(422, "이름·이메일·비밀번호를 확인해 주세요.")
        user_id = uuid.uuid4().hex
        with closing(connect()) as connection, connection:
            try:
                connection.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                                   (user_id, email, name, password_hash(payload.password), utc_now()))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "이미 등록된 이메일입니다.") from None
            token = issue_session(connection, user_id)
            append_audit(connection, user_id, "register")
            append_audit(connection, user_id, "login")
        return {"token": token, "user": {"id": user_id, "email": email, "name": name}}

    @app.post("/api/auth/login")
    def login(payload: Credentials, request: Request):
        email = payload.email.strip().lower()
        identifier = login_identifier(request, email)
        with closing(connect()) as connection, connection:
            reject_if_login_blocked(connection, identifier)
            user = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            valid = password_matches(payload.password, user["password_hash"] if user else password_hash("invalid-password"))
            if not user or not valid:
                record_login_failure(connection, identifier)
                connection.commit()
                raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")
            connection.execute("DELETE FROM login_attempts WHERE identifier = ?", (identifier,))
            token = issue_session(connection, user["id"])
            append_audit(connection, user["id"], "login")
        return {"token": token, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}

    @app.post("/api/auth/logout")
    def logout(authorization: str | None = Header(default=None), user: sqlite3.Row = Depends(current_user)):
        token = authorization[7:].strip()
        with closing(connect()) as connection, connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
            append_audit(connection, user["id"], "logout")
        return {"ok": True}

    @app.get("/api/me")
    def me(user: sqlite3.Row = Depends(current_user)):
        return {"id": user["id"], "email": user["email"], "name": user["name"], "createdAt": user["created_at"]}

    @app.put("/api/me")
    def update_profile(payload: ProfileUpdate, user: sqlite3.Row = Depends(current_user)):
        name = payload.name.strip()
        if not name or len(name) > 100:
            raise HTTPException(422, "이름은 1~100자로 입력해 주세요.")
        with closing(connect()) as connection, connection:
            connection.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
            append_audit(connection, user["id"], "profile_update")
        return {"id": user["id"], "email": user["email"], "name": name, "createdAt": user["created_at"]}

    @app.put("/api/auth/password")
    def change_password(
        payload: PasswordChange, authorization: str | None = Header(default=None),
        user: sqlite3.Row = Depends(current_user),
    ):
        if not password_matches(payload.current_password, user["password_hash"]):
            raise HTTPException(403, "현재 비밀번호가 올바르지 않습니다.")
        if not 8 <= len(payload.new_password) <= 128:
            raise HTTPException(422, "새 비밀번호는 8자 이상이어야 합니다.")
        with closing(connect()) as connection, connection:
            connection.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                               (password_hash(payload.new_password), user["id"]))
            current_token_hash = hashlib.sha256(authorization[7:].strip().encode()).hexdigest()
            connection.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?", (user["id"], current_token_hash)
            )
            append_audit(connection, user["id"], "password_change")
        return {"ok": True}

    @app.post("/api/evidence", status_code=201)
    async def upload_evidence(
        file: UploadFile = File(...), name: str = Form(""), kind: str = Form("파일"),
        source: str = Form("직접 수집"), description: str = Form(""), memo: str = Form(""),
        client_selected_at: str = Form(""),
        user: sqlite3.Row = Depends(current_user),
    ):
        filename = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
        if not filename or any(ord(char) < 32 for char in filename) or len(filename) > 255:
            raise HTTPException(422, "파일명을 확인해 주세요.")
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, "허용되지 않은 파일 형식입니다.")
        if len(name) > 200 or len(kind) > 50 or len(source) > 200 or len(description) > 4000 or len(memo) > 4000:
            raise HTTPException(422, "입력 길이를 확인해 주세요.")
        header = await file.read(SNIFF_SIZE)
        await file.seek(0)
        if not header:
            await file.close()
            raise HTTPException(422, "빈 파일은 등록할 수 없습니다.")
        if not file_signature_matches(extension, header, file.file):
            await file.close()
            raise HTTPException(415, "파일 확장자와 실제 파일 형식이 일치하지 않습니다.")
        await file.seek(0)
        evidence_id = f"EV-{uuid.uuid4().hex}"
        stored_name = uuid.uuid4().hex
        target = files_dir / stored_name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        size = 0
        digest = hashlib.sha256()
        display_name = name.strip() or filename
        evidence_kind = kind.strip() or "파일"
        evidence_source = source.strip() or "직접 수집"
        registered_at = utc_now()
        selected_at = normalized_client_time(client_selected_at)
        aad = evidence_aad({
            "schema": AAD_VERSION,
            "evidenceId": evidence_id,
            "ownerId": user["id"],
            "name": display_name,
            "filename": filename,
            "kind": evidence_kind,
            "source": evidence_source,
            "registeredAt": registered_at,
            "keyVersion": evidence_crypto.key_version,
        })
        encryptor, salt, nonce = evidence_crypto.new_encryptor(aad)
        try:
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while chunk := await file.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > MAX_FILE_SIZE:
                        raise HTTPException(413, "파일은 50 MB 이하만 등록할 수 있습니다.")
                    digest.update(chunk)
                    encrypted = encryptor.update(chunk)
                    if encrypted:
                        output.write(encrypted)
                if size == 0:
                    raise HTTPException(422, "빈 파일은 등록할 수 없습니다.")
                final = encryptor.finalize()
                if final:
                    output.write(final)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(0o400)
            encryption = evidence_crypto.metadata(salt, nonce, encryptor.tag, AAD_VERSION)
            with closing(connect()) as connection, connection:
                connection.execute("""INSERT INTO evidence (
                    id, owner_id, name, filename, stored_name, type, mime, size, kind, source,
                    description, memo, client_selected_at, registered_at, sha256, verification_status,
                    encryption_algorithm, encryption_key_version, encryption_salt, encryption_nonce, encryption_tag,
                    encryption_aad_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                   (evidence_id, user["id"], display_name, filename, stored_name,
                                    extension[1:].upper(), file.content_type or "application/octet-stream", size,
                                    evidence_kind, evidence_source, description, memo, selected_at, registered_at,
                                    digest.hexdigest(), "NOT_VERIFIED",
                                    encryption.algorithm, encryption.key_version, encryption.salt,
                                    encryption.nonce, encryption.tag, encryption.aad_version))
                append_audit(connection, user["id"], "upload", evidence_id, registered_at)
                row = owned_evidence(connection, user["id"], evidence_id)
                return evidence_dict(connection, row)
        except Exception:
            if target.exists():
                target.chmod(0o600)
            target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/evidence")
    def list_evidence(user: sqlite3.Row = Depends(current_user)):
        with closing(connect()) as connection, connection:
            rows = connection.execute("SELECT * FROM evidence WHERE owner_id = ? ORDER BY registered_at DESC", (user["id"],)).fetchall()
            return [evidence_dict(connection, row) for row in rows]

    @app.get("/api/evidence/{evidence_id}")
    def get_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user)):
        with closing(connect()) as connection, connection:
            return evidence_dict(connection, owned_evidence(connection, user["id"], evidence_id))

    @app.get("/api/evidence/{evidence_id}/download")
    def download_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user)):
        with closing(connect()) as connection, connection:
            row = owned_evidence(connection, user["id"], evidence_id)
            if not re.fullmatch(r"[0-9a-f]{32}", row["stored_name"]):
                raise HTTPException(500, "저장 파일 경로가 유효하지 않습니다.")
            target = files_dir / row["stored_name"]
            if not target.is_file():
                raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
            plain = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
            try:
                for chunk in decrypted_chunks(target, row):
                    plain.write(chunk)
                plain.seek(0)
            except InvalidTag:
                plain.close()
                raise HTTPException(409, "암호화 파일의 인증 정보가 일치하지 않아 다운로드할 수 없습니다.") from None
            except CryptoConfigurationError as error:
                plain.close()
                raise HTTPException(500, str(error)) from error
            except OSError as error:
                plain.close()
                raise HTTPException(500, "저장된 파일을 안전하게 복호화하지 못했습니다.") from error
            append_audit(connection, user["id"], "download", evidence_id)
            encoded_name = quote(row["filename"], safe="")
            headers = {
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
                "Content-Length": str(row["size"]),
                "X-Content-Type-Options": "nosniff",
            }

            def plain_chunks():
                while chunk := plain.read(CHUNK_SIZE):
                    yield chunk

            return StreamingResponse(
                plain_chunks(),
                media_type="application/octet-stream",
                headers=headers,
                background=BackgroundTask(plain.close),
            )

    @app.post("/api/evidence/{evidence_id}/verify")
    def verify_evidence(evidence_id: str, user: sqlite3.Row = Depends(current_user)):
        started = utc_now()
        with closing(connect()) as connection, connection:
            row = owned_evidence(connection, user["id"], evidence_id)
            if not re.fullmatch(r"[0-9a-f]{32}", row["stored_name"]):
                raise HTTPException(500, "저장 파일 경로가 유효하지 않습니다.")
            target = files_dir / row["stored_name"]
            current_hash = None
            status = "UNAVAILABLE"
            reason = "저장된 파일을 읽을 수 없어 검증하지 못했습니다."
            failure_code = "FILE_UNAVAILABLE"
            try:
                digest = hashlib.sha256()
                for chunk in decrypted_chunks(target, row):
                    digest.update(chunk)
                current_hash = digest.hexdigest()
                status = "MATCH" if hmac.compare_digest(current_hash, row["sha256"]) else "MISMATCH"
                reason = "등록 당시 SHA-256과 일치합니다." if status == "MATCH" else "등록 당시 SHA-256과 일치하지 않습니다."
                failure_code = "" if status == "MATCH" else "HASH_MISMATCH"
            except InvalidTag:
                status = "MISMATCH"
                reason = "암호화 파일 또는 인증 태그가 변경되어 AES-GCM 인증에 실패했습니다."
                failure_code = "AUTHENTICATION_FAILED"
            except CryptoConfigurationError:
                reason = "암호화 메타데이터가 올바르지 않아 검증하지 못했습니다."
                failure_code = "ENCRYPTION_METADATA_INVALID"
            except OSError:
                pass
            completed = utc_now()
            result_id = f"VERIFY-{uuid.uuid4().hex}"
            connection.execute("""INSERT INTO verifications (
                id, evidence_id, user_id, started_at, completed_at, expected_hash,
                current_hash, status, reason, failure_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                               (result_id, evidence_id, user["id"], started, completed, row["sha256"],
                                current_hash, status, reason, failure_code))
            connection.execute("UPDATE evidence SET verification_status = ? WHERE id = ?", (status, evidence_id))
            append_audit(connection, user["id"], "verify:" + status, evidence_id, completed)
            return {"result": verification_dict(connection.execute("SELECT * FROM verifications WHERE id = ?", (result_id,)).fetchone(), row["filename"]), "saved": True}

    @app.get("/api/audit")
    def audit_log(user: sqlite3.Row = Depends(current_user)):
        with closing(connect()) as connection, connection:
            integrity = audit_integrity(connection, user["id"])
            rows = connection.execute("""SELECT audit.id, audit.action, audit.evidence_id, audit.created_at,
                evidence.name AS evidence_name, evidence.filename AS evidence_filename
                FROM audit LEFT JOIN evidence ON evidence.id = audit.evidence_id AND evidence.owner_id = audit.user_id
                WHERE audit.user_id = ? ORDER BY audit.id DESC LIMIT 100""", (user["id"],)).fetchall()
            return [{"action": row["action"], "evidenceId": row["evidence_id"],
                     "evidenceName": row["evidence_name"], "evidenceFilename": row["evidence_filename"],
                     "createdAt": row["created_at"], "chainStatus": integrity[row["id"]]} for row in rows]

    @app.get("/")
    def home():
        return RedirectResponse("/index.html")

    @app.get("/{file_path:path}", include_in_schema=False)
    def static_file(file_path: str):
        requested = Path(file_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise HTTPException(404)
        if len(requested.parts) == 1 and requested.suffix.lower() in {".html", ".css", ".js"}:
            target = ROOT / requested.name
        elif len(requested.parts) == 3 and requested.parts[0] == "assets" and requested.suffix.lower() in {".svg", ".ttf", ".txt"}:
            target = ROOT.joinpath(*requested.parts)
        else:
            raise HTTPException(404)
        if not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    return app


app = create_app()
