"""The browser engine the cloner drives: Playwright's core library, kept in a folder of the app's own.

One copy under the app's data folder (``<data>\\tools\\clone-browser``), installed with
``npm install --prefix``: nothing global, no privilege. It is only the library; the browser
itself is the Chrome or Edge already on the PC, so nothing big is downloaded. It runs on the
Node.js already on the PC.

The install runs as a background job whose output is announced on the event bus
(``clone.install``) and appended to ``logs\\clone.log``. The pieces that run commands are
injected, so the tests never start a process.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentfit import paths
from agentfit.clone import proc
from agentfit.db import utcnow
from agentfit.events import EventBus
from agentfit.events import bus as default_bus
from agentfit.logging_setup import get_logger, log_event

log = get_logger("clone")

PACKAGE = "playwright-core"
# The version the browser helper was written and tested against.
PACKAGE_VERSION = "1.58.2"
# The kit (ai-site-cloner) asks for Node 22 or newer in its package.json.
NODE_MINIMUM = (22, 0)
NODE_PREFERRED = (22, 0)
JOB_TIMEOUT = 900.0


class CloneSetupError(RuntimeError):
    """What the person asked for cannot be done yet; the message says what to change."""


class BrowserInstall:
    def __init__(
        self,
        install_dir: Path | None = None,
        *,
        which: Callable[[str], str | None] | None = None,
        run: Callable[[list[str], float], tuple[int, str]] | None = None,
        spawn: Callable[[list[str], Path], Any] | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.install_dir = Path(install_dir) if install_dir else paths.local_dir() / "tools" / "clone-browser"
        self._which = which or shutil.which
        self._run = run or proc.run_command
        self._spawn = spawn or proc.spawn_command
        self.bus = bus or default_bus
        self._lock = threading.RLock()
        self._job: proc.Job | None = None
        self._node_cache: tuple[float, dict[str, Any]] | None = None
        self.log_path = paths.logs_dir() / "clone.log"

    # -- where things are ---------------------------------------------------
    @property
    def package_path(self) -> Path:
        return self.install_dir / "node_modules" / PACKAGE / "package.json"

    def installed_version(self) -> str | None:
        try:
            data = json.loads(self.package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return str(data.get("version") or "").strip() or None

    def node_exe(self) -> str | None:
        found = self._which("node")
        return str(proc.resolve_true_case(found)) if found else None

    def npm_exe(self) -> str | None:
        found = self._which("npm") or self._which("npm.cmd")
        return str(proc.resolve_true_case(found)) if found else None

    def git_exe(self) -> str | None:
        return self._which("git")

    def node(self) -> dict[str, Any]:
        """Node.js on this PC: where, which version, and whether it is enough. Cached for a minute."""
        with self._lock:
            if self._node_cache and time.monotonic() - self._node_cache[0] < 60:
                return self._node_cache[1]
        path = self.node_exe()
        info: dict[str, Any] = {"path": path, "version": None, "ok": False, "preferred": False, "detail": None}
        if not path:
            info["detail"] = "Node.js is not installed (or not on PATH). The cloner runs on it: install the LTS from nodejs.org."
        else:
            try:
                code, output = self._run([path, "--version"], 30.0)
                version = output.strip().splitlines()[0] if output.strip() else ""
            except Exception as exc:
                code, version = 1, ""
                info["detail"] = str(exc)
            info["version"] = version or None
            fits = proc.at_least(version, NODE_MINIMUM) if code == 0 else None
            info["ok"] = bool(fits)
            info["preferred"] = bool(proc.at_least(version, NODE_PREFERRED)) if code == 0 else False
            if fits is False:
                info["detail"] = f"Node {version} is too old: the cloner needs Node {NODE_MINIMUM[0]} or newer. Install the current LTS."
        with self._lock:
            self._node_cache = (time.monotonic(), info)
        return info

    # -- status -------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        version = self.installed_version()
        with self._lock:
            job = self._job.view() if self._job else None
        return {
            "installed": version is not None,
            "version": version,
            "install_dir": str(self.install_dir),
            "node": self.node(),
            "npm": self.npm_exe(),
            "git": self.git_exe(),
            "job": job,
        }

    def node_command(self) -> str:
        node = self.node_exe()
        if not node:
            raise CloneSetupError("Node.js is not installed (or not on PATH), so the browser cannot be driven. Install the LTS from nodejs.org.")
        if self.installed_version() is None:
            raise CloneSetupError("The browser engine is not installed yet: press Prepare on the Clone a site page.")
        return node

    # -- install / update ---------------------------------------------------
    def install(self) -> dict[str, Any]:
        npm = self.npm_exe()
        if not npm:
            raise CloneSetupError("npm is not on this PC. It comes with Node.js: install the LTS from nodejs.org.")
        node = self.node()
        if not node["ok"]:
            raise CloneSetupError(node["detail"] or "This PC's Node.js is not a version the cloner supports.")
        target = f"{PACKAGE}@{PACKAGE_VERSION}"
        argv = [npm, "install", "--prefix", str(self.install_dir), target, "--no-fund", "--no-audit", "--loglevel=error"]
        with self._lock:
            if self._job is not None and self._job.state == "running":
                raise CloneSetupError(f"{self._job.label} is still running.")
            self._job = proc.Job(kind="install", label=f"Installing the browser engine ({target})", started_at=utcnow())
            job = self._job
        paths.ensure(self.install_dir)
        self._append(job, f"$ {' '.join(argv)}")
        self._announce(job)
        threading.Thread(target=self._work, args=(job, argv), name="clone-install", daemon=True).start()
        return job.view()

    def cancel(self) -> bool:
        with self._lock:
            job = self._job
        if job is None or job.state != "running" or job.process is None:
            return False
        job.state = "cancelled"
        with contextlib.suppress(OSError):
            job.process.terminate()
        self._announce(job)
        return True

    def _append(self, job: proc.Job, line: str) -> None:
        job.lines.append(line)
        with contextlib.suppress(OSError), self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _announce(self, job: proc.Job) -> None:
        self.bus.publish("clone.install", job.view())

    def _work(self, job: proc.Job, argv: list[str]) -> None:
        started = time.monotonic()
        try:
            process = self._spawn(argv, self.install_dir)
        except Exception as exc:
            job.state, job.error, job.finished_at = "failed", str(exc), utcnow()
            self._append(job, f"ERROR: {exc}")
            self._announce(job)
            return
        job.process = process
        last = 0.0
        for line in process.stdout or []:
            self._append(job, line.rstrip("\r\n"))
            if time.monotonic() - last > 0.5:
                last = time.monotonic()
                self._announce(job)
            if time.monotonic() - started > JOB_TIMEOUT:
                self._append(job, "ERROR: npm did not finish in time; stopping it.")
                process.kill()
                break
        code = process.wait()
        job.exit_code, job.finished_at = code, utcnow()
        if job.state != "cancelled":
            if code == 0 and self.installed_version():
                job.state = "done"
                self._append(job, f"Browser engine {self.installed_version()} ready in {time.monotonic() - started:.1f}s")
            else:
                job.state, job.error = "failed", f"npm exited with {code}"
                self._append(job, f"FAILED (exit code {code})")
        self._announce(job)
        log_event(log, "browser engine install finished", exit_code=code, version=self.installed_version())
