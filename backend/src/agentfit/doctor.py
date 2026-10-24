"""``willitrun doctor``: environment checks reported as PASS, WARNING or FAIL."""

from __future__ import annotations

import os
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import BaseModel

from agentfit import paths, storage
from agentfit.app import AppServices

PASS, WARNING, FAIL = "PASS", "WARNING", "FAIL"


class Check(BaseModel):
    name: str
    status: str
    detail: str
    hint: str = ""


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=True):
            pass
    except OSError:
        return False
    return True


def run_doctor(services: AppServices) -> list[Check]:
    checks: list[Check] = []

    def add(name: str, status: str, detail: str, hint: str = "") -> None:
        checks.append(Check(name=name, status=status, detail=detail, hint=hint))

    profile = services.hardware(refresh=True)
    settings = services.settings

    # Windows
    if not profile.os.caption or profile.os.caption == "Unknown":
        add("Windows version", FAIL, "Could not read the Windows version.", "Check that PowerShell and WMI are working.")
    elif profile.os.is_windows_11:
        add("Windows version", PASS, f"{profile.os.caption} (build {profile.os.build})")
    else:
        add("Windows version", WARNING, f"{profile.os.caption} (build {profile.os.build})", "Windows 11 x64 is the supported target.")

    # CPU
    cpu = profile.cpu
    if not cpu.physical_cores:
        add("CPU", FAIL, "CPU could not be detected.")
    elif cpu.features.get("avx2") is False:
        add("CPU", WARNING, f"{cpu.model}: no AVX2", "CPU inference needs AVX2 to be usable.")
    else:
        features = ", ".join(k.upper() for k, v in cpu.features.items() if v)
        add("CPU", PASS, f"{cpu.model}, {cpu.physical_cores} cores / {cpu.logical_cores} threads ({features})")

    # RAM
    ram = profile.ram
    if ram.total_gb < 8:
        add("RAM", FAIL, f"{ram.total_gb:.1f} GB installed", "At least 8 GB is needed; 16 GB or more is recommended.")
    elif ram.total_gb < 16:
        add("RAM", WARNING, f"{ram.total_gb:.1f} GB installed", "16 GB or more is recommended.")
    else:
        kind = f" {ram.memory_type}-{ram.speed_mhz}" if ram.memory_type and ram.speed_mhz else ""
        add("RAM", PASS, f"{ram.total_gb:.1f} GB{kind}, {ram.available_gb:.1f} GB available")

    # GPU
    gpu = profile.primary_gpu
    if gpu is None:
        if profile.gpus:
            add("GPU", WARNING, f"Only integrated graphics: {profile.gpus[0].name}", "Models will run on the CPU.")
        else:
            add("GPU", WARNING, "No GPU detected", "Models will run on the CPU.")
    else:
        extra = f" (+{len(profile.inference_gpus) - 1} more)" if len(profile.inference_gpus) > 1 else ""
        add("GPU", PASS, f"{gpu.name}, {gpu.vram_total_gb:.1f} GB VRAM{extra}; {services.budget().vram_usable_gb:.1f} GB recommended usable")
        if gpu.driver_version:
            add("GPU driver", PASS, f"{gpu.vendor.value.upper()} driver {gpu.driver_version}")
        else:
            add("GPU driver", WARNING, "Driver version unknown", "Install the latest GPU driver.")
        if gpu.vendor.value == "nvidia":
            if gpu.cuda_version:
                cc = f", compute capability {gpu.compute_capability}" if gpu.compute_capability else ""
                add("CUDA", PASS, f"Driver supports CUDA {gpu.cuda_version}{cc}")
            else:
                add("CUDA", WARNING, "CUDA version could not be read from nvidia-smi", "Reinstall the NVIDIA driver if GPU inference fails.")
        else:
            add("CUDA", WARNING, "Not an NVIDIA GPU: LM Studio will use the Vulkan backend.")

    # LM Studio
    lm = services.lmstudio_status(deep=True)
    if lm.installed:
        add("LM Studio", PASS, f"Found at {lm.app_path or lm.home}")
    else:
        add("LM Studio", FAIL, "LM Studio is not installed", "Download it from https://lmstudio.ai/download")
    if lm.cli_path:
        add("lms CLI", PASS, f"{lm.cli_path}" + (f" (version {lm.cli_version})" if lm.cli_version else ""))
    else:
        add("lms CLI", WARNING, "lms CLI not found", "Open LM Studio once so it installs the CLI.")
    if lm.api_reachable:
        add("LM Studio server", PASS, f"Reachable on port {lm.server_port}" + (f"; engines: {', '.join(lm.engines[:2])}" if lm.engines else ""))
    elif lm.server_running:
        add("LM Studio server", WARNING, f"Running on port {lm.server_port}, but Will It Run is configured for port {settings.runtime.lmstudio_port}", "Update the port in Settings.")
    else:
        add("LM Studio server", WARNING if lm.installed else FAIL, "Local server is not running", "Start it in LM Studio (Developer tab) or run: lms server start")

    # Python and LangGraph
    add("Python runtime", PASS, f"Python {sys.version.split()[0]}" + (" (bundled)" if getattr(sys, "frozen", False) else ""))
    try:
        add("LangGraph", PASS, f"langgraph {version('langgraph')}")
    except PackageNotFoundError:
        try:
            import langgraph  # noqa: F401

            add("LangGraph", PASS, "langgraph available")
        except ImportError:
            add("LangGraph", FAIL, "langgraph is not installed", "Reinstall Will It Run.")

    # Model directory
    models_dir = settings.storage.custom_model_dir or lm.models_dir
    if not models_dir:
        add("Model directory", WARNING, "Model directory unknown", "Set one in Settings > Storage.")
    elif not Path(models_dir).is_dir():
        add("Model directory", FAIL, f"{models_dir} does not exist")
    else:
        add("Model directory", PASS, f"{models_dir} ({len(services.installed_models())} models installed)")
        if _writable(Path(models_dir)):
            add("Permissions", PASS, f"Can write to {models_dir}")
        else:
            add("Permissions", FAIL, f"Cannot write to {models_dir}", "Choose a different model directory or fix its permissions.")

    # Disk
    if models_dir and os.path.isdir(models_dir):
        free = storage.free_gb_at(models_dir) or 0.0
        drive = storage.drive_of(models_dir)
        total = next((d.total_gb for d in profile.drives if d.letter.upper() == drive), 0.0)
        reserve = storage.reserve_gb(total, settings.storage) if total else settings.storage.min_free_gb
        safe = free - reserve
        if safe <= 0:
            add("Free disk", FAIL, f"{free:.0f} GB free on {drive}, below the {reserve:.0f} GB safety reserve", "Free up space or move model storage.")
        elif safe < 20:
            add("Free disk", WARNING, f"{free:.0f} GB free on {drive}; {safe:.0f} GB safely usable for models")
        else:
            add("Free disk", PASS, f"{free:.0f} GB free on {drive}; {safe:.0f} GB safely usable for models")

    # Database
    try:
        schema = services.db.schema_version()
        services.db.query("SELECT 1")
        add("Database", PASS, f"{paths.db_path()} (schema v{schema})")
    except Exception as exc:
        add("Database", FAIL, f"Database error: {exc}")
    if not _writable(paths.local_dir()):
        add("Data directory", FAIL, f"Cannot write to {paths.local_dir()}")
    return checks


def overall(checks: list[Check]) -> str:
    if any(c.status == FAIL for c in checks):
        return FAIL
    if any(c.status == WARNING for c in checks):
        return WARNING
    return PASS
