"""Windows probes: one PowerShell/CIM round-trip returning JSON, plus pure parsers.

The parsers take plain dicts so unit tests can feed them recorded fixtures.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
from typing import Any

from agentfit.hardware import gpu_tables
from agentfit.hardware.models import (
    MIB,
    Confidence,
    CpuInfo,
    DriveInfo,
    GpuBackend,
    GpuInfo,
    GpuVendor,
    MemoryModule,
    OsInfo,
    RamInfo,
)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Win32_VideoController.AdapterRAM is a uint32 and saturates at 4 GB, so the true size is
# read from the driver's registry key (HardwareInformation.qwMemorySize, REG_QWORD bytes).
# The registry approach is adapted from llmfit (MIT, Copyright (c) Alex Jones).
PROBE_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$os  = Get-CimInstance Win32_OperatingSystem
$cpu = @(Get-CimInstance Win32_Processor)
$mem = @(Get-CimInstance Win32_PhysicalMemory)
$vid = @(Get-CimInstance Win32_VideoController)
$dsk = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')
$reg = @(Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\*' | ForEach-Object {
    $d = $_.DriverDesc
    $m = $_.'HardwareInformation.qwMemorySize'
    if ($null -eq $m) { $m = $_.'HardwareInformation.MemorySize' }
    if ($m -is [byte[]]) {
        if ($m.Length -ge 8) { $m = [System.BitConverter]::ToUInt64($m, 0) }
        elseif ($m.Length -ge 4) { $m = [System.BitConverter]::ToUInt32($m, 0) }
        else { $m = $null }
    } elseif ($m -is [int] -and $m -lt 0) { $m = [long]$m + 4294967296 }
    if ($d -and $m) { [pscustomobject]@{ name = [string]$d; bytes = [string]$m } }
})
$parts = @(Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object { [pscustomobject]@{ letter = [string]$_.DriveLetter; disk = [int]$_.DiskNumber } })
$phys  = @(Get-PhysicalDisk | ForEach-Object { [pscustomobject]@{ id = [string]$_.DeviceId; media = [string]$_.MediaType; bus = [string]$_.BusType } })
[pscustomobject]@{
  os = [pscustomobject]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber; arch = $os.OSArchitecture
                          total_kb = [string]$os.TotalVisibleMemorySize; free_kb = [string]$os.FreePhysicalMemory }
  cpu = @($cpu | ForEach-Object { [pscustomobject]@{ name = $_.Name; manufacturer = $_.Manufacturer; cores = $_.NumberOfCores
                          logical = $_.NumberOfLogicalProcessors; mhz = $_.MaxClockSpeed; arch = $_.Architecture } })
  memory = @($mem | ForEach-Object { [pscustomobject]@{ capacity = [string]$_.Capacity; speed = $_.Speed; configured = $_.ConfiguredClockSpeed
                          smbios_type = $_.SMBIOSMemoryType; manufacturer = $_.Manufacturer; slot = $_.DeviceLocator } })
  video = @($vid | ForEach-Object { [pscustomobject]@{ name = $_.Name; adapter_ram = [string]$_.AdapterRAM; driver = $_.DriverVersion
                          driver_date = [string]$_.DriverDate; pnp = $_.PNPDeviceID; status = $_.Status } })
  registry_vram = $reg
  disks = @($dsk | ForEach-Object { [pscustomobject]@{ letter = $_.DeviceID; label = $_.VolumeName; fs = $_.FileSystem
                          size = [string]$_.Size; free = [string]$_.FreeSpace } })
  partitions = $parts
  physical = $phys
} | ConvertTo-Json -Depth 5 -Compress
"""

_SMBIOS_MEMORY_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}
_CPU_ARCH = {0: "x86", 5: "ARM", 9: "x64", 12: "ARM64"}


