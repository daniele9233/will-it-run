"""What the hook scripts share: where the project is, how to run a program without a window, how to find its own command line tool.

Standard library only: a hook runs before the project is installed on a fresh clone, and it must never be the reason a session or a commit fails.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: a hook must not flash a console
DETACHED = (0x00000008 | 0x00000200) if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def utf8_output() -> None:
    """Hooks print to a pipe, and the pipe of Windows defaults to the ANSI code page."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")


def run(args: list[str], *, timeout: float = 30, stdin: str | None = None) -> tuple[int, str]:
    """Run a program quietly: (exit code, standard output). (-1, "") when it cannot start or takes too long."""
    try:
        done = subprocess.run(
            args, cwd=ROOT, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return done.returncode, done.stdout


def detach(args: list[str]) -> None:
    """Start a program that outlives this one and says nothing."""
    options: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": ROOT}
    if os.name == "nt":
        options["creationflags"] = DETACHED
    else:
        options["start_new_session"] = True
    with contextlib.suppress(OSError):
        subprocess.Popen(args, **options)


def project_python() -> str:
    """The Python of the backend's virtual environment, else the one running this script."""
    for candidate in (ROOT / "backend" / ".venv" / "Scripts" / "python.exe", ROOT / "backend" / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def willitrun() -> list[str] | None:
    """The project's command line tool, from the virtual environment or the PATH; None when it is not installed."""
    for candidate in (ROOT / "backend" / ".venv" / "Scripts" / "willitrun.exe", ROOT / "backend" / ".venv" / "bin" / "willitrun"):
        if candidate.exists():
            return [str(candidate)]
    found = shutil.which("willitrun")
    return [found] if found else None


def relative(path: str) -> str | None:
    """``path`` as the project writes it (forward slashes, from the root); None for a file outside the project."""
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return None
