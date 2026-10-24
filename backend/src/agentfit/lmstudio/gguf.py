"""What Will It Run reads from the header of a GGUF file: whether the model carries an MTP head.

A GGUF starts with a fixed header and the metadata (key, type, value) of the model. Models that bundle a
multi-token-prediction head, such as the ``-mtp`` files of Qwen3.8, say so with ``<architecture>.nextn_predict_layers``
above 0 (and have ``blk.N.nextn.*`` tensors). Only the metadata is read, never the tensors, and arrays of numbers are
skipped with a seek, so even a file of 12 GB takes a fraction of a second.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

MAGIC = b"GGUF"
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9
# No key or string of a real model is anywhere near these; a larger number means the file is not a GGUF.
_MAX_STRING = 1 << 26
_MAX_KEYS = 1 << 16
_SUFFIX = ".nextn_predict_layers"


def _take(handle: BinaryIO, fmt: str) -> int | float | bool:
    size = struct.calcsize(fmt)
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("truncated")
    return struct.unpack(fmt, data)[0]


def _string(handle: BinaryIO) -> str:
    length = int(_take(handle, "<Q"))
    if length > _MAX_STRING:
        raise ValueError("string too long")
    data = handle.read(length)
    if len(data) != length:
        raise ValueError("truncated")
    return data.decode("utf-8", "replace")


def _value(handle: BinaryIO, kind: int) -> int | float | bool | str | None:
    if kind in _SCALARS:
        return _take(handle, _SCALARS[kind])
    if kind == _STRING:
        return _string(handle)
    if kind == _ARRAY:
        inner = int(_take(handle, "<I"))
        count = int(_take(handle, "<Q"))
        if inner in _SCALARS:
            handle.seek(count * struct.calcsize(_SCALARS[inner]), 1)
        else:
            for _ in range(count):
                _value(handle, inner)
        return None
    raise ValueError(f"unknown value type {kind}")


def has_mtp_head(path: str | Path) -> bool:
    """True when the GGUF at ``path`` bundles an MTP head. False for anything else, including a file that cannot be read."""
    try:
        with Path(path).open("rb") as handle:
            magic, version, _tensors, keys = struct.unpack("<4sIQQ", handle.read(24))
            if magic != MAGIC or version < 2 or keys > _MAX_KEYS:
                return False
            for _ in range(keys):
                key = _string(handle)
                value = _value(handle, int(_take(handle, "<I")))
                if key.endswith(_SUFFIX) and isinstance(value, int) and value > 0:
                    return True
    except (OSError, struct.error, ValueError):
        return False
    return False