def run_probe(timeout: float = 45.0) -> dict[str, Any]:
    """Run the PowerShell probe. Returns {} when PowerShell is unavailable or fails."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PROBE_SCRIPT],
            capture_output=True,
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    start = text.find("{")
    if start < 0:
        return {}
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return [v for v in value if isinstance(v, dict)]


def parse_os(raw: dict[str, Any]) -> OsInfo:
    os_raw = raw.get("os") or {}
    return OsInfo(
        caption=(os_raw.get("caption") or "Unknown").strip(),
        version=os_raw.get("version") or "",
        build=str(os_raw.get("build") or ""),
        architecture=os_raw.get("arch") or "",
    )


def parse_cpu(raw: dict[str, Any]) -> CpuInfo:
    cpus = _as_list(raw.get("cpu"))
    if not cpus:
        return CpuInfo()
    first = cpus[0]
    manufacturer = (first.get("manufacturer") or "Unknown").strip()
    pretty = {"GenuineIntel": "Intel", "AuthenticAMD": "AMD"}.get(manufacturer, manufacturer)
    return CpuInfo(
        manufacturer=pretty,
        model=" ".join((first.get("name") or "Unknown").split()),
        architecture=_CPU_ARCH.get(_int(first.get("arch"), 9), "x64"),
        physical_cores=sum(_int(c.get("cores")) for c in cpus),
        logical_cores=sum(_int(c.get("logical")) for c in cpus),
        max_clock_mhz=_int(first.get("mhz")),
        sockets=len(cpus),
    )


def estimate_ddr_bandwidth_gbps(speed_mhz: int | None, module_count: int) -> float | None:
    """Theoretical peak: transfers/s x 8 bytes x channels. Assumes at most dual channel."""
    if not speed_mhz or module_count <= 0:
        return None
    channels = 2 if module_count >= 2 else 1
    return round(speed_mhz * 8 * channels / 1000, 1)


def parse_ram(raw: dict[str, Any]) -> RamInfo:
    os_raw = raw.get("os") or {}
    modules: list[MemoryModule] = []
    for m in _as_list(raw.get("memory")):
        capacity = _int(m.get("capacity"))
        if capacity <= 0:
            continue
        speed = _int(m.get("configured")) or _int(m.get("speed")) or None
        modules.append(
            MemoryModule(
                capacity_bytes=capacity,
                speed_mhz=speed,
                memory_type=_SMBIOS_MEMORY_TYPES.get(_int(m.get("smbios_type"))),
                manufacturer=(m.get("manufacturer") or "").strip() or None,
                slot=(m.get("slot") or "").strip() or None,
            )
        )
    speeds = [m.speed_mhz for m in modules if m.speed_mhz]
    types = [m.memory_type for m in modules if m.memory_type]
    speed = min(speeds) if speeds else None
    return RamInfo(
        total_bytes=_int(os_raw.get("total_kb")) * 1024,
        available_bytes=_int(os_raw.get("free_kb")) * 1024,
        installed_bytes=sum(m.capacity_bytes for m in modules) or None,
        memory_type=types[0] if types else None,
        speed_mhz=speed,
        modules=modules,
        bandwidth_gbps=estimate_ddr_bandwidth_gbps(speed, len(modules)),
    )


def normalize_gpu_name(name: str) -> str:
    """Lowercase and strip trademark decoration so WMI and registry names compare equal."""
    lowered = name.lower().replace("(tm)", " ").replace("(r)", " ").replace("(c)", " ")
    return " ".join("".join(c if c.isalnum() else " " for c in lowered).split())


def parse_registry_vram(raw: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in _as_list(raw.get("registry_vram")):
        size = _int(entry.get("bytes"))
        name = entry.get("name")
        if name and size > 0:
            key = normalize_gpu_name(name)
            out[key] = max(out.get(key, 0), size)
    return out


_VIRTUAL_ADAPTERS = ("microsoft basic", "remote display", "virtual", "parsec", "displaylink", "citrix", "vmware", "hyper-v", "meta virtual", "idd")


def parse_video_controllers(raw: dict[str, Any], ram_total_bytes: int = 0) -> list[GpuInfo]:
    """Build GPU entries from WMI, correcting VRAM from the registry."""
    registry = parse_registry_vram(raw)
    gpus: list[GpuInfo] = []
    for i, v in enumerate(_as_list(raw.get("video"))):
        name = " ".join((v.get("name") or "").split())
        if not name or any(t in name.lower() for t in _VIRTUAL_ADAPTERS):
            continue
        vendor = gpu_tables.vendor_from_name(name, v.get("pnp"))
        integrated = gpu_tables.is_integrated_gpu(name, vendor)
        wmi_bytes = _int(v.get("adapter_ram"))
        reg_bytes = registry.get(normalize_gpu_name(name), 0)
        sources = ["wmi"]
        if reg_bytes:
            vram_bytes, confidence = reg_bytes, Confidence.HIGH
            sources.append("registry")
        else:
            vram_bytes = wmi_bytes
            # A uint32 that reads ~4 GB is saturated; the real figure may be larger.
            confidence = Confidence.LOW if wmi_bytes >= 4 * 1024**3 - 64 * MIB else Confidence.MEDIUM
        if vendor == GpuVendor.NVIDIA:
            backend = GpuBackend.CUDA
        elif vendor in (GpuVendor.AMD, GpuVendor.INTEL):
            backend = GpuBackend.VULKAN
        else:
            backend = GpuBackend.NONE
        gpus.append(
            GpuInfo(
                index=i,
                vendor=vendor,
                name=name,
                vram_total_mb=round(vram_bytes / MIB),
                # Windows lets any adapter borrow up to half of system RAM as shared memory.
                shared_memory_mb=round(ram_total_bytes / 2 / MIB) if ram_total_bytes else None,
                driver_version=v.get("driver") or None,
                driver_date=(v.get("driver_date") or "")[:10] or None,
                is_integrated=integrated,
                backend=backend,
                compute_capability=gpu_tables.compute_capability(name) if vendor == GpuVendor.NVIDIA else None,
                memory_bandwidth_gbps=None if integrated else gpu_tables.memory_bandwidth_gbps(name),
                pci_id=v.get("pnp") or None,
                sources=sources,
                vram_confidence=confidence,
            )
        )
    return gpus


def parse_drives(raw: dict[str, Any]) -> list[DriveInfo]:
    letter_to_disk = {p.get("letter", "").upper(): str(p.get("disk")) for p in _as_list(raw.get("partitions"))}
    physical = {str(p.get("id")): p for p in _as_list(raw.get("physical"))}
    drives: list[DriveInfo] = []
    for d in _as_list(raw.get("disks")):
        letter = (d.get("letter") or "").rstrip("\\")
        if not letter:
            continue
        disk = physical.get(letter_to_disk.get(letter.rstrip(":").upper(), ""), {})
        media = (disk.get("media") or "").strip()
        drives.append(
            DriveInfo(
                letter=letter,
                label=(d.get("label") or "").strip() or None,
                filesystem=d.get("fs") or None,
                total_bytes=_int(d.get("size")),
                free_bytes=_int(d.get("free")),
                media_type=media if media and media.lower() != "unspecified" else None,
                bus_type=(disk.get("bus") or "").strip() or None,
            )
        )
    return sorted(drives, key=lambda d: d.letter)


# IsProcessorFeaturePresent ids (winnt.h PF_*).
_PF = {"sse4_1": 37, "sse4_2": 38, "avx": 39, "avx2": 40, "avx512f": 41}


def cpu_features() -> dict[str, bool | None]:
    """Query the instruction sets that llama.cpp CPU kernels care about."""
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except AttributeError:
        return {name: None for name in _PF}
    out: dict[str, bool | None] = {}
    for name, feature_id in _PF.items():
        try:
            out[name] = bool(kernel32.IsProcessorFeaturePresent(feature_id))
        except OSError:
            out[name] = None
    return out


def free_bytes_for_path(path: str) -> int | None:
    """Free space available to the current user on the volume holding ``path``."""
    free = ctypes.c_ulonglong(0)
    try:
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(path), ctypes.byref(free), None, None
        )
    except (AttributeError, OSError):
        return None
    return free.value if ok else None


# -- GPU memory a model holds in system RAM ---------------------------------------------

# Windows counts, per process, the GPU memory that lives in system RAM instead of on the card.
# For the process that runs the model this is the overflow itself: what did not fit in VRAM.
_SPILL_SCRIPT = (
    r"$s=(Get-Counter '\GPU Process Memory(*)\Shared Usage' -ErrorAction SilentlyContinue).CounterSamples;"
    r"foreach($x in $s){ if($x.CookedValue -ge 1MB){ $m=[regex]::Match($x.InstanceName,'pid_(\d+)');"
    " $p=Get-Process -Id ([int]$m.Groups[1].Value) -ErrorAction SilentlyContinue;"
    " '{0}|{1}|{2}' -f [int64]$x.CookedValue,$p.ProcessName,$p.Path } }"
)
# Processes that run models for LM Studio: its llama.cpp server, or anything started from its folders.
_MODEL_PROCESS_MARKS = ("llama-server", "llama", "lm studio", "lmstudio", ".lmstudio")


def parse_model_spill(text: str) -> float:
    """MiB of shared GPU memory held by model processes, from the lines the counter script prints."""
    total = 0
    for line in text.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        who = f"{parts[1]} {parts[2]}".lower()
        if any(mark in who for mark in _MODEL_PROCESS_MARKS):
            total += int(parts[0])
    return round(total / (1024 * 1024), 1)


def model_spill_mib(timeout: float = 12.0) -> float | None:
    """How much of the loaded model sits in system RAM right now, in MiB. None when Windows cannot say.

    When a model does not fit the card, Windows does not refuse the load: the driver keeps the
    overflow in system RAM and the model answers several times slower. nvidia-smi does not
    show it (the card just reads "nearly full"); this counter does. Measured on the
    development PC: 858 MiB here while a 27B model answered at 8 tokens per second instead of 60.
    """
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SPILL_SCRIPT],
            capture_output=True, timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return parse_model_spill(proc.stdout.decode("utf-8", errors="replace"))
