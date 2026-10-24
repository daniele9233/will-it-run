"""GPU lookup tables: memory bandwidth and NVIDIA compute capability.

Adapted from llmfit (https://github.com/AlexsJones/llmfit), ``llmfit-core/src/hardware.rs``
``gpu_memory_bandwidth_gbps`` / ``gpu_compute_capability``. MIT License, Copyright (c)
Alex Jones. See THIRD_PARTY_NOTICES.md. Entries are matched in order, so more specific
names ("4080 super") must precede their prefixes ("4080").
"""

from __future__ import annotations

import re

from agentfit.hardware.models import GpuVendor

# (substring, GB/s). Token generation is memory-bandwidth bound, so this drives tok/s.
_BANDWIDTH: list[tuple[str, float]] = [
    # NVIDIA RTX 50 (Blackwell)
    ("5090", 1792.0), ("5080", 960.0), ("5070 ti", 896.0), ("5070", 672.0),
    ("5060 ti", 448.0), ("5060", 256.0),
    # NVIDIA RTX 40 (Ada Lovelace)
    ("4090", 1008.0), ("4080 super", 736.0), ("4080", 717.0), ("4070 ti super", 672.0),
    ("4070 ti", 504.0), ("4070 super", 504.0), ("4070", 504.0), ("4060 ti", 288.0), ("4060", 272.0),
    # NVIDIA RTX 30 (Ampere)
    ("3090 ti", 1008.0), ("3090", 936.0), ("3080 ti", 912.0), ("3080", 760.0),
    ("3070 ti", 608.0), ("3070", 448.0), ("3060 ti", 448.0), ("3060", 360.0),
    # NVIDIA RTX 20 (Turing)
    ("2080 ti", 616.0), ("2080 super", 496.0), ("2080", 448.0), ("2070 super", 448.0),
    ("2070", 448.0), ("2060 super", 448.0), ("2060", 336.0),
    # NVIDIA GTX 16
    ("1660 ti", 288.0), ("1660 super", 336.0), ("1660", 192.0), ("1650 super", 192.0), ("1650", 128.0),
    # NVIDIA workstation
    ("a6000", 768.0), ("a5000", 768.0), ("a4000", 448.0),
    # AMD RDNA 4 / 3 / 2
    ("9070 xt", 624.0), ("9070", 488.0),
    ("7900 xtx", 960.0), ("7900 xt", 800.0), ("7900 gre", 576.0), ("7800 xt", 624.0),
    ("7700 xt", 432.0), ("7600", 288.0),
    ("6950 xt", 576.0), ("6900 xt", 512.0), ("6800 xt", 512.0), ("6800", 512.0),
    ("6700 xt", 384.0), ("6600 xt", 256.0), ("6600", 224.0),
]

# (substrings, "major.minor")
_COMPUTE_CAPABILITY: list[tuple[tuple[str, ...], str]] = [
    (("5090", "5080", "5070", "5060"), "10.0"),
    (("4090", "4080", "4070", "4060"), "8.9"),
    (("3090", "3080", "3070", "3060", "a6000", "a5000", "a4000", "a2000"), "8.6"),
    (("2080", "2070", "2060", "1660", "1650"), "7.5"),
    (("titan v",), "7.0"),
    (("1080", "1070", "1060", "1050"), "6.1"),
]

_MOBILE = re.compile(r"\b(laptop|mobile|max-q|notebook)\b|\d{4}m\b", re.IGNORECASE)


def is_mobile_gpu(name: str) -> bool:
    """Mobile parts share a model number with a desktop card but not its memory bus."""
    return bool(_MOBILE.search(name))


def memory_bandwidth_gbps(name: str) -> float | None:
    lower = name.lower()
    if is_mobile_gpu(lower):
        return None
    for needle, gbps in _BANDWIDTH:
        if needle in lower:
            return gbps
    return None


def compute_capability(name: str) -> str | None:
    lower = name.lower()
    for needles, cc in _COMPUTE_CAPABILITY:
        if any(n in lower for n in needles):
            return cc
    return None


def vendor_from_name(name: str, pnp_id: str | None = None) -> GpuVendor:
    pnp = (pnp_id or "").upper()
    if "VEN_10DE" in pnp:
        return GpuVendor.NVIDIA
    if "VEN_1002" in pnp or "VEN_1022" in pnp:
        return GpuVendor.AMD
    if "VEN_8086" in pnp:
        return GpuVendor.INTEL
    lower = name.lower()
    if any(t in lower for t in ("nvidia", "geforce", "quadro", "rtx", "gtx")):
        return GpuVendor.NVIDIA
    if any(t in lower for t in ("amd", "radeon", "ati ")):
        return GpuVendor.AMD
    if any(t in lower for t in ("intel", "arc(tm)", "iris", "uhd")):
        return GpuVendor.INTEL
    return GpuVendor.OTHER


_ARC_DISCRETE = re.compile(r"\barc(\s*\(tm\))?\s*(pro\s*)?[ab]\d{3}\b", re.IGNORECASE)
_AMD_DISCRETE = re.compile(r"\b(rx|pro w|firepro|r[579])\s?\d{3,4}", re.IGNORECASE)


def is_integrated_gpu(name: str, vendor: GpuVendor) -> bool:
    """Best-effort discrete/integrated split from the adapter name."""
    lower = name.lower()
    if vendor == GpuVendor.NVIDIA:
        return False
    if vendor == GpuVendor.INTEL:
        # "Intel(R) Arc(TM) A770 Graphics" is discrete; "Intel(R) Arc(TM) Graphics" is an iGPU.
        return not _ARC_DISCRETE.search(lower)
    if vendor == GpuVendor.AMD:
        if _AMD_DISCRETE.search(lower):
            return False
        return any(t in lower for t in ("radeon graphics", "radeon(tm) graphics", "vega", "890m", "880m", "780m", "760m", "680m", "660m", "8060s", "8050s"))
    return True
