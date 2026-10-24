"""Locate LM Studio on disk: home directory, CLI, application, models folder."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from agentfit.lmstudio.models import LMStudioInstall

DOWNLOAD_URL = "https://lmstudio.ai/download"


def lmstudio_home() -> Path:
    # LM Studio records a relocated home in ~/.lmstudio-home-pointer.
    pointer = Path.home() / ".lmstudio-home-pointer"
    if pointer.exists():
        try:
            target = Path(pointer.read_text(encoding="utf-8").strip())
            if target.is_dir():
                return target
        except OSError:
            pass
    return Path.home() / ".lmstudio"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def find_cli(home: Path) -> str | None:
    candidate = home / "bin" / "lms.exe"
    if candidate.exists():
        return str(candidate)
    return shutil.which("lms")


def find_app(home: Path) -> str | None:
    recorded = _read_json(home / ".internal" / "app-install-location.json").get("path")
    if recorded and Path(recorded).exists():
        return recorded
    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local:
        candidates.append(Path(local) / "Programs" / "LM Studio" / "LM Studio.exe")
    program_files = os.environ.get("PROGRAMFILES")
    if program_files:
        candidates.append(Path(program_files) / "LM Studio" / "LM Studio.exe")
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def find_models_dir(home: Path) -> str | None:
    """Read only the ``downloadsFolder`` key from LM Studio's settings files.

    The classic app keeps it in ``<home>/settings.json``; newer app variants keep it in
    ``<home>/apps/<app>/settings.json``. Falls back to the default ``<home>/models``.
    """
    candidates = [home / "settings.json"]
    apps = home / "apps"
    if apps.is_dir():
        candidates += sorted(apps.glob("*/settings.json"))
    for settings in candidates:
        folder = _read_json(settings).get("downloadsFolder")
        if isinstance(folder, str) and folder and Path(folder).is_dir():
            return folder
    default = home / "models"
    return str(default) if default.is_dir() else None


def configured_port(home: Path) -> int | None:
    port = _read_json(home / ".internal" / "http-server-config.json").get("port")
    return int(port) if isinstance(port, int) else None


def detect_install() -> LMStudioInstall:
    """Filesystem-only detection. Server and API state are filled in by the runtime."""
    home = lmstudio_home()
    notes: list[str] = []
    if not home.is_dir():
        return LMStudioInstall(installed=False, notes=["LM Studio home directory not found."])
    cli = find_cli(home)
    app = find_app(home)
    models_dir = find_models_dir(home)
    if not cli:
        notes.append("lms CLI not found. Open LM Studio once, or run its bootstrap, to install it.")
    if not models_dir:
        notes.append("Could not determine the LM Studio models directory.")
    return LMStudioInstall(
        installed=bool(cli or app),
        home=str(home),
        app_path=app,
        cli_path=cli,
        models_dir=models_dir,
        server_port=configured_port(home),
        notes=notes,
    )
