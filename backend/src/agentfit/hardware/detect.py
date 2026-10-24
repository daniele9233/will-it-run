"""Hardware detection orchestrator: merges nvidia-smi, WMI/CIM and registry probes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psutil

from agentfit.hardware import nvidia, windows
from agentfit.hardware.models import Confidence, GpuInfo, GpuVendor, HardwareProfile

ProgressFn = Callable[[str, str], None]

SCAN_STEPS = ["windows", "cpu", "ram", "gpu", "cuda", "storage"]


def merge_gpus(smi: list[GpuInfo], wmi: list[GpuInfo]) -> list[GpuInfo]:
    """Prefer nvidia-smi for NVIDIA cards (exact VRAM, live usage); keep WMI for the rest.

    WMI order is not nvidia-smi order, so NVIDIA entries are paired by name. The WMI
    entry only contributes fields nvidia-smi lacks (driver date, shared memory).
    """
    merged: list[GpuInfo] = []
    wmi_nvidia = [g for g in wmi if g.vendor == GpuVendor.NVIDIA]
    used: set[int] = set()
    for gpu in smi:
        match = next(
            (
                (i, w)
                for i, w in enumerate(wmi_nvidia)
                if i not in used and windows.normalize_gpu_name(w.name) == windows.normalize_gpu_name(gpu.name)
            ),
            None,
        )
        if match:
            i, w = match
            used.add(i)
            gpu = gpu.model_copy(
                update={
                    "driver_date": w.driver_date,
                    "shared_memory_mb": w.shared_memory_mb,
                    "sources": [*gpu.sources, *[s for s in w.sources if s not in gpu.sources]],
                }
            )
        merged.append(gpu)
    if not smi:
        # nvidia-smi missing or broken: fall back to WMI/registry for NVIDIA cards too.
        merged.extend(wmi_nvidia)
    merged.extend(g for g in wmi if g.vendor != GpuVendor.NVIDIA)
    # Discrete first, largest VRAM first; then give stable sequential indices.
    merged.sort(key=lambda g: (g.is_integrated, -g.vram_total_mb))
    return [g.model_copy(update={"index": i}) for i, g in enumerate(merged)]


def build_profile(raw: dict[str, Any], smi_gpus: list[GpuInfo], features: dict[str, bool | None]) -> HardwareProfile:
    """Pure assembly step, shared by live detection and fixture-driven tests."""
    warnings: list[str] = []
    os_info = windows.parse_os(raw)
    cpu = windows.parse_cpu(raw)
    cpu.features = features
    ram = windows.parse_ram(raw)
    gpus = merge_gpus(smi_gpus, windows.parse_video_controllers(raw, ram.total_bytes))
    drives = windows.parse_drives(raw)

    if not raw:
        warnings.append("Windows CIM probe returned no data; values come from fallbacks.")
    if not gpus:
        warnings.append("No GPU detected. Models will run on the CPU only.")
    elif not any(g.usable_for_inference for g in gpus):
        warnings.append("Only integrated graphics found. Inference will run on the CPU.")
    for gpu in gpus:
        if gpu.vram_confidence == Confidence.LOW:
            warnings.append(f"VRAM for {gpu.name} could not be confirmed (WMI reports a saturated 4 GB value).")
    if cpu.features.get("avx2") is False:
        warnings.append("CPU lacks AVX2; CPU inference will be very slow.")

    confidence = {
        "hardware_detection": Confidence.HIGH if raw and cpu.physical_cores else Confidence.LOW,
        "cpu": Confidence.HIGH if cpu.physical_cores else Confidence.LOW,
        "ram": Confidence.HIGH if ram.total_bytes else Confidence.LOW,
        "gpu": (
            min((g.vram_confidence for g in gpus), key=["LOW", "UNKNOWN", "MEDIUM", "HIGH"].index)
            if gpus
            else Confidence.HIGH
        ),
        "storage": Confidence.HIGH if drives else Confidence.LOW,
    }
    return HardwareProfile(
        os=os_info,
        cpu=cpu,
        ram=ram,
        gpus=gpus,
        drives=drives,
        detected_at=datetime.now(UTC).isoformat(timespec="seconds"),
        confidence=confidence,
        warnings=warnings,
    )


def _apply_fallbacks(profile: HardwareProfile) -> HardwareProfile:
    """Fill gaps from psutil when the CIM probe was unavailable."""
    if not profile.ram.total_bytes:
        vm = psutil.virtual_memory()
        profile.ram.total_bytes = int(vm.total)
        profile.ram.available_bytes = int(vm.available)
        profile.confidence["ram"] = Confidence.MEDIUM
    if not profile.cpu.physical_cores:
        profile.cpu.physical_cores = psutil.cpu_count(logical=False) or 0
        profile.cpu.logical_cores = psutil.cpu_count(logical=True) or 0
    return profile


def detect_hardware(progress: ProgressFn | None = None) -> HardwareProfile:
    """Run every probe. ``progress(step, state)`` receives 'running' / 'done' per step."""

    def report(step: str, state: str) -> None:
        if progress:
            progress(step, state)

    for step in ("windows", "cpu", "ram"):
        report(step, "running")
    raw = windows.run_probe()
    features = windows.cpu_features()
    for step in ("windows", "cpu", "ram"):
        report(step, "done")

    report("gpu", "running")
    smi = nvidia.detect()
    report("gpu", "done")
    report("cuda", "running")
    report("cuda", "done")
    report("storage", "running")
    profile = _apply_fallbacks(build_profile(raw, smi, features))
    report("storage", "done")
    return profile


def live_memory() -> dict[str, Any]:
    """Fast poll for the status bar: RAM plus per-GPU VRAM."""
    vm = psutil.virtual_memory()
    return {
        "ram_total_bytes": int(vm.total),
        "ram_used_bytes": int(vm.total - vm.available),
        "gpus": nvidia.live_usage(),
    }
