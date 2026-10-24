"""The browser a site is cloned from: ``browser.mjs`` under Node, driven over stdin/stdout.

The Node side is Playwright's core library (see ``install.py``) driving the Chrome or Edge
installed on this PC: no browser is downloaded for it. Each command is one JSON line and
gets one JSON line back.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentfit.clone.proc import NO_WINDOW, free_port, kill_tree
from agentfit.logging_setup import get_logger, log_event

__all__ = ["BrowserCancelled", "BrowserError", "BrowserHost", "find_browser", "free_port"]

log = get_logger("clone.browser")

HERE = Path(__file__).resolve().parent
HELPER = HERE / "browser.mjs"


class BrowserError(Exception):
    pass


class BrowserCancelled(BrowserError):
    pass


def _candidates() -> list[tuple[str, Path]]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    return [
        ("Chrome", pf / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ("Chrome", pf86 / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ("Chrome", local / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ("Edge", pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ("Edge", pf / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
    ]


def find_browser() -> dict[str, str] | None:
    """The Chrome or Edge installed on this PC. Every Windows 11 has Edge."""
    for name, path in _candidates():
        if path.is_file():
            return {"name": name, "executable": str(path)}
    return None


class BrowserHost:
    """One ``browser.mjs`` process: one browser, opened and closed with it."""

    def __init__(self, node: str, install_dir: Path, *, log_file: Path | None = None,
                 cancelled: Callable[[], bool] | None = None) -> None:
        self.node = node
        self.install_dir = install_dir
        self.log_file = log_file
        self.cancelled = cancelled or (lambda: False)
        self._process: subprocess.Popen[str] | None = None
        self._responses: dict[int, dict[str, Any]] = {}
        self._cond = threading.Condition()
        self._next = 0
        self._ready = False
        self._dead: str | None = None

    # -- process ----------------------------------------------------------------
    def start(self, timeout: float = 30.0) -> BrowserHost:
        if not (self.install_dir / "node_modules" / "playwright-core").is_dir():
            raise BrowserError("The browser engine is missing: press Prepare on the Clone a site page.")
        stderr: Any = subprocess.DEVNULL
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            stderr = self.log_file.open("a", encoding="utf-8")
        self._process = subprocess.Popen(
            [self.node, str(HELPER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=str(HERE),
            env={**os.environ, "WIR_BROWSER_DIR": str(self.install_dir), "NO_COLOR": "1"}, creationflags=NO_WINDOW,
        )
        threading.Thread(target=self._read, name="clone-browser", daemon=True).start()
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._ready and self._dead is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(min(0.5, left))
        if not self._ready:
            self.kill()
            raise BrowserError(self._dead or "The browser helper did not start.")
        return self

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._cond:
                if "id" in message:
                    self._responses[int(message["id"])] = message
                elif message.get("event") == "ready":
                    self._ready = True
                elif message.get("event") == "fatal":
                    self._dead = str(message.get("error") or "the browser helper stopped")
                self._cond.notify_all()
        with self._cond:
            self._dead = self._dead or f"The browser helper ended (exit code {process.poll()})."
            self._cond.notify_all()

    def call(self, cmd: str, args: dict[str, Any] | None = None, timeout: float = 120.0) -> Any:
        if self._process is None or self._process.stdin is None:
            raise BrowserError("The browser is not open.")
        with self._cond:
            self._next += 1
            call_id = self._next
        try:
            self._process.stdin.write(json.dumps({"id": call_id, "cmd": cmd, "args": args or {}}) + "\n")
            self._process.stdin.flush()
        except OSError as exc:
            raise BrowserError(f"The browser helper is gone: {exc}") from exc
        deadline = time.monotonic() + timeout
        with self._cond:
            while call_id not in self._responses:
                if self.cancelled():
                    raise BrowserCancelled("Stopped.")
                if self._dead is not None:
                    raise BrowserError(self._dead)
                left = deadline - time.monotonic()
                if left <= 0:
                    raise BrowserError(f"The browser did not finish '{cmd}' in {int(timeout)} s.")
                self._cond.wait(min(0.5, left))
            message = self._responses.pop(call_id)
        if not message.get("ok"):
            raise BrowserError(str(message.get("error") or f"{cmd} failed"))
        return message.get("result")

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None and self._dead is None:
            with contextlib.suppress(BrowserError):
                self.call("close", timeout=30.0)
        self.kill()

    def kill(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        with contextlib.suppress(OSError):
            if process.stdin:
                process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Playwright's browser is a child of node: the whole tree goes.
            kill_tree(process)
        log_event(log, "browser helper closed", pid=process.pid)

    def __enter__(self) -> BrowserHost:
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.close()
