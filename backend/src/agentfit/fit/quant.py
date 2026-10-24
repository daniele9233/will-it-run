"""GGUF quantization tables and filename parsing.

Bytes-per-parameter, speed multipliers and quality penalties are adapted from llmfit
(https://github.com/AlexsJones/llmfit, ``llmfit-core/src/models.rs``). MIT License,
Copyright (c) Alex Jones. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re

# Ordered best quality -> most compressed. Used when a repo's real file list is unknown.
QUANT_HIERARCHY = ["Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q3_K_M", "Q2_K"]

# Whole-file bytes per parameter (includes higher-precision embeddings/output layers).
_BPP_BY_BITS = {1: 0.25, 2: 0.37, 3: 0.48, 4: 0.58, 5: 0.68, 6: 0.80, 8: 1.05}
_BPP_EXACT = {"F32": 4.0, "F16": 2.0, "BF16": 2.0, "MXFP4": 0.55, "I2_S": 0.42, "TQ1_0": 0.42, "TQ2_0": 0.42}

# Bytes per parameter actually read per generated token (the tok/s roofline input).
_SPEED_BYTES_BY_BITS = {1: 0.19, 2: 0.25, 3: 0.375, 4: 0.5, 5: 0.625, 6: 0.75, 8: 1.0}
_SPEED_BYTES_EXACT = {"F32": 4.0, "F16": 2.0, "BF16": 2.0, "MXFP4": 0.53, "I2_S": 0.40, "TQ1_0": 0.40, "TQ2_0": 0.40}

_QUALITY_PENALTY_BY_BITS = {1: -20.0, 2: -12.0, 3: -8.0, 4: -5.0, 5: -2.0, 6: -1.0, 8: 0.0}
_SPEED_MULT_BY_BITS = {1: 1.4, 2: 1.35, 3: 1.25, 4: 1.15, 5: 1.0, 6: 0.95, 8: 0.8}

# Size nudges for the K-quant suffix relative to the "_M" baseline.
_SUFFIX_SCALE = {"XXS": 0.90, "XS": 0.93, "S": 0.96, "M": 1.0, "L": 1.05, "XL": 1.08}

_QUANT_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"(?:UD-)?I?Q(?P<bits>[1-8])(?:_[01K])?(?:_(?P<suffix>XXS|XS|XL|NL|S|M|L))?"
    r"|BF16|F16|F32|FP16|FP32|MXFP4|I2_S|TQ[12]_0"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def normalize_quant(label: str) -> str:
    """Canonical form: upper-case, without the Unsloth dynamic ("UD-") prefix."""
    upper = label.strip().upper().replace("FP16", "F16").replace("FP32", "F32")
    return upper[3:] if upper.startswith("UD-") else upper


def parse_quant_from_filename(filename: str) -> str | None:
    """Extract the quantization label from a GGUF file name.

    >>> parse_quant_from_filename("Qwen3.8-27B-UD-Q3_K_XL.gguf")
    'Q3_K_XL'
    """
    stem = filename.rsplit("/", 1)[-1]
    stem = re.sub(r"\.gguf$", "", stem, flags=re.IGNORECASE)
    matches = list(_QUANT_RE.finditer(stem))
    if not matches:
        return None
    # The quant is conventionally the last such token ("...-27B-Q4_K_M").
    return normalize_quant(matches[-1].group(1))


def quant_in(text: str) -> str | None:
    """The quantization named anywhere in a text (a model key such as ``org/model@q4_k_m``, a display name, a file name)."""
    matches = list(_QUANT_RE.finditer(text))
    return normalize_quant(matches[-1].group(1)) if matches else None


def strip_quant(text: str) -> str:
    """The text without any quantization label and without the word GGUF: what two quantizations of one model share."""
    cleaned = _QUANT_RE.sub(" ", text)
    cleaned = re.sub(r"(?<![A-Za-z0-9])gguf(?![A-Za-z0-9])", " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", re.sub(r"[-_@/\\.:()\[\]]+", " ", cleaned)).strip().lower()


def quant_bits(quant: str) -> int | None:
    q = normalize_quant(quant)
    if q in ("F32",):
        return 32
    if q in ("F16", "BF16"):
        return 16
    if q in ("MXFP4",):
        return 4
    if q in ("I2_S", "TQ1_0", "TQ2_0"):
        return 2
    match = re.match(r"I?Q([1-8])", q)
    return int(match.group(1)) if match else None


def _suffix_scale(quant: str) -> float:
    match = re.search(r"_(XXS|XS|XL|NL|S|M|L)$", normalize_quant(quant))
    return _SUFFIX_SCALE.get(match.group(1), 1.0) if match else 1.0


def is_recognized(quant: str) -> bool:
    q = normalize_quant(quant)
    return q in _BPP_EXACT or quant_bits(q) in _BPP_BY_BITS


def bytes_per_param(quant: str) -> float:
    """Whole-file bytes per parameter. Unknown labels are sized as 4-bit."""
    q = normalize_quant(quant)
    if q in _BPP_EXACT:
        return _BPP_EXACT[q]
    base = _BPP_BY_BITS.get(quant_bits(q) or 4, 0.58)
    # imatrix "IQ" quants are slightly smaller than K-quants of the same bit width.
    if q.startswith("IQ"):
        base *= 0.94
    return base * _suffix_scale(q)


def speed_bytes_per_param(quant: str) -> float:
    q = normalize_quant(quant)
    if q in _SPEED_BYTES_EXACT:
        return _SPEED_BYTES_EXACT[q]
    return _SPEED_BYTES_BY_BITS.get(quant_bits(q) or 4, 0.5)


# Below 4 bits the loss grows much faster than the bit count suggests, so the penalty
# follows the measured accuracy level (see fit.accuracy) instead of the bit width:
# level 2 = noticeable loss, 1 = high, 0 = severe.
_LOW_BIT_PENALTY_BY_LEVEL = {2: -9.0, 1: -16.0, 0: -30.0}


def quality_penalty(quant: str) -> float:
    """Points (0..-30) lost to quantization on a 100-point quality scale."""
    q = normalize_quant(quant)
    if q in ("F32", "F16", "BF16", "MXFP4"):
        return 0.0
    if q in ("I2_S", "TQ1_0", "TQ2_0"):
        return -6.0
    bits = quant_bits(q) or 4
    if bits <= 3:
        from agentfit.fit.accuracy import accuracy_for  # local import: accuracy imports this module

        level = accuracy_for(q).level
        return _LOW_BIT_PENALTY_BY_LEVEL.get(level, _QUALITY_PENALTY_BY_BITS.get(bits, -8.0))
    return _QUALITY_PENALTY_BY_BITS.get(bits, -5.0)


def speed_multiplier(quant: str) -> float:
    q = normalize_quant(quant)
    if q in ("F16", "BF16"):
        return 0.6
    return _SPEED_MULT_BY_BITS.get(quant_bits(q) or 5, 1.0)


def quality_rank(quant: str) -> float:
    """Sortable quality: higher is better. Ties broken by suffix size."""
    bits = quant_bits(quant) or 4
    return bits + (_suffix_scale(quant) - 1.0)


def estimate_file_gb(params_b: float, quant: str) -> float:
    return params_b * bytes_per_param(quant)
