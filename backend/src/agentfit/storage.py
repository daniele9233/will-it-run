"""Disk-space safety for model downloads.

A download is only allowed when, after it completes, the drive still has its safety
reserve free. The reserve is ``reserve_percent`` of the drive's capacity, bounded below
by ``min_free_gb`` and above by ``MAX_RESERVE_GB`` so very large drives are not locked up.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from agentfit.config import StorageSettings
from agentfit.hardware.models import GIB, DriveInfo
from agentfit.hardware.windows import free_bytes_for_path
from agentfit.i18n import tr

MAX_RESERVE_GB = 30.0
MIN_TEMP_GB = 0.5


class StorageLocation(BaseModel):
    path: str
    drive: str
    free_gb: float
    total_gb: float
    reserve_gb: float
    safe_available_gb: float
    is_lmstudio_dir: bool = False
    recommended: bool = False
    reason: str = ""


class DiskPlan(BaseModel):
    target_dir: str
    drive: str
    download_gb: float
    temp_gb: float
    required_gb: float
    free_gb: float
    reserve_gb: float
    safe_available_gb: float
    after_install_free_gb: float
    after_install_safe_gb: float
    safe: bool
    verdict: str
    # The verdict without its SAFE / NOT SAFE prefix, for interfaces that show their own heading.
    reason: str = ""
    options: list[str] = Field(default_factory=list)


def drive_of(path: str) -> str:
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    return drive.upper() or path


def reserve_gb(total_gb: float, settings: StorageSettings) -> float:
    by_percent = total_gb * settings.reserve_percent / 100
    return round(min(max(settings.min_free_gb, by_percent), max(MAX_RESERVE_GB, settings.min_free_gb)), 2)


def _existing_parent(path: str) -> str:
    current = Path(path)
    while not current.exists() and current.parent != current:
        current = current.parent
    return str(current)


def free_gb_at(path: str) -> float | None:
    free = free_bytes_for_path(_existing_parent(path))
    return free / GIB if free is not None else None


def plan_download(
    size_bytes: int,
    target_dir: str,
    settings: StorageSettings,
    drives: list[DriveInfo],
    *,
    free_gb: float | None = None,
    smaller_options: list[str] | None = None,
) -> DiskPlan:
    """Decide whether a download of ``size_bytes`` into ``target_dir`` is safe."""
    drive = drive_of(target_dir)
    info = next((d for d in drives if d.letter.upper() == drive), None)
    total = info.total_gb if info else 0.0
    if free_gb is None:
        free_gb = free_gb_at(target_dir)
    if free_gb is None:
        free_gb = info.free_gb if info else 0.0
    download = size_bytes / GIB
    temp = max(MIN_TEMP_GB, download * settings.temp_overhead_percent / 100)
    required = download + temp
    reserve = reserve_gb(total, settings) if total else settings.min_free_gb
    safe_available = max(0.0, free_gb - reserve)
    safe = required <= safe_available

    options: list[str] = []
    if not safe:
        for option in smaller_options or []:
            options.append(tr("Use a smaller quantization: {option}", option=option))
        options.append(tr("Choose a smaller model"))
        options.append(tr("Delete unused models to free space"))
        for other in drives:
            if other.letter.upper() == drive:
                continue
            other_safe = other.free_gb - reserve_gb(other.total_gb, settings)
            if other_safe >= required:
                options.append(
                    tr("Save to drive {drive} instead ({gb:.0f} GB safely available)", drive=other.letter, gb=other_safe)
                )
        reason = tr(
            "It needs {required:.1f} GB but only {available:.1f} GB is safely available on {drive} "
            "({free:.1f} GB free minus a {reserve:.1f} GB reserve).",
            required=required, available=safe_available, drive=drive, free=free_gb, reserve=reserve,
        )
        verdict = tr("NOT SAFE: {reason}", reason=reason)
    else:
        reason = tr("{gb:.0f} GB will remain free on {drive}.", gb=free_gb - download, drive=drive)
        verdict = tr("SAFE TO INSTALL: {reason}", reason=reason)
    return DiskPlan(
        target_dir=target_dir,
        drive=drive,
        download_gb=round(download, 2),
        temp_gb=round(temp, 2),
        required_gb=round(required, 2),
        free_gb=round(free_gb, 2),
        reserve_gb=reserve,
        safe_available_gb=round(safe_available, 2),
        after_install_free_gb=round(free_gb - download, 2),
        after_install_safe_gb=round(safe_available - download, 2),
        safe=safe,
        verdict=verdict,
        reason=reason,
        options=options,
    )


def storage_locations(
    drives: list[DriveInfo], settings: StorageSettings, lmstudio_models_dir: str | None
) -> list[StorageLocation]:
    """Candidate model locations with a recommendation for where models should live."""
    locations: list[StorageLocation] = []
    lm_drive = drive_of(lmstudio_models_dir) if lmstudio_models_dir else None
    custom = settings.custom_model_dir or None
    for drive in drives:
        reserve = reserve_gb(drive.total_gb, settings)
        is_lm = drive.letter.upper() == lm_drive
        path = lmstudio_models_dir if is_lm and lmstudio_models_dir else f"{drive.letter}\\AI\\Models"
        if custom and drive_of(custom) == drive.letter.upper():
            path = custom
        locations.append(
            StorageLocation(
                path=path,
                drive=drive.letter,
                free_gb=round(drive.free_gb, 1),
                total_gb=round(drive.total_gb, 1),
                reserve_gb=reserve,
                safe_available_gb=round(max(0.0, drive.free_gb - reserve), 1),
                is_lmstudio_dir=is_lm,
            )
        )
    if locations:
        # Prefer LM Studio's own folder while it has room; otherwise the roomiest drive.
        lm = next((loc for loc in locations if loc.is_lmstudio_dir), None)
        roomiest = max(locations, key=lambda loc: loc.safe_available_gb)
        if lm and lm.safe_available_gb >= 20:
            lm.recommended = True
            lm.reason = tr("LM Studio already stores models here and there is room for more.")
        else:
            roomiest.recommended = True
            roomiest.reason = (
                tr("Most free space. LM Studio's current folder is nearly full.")
                if lm
                else tr("Most free space. LM Studio's folder was not found.")
            )
    return locations
