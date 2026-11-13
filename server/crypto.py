"""Streaming encryption and signing helpers for stored evidence files."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALGORITHM = "AES-256-GCM"
KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
TAG_BYTES = 16
KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


class CryptoError(RuntimeError):
    """Base class for failures exposed by the crypto interface."""


class AuthenticationFailedError(CryptoError):
    """Raised when AES-GCM authentication detects changed data or metadata."""


class KeyUnavailableError(CryptoError):
    """Raised when a referenced encryption key cannot be used."""


class InvalidCryptoInputError(CryptoError):
    """Raised when an algorithm, metadata value, or byte chunk is invalid."""


class CryptoInternalError(CryptoError):
    """Raised for an unexpected failure inside the cryptographic operation."""


class CryptoConfigurationError(CryptoError):
    """Base class for server-side cryptographic configuration failures."""


class KeyConfigurationError(CryptoConfigurationError):
    """Raised when the server's key source or active key setting is invalid."""


class InvalidEncryptionMetadataError(InvalidCryptoInputError):
    """Raised when persisted algorithm, key ID, salt, nonce, tag, or AAD metadata is invalid."""


@dataclass(frozen=True)
class EncryptionMetadata:
    algorithm: str
    key_version: str
    salt: str
    nonce: str
    tag: str
    aad_version: str = ""


@dataclass(frozen=True)
class EncryptionResult:
    """Metadata and byte counts returned after one encryption completes."""

    metadata: EncryptionMetadata
    plaintext_size: int
    stored_size: int


