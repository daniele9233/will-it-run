from __future__ import annotations

import pytest

from agentfit.config import HardwareSettings
from agentfit.hardware import compute_budget, gpu_tables, nvidia, windows
from agentfit.hardware.models import Confidence, GpuBackend, GpuVendor
from conftest import budget_for, load_profile

# -- NVIDIA parsing -----------------------------------------------------------


def test_nvidia_csv_parses_all_fields():
    text = "0, NVIDIA GeForce RTX 4080 SUPER, 16376, 1246, 14802, 610.62, 8, 40, GPU-abc, 00000000:01:00.0"
    (gpu,) = nvidia.parse_query_csv(text)
    assert gpu.name == "NVIDIA GeForce RTX 4080 SUPER"
    assert gpu.vendor == GpuVendor.NVIDIA and gpu.backend == GpuBackend.CUDA
    assert gpu.vram_total_mb == 16376 and gpu.vram_used_mb == 1246 and gpu.vram_free_mb == 14802
    assert gpu.driver_version == "610.62"
    assert gpu.memory_bandwidth_gbps == 736.0
    assert gpu.compute_capability == "8.9"
    assert gpu.vram_confidence == Confidence.HIGH


def test_nvidia_csv_tolerates_not_available_cells_and_junk():
    text = "garbage line\n0, NVIDIA GeForce RTX 3060, 12288, [N/A], [N/A], 551.23, N/A, N/A, GPU-x, 00000000:01:00.0\n"
    (gpu,) = nvidia.parse_query_csv(text)
    assert gpu.vram_total_mb == 12288
    assert gpu.vram_used_mb is None and gpu.utilization_percent is None


def test_nvidia_csv_empty_output_means_no_gpus():
    assert nvidia.parse_query_csv("") == []


@pytest.mark.parametrize(
    "banner, expected",
    [
        ("| NVIDIA-SMI 560.94   Driver Version: 560.94   CUDA Version: 12.6 |", "12.6"),
        ("| NVIDIA-SMI 610.62   KMD Version: 610.62   CUDA UMD Version: 13.3 |", "13.3"),
        ("no cuda here", None),
    ],
)
def test_cuda_version_from_banner(banner, expected):
    assert nvidia.parse_cuda_version(banner) == expected


def test_mobile_gpu_has_no_desktop_bandwidth():
    assert gpu_tables.memory_bandwidth_gbps("NVIDIA GeForce RTX 4070 Laptop GPU") is None
    assert gpu_tables.memory_bandwidth_gbps("NVIDIA GeForce RTX 4070") == 504.0


def test_specific_names_win_over_prefixes():
    assert gpu_tables.memory_bandwidth_gbps("RTX 4080 SUPER") == 736.0
    assert gpu_tables.memory_bandwidth_gbps("RTX 4080") == 717.0
    assert gpu_tables.memory_bandwidth_gbps("RTX 4070 Ti SUPER") == 672.0


# -- AMD and Intel parsing ----------------------------------------------------


def test_amd_discrete_uses_registry_vram_not_saturated_wmi():
    profile = load_profile("amd_rx7900xtx_24gb_32gb")
    discrete = profile.primary_gpu
    assert discrete is not None
    assert discrete.vendor == GpuVendor.AMD and discrete.backend == GpuBackend.VULKAN
    assert discrete.name == "AMD Radeon RX 7900 XTX"
    assert round(discrete.vram_total_gb) == 24  # WMI alone would have said 4 GB
    assert "registry" in discrete.sources and discrete.vram_confidence == Confidence.HIGH
    assert discrete.memory_bandwidth_gbps == 960.0


def test_amd_integrated_graphics_is_not_an_inference_gpu():
    profile = load_profile("amd_rx7900xtx_24gb_32gb")
    integrated = [g for g in profile.gpus if g.is_integrated]
    assert [g.name for g in integrated] == ["AMD Radeon(TM) Graphics"]
    assert integrated[0] not in profile.inference_gpus


def test_intel_arc_is_discrete_and_uhd_is_integrated():
    profile = load_profile("intel_arc_a770_16gb_32gb")
    arc = profile.primary_gpu
    assert arc is not None and arc.vendor == GpuVendor.INTEL
    assert "A770" in arc.name and not arc.is_integrated
    assert round(arc.vram_total_gb) == 16
    uhd = next(g for g in profile.gpus if "UHD" in g.name)
    assert uhd.is_integrated and not uhd.usable_for_inference
    assert uhd.shared_memory_mb and uhd.shared_memory_mb > 8000


