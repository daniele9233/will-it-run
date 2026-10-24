"""What one cloning run keeps open: its browser, its dev server and the question it may be asking.

The browser and the server are made the first time a tool needs them and closed when the run
ends, whatever way it ends, so a stopped run does not leave a Chrome or a Node behind.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentfit import paths
from agentfit.clone import asking
from agentfit.clone.browser import BrowserError, BrowserHost, find_browser
from agentfit.clone.devserver import DevServer
from agentfit.clone.install import BrowserInstall, CloneSetupError
from agentfit.events import EventBus
from agentfit.i18n import tr

VIEWPORT = (1440, 900)

Emit = Callable[[str, dict[str, Any]], None]


class CloneSession:
    def __init__(self, run_id: str, workspace: Path, install: BrowserInstall, cancelled: Callable[[], bool] | None = None,
                 emit: Emit | None = None, bus: EventBus | None = None) -> None:
        self.run_id = run_id
        self.workspace = Path(workspace)
        self.install = install
        self.cancelled = cancelled or (lambda: False)
        self.emit: Emit = emit or (lambda _type, _payload: None)
        self.bus = bus
        self._host: BrowserHost | None = None
        self._dev: DevServer | None = None
        self._lock = threading.RLock()
        self.viewport = VIEWPORT

    def browser(self) -> BrowserHost:
        with self._lock:
            if self._host is not None:
                return self._host
            found = find_browser()
            if found is None:
                raise BrowserError("Neither Chrome nor Edge was found on this PC: the cloner needs one of them.")
            try:
                node = self.install.node_command()
            except CloneSetupError as exc:
                raise BrowserError(str(exc)) from exc
            host = BrowserHost(node, self.install.install_dir, log_file=paths.logs_dir() / "clone-browser.log", cancelled=self.cancelled)
            host.start()
            try:
                host.call("launch", {"executable": found["executable"], "width": VIEWPORT[0], "height": VIEWPORT[1]}, timeout=60)
            except BrowserError:
                host.kill()
                raise
            self._host = host
            return host

    def dev(self) -> DevServer:
        with self._lock:
            if self._dev is None:
                npm = self.install.npm_exe()
                if not npm:
                    raise CloneSetupError("npm is not on this PC. It comes with Node.js: install the LTS from nodejs.org.")
                self._dev = DevServer(self.workspace, npm)
            return self._dev

    def stop_dev(self) -> bool:
        with self._lock:
            dev, running = self._dev, bool(self._dev and self._dev.running)
        if dev is not None:
            dev.stop()
        return running

    def ask(self, question: str, options: list[str]) -> str:
        """Ask the person, in the interface, and wait for the answer."""
        def notify(text: str) -> None:
            if self.bus is not None:
                self.bus.publish("notify", {"title": tr("The agent has a question"), "body": text[:140]})

        try:
            return asking.ask(self.run_id, question, options, emit=self.emit, cancelled=self.cancelled, notify=notify)
        except asking.Stopped as exc:
            raise BrowserError("Stopped.") from exc

    def close(self) -> None:
        with self._lock:
            host, dev = self._host, self._dev
            self._host = self._dev = None
        if host is not None:
            host.close()
        if dev is not None:
            dev.stop()


class SessionRegistry:
    """The sessions of the runs that are working, by run id."""

    def __init__(self, install: BrowserInstall, bus: EventBus | None = None) -> None:
        self.install = install
        self.bus = bus
        self._sessions: dict[str, CloneSession] = {}
        self._lock = threading.Lock()

    def session(self, run_id: str, workspace: Path, cancelled: Callable[[], bool] | None = None, emit: Emit | None = None) -> CloneSession:
        with self._lock:
            known = self._sessions.get(run_id)
            if known is None:
                known = self._sessions[run_id] = CloneSession(run_id, workspace, self.install, cancelled, emit, self.bus)
            return known

    def close(self, run_id: str) -> None:
        with self._lock:
            known = self._sessions.pop(run_id, None)
        if known is not None:
            known.close()
