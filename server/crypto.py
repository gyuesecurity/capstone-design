"""Streaming encryption helpers for stored evidence files."""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


ALGORITHM = "AES-256-GCM"
KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
TAG_BYTES = 16


class CryptoConfigurationError(RuntimeError):
    """Raised when encryption configuration or stored metadata is invalid."""


@dataclass(frozen=True)
class EncryptionMetadata:
    algorithm: str
    key_version: str
    salt: str
    nonce: str
    tag: str


def _decode_master_key(value: str) -> bytes:
    value = value.strip()
    try:
        if len(value) == KEY_BYTES * 2:
            key = bytes.fromhex(value)
        else:
            key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as error:
        raise CryptoConfigurationError("EVIDENCE_MASTER_KEY 형식이 올바르지 않습니다.") from error
    if len(key) != KEY_BYTES:
        raise CryptoConfigurationError("EVIDENCE_MASTER_KEY는 정확히 32바이트여야 합니다.")
    return key


def load_master_key(storage: Path) -> bytes:
    """Load an external key, or create a permission-restricted local development key."""
    configured = os.environ.get("EVIDENCE_MASTER_KEY")
    if configured:
        return _decode_master_key(configured)

    keys_dir = storage / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    key_path = keys_dir / "master-v1.key"
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = key_path.read_bytes()
    else:
        key = secrets.token_bytes(KEY_BYTES)
        with os.fdopen(descriptor, "wb") as output:
            output.write(key)
            output.flush()
            os.fsync(output.fileno())
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    if len(key) != KEY_BYTES:
        raise CryptoConfigurationError(f"로컬 마스터 키가 손상되었습니다: {key_path}")
    return key


class EvidenceCrypto:
    """Derive per-evidence keys and encrypt/decrypt file streams with AES-GCM."""

    def __init__(self, master_key: bytes, key_version: str = "v1"):
        if len(master_key) != KEY_BYTES:
            raise CryptoConfigurationError("마스터 키는 정확히 32바이트여야 합니다.")
        self._master_key = master_key
        self.key_version = key_version

    @classmethod
    def from_storage(cls, storage: Path) -> "EvidenceCrypto":
        version = os.environ.get("EVIDENCE_KEY_VERSION", "v1").strip() or "v1"
        return cls(load_master_key(storage), version)

    def _derive_key(self, salt: bytes, key_version: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=salt,
            info=b"EVIDENCE:file-encryption:" + key_version.encode("utf-8"),
        ).derive(self._master_key)

    def new_encryptor(self):
        salt = secrets.token_bytes(SALT_BYTES)
        nonce = secrets.token_bytes(NONCE_BYTES)
        key = self._derive_key(salt, self.key_version)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        return encryptor, salt, nonce

    def metadata(self, salt: bytes, nonce: bytes, tag: bytes) -> EncryptionMetadata:
        if len(tag) != TAG_BYTES:
            raise CryptoConfigurationError("AES-GCM 인증 태그 길이가 올바르지 않습니다.")
        return EncryptionMetadata(
            algorithm=ALGORITHM,
            key_version=self.key_version,
            salt=base64.urlsafe_b64encode(salt).decode("ascii"),
            nonce=base64.urlsafe_b64encode(nonce).decode("ascii"),
            tag=base64.urlsafe_b64encode(tag).decode("ascii"),
        )

    def _decryptor(self, metadata: EncryptionMetadata):
        if metadata.algorithm != ALGORITHM:
            raise CryptoConfigurationError(f"지원하지 않는 암호화 방식입니다: {metadata.algorithm}")
        try:
            salt = base64.urlsafe_b64decode(metadata.salt)
            nonce = base64.urlsafe_b64decode(metadata.nonce)
            tag = base64.urlsafe_b64decode(metadata.tag)
        except (ValueError, binascii.Error) as error:
            raise CryptoConfigurationError("저장된 암호화 메타데이터가 올바르지 않습니다.") from error
        if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES or len(tag) != TAG_BYTES:
            raise CryptoConfigurationError("저장된 암호화 메타데이터 길이가 올바르지 않습니다.")
        key = self._derive_key(salt, metadata.key_version)
        return Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()

    def iter_decrypted(self, source: BinaryIO, metadata: EncryptionMetadata, chunk_size: int) -> Iterator[bytes]:
        decryptor = self._decryptor(metadata)
        while chunk := source.read(chunk_size):
            plain = decryptor.update(chunk)
            if plain:
                yield plain
        final = decryptor.finalize()
        if final:
            yield final