@pytest.mark.parametrize(
    "name, vendor, integrated",
    [
        ("Intel(R) Arc(TM) A770 Graphics", GpuVendor.INTEL, False),
        ("Intel(R) Arc(TM) B580 Graphics", GpuVendor.INTEL, False),
        ("Intel(R) Arc(TM) Graphics", GpuVendor.INTEL, True),
        ("Intel(R) Iris(R) Xe Graphics", GpuVendor.INTEL, True),
        ("AMD Radeon RX 6700 XT", GpuVendor.AMD, False),
        ("AMD Radeon(TM) 780M", GpuVendor.AMD, True),
        ("NVIDIA GeForce GTX 1650", GpuVendor.NVIDIA, False),
    ],
)
def test_integrated_detection(name, vendor, integrated):
    assert gpu_tables.vendor_from_name(name) == vendor
    assert gpu_tables.is_integrated_gpu(name, vendor) is integrated


def test_saturated_wmi_value_without_registry_is_low_confidence():
    raw = {"video": [{"name": "AMD Radeon RX 6800", "adapter_ram": "4293918720", "pnp": "PCI\\VEN_1002"}]}
    (gpu,) = windows.parse_video_controllers(raw)
    assert gpu.vram_confidence == Confidence.LOW


def test_virtual_display_adapters_are_ignored():
    raw = {"video": [{"name": "Microsoft Basic Display Adapter", "adapter_ram": "0"}, {"name": "Parsec Virtual Display Adapter", "adapter_ram": "0"}]}
    assert windows.parse_video_controllers(raw) == []


def test_gpu_names_match_across_trademark_decoration():
    assert windows.normalize_gpu_name("AMD Radeon(TM) Graphics") == windows.normalize_gpu_name("AMD Radeon Graphics")


# -- RAM ----------------------------------------------------------------------


def test_ram_detection_reads_modules_type_and_speed():
    profile = load_profile("rtx4080super_16gb_32gb")
    ram = profile.ram
    assert len(ram.modules) == 2
    assert ram.installed_bytes == 32 * 1024**3
    assert ram.memory_type == "DDR5" and ram.speed_mhz == 4800
    assert ram.bandwidth_gbps == 76.8  # 4800 MT/s x 8 bytes x 2 channels


def test_installed_ram_is_not_treated_as_available():
    profile = load_profile("rtx4080super_16gb_32gb")
    assert profile.ram.total_bytes < profile.ram.installed_bytes
    assert profile.ram.available_bytes < profile.ram.total_bytes
    budget = compute_budget(profile)
    assert budget.ram_usable_gb < profile.ram.total_gb
    assert budget.ram_usable_gb == pytest.approx(profile.ram.total_gb * 0.85, abs=0.05)


def test_ddr4_type_and_single_stick_bandwidth():
    assert load_profile("rtx3060_12gb_32gb").ram.memory_type == "DDR4"
    assert windows.estimate_ddr_bandwidth_gbps(3200, 1) == 25.6
    assert windows.estimate_ddr_bandwidth_gbps(None, 2) is None


def test_ram_parsing_survives_missing_module_data():
    ram = windows.parse_ram({"os": {"total_kb": "16000000", "free_kb": "8000000"}, "memory": None})
    assert ram.total_bytes == 16000000 * 1024 and ram.modules == [] and ram.installed_bytes is None


# -- storage ------------------------------------------------------------------


def test_windows_storage_detection():
    drives = load_profile("rtx4080super_16gb_32gb").drives
    assert [d.letter for d in drives] == ["C:", "D:"]
    c, d = drives
    assert round(c.free_gb) == 11 and round(d.free_gb) == 116
    assert c.filesystem == "NTFS" and d.media_type == "SSD" and d.bus_type == "NVMe"


def test_single_disk_probe_object_instead_of_list():
    raw = {"disks": {"letter": "C:", "fs": "NTFS", "size": "1000", "free": "400"}}
    (drive,) = windows.parse_drives(raw)
    assert drive.letter == "C:" and drive.free_bytes == 400


# -- CPU and OS ---------------------------------------------------------------


def test_cpu_parsing():
    cpu = load_profile("rtx4080super_16gb_32gb").cpu
    assert cpu.manufacturer == "Intel" and cpu.model == "Intel(R) Core(TM) i7-14700K"
    assert cpu.physical_cores == 20 and cpu.logical_cores == 28 and cpu.architecture == "x64"
    assert cpu.features["avx2"] is True and cpu.features["avx512f"] is False


