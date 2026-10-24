"""What this PC has for checking answers: Python and Node to run code, Terraform, ansible-lint, PostgreSQL's psql.

A run asks once and keeps the answer, and the report says which tools were there: two runs made with different tools
are not equally strict, and the person comparing them has to be able to tell.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Tool:
    name: str
    path: str
    version: str


def _version(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=15, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or proc.stderr or b"").decode("utf-8", errors="replace").strip()
    return out.splitlines()[0] if out else None


def _digits(text: str) -> str:
    import re

    match = re.search(r"\d+(?:\.\d+)+", text)
    return match.group(0) if match else text.strip()


class Tools:
    """Detected on first use, then remembered. ``overrides`` replaces a tool (None: pretend it is not installed): for tests."""

    def __init__(self, overrides: dict[str, Tool | None] | None = None) -> None:
        self._overrides = overrides or {}
        self._cache: dict[str, Tool | None] = {}

    def get(self, name: str) -> Tool | None:
        if name in self._overrides:
            return self._overrides[name]
        if name not in self._cache:
            self._cache[name] = self._detect(name)
        return self._cache[name]

    def _detect(self, name: str) -> Tool | None:
        if name == "python":
            path = (shutil.which("python") or shutil.which("py")) if getattr(sys, "frozen", False) else sys.executable
            if not path:
                return None
            text = _version([path, "--version"])
            return Tool("python", path, _digits(text)) if text else None
        exe = {"ansible_lint": "ansible-lint"}.get(name, name)
        path = shutil.which(exe)
        if not path:
            return None
        if name == "terraform":
            try:
                proc = subprocess.run([path, "version", "-json"], capture_output=True, timeout=15, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, check=False)
                version = json.loads(proc.stdout.decode("utf-8", errors="replace")).get("terraform_version")
            except (OSError, subprocess.SubprocessError, ValueError):
                version = None
            text = version or _version([path, "version"])
        else:
            text = _version([path, "--version"])
        return Tool(name, path, _digits(text)) if text else None

    def python(self) -> Tool | None:
        return self.get("python")

    def node(self) -> Tool | None:
        return self.get("node")

    def terraform(self) -> Tool | None:
        return self.get("terraform")

    def ansible_lint(self) -> Tool | None:
        return self.get("ansible_lint")

    def psql(self) -> Tool | None:
        return self.get("psql")

    def report(self) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for name in ("python", "node", "terraform", "ansible_lint", "psql"):
            tool = self.get(name)
            out[name] = {"available": tool is not None, "version": tool.version if tool else None, "path": tool.path if tool else None}
        return out
