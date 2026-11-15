import base64
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from server.crypto import (
    ALGORITHM,
    AuthenticationFailedError,
    EvidenceCrypto,
    InvalidCryptoInputError,
    InvalidEncryptionMetadataError,
    KeyConfigurationError,
    KeyUnavailableError,
    Sha256Hasher,
    sha256_chunks,
)


class CryptoInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.crypto = EvidenceCrypto({"v1": b"K" * 32}, "v1")

    def encrypt(self, chunks, aad=b"evidence:v1"):
        session = self.crypto.start_encryption(aad)
        ciphertext = b"".join(session.update(chunk) for chunk in chunks)
        final, result = session.finalize("v1")
        return ciphertext + final, result

    def test_sha256_vectors_and_incremental_input(self):
        self.assertEqual(
            sha256_chunks([]),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        hasher = Sha256Hasher()
        hasher.update(b"a")
        hasher.update(bytearray(b"bc"))
        self.assertEqual(
            hasher.hexdigest(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_encrypt_decrypt_interface_and_metadata(self):
        ciphertext, result = self.encrypt([b"original ", b"evidence"])
        self.assertEqual(result.metadata.algorithm, ALGORITHM)
        self.assertEqual(result.metadata.key_version, "v1")
        self.assertEqual(result.plaintext_size, len(b"original evidence"))
        self.assertEqual(result.stored_size, len(ciphertext))
        self.assertEqual(result.stored_size, result.plaintext_size)

        plain = self.crypto.decrypt_verified([ciphertext[:3], ciphertext[3:]], result.metadata, b"evidence:v1")
        try:
            self.assertEqual(plain.read(), b"original evidence")
        finally:
            plain.close()

    def test_each_encryption_uses_a_fresh_nonce(self):
        first_ciphertext, first = self.encrypt([b"same"])
        second_ciphertext, second = self.encrypt([b"same"])
        self.assertNotEqual(first.metadata.nonce, second.metadata.nonce)
        self.assertNotEqual(first.metadata.salt, second.metadata.salt)
        self.assertNotEqual(first_ciphertext, second_ciphertext)

    def test_tampering_is_reported_as_authentication_failure(self):
        ciphertext, result = self.encrypt([b"protected evidence"])
        changed = bytes([ciphertext[0] ^ 1]) + ciphertext[1:]
        with self.assertRaises(AuthenticationFailedError):
            self.crypto.decrypt_verified([changed], result.metadata, b"evidence:v1")

    def test_key_and_input_failures_are_distinct(self):
        ciphertext, result = self.encrypt([b"data"])
        with self.assertRaises(KeyUnavailableError):
            self.crypto.decrypt_verified(
                [ciphertext], replace(result.metadata, key_version="missing"), b"evidence:v1"
            )
        with self.assertRaises(InvalidEncryptionMetadataError):
            self.crypto.decrypt_verified(
                [ciphertext], replace(result.metadata, nonce="not-base64!"), b"evidence:v1"
            )
        with self.assertRaises(InvalidCryptoInputError):
            sha256_chunks(["not bytes"])

    def test_server_fails_closed_without_an_explicit_key_source(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(KeyConfigurationError):
                EvidenceCrypto.from_storage(Path(directory))
            self.assertFalse((Path(directory) / "keys").exists())

    def test_development_key_generation_requires_an_explicit_flag(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"EVIDENCE_ALLOW_DEV_KEY_GENERATION": "true"}, clear=True
        ):
            crypto = EvidenceCrypto.from_storage(Path(directory))
            self.assertEqual(crypto.available_key_versions, ("v1",))
            self.assertEqual((Path(directory) / "keys" / "master-v1.key").stat().st_size, 32)

    def test_secret_keyring_file_is_loaded_outside_the_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            secret = root / "secrets" / "evidence-master-keys.json"
            secret.parent.mkdir()
            secret.write_text(json.dumps({
                "v1": base64.urlsafe_b64encode(b"S" * 32).decode("ascii")
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "EVIDENCE_MASTER_KEYS_FILE": str(secret),
                "EVIDENCE_KEY_VERSION": "v1",
            }, clear=True):
                crypto = EvidenceCrypto.from_storage(data)
            self.assertEqual(crypto.available_key_versions, ("v1",))
            self.assertFalse((data / "keys").exists())


if __name__ == "__main__":
    unittest.main()