def test_windows_11_detection():
    assert load_profile("rtx4080super_16gb_32gb").os.is_windows_11
    assert not windows.parse_os({"os": {"caption": "Microsoft Windows 10 Pro", "build": "19045"}}).is_windows_11


def test_empty_probe_yields_a_profile_with_a_warning():
    from agentfit.hardware.detect import build_profile

    profile = build_profile({}, [], {})
    assert profile.gpus == []
    assert any("CIM probe" in w for w in profile.warnings)
    assert profile.confidence["hardware_detection"] == Confidence.LOW


# -- VRAM reserve and budgets -------------------------------------------------


def test_vram_reserve_matches_documented_example(budget_4080s):
    gpu = budget_4080s.gpus[0]
    assert gpu.total_gb == pytest.approx(16.0, abs=0.05)
    assert gpu.os_reserve_gb == 1.0
    assert gpu.safety_reserve_gb == pytest.approx(0.8, abs=0.01)
    assert budget_4080s.vram_usable_gb == pytest.approx(14.2, abs=0.05)


def test_vram_reserve_is_configurable():
    profile = load_profile("rtx4080super_16gb_32gb")
    generous = compute_budget(profile, HardwareSettings(vram_os_reserve_gb=0.5, vram_reserve_percent=0))
    cautious = compute_budget(profile, HardwareSettings(vram_os_reserve_gb=2.0, vram_reserve_percent=10))
    assert generous.vram_usable_gb == pytest.approx(15.5, abs=0.05)
    assert cautious.vram_usable_gb == pytest.approx(12.4, abs=0.05)


def test_integrated_gpu_adds_nothing_to_the_budget():
    budget = budget_for("rtx4080super_16gb_32gb")
    assert len(budget.gpus) == 1 and not budget.split_supported


def test_cpu_only_machine_has_no_vram_budget():
    budget = budget_for("cpu_only_16gb")
    assert not budget.has_gpu and budget.vram_usable_gb == 0
    assert budget.ram_usable_gb == pytest.approx(13.5, abs=0.2)


def test_dual_gpu_detected_separately_and_not_naively_summed():
    profile = load_profile("dual_nvidia_4090_3090_64gb")
    assert [round(g.vram_total_gb) for g in profile.inference_gpus] == [24, 24]
    budget = compute_budget(profile)
    assert budget.split_supported
    assert budget.single_gpu_usable_gb < 24
    # A split model gets less than 24 + 24: reserves and per-GPU overhead come off.
    assert budget.single_gpu_usable_gb < budget.split_usable_gb < 44
    # The slower card bounds throughput.
    assert budget.gpu_bandwidth_gbps == 936.0


@pytest.mark.parametrize(
    "fixture, vram",
    [
        ("rtx3060_12gb_32gb", 12),
        ("rtx4060ti_16gb_32gb", 16),
        ("rtx4080super_16gb_32gb", 16),
        ("rtx4090_24gb_64gb", 24),
        ("rtx5090_32gb_64gb", 32),
    ],
)
def test_every_single_gpu_fixture(fixture, vram):
    profile = load_profile(fixture)
    gpu = profile.primary_gpu
    assert gpu is not None and round(gpu.vram_total_gb) == vram
    assert gpu.sources[0] == "nvidia-smi"
    budget = compute_budget(profile)
    assert 0 < budget.vram_usable_gb < vram
    assert budget.gpu_bandwidth_gbps is not None
    assert profile.fingerprint() == load_profile(fixture).fingerprint()


def test_the_overflow_of_a_model_is_what_its_process_holds_in_system_ram():
    """Lines as the counter script prints them on the development PC: bytes, process, path."""
    lines = "\n".join([
        "899678208|llama-server|C:\\Users\\me\\.lmstudio\\extensions\\backends\\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.56.0\\llama-server.exe",
        "68157440|CefSharp.BrowserSubprocess|C:\\Program Files\\Some App\\CefSharp.BrowserSubprocess.exe",
        "31457280|LM Studio|C:\\Users\\me\\AppData\\Local\\Programs\\LM Studio\\LM Studio.exe",
        "garbage",
        "",
    ])
    # The model server and LM Studio's own window count; another program's share of system RAM does not.
    assert windows.parse_model_spill(lines) == 888.0
    assert windows.parse_model_spill("") == 0.0
    assert windows.parse_model_spill("12345|dwm|") == 0.0
