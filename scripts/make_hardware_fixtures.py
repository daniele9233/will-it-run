"""Generate the Windows hardware fixtures used by the test suite.

Each fixture mirrors what the live probes return: the JSON document produced by the
PowerShell/CIM probe (``agentfit.hardware.windows.PROBE_SCRIPT``) and the CSV text from
``nvidia-smi --query-gpu=...``. Values follow real hardware: WMI's AdapterRAM saturates
at 4 GB while the registry carries the true size.

Usage: python scripts/make_hardware_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "backend/tests/fixtures/hardware"
GIB = 1024**3
WMI_CAP = 4293918720  # what Win32_VideoController.AdapterRAM reports for any card >= 4 GB

NVIDIA = {
    "RTX 3060": ("NVIDIA GeForce RTX 3060", 12288, "VEN_10DE&DEV_2504"),
    "RTX 4060 Ti": ("NVIDIA GeForce RTX 4060 Ti", 16380, "VEN_10DE&DEV_2805"),
    "RTX 4080 SUPER": ("NVIDIA GeForce RTX 4080 SUPER", 16376, "VEN_10DE&DEV_2702"),
    "RTX 4090": ("NVIDIA GeForce RTX 4090", 24564, "VEN_10DE&DEV_2684"),
    "RTX 3090": ("NVIDIA GeForce RTX 3090", 24576, "VEN_10DE&DEV_2204"),
    "RTX 5090": ("NVIDIA GeForce RTX 5090", 32607, "VEN_10DE&DEV_2B85"),
}


def make(
    name: str,
    cpu: tuple[str, str, int, int],
    ram_gb: int,
    nvidia: list[str],
    other_gpus: list[dict] | None = None,
    memory_type: int = 34,
    speed: int = 5600,
    drives: list[tuple[str, int, int]] | None = None,
) -> None:
    cpu_name, vendor, cores, threads = cpu
    video, registry, smi = [], [], []
    for index, key in enumerate(nvidia):
        gpu_name, mib, pnp = NVIDIA[key]
        video.append({"name": gpu_name, "adapter_ram": str(WMI_CAP), "driver": "32.0.15.6094", "driver_date": "20250114000000.000000-000", "pnp": f"PCI\\{pnp}&SUBSYS_0000&REV_A1\\4&0&0&00{index}", "status": "OK"})
        registry.append({"name": gpu_name, "bytes": str(mib * 1024 * 1024)})
        used = 900 if index == 0 else 12
        smi.append(f"{index}, {gpu_name}, {mib}, {used}, {mib - used}, 560.94, 3, 41, GPU-0000000{index}, 00000000:0{index + 1}:00.0")
    for gpu in other_gpus or []:
        video.append({"name": gpu["name"], "adapter_ram": str(min(gpu["bytes"], WMI_CAP)), "driver": gpu.get("driver", "31.0.101.5186"), "driver_date": "20240901000000.000000-000", "pnp": gpu["pnp"], "status": "OK"})
        registry.append({"name": gpu["name"], "bytes": str(gpu["bytes"])})
    sticks = 2
    visible_kb = int(ram_gb * 1024 * 1024 * 0.993)
    probe = {
        "os": {"caption": "Microsoft Windows 11 Pro", "version": "10.0.26100", "build": "26100", "arch": "64 bit", "total_kb": str(visible_kb), "free_kb": str(int(visible_kb * 0.6))},
        "cpu": [{"name": cpu_name, "manufacturer": vendor, "cores": cores, "logical": threads, "mhz": 3400, "arch": 9}],
        "memory": [{"capacity": str(ram_gb // sticks * GIB), "speed": speed, "configured": speed, "smbios_type": memory_type, "manufacturer": "Kingston", "slot": f"DIMM {i}"} for i in range(sticks)],
        "video": video,
        "registry_vram": registry,
        "disks": [{"letter": letter, "label": "", "fs": "NTFS", "size": str(total * GIB), "free": str(free * GIB)} for letter, total, free in (drives or [("C:", 931, 412)])],
        "partitions": [{"letter": letter[0], "disk": i} for i, (letter, _, _) in enumerate(drives or [("C:", 931, 412)])],
        "physical": [{"id": str(i), "media": "SSD", "bus": "NVMe"} for i, _ in enumerate(drives or [("C:", 931, 412)])],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"probe": probe, "nvidia_smi": "\n".join(smi)}, indent=2), encoding="utf-8")
    print("wrote", name)


INTEL_14700K = ("Intel(R) Core(TM) i7-14700K", "GenuineIntel", 20, 28)
RYZEN_7800X3D = ("AMD Ryzen 7 7800X3D 8-Core Processor", "AuthenticAMD", 8, 16)
RYZEN_5600 = ("AMD Ryzen 5 5600 6-Core Processor", "AuthenticAMD", 6, 12)
INTEL_13400 = ("13th Gen Intel(R) Core(TM) i5-13400", "GenuineIntel", 10, 16)
UHD_770 = {"name": "Intel(R) UHD Graphics 770", "bytes": 2 * GIB, "pnp": "PCI\\VEN_8086&DEV_A780&SUBSYS_0000&REV_04\\3&0&0&10"}

make("rtx3060_12gb_32gb", RYZEN_5600, 32, ["RTX 3060"], memory_type=26, speed=3200)
make("rtx4060ti_16gb_32gb", INTEL_13400, 32, ["RTX 4060 Ti"], [UHD_770], memory_type=26, speed=3200)
make("rtx4080super_16gb_32gb", INTEL_14700K, 32, ["RTX 4080 SUPER"], [UHD_770], speed=4800, drives=[("C:", 292, 11), ("D:", 639, 116)])
make("rtx4090_24gb_64gb", RYZEN_7800X3D, 64, ["RTX 4090"], speed=6000)
make("rtx5090_32gb_64gb", RYZEN_7800X3D, 64, ["RTX 5090"], speed=6000)
make("cpu_only_16gb", INTEL_13400, 16, [], [UHD_770], memory_type=26, speed=3200)
make("dual_nvidia_4090_3090_64gb", RYZEN_7800X3D, 64, ["RTX 4090", "RTX 3090"], speed=6000)
make(
    "amd_rx7900xtx_24gb_32gb", RYZEN_7800X3D, 32, [],
    [{"name": "AMD Radeon RX 7900 XTX", "bytes": 24 * GIB, "driver": "32.0.12033.1030", "pnp": "PCI\\VEN_1002&DEV_744C&SUBSYS_0000&REV_C8\\4&0&0&000"},
     {"name": "AMD Radeon(TM) Graphics", "bytes": 512 * 1024 * 1024, "driver": "32.0.12033.1030", "pnp": "PCI\\VEN_1002&DEV_164E&SUBSYS_0000&REV_C1\\4&0&0&001"}],
    speed=6000,
)
make(
    "intel_arc_a770_16gb_32gb", INTEL_13400, 32, [],
    [{"name": "Intel(R) Arc(TM) A770 Graphics", "bytes": 16 * GIB, "pnp": "PCI\\VEN_8086&DEV_56A0&SUBSYS_0000&REV_08\\4&0&0&000"}, UHD_770],
    memory_type=26, speed=3200,
)
