"""Finding, starting and stopping the ComfyUI on this PC.

Will It Run does not ship ComfyUI and never installs it silently: it looks for it where
it is normally found, starts the copy it found when asked, and when there is none it says
so and offers the official download. Two things it will not do are guess and pretend:

- "installed" means a folder with ``main.py`` and a usable Python, checked on disk;
- "running" means ``/system_stats`` answered, whoever started it.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentfit import paths
from agentfit.events import EventBus
from agentfit.i18n import tr
from agentfit.imagegen.comfy import ComfyClient, ComfyError

#: Where ComfyUI is installed on the machines this app has seen, then the usual places.
KNOWN_ROOTS = (
    r"D:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI",
    r"C:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI",
    r"D:\ComfyUI",
    r"C:\ComfyUI",
    r"C:\ComfyUI_windows_portable\ComfyUI",
    r"D:\ComfyUI_windows_portable\ComfyUI",
)

DOWNLOAD_URL = "https://www.comfy.org/download"
PORTABLE_URL = "https://github.com/Comfy-Org/ComfyUI/releases/latest"
GGUF_NODES_URL = "https://github.com/city96/ComfyUI-GGUF"


class EngineError(Exception):
    """The engine cannot be started: something is missing, and here is what."""


@dataclass(frozen=True)
class Install:
    """A ComfyUI found on disk."""

    root: Path  # the folder holding main.py
    python: Path | None
    models_dir: Path
    kind: str  # "desktop" | "portable" | "manual"

    def to_json(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "python": str(self.python) if self.python else None,
            "models_dir": str(self.models_dir),
            "kind": self.kind,
        }


def _python_for(root: Path) -> tuple[Path | None, str]:
    """The interpreter that can run main.py, and how the folder is laid out."""
    venv = root / ".venv" / "Scripts" / "python.exe"
    if venv.is_file():
        return venv, "manual"
    portable = root / "python_embeded" / "python.exe"
    if portable.is_file():
        return portable, "portable"
    # ComfyUI Desktop keeps an embedded python next to its resources.
    for candidate in root.glob("python_embeded*/python.exe"):
        return candidate, "portable"
    return None, "manual"


def _install_at(root: Path) -> Install | None:
    if not (root / "main.py").is_file():
        return None
    python, kind = _python_for(root)
    return Install(root=root, python=python, models_dir=root / "models", kind=kind)


def _candidate_roots(configured: str) -> list[Path]:
    roots: list[Path] = []
    if configured.strip():
        roots.append(Path(configured.strip()))
    for raw in KNOWN_ROOTS:
        roots.append(Path(raw))
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    home = Path.home()
    if local:
        # ComfyUI Desktop installs itself here, with the engine under resources/.
        desktop = Path(local) / "Programs" / "@comfyorgcomfyui-electron"
        roots.append(desktop / "resources" / "ComfyUI")
        roots.append(desktop / "resources")
    if roaming:
        roots.append(Path(roaming) / "ComfyUI")
    roots.extend([home / "ComfyUI", home / "Documents" / "ComfyUI", home / "Desktop" / "ComfyUI"])
    # A ComfyUI folder at the top of any fixed drive, one level down.
    for letter in "CDEFG":
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        try:
            for child in drive.iterdir():
                if child.is_dir() and child.name.lower().startswith("comfyui"):
                    roots.append(child)
        except OSError:
            continue
    seen: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.append(resolved)
    return seen


def find_install(configured: str = "") -> Install | None:
    """The first ComfyUI on this PC that can actually run, configured folder first."""
    for root in _candidate_roots(configured):
        install = _install_at(root)
        if install is not None and install.python is not None:
            return install
    # A folder with main.py but no interpreter is still worth reporting.
    for root in _candidate_roots(configured):
        install = _install_at(root)
        if install is not None:
            return install
    return None


class ComfyEngine:
    """The ComfyUI process Will It Run can start, plus what it is doing now."""

    def __init__(self, settings: Any, bus: EventBus) -> None:
        self.settings = settings
        self.bus = bus
        self._process: subprocess.Popen[bytes] | None = None
        self._log_path = paths.ensure(paths.logs_dir()) / "comfyui.log"
        self._install: Install | None = None
        self._install_checked = False

    # -- configuration ------------------------------------------------------
    @property
    def config(self) -> Any:
        return self.settings.images

    @property
    def url(self) -> str:
        return str(getattr(self.config, "comfy_url", "") or "http://127.0.0.1:8188")

    @property
    def port(self) -> int:
        match = re.search(r":(\d+)", self.url)
        return int(match.group(1)) if match else 8188

    @property
    def log_path(self) -> Path:
        return self._log_path

    def forget_install(self) -> None:
        """Forget the folder found earlier: the settings changed, or the person moved it."""
        self._install = None
        self._install_checked = False

    def install(self, *, refresh: bool = False) -> Install | None:
        if refresh or not self._install_checked:
            self._install = find_install(str(getattr(self.config, "comfy_dir", "") or ""))
            self._install_checked = True
        return self._install

    def client(self) -> ComfyClient:
        return ComfyClient(self.url, timeout=float(self.config.request_timeout_seconds))

    # -- what is going on ---------------------------------------------------
    def running(self) -> dict[str, Any] | None:
        """ComfyUI's own answer about itself, or None when it is not answering."""
        try:
            return self.client().stats()
        except ComfyError:
            return None

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        install = self.install(refresh=refresh)
        stats = self.running()
        started_by_us = self._process is not None and self._process.poll() is None
        return {
            "installed": install is not None and install.python is not None,
            "running": stats is not None,
            "url": self.url,
            "version": ((stats or {}).get("system") or {}).get("comfyui_version"),
            "install": install.to_json() if install else None,
            "started_by_us": started_by_us,
            "download_url": DOWNLOAD_URL,
            "portable_url": PORTABLE_URL,
            "gguf_nodes_url": GGUF_NODES_URL,
            "log": str(self._log_path),
        }

    # -- starting and stopping ---------------------------------------------
    def start(self, *, timeout: float = 180.0) -> dict[str, Any]:
        if self.running() is not None:
            return {**self.status(), "already_running": True}
        install = self.install(refresh=True)
        if install is None:
            raise EngineError(
                tr("ComfyUI is not installed on this PC. Install it, then ask Will It Run to look again.")
            )
        if install.python is None:
            raise EngineError(
                tr(
                    "ComfyUI was found in {root} but no Python interpreter is next to it. "
                    "Reinstall it, or point Settings > Images at the folder that holds main.py."
                ).format(root=install.root)
            )
        if self._process is not None and self._process.poll() is None:
            raise EngineError(tr("ComfyUI is already starting."))
        log = self._log_path.open("ab")
        log.write(f"\n--- Will It Run starts ComfyUI: {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
        log.flush()
        command = [
            str(install.python),
            "main.py",
            "--listen",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--disable-auto-launch",
            "--preview-method",
            "none",
        ]
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(install.root),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creation,
            )
        except OSError as exc:
            log.close()
            raise EngineError(tr("ComfyUI could not be started: {error}").format(error=exc)) from exc
        self.bus.publish("image.engine", {"state": "starting", "port": self.port})
        deadline = time.time() + timeout
        while time.time() < deadline:
            assert self._process is not None
            if self._process.poll() is not None:
                code = self._process.returncode
                self._process = None
                detail = self.log_tail(12)
                self.bus.publish("image.engine", {"state": "failed", "detail": detail})
                raise EngineError(
                    tr("ComfyUI stopped while starting (exit code {code}). {detail}").format(code=code, detail=detail)
                )
            if self.running() is not None:
                self.bus.publish("image.engine", {"state": "running", "port": self.port})
                return self.status()
            time.sleep(1.0)
        self.bus.publish("image.engine", {"state": "timeout"})
        raise EngineError(
            tr("ComfyUI did not answer on {url} within {timeout} seconds. See {log}.").format(
                url=self.url, timeout=int(timeout), log=self._log_path
            )
        )

    def stop(self, *, timeout: float = 15.0) -> dict[str, Any]:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return {**self.status(), "stopped": False, "reason": tr("ComfyUI was not started from here.")}
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self.bus.publish("image.engine", {"state": "stopped"})
        return {**self.status(), "stopped": True}

    def log_tail(self, lines: int = 25) -> str:
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        tail = "\n".join(text.splitlines()[-lines:])
        return tail[-1500:]
