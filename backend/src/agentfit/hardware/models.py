"""Typed hardware profile."""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field

GIB = 1024**3
MIB = 1024**2


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class GpuVendor(str, Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    OTHER = "other"


class GpuBackend(str, Enum):
    CUDA = "cuda"
    VULKAN = "vulkan"
    NONE = "none"


class OsInfo(BaseModel):
    caption: str = "Unknown"
    version: str = ""
    build: str = ""
    architecture: str = ""

    @property
    def is_windows_11(self) -> bool:
        try:
            return int(self.build) >= 22000
        except ValueError:
            return False


class CpuInfo(BaseModel):
    manufacturer: str = "Unknown"
    model: str = "Unknown"
    architecture: str = "x64"
    physical_cores: int = 0
    logical_cores: int = 0
    max_clock_mhz: int = 0
    sockets: int = 1
    # Instruction sets relevant to llama.cpp CPU inference; None = could not be determined.
    features: dict[str, bool | None] = Field(default_factory=dict)


class MemoryModule(BaseModel):
    capacity_bytes: int = 0
    speed_mhz: int | None = None
    memory_type: str | None = None
    manufacturer: str | None = None
    slot: str | None = None


class RamInfo(BaseModel):
    # What Windows can address. Slightly below installed because of hardware reservations.
    total_bytes: int = 0
    available_bytes: int = 0
    # Sum of DIMM capacities; None when SMBIOS does not report modules.
    installed_bytes: int | None = None
    memory_type: str | None = None
    speed_mhz: int | None = None
    modules: list[MemoryModule] = Field(default_factory=list)
    # Estimated dual-channel bandwidth, used when model weights spill to system RAM.
    bandwidth_gbps: float | None = None

    @property
    def total_gb(self) -> float:
        return self.total_bytes / GIB

    @property
    def available_gb(self) -> float:
        return self.available_bytes / GIB


class GpuInfo(BaseModel):
    index: int = 0
    vendor: GpuVendor = GpuVendor.OTHER
    name: str = "Unknown GPU"
    vram_total_mb: float = 0
    vram_used_mb: float | None = None
    vram_free_mb: float | None = None
    # Windows "shared GPU memory" (system RAM the adapter may borrow). Not usable as VRAM for fit.
    shared_memory_mb: float | None = None
    driver_version: str | None = None
    driver_date: str | None = None
    is_integrated: bool = False
    backend: GpuBackend = GpuBackend.NONE
    cuda_version: str | None = None
    compute_capability: str | None = None
    memory_bandwidth_gbps: float | None = None
    utilization_percent: float | None = None
    temperature_c: float | None = None
    pci_id: str | None = None
    uuid: str | None = None
    # Which probes contributed to this entry, e.g. ["nvidia-smi", "wmi", "registry"].
    sources: list[str] = Field(default_factory=list)
    vram_confidence: Confidence = Confidence.UNKNOWN

    @property
    def vram_total_gb(self) -> float:
        return self.vram_total_mb / 1024

    @property
    def usable_for_inference(self) -> bool:
        return not self.is_integrated and self.backend != GpuBackend.NONE and self.vram_total_mb >= 2048


class DriveInfo(BaseModel):
    letter: str
    label: str | None = None
    filesystem: str | None = None
    total_bytes: int = 0
    free_bytes: int = 0
    media_type: str | None = None
    bus_type: str | None = None

    @property
    def free_gb(self) -> float:
        return self.free_bytes / GIB

    @property
    def total_gb(self) -> float:
        return self.total_bytes / GIB


class HardwareProfile(BaseModel):
    os: OsInfo = Field(default_factory=OsInfo)
    cpu: CpuInfo = Field(default_factory=CpuInfo)
    ram: RamInfo = Field(default_factory=RamInfo)
    gpus: list[GpuInfo] = Field(default_factory=list)
    drives: list[DriveInfo] = Field(default_factory=list)
    detected_at: str = ""
    confidence: dict[str, Confidence] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    # True for synthetic profiles (fixtures, what-if analysis).
    simulated: bool = False

    @property
    def inference_gpus(self) -> list[GpuInfo]:
        return [g for g in self.gpus if g.usable_for_inference]

    @property
    def primary_gpu(self) -> GpuInfo | None:
        usable = self.inference_gpus
        if usable:
            return max(usable, key=lambda g: g.vram_total_mb)
        return None

    def fingerprint(self) -> str:
        """Stable id for "this hardware", so benchmarks can be matched to it."""
        parts = [self.cpu.model, str(round(self.ram.total_bytes / GIB))]
        parts += [f"{g.name}:{round(g.vram_total_mb / 1024)}" for g in self.gpus]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
