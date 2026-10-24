"""API keys of the cloud providers, encrypted for the Windows user who saved them.

A key is never written in clear: it goes through DPAPI (``CryptProtectData``), which
ties the bytes to this Windows account, and lands in ``<roaming>\\secrets\\<id>.key``.
Another account, or a copy of the file on another PC, cannot read it back. The settings
file, the database, the log and the HTTP API never see the key: the interface is told
only whether one is saved and its last four characters.

Off Windows (only the test suite runs there) the bytes are kept as they are, in a file
only its owner can read.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
from pathlib import Path

from agentfit import paths

_ENTROPY = b"WillItRun cloud key v1"


class SecretError(RuntimeError):
    pass


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> _Blob:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    def _take(blob: _Blob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob.pbData)  # type: ignore[attr-defined]

    def protect(data: bytes) -> bytes:
        source, entropy, out = _blob(data), _blob(_ENTROPY), _Blob()
        # CRYPTPROTECT_UI_FORBIDDEN: never show a dialog from a background process.
        if not ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
            ctypes.byref(source), "Will It Run", ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out)
        ):
            raise SecretError("Windows could not encrypt the key (CryptProtectData failed).")
        return _take(out)

    def unprotect(data: bytes) -> bytes:
        source, entropy, out = _blob(data), _blob(_ENTROPY), _Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
            ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out)
        ):
            raise SecretError("The saved key cannot be read by this Windows account. Save it again.")
        return _take(out)

else:  # pragma: no cover - the app is Windows only

    def protect(data: bytes) -> bytes:
        return data

    def unprotect(data: bytes) -> bytes:
        return data


def _dir() -> Path:
    return paths.ensure(paths.roaming_dir() / "secrets")


def _path(name: str) -> Path:
    if not name.isidentifier():
        raise SecretError(f"Invalid secret name: {name}")
    return _dir() / f"{name}.key"


def save(name: str, value: str) -> None:
    value = value.strip()
    if not value:
        raise SecretError("The key is empty.")
    path = _path(name)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(protect(value.encode("utf-8")))
    if sys.platform != "win32":  # pragma: no cover
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load(name: str) -> str | None:
    path = _path(name)
    if not path.is_file():
        return None
    return unprotect(path.read_bytes()).decode("utf-8")


def delete(name: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        _path(name).unlink()


def exists(name: str) -> bool:
    return _path(name).is_file()
