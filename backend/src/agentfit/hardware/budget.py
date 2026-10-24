"""Turn a hardware profile into the memory Will It Run is willing to plan with.

Installed memory is never treated as fully available: Windows and the desktop keep
VRAM, and a configurable safety margin sits on top.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentfit.config import HardwareSettings
from agentfit.hardware.models import GpuBackend, HardwareProfile
from agentfit.i18n import tr

# VRAM a second (and each further) GPU loses to its own runtime context when a model is split.
MULTI_GPU_SPLIT_OVERHEAD_GB = 0.5


class GpuBudget(BaseModel):
    index: int
    name: str
    backend: GpuBackend
    total_gb: float
    os_reserve_gb: float
    safety_reserve_gb: float
    usable_gb: float
    bandwidth_gbps: float | None = None


class ResourceBudget(BaseModel):
    gpus: list[GpuBudget] = Field(default_factory=list)
    # Largest single-GPU budget: the limit for runtimes that cannot split a model.
    single_gpu_usable_gb: float = 0.0
    # Budget when one model is split across all same-backend GPUs (llama.cpp layer split).
    split_usable_gb: float = 0.0
    split_supported: bool = False
    ram_total_gb: float = 0.0
    ram_reserve_gb: float = 0.0
    ram_usable_gb: float = 0.0
    gpu_bandwidth_gbps: float | None = None
    ddr_bandwidth_gbps: float = 50.0
    cpu_cores: int = 0
    explanation: list[str] = Field(default_factory=list)

    @property
    def has_gpu(self) -> bool:
        return self.single_gpu_usable_gb > 0

    @property
    def vram_usable_gb(self) -> float:
        """VRAM pool for a single model on the primary runtime (GGUF via llama.cpp)."""
        return self.split_usable_gb if self.split_supported else self.single_gpu_usable_gb


def compute_budget(profile: HardwareProfile, settings: HardwareSettings | None = None) -> ResourceBudget:
    settings = settings or HardwareSettings()
    explanation: list[str] = []
    gpu_budgets: list[GpuBudget] = []
    for i, gpu in enumerate(profile.inference_gpus):
        total = gpu.vram_total_gb
        # Only the GPU driving the desktop pays the Windows compositor reserve in full.
        os_reserve = settings.vram_os_reserve_gb if i == 0 else min(0.3, settings.vram_os_reserve_gb)
        safety = total * settings.vram_reserve_percent / 100
        usable = max(0.0, total - os_reserve - safety)
        gpu_budgets.append(
            GpuBudget(
                index=gpu.index,
                name=gpu.name,
                backend=gpu.backend,
                total_gb=round(total, 2),
                os_reserve_gb=round(os_reserve, 2),
                safety_reserve_gb=round(safety, 2),
                usable_gb=round(usable, 2),
                bandwidth_gbps=gpu.memory_bandwidth_gbps,
            )
        )
        explanation.append(
            tr(
                "{name}: {total:.1f} GB - {os:.1f} GB Windows/desktop - {safety:.1f} GB safety = {usable:.1f} GB recommended usable",
                name=gpu.name, total=total, os=os_reserve, safety=safety, usable=usable,
            )
        )

    single = max((g.usable_gb for g in gpu_budgets), default=0.0)
    # A model can only be split across GPUs that share a backend.
    primary_backend = gpu_budgets[0].backend if gpu_budgets else GpuBackend.NONE
    same_backend = [g for g in gpu_budgets if g.backend == primary_backend]
    split_supported = len(same_backend) > 1
    split = single
    if split_supported:
        split = sum(g.usable_gb for g in same_backend) - MULTI_GPU_SPLIT_OVERHEAD_GB * (len(same_backend) - 1)
        explanation.append(
            tr(
                "{n} {backend} GPUs: one model can be split across them (llama.cpp layer split) for about {gb:.1f} GB, "
                "not the raw sum. Runtimes without splitting are limited to the largest single GPU.",
                n=len(same_backend), backend=primary_backend.value.upper(), gb=split,
            )
        )

    ram_total = profile.ram.total_gb
    ram_reserve = ram_total * settings.ram_reserve_percent / 100
    ram_usable = max(0.0, ram_total - ram_reserve)
    explanation.append(
        tr(
            "System RAM: {total:.1f} GB - {reserve:.1f} GB reserved for Windows and apps = {usable:.1f} GB recommended usable",
            total=ram_total, reserve=ram_reserve, usable=ram_usable,
        )
    )

    # When split, the slowest card bounds throughput.
    bandwidths = [g.bandwidth_gbps for g in same_backend if g.bandwidth_gbps]
    gpu_bandwidth = min(bandwidths) if bandwidths and len(bandwidths) == len(same_backend) else None
    ddr = settings.ddr_bandwidth_gbps or profile.ram.bandwidth_gbps or 50.0

    return ResourceBudget(
        gpus=gpu_budgets,
        single_gpu_usable_gb=round(single, 2),
        split_usable_gb=round(split, 2),
        split_supported=split_supported,
        ram_total_gb=round(ram_total, 2),
        ram_reserve_gb=round(ram_reserve, 2),
        ram_usable_gb=round(ram_usable, 2),
        gpu_bandwidth_gbps=gpu_bandwidth,
        ddr_bandwidth_gbps=ddr,
        cpu_cores=profile.cpu.physical_cores,
        explanation=explanation,
    )
