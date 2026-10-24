"""How much of a model's quality survives each quantization.

The figures are reference measurements, not Will It Run estimates: the perplexity increase
over the unquantized model that the llama.cpp project publishes for each GGUF
quantization, measured on Llama-3-8B (``tools/quantize/quantize.cpp``, QUANT_OPTIONS;
MIT License, Copyright (c) The ggml authors. See THIRD_PARTY_NOTICES.md).

Perplexity is how surprised a model is by real text; lower is better. A quantization
that adds +0.02 is indistinguishable from the original in use, one that adds +3.5 is
visibly worse. The numbers hold for the model they were measured on: larger models
usually lose less at the same quantization and small ones lose more, so Will It Run shows
them as a guide and says so.

imatrix ("IQ") quantizations have no published perplexity in that table. They are shown
with the smallest measured quantization of the same bit width and marked as a rough guide.
"""

from __future__ import annotations

import math
import re

from pydantic import BaseModel

from agentfit.fit import quant as q
from agentfit.i18n import tr

REFERENCE_MODEL = "Llama-3-8B"
REFERENCE_SOURCE = "llama.cpp tools/quantize/quantize.cpp"
REFERENCE_URL = "https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/quantize.cpp"
# Llama-3-8B has 8.03 billion parameters; file sizes in the table are in GiB.
_REFERENCE_PARAMS = 8.03e9

# quant -> (file size in GiB on the reference model, perplexity increase over F16)
MEASURED: dict[str, tuple[float, float]] = {
    "Q8_0": (7.96, 0.0026),
    "Q6_K": (6.14, 0.0217),
    "Q5_K_M": (5.33, 0.0569),
    "Q5_K_S": (5.21, 0.1049),
    "Q5_1": (5.65, 0.1062),
    "Q5_0": (5.21, 0.1316),
    "Q4_K_M": (4.58, 0.1754),
    "Q4_K_S": (4.37, 0.2689),
    "Q4_1": (4.78, 0.4511),
    "Q4_0": (4.34, 0.4685),
    "Q3_K_L": (4.03, 0.5562),
    "Q3_K_M": (3.74, 0.6569),
    "Q3_K_S": (3.41, 1.6321),
    "Q2_K_S": (2.96, 3.1836),
    "Q2_K": (2.96, 3.5199),
}

# Bits per weight that llama.cpp states for quantizations it gives no perplexity for.
STATED_BPW: dict[str, float] = {
    "IQ1_S": 1.56, "IQ1_M": 1.75, "TQ1_0": 1.69, "TQ2_0": 2.06,
    "IQ2_XXS": 2.06, "IQ2_XS": 2.31, "IQ2_S": 2.5, "IQ2_M": 2.7,
    "IQ3_XXS": 3.06, "IQ3_XS": 3.3, "IQ3_S": 3.44, "IQ3_M": 3.66,
    "IQ4_XS": 4.25, "IQ4_NL": 4.5,
}

# The quantizations shown in the reference table of the guide, best first.
LADDER = ["Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S", "Q4_K_M", "Q4_K_S", "Q3_K_L", "Q3_K_M", "Q3_K_S", "Q2_K"]

_SUFFIX_ORDER = ["XXS", "XS", "S", "M", "L", "XL"]

# (upper bound of the perplexity increase, level 5..0)
_LEVELS = ((0.03, 5), (0.11, 4), (0.30, 3), (0.70, 2), (2.0, 1), (math.inf, 0))


class Accuracy(BaseModel):
    quant: str
    # 5 = indistinguishable from the original ... 0 = severe loss.
    level: int
    label: str
    ppl_delta: float | None = None
    bits_per_weight: float | None = None
    # "measured": published figure for this exact quantization.
    # "nearest":  published figure of the closest named quantization (e.g. Q4_K_L -> Q4_K_M).
    # "bits":     no published figure; grouped with a measured quantization of the same bit width.
    # "original": unquantized weights.
    # "unknown":  the label is not a recognized quantization.
    basis: str
    reference: str | None = None
    note: str = ""


def level_for(ppl_delta: float) -> int:
    return next(level for bound, level in _LEVELS if ppl_delta <= bound)


def level_label(level: int) -> str:
    return {
        5: tr("Near lossless"),
        4: tr("Very low loss"),
        3: tr("Low loss"),
        2: tr("Noticeable loss"),
        1: tr("High loss"),
        0: tr("Severe loss"),
    }[level]


