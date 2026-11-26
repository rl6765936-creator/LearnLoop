"""Field encryption behind a small interface (spec §38: local + server encryption APPROVED).

- `Cipher` is the interface. `AesGcmCipher` encrypts with AES-256-GCM (authenticated: a
  changed or swapped value is detected). Every value is bound to WHERE it is stored
  (table + row) through "associated data", so a ciphertext copied to another row fails.
- KEY MANAGEMENT IS [OPEN] (spec §38/§51): who holds the key, recovery, moving computers.
  `DevKeyFile` is for DEVELOPMENT ONLY – a random key in a local file. It is not a
  production design and must not be used for real data.
- `PlaintextCipher` exists only for tests and must be chosen explicitly.
Requires the `cryptography` package for AES-GCM.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

VERSION = b"\x01"


class CryptoUnavailable(RuntimeError):
    pass


class Cipher(Protocol):
    name: str

    def encrypt(self, plaintext: bytes, context: str) -> bytes: ...
    def decrypt(self, ciphertext: bytes, context: str) -> bytes: ...


class AesGcmCipher:
    name = "aes-256-gcm"

    def __init__(self, key: bytes):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as e:
            raise CryptoUnavailable("the 'cryptography' package is required for encryption") from e
        if len(key) != 32:
            raise ValueError("key must be 32 bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, context: str) -> bytes:
        nonce = os.urandom(12)
        return VERSION + nonce + self._aead.encrypt(nonce, plaintext, context.encode("utf-8"))

    def decrypt(self, ciphertext: bytes, context: str) -> bytes:
        if not ciphertext.startswith(VERSION):
            raise ValueError("unknown ciphertext version")
        nonce, body = ciphertext[1:13], ciphertext[13:]
        return self._aead.decrypt(nonce, body, context.encode("utf-8"))


class PlaintextCipher:
    """TESTS ONLY – stores data unencrypted. Never the default."""
    name = "plaintext-test-only"

    def encrypt(self, plaintext: bytes, context: str) -> bytes:
        return plaintext

    def decrypt(self, ciphertext: bytes, context: str) -> bytes:
        return ciphertext


class DevKeyFile:
    """DEVELOPMENT ONLY. Creates/reads a random 32-byte key in a local file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def key(self) -> bytes:
        if self.path.exists():
            data = self.path.read_bytes()
            if len(data) != 32:
                raise ValueError("development key file is damaged")
            return data
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = os.urandom(32)
        self.path.write_bytes(key)
        return key


def crypto_available() -> bool:
    try:
        import cryptography.hazmat.primitives.ciphers.aead  # noqa: F401
        return True
    except ImportError:
        return False
