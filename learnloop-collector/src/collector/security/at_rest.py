"""Encryption at rest of the Collector's local files that hold STUDY TEXT (spec §57;
decision 2026-11-24 Q16: Windows DPAPI).

What is protected: outbox entries (filtered text waiting for transfer, including quarantined
ones) and the content she typed for unreadable files (review: manual text).

How: each file is encrypted with Windows DPAPI ("Data Protection API") for the current Windows
user. Only this Windows user on this computer can open it; a copy of the file elsewhere is
unreadable. No key file, no password, no new library.

Why no recovery code here (unlike the brain): these files are a CACHE. If they can no longer be
opened (new computer, Windows reinstalled), nothing is lost – the Collector simply reads the study
folder again. The brain's data, which is NOT re-creatable, has the recovery code.

Other systems (the cloud test machine, Linux, macOS): DPAPI does not exist. Development only:
files are written with a "not encrypted" header, so it is always visible which protection a
file has. LearnLoop runs on Windows.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path

DPAPI_HEADER, PLAIN_HEADER = b"LLC1", b"LLP0"
_ENTROPY = b"learnloop-collector-at-rest"


class AtRestError(RuntimeError):
    pass


class DpapiProtector:
    name, header = "dpapi", DPAPI_HEADER

    def __init__(self):
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        self._Blob, self._crypt32, self._kernel32 = Blob, ctypes.windll.crypt32, ctypes.windll.kernel32

    def _blob(self, data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        return self._Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(self, fn, data: bytes) -> bytes:
        blob_in, _keep = self._blob(data)
        entropy, _keep2 = self._blob(_ENTROPY)
        blob_out = self._Blob()
        if not fn(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, 0x01, ctypes.byref(blob_out)):
            raise AtRestError("Windows could not protect/unprotect a local file")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            self._kernel32.LocalFree(blob_out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptProtectData, data)

    def unprotect(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptUnprotectData, data)


class PlainProtector:
    """Development only (no DPAPI on this system). Marked, never silent."""
    name, header = "none (development)", PLAIN_HEADER

    def protect(self, data: bytes) -> bytes:
        return data

    def unprotect(self, data: bytes) -> bytes:
        return data


_protector = None


def protector():
    global _protector
    if _protector is None:
        _protector = DpapiProtector() if sys.platform == "win32" else PlainProtector()
    return _protector


def set_protector(p) -> None:
    """Tests only."""
    global _protector
    _protector = p


def encode(data) -> bytes:
    p = protector()
    return p.header + p.protect(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def decode(raw: bytes):
    if raw[:1] in (b"{", b"["):                 # written before encryption existed – still readable;
        return json.loads(raw.decode("utf-8"))  # it is re-written encrypted on the next save
    head, body = raw[:4], raw[4:]
    p = protector()
    if head == DPAPI_HEADER and p.header == DPAPI_HEADER:
        return json.loads(p.unprotect(body).decode("utf-8"))
    if head == PLAIN_HEADER:
        return json.loads(body.decode("utf-8"))
    if head == getattr(p, "header", None):
        return json.loads(p.unprotect(body).decode("utf-8"))
    raise AtRestError("local file is protected for another Windows user or computer")


def write(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encode(data))
    os.replace(tmp, path)


def read(path: Path):
    return decode(Path(path).read_bytes())
