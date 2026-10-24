"""A dev server for the project a clone is built in, started and stopped by the app.

The agents compare their page with the original in a browser, and the person opens the result
in one: both need the project served on 127.0.0.1. The server is ``npm run dev`` of the
project (Next.js or Vite), on a free port, killed with everything it started when it stops.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from agentfit.clone import proc
from agentfit.logging_setup import get_logger, log_event

log = get_logger("clone.dev")

START_TIMEOUT = 150.0
BUILD_TIMEOUT = 900.0

_alive: set[DevServer] = set()
_alive_lock = threading.Lock()


class DevServerError(RuntimeError):
    pass


def _build(folder: Path, npm: str, cancelled: Callable[[], bool] | None) -> None:
    """``npm run build``, so that ``npm start`` serves what is in the project now."""
    try:
        code, lines, timed_out = proc.run_streaming([npm, "run", "build"], folder, cancelled=cancelled, timeout=BUILD_TIMEOUT,
                                                    env={"NEXT_TELEMETRY_DISABLED": "1"})
    except proc.Cancelled as exc:
        raise DevServerError("Stopped.") from exc
    if timed_out:
        raise DevServerError(f"The build did not finish within {int(BUILD_TIMEOUT)} s.")
    if code != 0:
        raise DevServerError("The build failed, so there is nothing to serve:\n" + "\n".join(lines[-25:]))


def _flags(folder: Path, port: int, script: str = "dev") -> list[str]:
    """The arguments that put the project's server on a port, on this PC only."""
    try:
        package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevServerError("The folder has no readable package.json, so there is nothing to serve.") from exc
    deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
    if script not in (package.get("scripts") or {}):
        raise DevServerError(f'package.json has no "{script}" script.')
    if "next" in deps:
        return ["-p", str(port), "-H", "127.0.0.1"]
    if script != "dev":
        raise DevServerError("Only a Next.js project can be served from its build here.")
    if "vite" in deps:
        return ["--port", str(port), "--host", "127.0.0.1", "--strictPort"]
    raise DevServerError("Only Next.js and Vite projects can be served here.")


class DevServer:
    def __init__(self, folder: Path, npm: str) -> None:
        self.folder = Path(folder)
        self.npm = npm
        self.port: int | None = None
        self.production = False
        self._process: subprocess.Popen[str] | None = None
        self._tail: deque[str] = deque(maxlen=40)

    @property
    def url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port and self.running else None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, cancelled: Callable[[], bool] | None = None, timeout: float = START_TIMEOUT, production: bool = False) -> str:
        """Serve the project. ``production``: build it first and serve the build (``npm start``), as the kit's QA wants."""
        if self.running and self.url:
            return self.url
        if not (self.folder / "node_modules").is_dir():
            raise DevServerError("The project's packages are not installed yet (no node_modules): run npm install first.")
        port = proc.free_port()
        self.production = production
        if production:
            _build(self.folder, self.npm, cancelled)
            argv = [self.npm, "start", "--", *_flags(self.folder, port, script="start")]
        else:
            argv = [self.npm, "run", "dev", "--", *_flags(self.folder, port)]
        self._tail.clear()
        self._process = proc.spawn_command(argv, self.folder, {"NEXT_TELEMETRY_DISABLED": "1", "BROWSER": "none"})
        self.port = port
        threading.Thread(target=self._read, args=(self._process,), name="clone-dev", daemon=True).start()
        with _alive_lock:
            _alive.add(self)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled and cancelled():
                self.stop()
                raise DevServerError("Stopped.")
            if self._process.poll() is not None:
                raise DevServerError("The dev server stopped at once:\n" + "\n".join(list(self._tail)[-12:]))
            if proc.port_open(port):
                log_event(log, "dev server ready", folder=str(self.folder), port=port)
                return f"http://127.0.0.1:{port}"
            time.sleep(0.5)
        output = "\n".join(list(self._tail)[-12:])
        self.stop()
        raise DevServerError(f"The dev server did not answer within {int(timeout)} s.\n{output}")

    def _read(self, process: subprocess.Popen[str]) -> None:
        for line in process.stdout or []:
            text = line.rstrip()
            if text:
                self._tail.append(text[:300])

    def output(self, lines: int = 20) -> str:
        return "\n".join(list(self._tail)[-lines:])

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            proc.kill_tree(process)
        with _alive_lock:
            _alive.discard(self)

    def __hash__(self) -> int:
        return id(self)


def stop_all() -> None:
    """Every dev server this program started goes with it."""
    with _alive_lock:
        servers = list(_alive)
    for server in servers:
        with contextlib.suppress(Exception):
            server.stop()


atexit.register(stop_all)
