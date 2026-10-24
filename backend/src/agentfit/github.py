"""The user's GitHub account, through the GitHub CLI.

Will It Run does not implement GitHub sign-in itself and never holds a GitHub token. It
drives ``gh``, GitHub's own command-line tool:

* signing in is ``gh auth login --web``: the user authorises in the browser, on
  github.com, and ``gh`` keeps the token in the Windows credential store;
* the list of repositories is ``gh api user/repos``;
* cloning a private repository lets Git ask ``gh`` for the credential, for that one
  command, without changing the user's Git configuration.

Without ``gh`` installed nothing here works, and the caller is told so: a public
repository can still be cloned from its address.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

HOST = "github.com"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEVICE_URL = "https://github.com/login/device"
# gh prints the one-time code as XXXX-XXXX and waits for the user to enter it on github.com.
_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
_URL = re.compile(r"https://github\.com/login/device[^\s]*")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
MAX_REPOS = 500

Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


class GitHubError(Exception):
    pass


def cli_path() -> str | None:
    return shutil.which("gh")


def _env() -> dict[str, str]:
    # No prompts, no colour codes, no update banner in what is parsed.
    return {**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "CLICOLOR": "0", "GH_NO_UPDATE_NOTIFIER": "1"}


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    cli = cli_path()
    if cli is None:
        raise GitHubError("The GitHub CLI (gh) is not installed on this PC.")
    return subprocess.run(
        [cli, *args], env=_env(), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
    )


def _last_line(text: str) -> str:
    lines = [_ANSI.sub("", line).strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no message from gh"


@dataclass(frozen=True)
class Account:
    # False when gh is not installed.
    cli: bool
    signed_in: bool
    login: str | None = None


def account(runner: Runner | None = None) -> Account:
    """Who gh is signed in as. Read from gh's own settings: no network, and no token is touched."""
    if runner is None and cli_path() is None:
        return Account(cli=False, signed_in=False)
    try:
        result = (runner or _run)(["config", "get", "user", "--host", HOST], 15)
    except (GitHubError, OSError, subprocess.TimeoutExpired):
        return Account(cli=True, signed_in=False)
    login = result.stdout.strip() if result.returncode == 0 else ""
    return Account(cli=True, signed_in=bool(login), login=login or None)


def repositories(runner: Runner | None = None) -> list[dict[str, Any]]:
    """Repositories the signed-in account can reach, most recently pushed first."""
    query = "user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member"
    shape = ".[] | {full_name, private, description, pushed_at, default_branch, html_url, fork, archived}"
    try:
        result = (runner or _run)(["api", query, "--paginate", "--jq", shape], 90)
    except subprocess.TimeoutExpired as exc:
        raise GitHubError("GitHub did not answer in time. Check the connection and try again.") from exc
    except OSError as exc:
        raise GitHubError(f"The GitHub CLI could not be started: {exc}") from exc
    if result.returncode != 0:
        message = _last_line(result.stderr)
        if "401" in message or "auth login" in message or "authentication" in message.lower():
            raise GitHubError("The GitHub sign-in is no longer valid. Sign in again.")
        raise GitHubError(f"GitHub did not return the repositories: {message}")
    repos: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("full_name") and str(item.get("html_url", "")).startswith(f"https://{HOST}/"):
            repos.append(item)
        if len(repos) >= MAX_REPOS:
            break
    return repos


def git_credential_args() -> list[str]:
    """Options that make one ``git`` command take the GitHub credential from gh.

    Empty when gh is not installed or not signed in. The user's Git configuration is not
    changed: the options apply to the command they are passed to and nothing else.
    """
    cli = cli_path()
    if cli is None or not account().signed_in:
        return []
    helper = f"!'{cli.replace(chr(92), '/')}' auth git-credential"
    key = f"credential.https://{HOST}.helper"
    # The empty value first drops the helpers configured elsewhere for this host.
    return ["-c", f"{key}=", "-c", f"{key}={helper}"]


class Login:
    """One browser sign-in in progress, run by ``gh auth login --web``.

    gh prints a one-time code and waits while the user enters it on github.com. This
    object reads the code so the interface can show it, and reports how it ended.
    """

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen) -> None:
        self._popen = popen
        self._lock = threading.Lock()
        self._process: Any = None
        self._ready = threading.Event()
        self.state = "idle"  # idle | waiting | done | failed | cancelled
        self.code: str | None = None
        self.url = DEVICE_URL
        self.error: str | None = None

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "waiting" and self._process is not None and self._process.poll() is None:
                return self.snapshot()
            cli = cli_path()
            if cli is None and self._popen is subprocess.Popen:
                raise GitHubError("The GitHub CLI (gh) is not installed on this PC.")
            self.state, self.code, self.error, self.url = "waiting", None, None, DEVICE_URL
            self._ready.clear()
            args = [cli or "gh", "auth", "login", "--hostname", HOST, "--git-protocol", "https", "--web", "--clipboard"]
            try:
                self._process = self._popen(
                    args, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                )
            except OSError as exc:
                self.state, self.error = "failed", str(exc)
                raise GitHubError(f"The GitHub CLI could not be started: {exc}") from exc
            threading.Thread(target=self._follow, args=(self._process,), daemon=True, name="github-login").start()
        # The code appears within a second or two; give it a moment so the first answer carries it.
        self._ready.wait(8)
        return self.snapshot()

    def _follow(self, process: Any) -> None:
        last = ""
        for raw in process.stdout:
            line = _ANSI.sub("", raw).strip()
            if not line:
                continue
            last = line
            code = _CODE.search(line)
            if code and self.code is None:
                self.code = code.group(1)
            url = _URL.search(line)
            if url:
                self.url = url.group(0)
            if self.code:
                self._ready.set()
        returncode = process.wait()
        with self._lock:
            if self._process is not process or self.state == "cancelled":
                return
            if returncode == 0:
                self.state = "done"
            else:
                self.state, self.error = "failed", last or "gh ended without signing in"
        self._ready.set()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self.state = "cancelled"
                with contextlib.suppress(OSError):
                    self._process.terminate()
            elif self.state == "waiting":
                self.state = "cancelled"
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "code": self.code, "url": self.url, "error": self.error}