def _bpw_from_size(size_gib: float) -> float:
    return round(size_gib * 1024**3 * 8 / _REFERENCE_PARAMS, 2)


def _nearest_measured(label: str) -> str | None:
    """Closest measured quantization of the same bit width, never a better one."""
    match = re.fullmatch(r"Q([2-8])_K(?:_(XXS|XS|S|M|L|XL))?", label)
    if not match:
        return None
    bits, suffix = match.group(1), match.group(2) or "M"
    rank = _SUFFIX_ORDER.index(suffix)
    candidates = [f"Q{bits}_K_{s}" for s in reversed(_SUFFIX_ORDER[: rank + 1])] + [f"Q{bits}_K"]
    found = next((c for c in candidates if c in MEASURED), None)
    if found:
        return found
    # A smaller suffix than anything measured: fall back to the smallest measured one.
    return next((f"Q{bits}_K_{s}" for s in _SUFFIX_ORDER if f"Q{bits}_K_{s}" in MEASURED), None)


def accuracy_for(quant: str | None) -> Accuracy:
    label = q.normalize_quant(quant or "")
    if label in ("F32", "F16", "BF16"):
        return Accuracy(
            quant=label, level=5, label=tr("Original precision"), ppl_delta=0.0,
            bits_per_weight=32.0 if label == "F32" else 16.0, basis="original",
            note=tr("Unquantized weights: this is the model exactly as published."),
        )
    if label in MEASURED:
        size, delta = MEASURED[label]
        level = level_for(delta)
        return Accuracy(
            quant=label, level=level, label=level_label(level), ppl_delta=delta, bits_per_weight=_bpw_from_size(size),
            basis="measured", reference=label,
            note=tr("+{delta} perplexity over the original, measured by llama.cpp on {model}.", delta=f"{delta:.4f}", model=REFERENCE_MODEL),
        )
    nearest = _nearest_measured(label)
    if nearest:
        size, delta = MEASURED[nearest]
        level = level_for(delta)
        return Accuracy(
            quant=label, level=level, label=level_label(level), ppl_delta=delta, bits_per_weight=_bpw_from_size(size),
            basis="nearest", reference=nearest,
            note=tr(
                "No published figure for {quant}; shown with the one for {nearest} (+{delta}), the closest measured quantization.",
                quant=label, nearest=nearest, delta=f"{delta:.4f}",
            ),
        )
    bpw = STATED_BPW.get(label)
    if bpw is not None or label.startswith("IQ"):
        bits = q.quant_bits(label) or 2
        proxy = {4: "Q4_K_S", 3: "Q3_K_S"}.get(bits, "Q2_K")
        level = level_for(MEASURED[proxy][1])
        return Accuracy(
            quant=label, level=level, label=level_label(level), ppl_delta=None, bits_per_weight=bpw, basis="bits",
            reference=proxy,
            note=tr(
                "No published perplexity for {quant}. Shown with {proxy}, the smallest measured quantization of the "
                "same bit width; treat it as a rough guide.",
                quant=label, proxy=proxy,
            ),
        )
    bits = q.quant_bits(label) if label else None
    if bits in (2, 3, 4, 5, 6, 8):
        proxy = {8: "Q8_0", 6: "Q6_K", 5: "Q5_K_M", 4: "Q4_K_M", 3: "Q3_K_M", 2: "Q2_K"}[bits]
        size, delta = MEASURED[proxy]
        level = level_for(delta)
        return Accuracy(
            quant=label, level=level, label=level_label(level), ppl_delta=None, bits_per_weight=_bpw_from_size(size),
            basis="bits", reference=proxy,
            note=tr("No published figure for {quant}; placed with other {bits}-bit quantizations.", quant=label, bits=bits),
        )
    return Accuracy(
        quant=label or "?", level=0, label=tr("Unknown"), basis="unknown",
        note=tr("This quantization is not recognized, so no accuracy figure is shown."),
    )


def reference_table() -> list[Accuracy]:
    return [accuracy_for(name) for name in LADDER]


def reference_info() -> dict[str, str]:
    return {
        "model": REFERENCE_MODEL,
        "source": REFERENCE_SOURCE,
        "url": REFERENCE_URL,
        "caveat": tr(
            "Measured on one 8B model. Larger models usually lose less at the same quantization; smaller ones lose more."
        ),
    }
