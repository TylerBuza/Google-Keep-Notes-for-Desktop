"""Atomic, current-Windows-user DPAPI storage. Never fall back to plaintext."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile

MAGIC = b"KeepDesktop-DPAPI-v1\0"


class StorageError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise StorageError("Secure storage requires Windows DPAPI; refusing plaintext storage.")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; deliberately NOT LOCAL_MACHINE.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise StorageError("Windows could not unlock/save protected data for this user.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def atomic_write(path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".keepdesktop-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_protected(path, value) -> None:
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    # Encryption happens BEFORE creating any temporary file.
    atomic_write(path, MAGIC + _crypt(raw, decrypt=False))


def load_protected(path):
    data = Path(path).read_bytes()
    if not data.startswith(MAGIC):
        raise StorageError("Unprotected or unsupported data file; explicit migration required.")
    try:
        return json.loads(_crypt(data[len(MAGIC):], decrypt=True))
    except (ValueError, UnicodeError) as exc:
        raise StorageError("Protected data is damaged; the original file was left intact.") from exc