def _bytes_chunk(value: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise InvalidCryptoInputError("암호화 입력 조각은 바이트 형식이어야 합니다.")
    return bytes(value)


class Sha256Hasher:
    """Incremental SHA-256 calculator for chunks supplied by the caller."""

    def __init__(self):
        self._digest = hashlib.sha256()

    def update(self, chunk: bytes | bytearray | memoryview) -> None:
        self._digest.update(_bytes_chunk(chunk))

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def sha256_chunks(chunks: Iterable[bytes | bytearray | memoryview]) -> str:
    """Return the lowercase 64-character SHA-256 of unchanged input bytes."""
    digest = Sha256Hasher()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def _validate_key_version(value: str) -> str:
    value = value.strip()
    if not KEY_VERSION_PATTERN.fullmatch(value):
        raise KeyConfigurationError("키 버전은 영문, 숫자, 점, 밑줄, 하이픈으로 1~32자여야 합니다.")
    return value


def _decode_master_key(value: str) -> bytes:
    value = value.strip()
    try:
        if len(value) == KEY_BYTES * 2:
            key = bytes.fromhex(value)
        else:
            key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as error:
        raise KeyConfigurationError("마스터 키 형식이 올바르지 않습니다.") from error
    if len(key) != KEY_BYTES:
        raise KeyConfigurationError("마스터 키는 정확히 32바이트여야 합니다.")
    return key


def _decode_keyring(value: str, active_version: str) -> dict[str, bytes]:
    try:
        values = json.loads(value)
    except json.JSONDecodeError as error:
        raise KeyConfigurationError("마스터 키링은 JSON 객체여야 합니다.") from error
    if not isinstance(values, dict) or not values:
        raise KeyConfigurationError("마스터 키링에는 한 개 이상의 키가 필요합니다.")
    keys = {_validate_key_version(str(version)): _decode_master_key(str(key)) for version, key in values.items()}
    if active_version not in keys:
        raise KeyConfigurationError(f"활성 키 버전 {active_version!r}이 마스터 키링에 없습니다.")
    return keys


def _load_environment_keys(active_version: str) -> dict[str, bytes] | None:
    configured_keyring = os.environ.get("EVIDENCE_MASTER_KEYS")
    configured_keyring_file = os.environ.get("EVIDENCE_MASTER_KEYS_FILE")
    configured_key = os.environ.get("EVIDENCE_MASTER_KEY")
    configured_sources = sum(bool(value and value.strip()) for value in (
        configured_keyring, configured_keyring_file, configured_key
    ))
    if configured_sources > 1:
        raise KeyConfigurationError("암호화 키 소스는 환경변수, secret 파일, 단일 키 중 하나만 설정해야 합니다.")

    if configured_keyring_file:
        try:
            keyring_text = Path(configured_keyring_file).read_text(encoding="utf-8")
        except OSError as error:
            raise KeyConfigurationError("EVIDENCE_MASTER_KEYS_FILE을 읽을 수 없습니다.") from error
        return _decode_keyring(keyring_text, active_version)

    if configured_keyring:
        return _decode_keyring(configured_keyring, active_version)
    if configured_key:
        return {active_version: _decode_master_key(configured_key)}
    return None


def _development_key_generation_enabled() -> bool:
    value = os.environ.get("EVIDENCE_ALLOW_DEV_KEY_GENERATION", "false").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise KeyConfigurationError("EVIDENCE_ALLOW_DEV_KEY_GENERATION은 true 또는 false여야 합니다.")


def _load_local_keys(storage: Path, active_version: str) -> dict[str, bytes]:
    keys_dir = storage / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    active_path = keys_dir / f"master-{active_version}.key"
    try:
        descriptor = os.open(active_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as output:
            output.write(secrets.token_bytes(KEY_BYTES))
            output.flush()
            os.fsync(output.fileno())

    keys: dict[str, bytes] = {}
    for key_path in keys_dir.glob("master-*.key"):
        version = key_path.stem.removeprefix("master-")
        if not KEY_VERSION_PATTERN.fullmatch(version):
            continue
        key = key_path.read_bytes()
        if len(key) != KEY_BYTES:
            raise KeyConfigurationError(f"로컬 개발 키가 손상되었습니다: {key_path}")
        with suppress(OSError):
            key_path.chmod(0o600)
        keys[version] = key
    if active_version not in keys:
        raise KeyConfigurationError(f"활성 로컬 개발 키를 읽을 수 없습니다: {active_path}")
    return keys


class EncryptionSession:
    """Stateful streaming encryptor; create a new instance for each file."""

    def __init__(self, owner: EvidenceCrypto, aad: bytes):
        self._owner = owner
        self._encryptor, self._salt, self._nonce = owner._new_encryptor(aad)
        self._plaintext_size = 0
        self._stored_size = 0
        self._finalized = False

    def update(self, chunk: bytes | bytearray | memoryview) -> bytes:
        if self._finalized:
            raise InvalidCryptoInputError("이미 종료된 암호화 작업에는 입력을 추가할 수 없습니다.")
        plain = _bytes_chunk(chunk)
        try:
            encrypted = self._encryptor.update(plain)
        except Exception as error:
            raise CryptoInternalError("암호화 처리 중 내부 오류가 발생했습니다.") from error
        self._plaintext_size += len(plain)
        self._stored_size += len(encrypted)
        return encrypted

    def finalize(self, aad_version: str = "") -> tuple[bytes, EncryptionResult]:
        if self._finalized:
            raise InvalidCryptoInputError("암호화 작업은 한 번만 종료할 수 있습니다.")
        self._finalized = True
        try:
            final = self._encryptor.finalize()
            metadata = self._owner._metadata(self._salt, self._nonce, self._encryptor.tag, aad_version)
        except CryptoError:
            raise
        except Exception as error:
            raise CryptoInternalError("암호화 종료 중 내부 오류가 발생했습니다.") from error
        self._stored_size += len(final)
        return final, EncryptionResult(metadata, self._plaintext_size, self._stored_size)


class EvidenceCrypto:
    """Manage versioned keys and encrypt/decrypt file streams with AES-GCM."""

    def __init__(self, master_keys: dict[str, bytes], key_version: str = "v1"):
        self.key_version = _validate_key_version(key_version)
        if self.key_version not in master_keys:
            raise KeyConfigurationError(f"활성 키 버전 {self.key_version!r}이 키링에 없습니다.")
        if any(len(key) != KEY_BYTES for key in master_keys.values()):
            raise KeyConfigurationError("모든 마스터 키는 정확히 32바이트여야 합니다.")
        self._master_keys = dict(master_keys)

    @classmethod
    def from_storage(cls, storage: Path) -> EvidenceCrypto:
        version = _validate_key_version(os.environ.get("EVIDENCE_KEY_VERSION", "v1"))
        keys = _load_environment_keys(version)
        if keys is None:
            if not _development_key_generation_enabled():
                raise KeyConfigurationError(
                    "암호화 키가 설정되지 않았습니다. 운영·시연 환경에서는 secret 키링을 주입하고, "
                    "로컬 개발에서만 EVIDENCE_ALLOW_DEV_KEY_GENERATION=true를 사용하세요."
                )
            keys = _load_local_keys(storage, version)
        return cls(keys, version)

    @property
    def available_key_versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._master_keys))

    def _master_key(self, key_version: str) -> bytes:
        try:
            return self._master_keys[key_version]
        except KeyError as error:
            raise KeyUnavailableError(f"암호화 키 버전 {key_version!r}을 사용할 수 없습니다.") from error

    def _derive_key(self, salt: bytes, key_version: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=salt,
            info=b"EVIDENCE:file-encryption:" + key_version.encode("utf-8"),
        ).derive(self._master_key(key_version))

    def _new_encryptor(self, aad: bytes = b""):
        salt = secrets.token_bytes(SALT_BYTES)
        nonce = secrets.token_bytes(NONCE_BYTES)
        key = self._derive_key(salt, self.key_version)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        if aad:
            encryptor.authenticate_additional_data(aad)
        return encryptor, salt, nonce

    def start_encryption(self, aad: bytes = b"") -> EncryptionSession:
        """Start one streaming encryption operation using a fresh salt and nonce."""
        return EncryptionSession(self, _bytes_chunk(aad))

    def _metadata(self, salt: bytes, nonce: bytes, tag: bytes, aad_version: str = "") -> EncryptionMetadata:
        if len(tag) != TAG_BYTES:
            raise CryptoInternalError("AES-GCM 인증 태그 길이가 올바르지 않습니다.")
        return EncryptionMetadata(
            algorithm=ALGORITHM,
            key_version=self.key_version,
            salt=base64.urlsafe_b64encode(salt).decode("ascii"),
            nonce=base64.urlsafe_b64encode(nonce).decode("ascii"),
            tag=base64.urlsafe_b64encode(tag).decode("ascii"),
            aad_version=aad_version,
        )

    def _decryptor(self, metadata: EncryptionMetadata, aad: bytes = b""):
        if metadata.algorithm != ALGORITHM:
            raise InvalidEncryptionMetadataError(f"지원하지 않는 암호화 방식입니다: {metadata.algorithm}")
        if not KEY_VERSION_PATTERN.fullmatch(metadata.key_version):
            raise InvalidEncryptionMetadataError("저장된 키 참조값 형식이 올바르지 않습니다.")
        try:
            salt = base64.b64decode(metadata.salt, altchars=b"-_", validate=True)
            nonce = base64.b64decode(metadata.nonce, altchars=b"-_", validate=True)
            tag = base64.b64decode(metadata.tag, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as error:
            raise InvalidEncryptionMetadataError("저장된 암호화 메타데이터가 올바르지 않습니다.") from error
        if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES or len(tag) != TAG_BYTES:
            raise InvalidEncryptionMetadataError("저장된 암호화 메타데이터 길이가 올바르지 않습니다.")
        key = self._derive_key(salt, metadata.key_version)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        if aad:
            decryptor.authenticate_additional_data(aad)
        return decryptor

    def decrypt_verified(
        self,
        chunks: Iterable[bytes | bytearray | memoryview],
        metadata: EncryptionMetadata,
        aad: bytes = b"",
        spool_limit: int = 8 * 1024 * 1024,
    ) -> BinaryIO:
        """Return seekable plaintext only after AES-GCM authentication succeeds.

        The caller owns the returned temporary stream and must close it. Input
        iteration and persistent file I/O remain the caller's responsibility.
        """
        if spool_limit < 0:
            raise InvalidCryptoInputError("임시 메모리 제한은 0 이상이어야 합니다.")
        plain = tempfile.SpooledTemporaryFile(max_size=spool_limit, mode="w+b")
        try:
            decryptor = self._decryptor(metadata, _bytes_chunk(aad))
            for chunk in chunks:
                decrypted = decryptor.update(_bytes_chunk(chunk))
                if decrypted:
                    plain.write(decrypted)
            final = decryptor.finalize()
            if final:
                plain.write(final)
            plain.seek(0)
            return plain
        except InvalidTag as error:
            plain.close()
            raise AuthenticationFailedError("암호문 또는 인증 정보가 일치하지 않습니다.") from error
        except (CryptoError, OSError):
            plain.close()
            raise
        except Exception as error:
            plain.close()
            raise CryptoInternalError("복호화 처리 중 내부 오류가 발생했습니다.") from error

    def sign(self, purpose: str, payload: bytes, key_version: str | None = None) -> tuple[str, str]:
        """Return a versioned HMAC for append-only metadata such as audit entries."""
        version = key_version or self.key_version
        signing_key = HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=None,
            info=b"EVIDENCE:service-signing:" + purpose.encode("utf-8") + b":" + version.encode("utf-8"),
        ).derive(self._master_key(version))
        return version, hmac.new(signing_key, payload, hashlib.sha256).hexdigest()

    def signature_matches(self, purpose: str, payload: bytes, key_version: str, signature: str) -> bool:
        _, expected = self.sign(purpose, payload, key_version)
        return hmac.compare_digest(expected, signature)
