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
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALGORITHM = "AES-256-GCM"
KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
TAG_BYTES = 16
KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


class CryptoConfigurationError(RuntimeError):
    """Raised when encryption configuration or stored metadata is invalid."""


@dataclass(frozen=True)
class EncryptionMetadata:
    algorithm: str
    key_version: str
    salt: str
    nonce: str
    tag: str
    aad_version: str = ""


def _validate_key_version(value: str) -> str:
    value = value.strip()
    if not KEY_VERSION_PATTERN.fullmatch(value):
        raise CryptoConfigurationError("키 버전은 영문, 숫자, 점, 밑줄, 하이픈으로 1~32자여야 합니다.")
    return value


def _decode_master_key(value: str) -> bytes:
    value = value.strip()
    try:
        if len(value) == KEY_BYTES * 2:
            key = bytes.fromhex(value)
        else:
            key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as error:
        raise CryptoConfigurationError("마스터 키 형식이 올바르지 않습니다.") from error
    if len(key) != KEY_BYTES:
        raise CryptoConfigurationError("마스터 키는 정확히 32바이트여야 합니다.")
    return key


def _load_environment_keys(active_version: str) -> dict[str, bytes] | None:
    configured_keyring = os.environ.get("EVIDENCE_MASTER_KEYS")
    if configured_keyring:
        try:
            values = json.loads(configured_keyring)
        except json.JSONDecodeError as error:
            raise CryptoConfigurationError("EVIDENCE_MASTER_KEYS는 JSON 객체여야 합니다.") from error
        if not isinstance(values, dict) or not values:
            raise CryptoConfigurationError("EVIDENCE_MASTER_KEYS에는 한 개 이상의 키가 필요합니다.")
        keys = {_validate_key_version(str(version)): _decode_master_key(str(key)) for version, key in values.items()}
        if active_version not in keys:
            raise CryptoConfigurationError(f"활성 키 버전 {active_version!r}이 EVIDENCE_MASTER_KEYS에 없습니다.")
        return keys

    configured_key = os.environ.get("EVIDENCE_MASTER_KEY")
    if configured_key:
        return {active_version: _decode_master_key(configured_key)}
    return None


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
            raise CryptoConfigurationError(f"로컬 마스터 키가 손상되었습니다: {key_path}")
        with suppress(OSError):
            key_path.chmod(0o600)
        keys[version] = key
    if active_version not in keys:
        raise CryptoConfigurationError(f"활성 로컬 키를 읽을 수 없습니다: {active_path}")
    return keys


class EvidenceCrypto:
    """Manage versioned keys and encrypt/decrypt file streams with AES-GCM."""

    def __init__(self, master_keys: dict[str, bytes], key_version: str = "v1"):
        self.key_version = _validate_key_version(key_version)
        if self.key_version not in master_keys:
            raise CryptoConfigurationError(f"활성 키 버전 {self.key_version!r}이 키링에 없습니다.")
        if any(len(key) != KEY_BYTES for key in master_keys.values()):
            raise CryptoConfigurationError("모든 마스터 키는 정확히 32바이트여야 합니다.")
        self._master_keys = dict(master_keys)

    @classmethod
    def from_storage(cls, storage: Path) -> EvidenceCrypto:
        version = _validate_key_version(os.environ.get("EVIDENCE_KEY_VERSION", "v1"))
        keys = _load_environment_keys(version) or _load_local_keys(storage, version)
        return cls(keys, version)

    @property
    def available_key_versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._master_keys))

    def _master_key(self, key_version: str) -> bytes:
        try:
            return self._master_keys[key_version]
        except KeyError as error:
            raise CryptoConfigurationError(f"암호화 키 버전 {key_version!r}을 사용할 수 없습니다.") from error

    def _derive_key(self, salt: bytes, key_version: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=salt,
            info=b"EVIDENCE:file-encryption:" + key_version.encode("utf-8"),
        ).derive(self._master_key(key_version))

    def new_encryptor(self, aad: bytes = b""):
        salt = secrets.token_bytes(SALT_BYTES)
        nonce = secrets.token_bytes(NONCE_BYTES)
        key = self._derive_key(salt, self.key_version)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        if aad:
            encryptor.authenticate_additional_data(aad)
        return encryptor, salt, nonce

    def metadata(self, salt: bytes, nonce: bytes, tag: bytes, aad_version: str = "") -> EncryptionMetadata:
        if len(tag) != TAG_BYTES:
            raise CryptoConfigurationError("AES-GCM 인증 태그 길이가 올바르지 않습니다.")
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
            raise CryptoConfigurationError(f"지원하지 않는 암호화 방식입니다: {metadata.algorithm}")
        try:
            salt = base64.b64decode(metadata.salt, altchars=b"-_", validate=True)
            nonce = base64.b64decode(metadata.nonce, altchars=b"-_", validate=True)
            tag = base64.b64decode(metadata.tag, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as error:
            raise CryptoConfigurationError("저장된 암호화 메타데이터가 올바르지 않습니다.") from error
        if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES or len(tag) != TAG_BYTES:
            raise CryptoConfigurationError("저장된 암호화 메타데이터 길이가 올바르지 않습니다.")
        key = self._derive_key(salt, metadata.key_version)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        if aad:
            decryptor.authenticate_additional_data(aad)
        return decryptor

    def iter_decrypted(
        self, source: BinaryIO, metadata: EncryptionMetadata, chunk_size: int, aad: bytes = b""
    ) -> Iterator[bytes]:
        decryptor = self._decryptor(metadata, aad)
        while chunk := source.read(chunk_size):
            plain = decryptor.update(chunk)
            if plain:
                yield plain
        final = decryptor.finalize()
        if final:
            yield final

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
