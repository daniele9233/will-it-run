"""NVIDIA detection through nvidia-smi (installed with every NVIDIA Windows driver)."""

from __future__ import annotations

import re
import shutil
import subprocess

from agentfit.hardware import gpu_tables
from agentfit.hardware.models import Confidence, GpuBackend, GpuInfo, GpuVendor

QUERY_FIELDS = [
    "index",
    "name",
    "memory.total",
    "memory.used",
    "memory.free",
    "driver_version",
    "utilization.gpu",
    "temperature.gpu",
    "uuid",
    "pci.bus_id",
]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _num(value: str) -> float | None:
    value = value.strip()
    if not value or value.upper().startswith("N/A") or value.startswith("["):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_query_csv(text: str) -> list[GpuInfo]:
    """Parse ``nvidia-smi --query-gpu=<QUERY_FIELDS> --format=csv,noheader,nounits``."""
    gpus: list[GpuInfo] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < len(QUERY_FIELDS) or _num(cells[0]) is None:
            continue
        name = cells[1]
        total = _num(cells[2])
        gpus.append(
            GpuInfo(
                index=int(float(cells[0])),
                vendor=GpuVendor.NVIDIA,
                name=name,
                vram_total_mb=total or 0,
                vram_used_mb=_num(cells[3]),
                vram_free_mb=_num(cells[4]),
                driver_version=cells[5] or None,
                utilization_percent=_num(cells[6]),
                temperature_c=_num(cells[7]),
                uuid=cells[8] or None,
                pci_id=cells[9] or None,
                is_integrated=False,
                backend=GpuBackend.CUDA,
                compute_capability=gpu_tables.compute_capability(name),
                memory_bandwidth_gbps=gpu_tables.memory_bandwidth_gbps(name),
                sources=["nvidia-smi"],
                vram_confidence=Confidence.HIGH if total else Confidence.LOW,
            )
        )
    return gpus


# Older drivers print "CUDA Version: 12.4"; 600-series drivers print "CUDA UMD Version: 13.3".
_CUDA_RE = re.compile(r"CUDA(?:\s+UMD)?\s+Version:\s*([0-9.]+)")


def parse_cuda_version(banner: str) -> str | None:
    """Extract the driver's maximum supported CUDA version from the nvidia-smi banner."""
    match = _CUDA_RE.search(banner)
    return match.group(1) if match else None


def _run(args: list[str], timeout: float = 10.0, *, allow_nonzero: bool = False) -> str | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, *args], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 or allow_nonzero else None


def detect() -> list[GpuInfo]:
    out = _run([f"--query-gpu={','.join(QUERY_FIELDS)}", "--format=csv,noheader,nounits"])
    if not out:
        return []
    gpus = parse_query_csv(out)
    if gpus:
        cuda = parse_cuda_version(_run([], allow_nonzero=True) or "")
        for gpu in gpus:
            gpu.cuda_version = cuda
    return gpus


def live_usage() -> list[dict[str, float | int | str | None]]:
    """Cheap poll used by the status bar and the benchmark sampler."""
    out = _run(
        ["--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
        timeout=5.0,
    )
    rows: list[dict[str, float | int | str | None]] = []
    for line in (out or "").splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 6 or _num(cells[0]) is None:
            continue
        rows.append(
            {
                "index": int(float(cells[0])),
                "name": cells[1],
                "vram_total_mb": _num(cells[2]),
                "vram_used_mb": _num(cells[3]),
                "utilization_percent": _num(cells[4]),
                "temperature_c": _num(cells[5]),
            }
        )
    return rows
