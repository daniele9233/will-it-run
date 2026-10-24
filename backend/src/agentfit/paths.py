"""Windows data locations.

Machine-local, regenerable data (database, caches, logs, workflow checkpoints) lives in
``%LOCALAPPDATA%\\Will It Run\\Data``. The per-user installer puts the application itself in
``%LOCALAPPDATA%\\Will It Run``, so data gets its own subfolder and never mixes with program
files. User-authored, roamable data (settings, workflows) lives in ``%APPDATA%\\Will It Run``.
``AGENTFIT_HOME`` redirects both, which tests and portable installs use.

The app was first released as "AgentFit". On the first start under the new name the old
folders are copied to the new ones, so settings, benchmarks, teams and run history come
along. The old folders are left untouched; they can be deleted by hand afterwards.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from agentfit.branding import APP_NAME, LEGACY_APP_NAME

_legacy_checked = False


def _env_dir(var: str, fallback: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else fallback


def _local_base() -> Path:
    return _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local")


def _roaming_base() -> Path:
    return _env_dir("APPDATA", Path.home() / "AppData" / "Roaming")


def adopt_legacy_data() -> list[str]:
    """Copy the data folders of the previous product name to the current ones, once.

    Only folders that do not exist yet are created, so nothing is ever overwritten.
    Returns what was copied, for the log.
    """
    global _legacy_checked
    if _legacy_checked or LEGACY_APP_NAME == APP_NAME:
        return []
    _legacy_checked = True
    copied: list[str] = []
    pairs = (
        (_local_base() / LEGACY_APP_NAME / "Data", _local_base() / APP_NAME / "Data"),
        (_roaming_base() / LEGACY_APP_NAME, _roaming_base() / APP_NAME),
    )
    for old, new in pairs:
        if not old.is_dir() or new.exists():
            continue
        try:
            shutil.copytree(old, new)
        except OSError:
            # A half-made copy would look like valid data on the next start.
            shutil.rmtree(new, ignore_errors=True)
            continue
        copied.append(f"{old} -> {new}")
    return copied


def local_dir() -> Path:
    override = os.environ.get("AGENTFIT_HOME")
    if override:
        return Path(override) / "local"
    adopt_legacy_data()
    return _local_base() / APP_NAME / "Data"


def roaming_dir() -> Path:
    override = os.environ.get("AGENTFIT_HOME")
    if override:
        return Path(override) / "roaming"
    adopt_legacy_data()
    return _roaming_base() / APP_NAME


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return ensure(local_dir()) / "agentfit.sqlite3"


def checkpoint_db_path() -> Path:
    return ensure(local_dir()) / "checkpoints.sqlite3"


def cache_dir() -> Path:
    return ensure(local_dir() / "cache")


def logs_dir() -> Path:
    return ensure(local_dir() / "logs")


def workspaces_dir() -> Path:
    return ensure(local_dir() / "workspaces")


def settings_path() -> Path:
    return ensure(roaming_dir()) / "settings.yaml"


def workflows_dir() -> Path:
    return ensure(roaming_dir() / "workflows")
