"""Protection of the database encryption key (user decision 2026-11-24, APPROVED: DPAPI + an
offline recovery code).

What it is:
- The DATA KEY (32 random bytes) encrypts LearnLoop's database fields (AES-256-GCM, crypto.py).
- The data key is never stored in plain form. It is stored twice, each copy locked differently:
    key.dpapi     locked by Windows DPAPI ("Data Protection API"): Windows encrypts it with a
                  secret tied to YOUR Windows user account. Other users on the computer, or the
                  file copied to another computer, cannot open it.
    key.recovery  locked by a RECOVERY CODE that is shown to you ONCE and never written to disk.
                  You keep it offline (on paper). It is needed only if Windows can no longer open
                  key.dpapi (new computer, reinstalled Windows, user profile lost).
- The recovery code is turned into a key with scrypt (a deliberately slow function), so guessing
  it is expensive.

What it is not: the recovery code is NOT stored next to the encrypted files (or anywhere).
"""

from __future__ import annotations

import base64
import ctypes
import os
import secrets
import sys
from pathlib import Path

RECOVERY_GROUPS = 6          # 6 groups of 5 base32 characters = 150 bits


class KeyStoreError(RuntimeError):
    pass


# --------------------------------------------------------------------------- protectors
class DpapiProtector:
    """Windows DPAPI for the current user (CryptProtectData / CryptUnprotectData)."""

    def __init__(self):
        if sys.platform != "win32":
            raise KeyStoreError("DPAPI is only available on Windows")
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        self._Blob, self._crypt32, self._kernel32 = Blob, ctypes.windll.crypt32, ctypes.windll.kernel32

    def _call(self, fn, data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = self._Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = self._Blob()
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out))  # UI_FORBIDDEN
        if not ok:
            raise KeyStoreError("Windows could not protect/unprotect the key")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            self._kernel32.LocalFree(blob_out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptProtectData, data)

    def unprotect(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptUnprotectData, data)


# --------------------------------------------------------------------------- recovery wrapping
def _kek(code: str, salt: bytes) -> bytes:
    import hashlib
    normalized = code.replace("-", "").replace(" ", "").upper().encode("ascii")
    return hashlib.scrypt(normalized, salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def _wrap(key: bytes, code: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    return b"LLR1" + salt + nonce + AESGCM(_kek(code, salt)).encrypt(nonce, key, b"learnloop-recovery")


def _unwrap(blob: bytes, code: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if blob[:4] != b"LLR1":
        raise KeyStoreError("recovery file is damaged")
    salt, nonce, ct = blob[4:20], blob[20:32], blob[32:]
    try:
        return AESGCM(_kek(code, salt)).decrypt(nonce, ct, b"learnloop-recovery")
    except InvalidTag:
        raise KeyStoreError("wrong recovery code") from None


def new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes((RECOVERY_GROUPS * 5 * 5 + 7) // 8 + 1)).decode("ascii").rstrip("=")
    raw = raw[: RECOVERY_GROUPS * 5]
    return "-".join(raw[i:i + 5] for i in range(0, len(raw), 5))


# --------------------------------------------------------------------------- key store
class KeyStore:
    def __init__(self, directory: Path, protector=None):
        self.dir = Path(directory)
        self.protector = protector
        self.dpapi_file, self.recovery_file = self.dir / "key.dpapi", self.dir / "key.recovery"

    def _protector(self):
        if self.protector is None:
            self.protector = DpapiProtector()
        return self.protector

    def exists(self) -> bool:
        return self.dpapi_file.exists()

    def create(self) -> tuple[bytes, str]:
        """New data key. Returns (key, recovery_code). The code is NOT saved anywhere."""
        if self.exists() or self.recovery_file.exists():
            raise KeyStoreError("a key already exists – refusing to overwrite it")
        key, code = os.urandom(32), new_recovery_code()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.recovery_file.write_bytes(_wrap(key, code))
        self.dpapi_file.write_bytes(self._protector().protect(key))
        return key, code

    def load(self) -> bytes:
        if not self.exists():
            raise KeyStoreError("no key yet – run the first-time setup")
        key = self._protector().unprotect(self.dpapi_file.read_bytes())
        if len(key) != 32:
            raise KeyStoreError("protected key is damaged")
        return key

    def recover(self, code: str) -> bytes:
        """New computer / Windows reinstalled: open the key with the recovery code and protect
        it again for the CURRENT Windows user. The old key.dpapi is replaced, not deleted
        elsewhere."""
        if not self.recovery_file.exists():
            raise KeyStoreError("no recovery file")
        key = _unwrap(self.recovery_file.read_bytes(), code)
        tmp = self.dpapi_file.with_suffix(".tmp")
        tmp.write_bytes(self._protector().protect(key))
        os.replace(tmp, self.dpapi_file)
        return key
