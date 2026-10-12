"""Same-origin API and static app for the EVIDENCE capstone prototype."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_SIZE = 50 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
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
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    storage = Path(data_dir or os.environ.get("EVIDENCE_DATA_DIR", ROOT / "data")).resolve()
    files_dir = storage / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    db_path = storage / "evidence.sqlite3"

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

    app = FastAPI(title="EVIDENCE API", version="0.1.0")

    def issue_session(connection: sqlite3.Connection, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(timespec="milliseconds")
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
        return token

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
            "filename": filename,
        }

    def evidence_dict(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        history = [verification_dict(item, row["filename"]) for item in connection.execute(
            "SELECT * FROM verifications WHERE evidence_id = ? ORDER BY completed_at DESC", (row["id"],)
        ).fetchall()]
        return {
            "id": row["id"], "name": row["name"], "filename": row["filename"],
            "type": row["type"], "mime": row["mime"], "size": row["size"],
            "kind": row["kind"], "source": row["source"], "description": row["description"], "memo": row["memo"],
            "selectedAt": row["registered_at"], "registeredAt": row["registered_at"],
            "date": row["registered_at"][:10], "sha256": row["sha256"],
            "verificationStatus": row["verification_status"], "status": STATUS_LABELS[row["verification_status"]],
            "lastVerification": history[0] if history else None, "verifications": history, "storage": "server",
        }

    @app.get("/api/health")
    def health():
        return {"backend": True, "maxFileSize": MAX_FILE_SIZE, "extensions": sorted(ALLOWED_EXTENSIONS)}

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
            connection.execute("INSERT INTO audit(user_id, action, created_at) VALUES (?, ?, ?)", (user_id, "register", utc_now()))
        return {"token": token, "user": {"id": user_id, "email": email, "name": name}}

    @app.post("/api/auth/login")
    def login(payload: Credentials):
        with closing(connect()) as connection, connection:
            user = connection.execute("SELECT * FROM users WHERE email = ?", (payload.email.strip().lower(),)).fetchone()
            valid = password_matches(payload.password, user["password_hash"] if user else password_hash("invalid-password"))
            if not user or not valid:
                raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")
            token = issue_session(connection, user["id"])
            connection.execute("INSERT INTO audit(user_id, action, created_at) VALUES (?, ?, ?)", (user["id"], "login", utc_now()))
        return {"token": token, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}

    @app.post("/api/auth/logout")
    def logout(authorization: str | None = Header(default=None), user: sqlite3.Row = Depends(current_user)):
        token = authorization[7:].strip()
        with closing(connect()) as connection, connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
            connection.execute("INSERT INTO audit(user_id, action, created_at) VALUES (?, ?, ?)", (user["id"], "logout", utc_now()))
        return {"ok": True}

    @app.get("/api/me")
    def me(user: sqlite3.Row = Depends(current_user)):
        return {"id": user["id"], "email": user["email"], "name": user["name"]}

    @app.put("/api/auth/password")
    def change_password(payload: PasswordChange, user: sqlite3.Row = Depends(current_user)):
        if not password_matches(payload.current_password, user["password_hash"]):
            raise HTTPException(403, "현재 비밀번호가 올바르지 않습니다.")
        if not 8 <= len(payload.new_password) <= 128:
            raise HTTPException(422, "새 비밀번호는 8자 이상이어야 합니다.")
        with closing(connect()) as connection, connection:
            connection.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                               (password_hash(payload.new_password), user["id"]))
            connection.execute("INSERT INTO audit(user_id, action, created_at) VALUES (?, ?, ?)",
                               (user["id"], "password_change", utc_now()))
        return {"ok": True}

    @app.post("/api/evidence", status_code=201)
    async def upload_evidence(
        file: UploadFile = File(...), name: str = Form(""), kind: str = Form("파일"),
        source: str = Form("직접 수집"), description: str = Form(""), memo: str = Form(""),
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
        evidence_id = f"EV-{uuid.uuid4().hex}"
        stored_name = uuid.uuid4().hex
        target = files_dir / stored_name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        size = 0
        digest = hashlib.sha256()
        try:
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while chunk := await file.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > MAX_FILE_SIZE:
                        raise HTTPException(413, "파일은 50 MB 이하만 등록할 수 있습니다.")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(0o400)
            now = utc_now()
            with closing(connect()) as connection, connection:
                connection.execute("""INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                   (evidence_id, user["id"], name.strip() or filename, filename, stored_name,
                                    extension[1:].upper(), file.content_type or "application/octet-stream", size,
                                    kind, source, description, memo, now, digest.hexdigest(), "NOT_VERIFIED"))
                connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                                   (user["id"], "upload", evidence_id, now))
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
            connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                               (user["id"], "download", evidence_id, utc_now()))
            return FileResponse(target, media_type="application/octet-stream", filename=row["filename"])

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
            try:
                digest = hashlib.sha256()
                with target.open("rb") as source:
                    while chunk := source.read(CHUNK_SIZE):
                        digest.update(chunk)
                current_hash = digest.hexdigest()
                status = "MATCH" if hmac.compare_digest(current_hash, row["sha256"]) else "MISMATCH"
                reason = "등록 당시 SHA-256과 일치합니다." if status == "MATCH" else "등록 당시 SHA-256과 일치하지 않습니다."
            except OSError:
                pass
            completed = utc_now()
            result_id = f"VERIFY-{uuid.uuid4().hex}"
            connection.execute("""INSERT INTO verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                               (result_id, evidence_id, user["id"], started, completed, row["sha256"], current_hash, status, reason))
            connection.execute("UPDATE evidence SET verification_status = ? WHERE id = ?", (status, evidence_id))
            connection.execute("INSERT INTO audit(user_id, action, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                               (user["id"], "verify:" + status, evidence_id, completed))
            return {"result": verification_dict(connection.execute("SELECT * FROM verifications WHERE id = ?", (result_id,)).fetchone(), row["filename"]), "saved": True}

    @app.get("/api/audit")
    def audit_log(user: sqlite3.Row = Depends(current_user)):
        with closing(connect()) as connection, connection:
            rows = connection.execute("SELECT action, evidence_id, created_at FROM audit WHERE user_id = ? ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
            return [{"action": row["action"], "evidenceId": row["evidence_id"], "createdAt": row["created_at"]} for row in rows]

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
