"""Starting programs the way Windows needs them, for everything the site cloner runs.

npm is a batch file and has to go through ``cmd.exe``; ``node`` must be launched as the
file system spells it. Both traps were measured by the sibling app LocaLQuantiX and again
here: ``cmd /d /c "C:\\Program Files\\nodejs\\npm.cmd" ... "...\\Will It Run\\..."`` lost its
outer quotes and every install from the installed app failed with ``"C:\\Program" is not
recognised``. These helpers are the one place that knows it.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
JOB_LINES = 400


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def at_least(version: str, minimum: tuple[int, int]) -> bool | None:
    """Whether ``version`` is at least major.minor; None when it is not a version."""
    parsed = parse_version(version)
    if parsed is None:
        return None
    return parsed[:2] >= minimum


def resolve_true_case(path: str | Path) -> Path:
    """The path as the file system spells it (``node.exe``, not the ``node.EXE`` a PATH lookup gives)."""
    target = Path(path)
    try:
        target = target.resolve()
    except OSError:
        return target
    parts = target.parts
    if len(parts) < 2:
        return target
    current = Path(parts[0])
    for part in parts[1:]:
        try:
            spelled = next((e.name for e in current.iterdir() if e.name.lower() == part.lower()), None)
        except OSError:
            spelled = None
        current = current / (spelled or part)
    return current


def command(argv: list[str]) -> list[str] | str:
    """How ``argv`` is started. A batch file (npm) goes through ``cmd.exe /d /s /c "<line>"``.

    The extra pair of quotes with ``/s`` is what keeps the quotes inside the line: without it
    cmd drops the first and the last quote of a line that has more than two.
    """
    if os.name == "nt" and argv and argv[0].lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return f'"{comspec}" /d /s /c "{subprocess.list2cmdline(argv)}"'
    return argv


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {**os.environ, "NO_COLOR": "1", "npm_config_fund": "false", "npm_config_audit": "false",
            "npm_config_update_notifier": "false", "CI": "1", **(extra or {})}


def run_command(argv: list[str], timeout: float = 60.0, env: dict[str, str] | None = None, cwd: Path | None = None) -> tuple[int, str]:
    result = subprocess.run(
        command(argv), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        stdin=subprocess.DEVNULL, env=clean_env(env), creationflags=NO_WINDOW, cwd=str(cwd) if cwd else None,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def spawn_command(argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    # The folder in its long, true-case form: Next.js's dev server crashes on Windows ("Assertion failed:
    # !_wcsnicmp(filename, dir, dirlen), fs-event.c") when it is started in a folder given by its 8.3 short
    # name (a user folder shortened to something like USERNA~1), which a TEMP folder or a typed path can be.
    return subprocess.Popen(
        command(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
        encoding="utf-8", errors="replace", bufsize=1, cwd=str(resolve_true_case(cwd)), env=clean_env(env), creationflags=NO_WINDOW,
    )


def kill_tree(process: subprocess.Popen[Any]) -> None:
    """The process and everything it started (npm starts node starts a dev server)."""
    if process.poll() is not None:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW, timeout=15)
    with contextlib.suppress(OSError):
        process.kill()


class Cancelled(Exception):
    """The person pressed Stop while a command was running."""


def run_streaming(argv: list[str], cwd: Path, *, cancelled: Callable[[], bool] | None = None, timeout: float = 900.0,
                  on_line: Callable[[str], None] | None = None, env: dict[str, str] | None = None) -> tuple[int, list[str], bool]:
    """Run a command, following its output line by line. Returns (exit code, lines, timed out).

    Stop ends the command (and what it started) and raises ``Cancelled``; a command that does not
    finish in ``timeout`` seconds is killed and reported as timed out.
    """
    started = time.monotonic()
    process = spawn_command(argv, cwd, env)
    lines: list[str] = []

    def read() -> None:
        for line in process.stdout or []:
            text = line.rstrip()
            if text:
                lines.append(text[:400])
                if on_line is not None:
                    on_line(text[:400])

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    timed_out = False
    try:
        while process.poll() is None:
            if cancelled is not None and cancelled():
                kill_tree(process)
                raise Cancelled()
            if time.monotonic() - started > timeout:
                kill_tree(process)
                timed_out = True
                break
            time.sleep(0.4)
    finally:
        reader.join(timeout=3)
    return (process.returncode if process.returncode is not None else -1), lines, timed_out


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


@dataclass
class Job:
    """A background command whose output the interface follows (installing the browser engine)."""

    kind: str
    label: str
    state: str = "running"
    started_at: str = ""
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=JOB_LINES))
    process: Any = None

    def view(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "state": self.state, "started_at": self.started_at,
                "finished_at": self.finished_at, "exit_code": self.exit_code, "error": self.error, "output": list(self.lines)}
