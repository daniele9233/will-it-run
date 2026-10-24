"""The GitHub account, through a GitHub CLI that is scripted here.

No test starts the real ``gh``: ``conftest`` hides it, and each test hands in the
commands' answers. Nothing here signs in anywhere or reads a real account.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from agentfit import github
from agentfit.workflows import sources


def answers(table: dict[str, tuple[int, str, str]]):
    """A runner that answers by the first two words of the gh command."""
    calls: list[list[str]] = []

    def run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        code, out, err = table.get(" ".join(args[:2]), (1, "", "unknown command"))
        return subprocess.CompletedProcess(args, code, out, err)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def repo(name: str, **extra: Any) -> str:
    base = {"full_name": name, "private": True, "description": None, "pushed_at": "2026-10-01T10:00:00Z", "default_branch": "main",
            "html_url": f"https://github.com/{name}", "fork": False, "archived": False}
    return json.dumps({**base, **extra})


def test_account_is_read_from_ghs_settings_not_from_a_token():
    signed = answers({"config get": (0, "octocat\n", "")})
    assert github.account(signed) == github.Account(cli=True, signed_in=True, login="octocat")
    # Asked gh's settings for the user name: never `auth token`, never the network.
    assert signed.calls == [["config", "get", "user", "--host", "github.com"]]
    assert github.account(answers({"config get": (0, "\n", "")})) == github.Account(cli=True, signed_in=False)
    assert github.account(answers({"config get": (1, "", "not logged in")})).signed_in is False
    # Without gh on the PC (the default in tests) there is no account and nothing is run.
    assert github.account() == github.Account(cli=False, signed_in=False)
    assert github.git_credential_args() == []


def test_repositories_are_listed_and_anything_that_is_not_a_github_repository_is_dropped():
    lines = "\n".join([repo("octocat/app"), repo("octocat/site", private=False), "not json", repo("evil/x", html_url="https://example.com/evil/x"), json.dumps({"no": "name"})])
    runner = answers({"api user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member": (0, lines, "")})
    found = github.repositories(runner)
    assert [r["full_name"] for r in found] == ["octocat/app", "octocat/site"]
    assert found[0]["private"] is True and found[1]["html_url"] == "https://github.com/octocat/site"
    assert "--paginate" in runner.calls[0]


def test_an_expired_sign_in_or_a_failure_is_said_plainly():
    key = "api user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member"
    with pytest.raises(github.GitHubError, match="no longer valid"):
        github.repositories(answers({key: (1, "", "gh: Bad credentials (HTTP 401)")}))
    with pytest.raises(github.GitHubError, match="did not return the repositories: dial tcp: connection refused"):
        github.repositories(answers({key: (1, "", "dial tcp: connection refused")}))

    def slow(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, timeout)

    with pytest.raises(github.GitHubError, match="did not answer in time"):
        github.repositories(slow)


class FakeGh:
    """Stands in for the ``gh auth login --web`` process: prints the code, then waits to be told how it ends."""

    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        self.release = threading.Event()
        self.returncode: int | None = None
        self._final = returncode
        self.terminated = False
        self.stdout = self._stream(lines)

    def _stream(self, lines: list[str]):
        yield from lines
        self.release.wait(5)

    def wait(self) -> int:
        self.returncode = self._final
        return self._final

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self._final = 1
        self.release.set()


def test_sign_in_shows_the_one_time_code_and_reports_the_outcome():
    process = FakeGh(["\x1b[33m!\x1b[0m First copy your one-time code: \x1b[1mAB12-CD34\x1b[0m\n", "Open this URL to continue in your web browser: https://github.com/login/device\n"])
    started: list[list[str]] = []

    def popen(args: list[str], **kwargs: Any) -> FakeGh:
        started.append(args)
        assert kwargs["stdin"] == subprocess.DEVNULL  # gh can never wait for a key press
        return process

    login = github.Login(popen)
    first = login.start()
    assert first == {"state": "waiting", "code": "AB12-CD34", "url": "https://github.com/login/device", "error": None}
    assert started[0][1:] == ["auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web", "--clipboard"]
    # Asking again while it waits does not start a second sign-in.
    assert login.start()["code"] == "AB12-CD34" and len(started) == 1
    process.release.set()
    for _ in range(200):
        if login.snapshot()["state"] != "waiting":
            break
        threading.Event().wait(0.01)
    assert login.snapshot()["state"] == "done"


def test_sign_in_that_fails_or_is_cancelled_says_so():
    failing = FakeGh(["error: device code expired\n"], returncode=1)
    failing.release.set()
    login = github.Login(lambda args, **kwargs: failing)
    login.start()
    for _ in range(200):
        if login.snapshot()["state"] != "waiting":
            break
        threading.Event().wait(0.01)
    assert login.snapshot() == {"state": "failed", "code": None, "url": github.DEVICE_URL, "error": "error: device code expired"}

    waiting = FakeGh(["! First copy your one-time code: ZZ99-YY88\n"])
    login = github.Login(lambda args, **kwargs: waiting)
    assert login.start()["state"] == "waiting"
    assert login.cancel()["state"] == "cancelled" and waiting.terminated
    threading.Event().wait(0.05)
    assert login.snapshot()["state"] == "cancelled"  # the process ending afterwards does not turn it into a failure


def test_git_takes_the_credential_from_gh_for_one_command_only(monkeypatch, tmp_path):
    monkeypatch.setattr(github, "cli_path", lambda: "C:\\Program Files\\GitHub CLI\\gh.exe")
    monkeypatch.setattr(github, "account", lambda runner=None: github.Account(cli=True, signed_in=True, login="octocat"))
    args = github.git_credential_args()
    assert args == [
        "-c", "credential.https://github.com.helper=",
        "-c", "credential.https://github.com.helper=!'C:/Program Files/GitHub CLI/gh.exe' auth git-credential",
    ]
    seen: list[list[str]] = []

    def clone(git_args: list[str], cwd: Any, timeout: float) -> subprocess.CompletedProcess[str]:
        seen.append(git_args)
        return subprocess.CompletedProcess(git_args, 0, "", "")

    sources.checkout("https://github.com/octocat/private-app", base=tmp_path, runner=clone)
    # The options come before the git command, so they configure it without touching any config file.
    assert seen[0][:4] == args and seen[0][4] == "clone" and seen[0][-3:-1] == ["--", "https://github.com/octocat/private-app.git"]
    monkeypatch.setattr(github, "account", lambda runner=None: github.Account(cli=True, signed_in=False))
    assert github.git_credential_args() == []


def test_api_reports_the_account_and_lists_repositories(monkeypatch):
    from fastapi.testclient import TestClient

    from agentfit.api.server import create_app
    from agentfit.app import AppServices
    from agentfit.config import Settings
    from agentfit.db import Database
    from agentfit.events import EventBus

    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    api = TestClient(create_app(AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())))
    # No GitHub CLI: said as a fact, and signing in is refused with the reason.
    assert api.get("/api/github").json() == {"cli": False, "signed_in": False, "login": None, "sign_in": {"state": "idle", "code": None, "url": github.DEVICE_URL, "error": None}}
    refused = api.post("/api/github/login")
    assert refused.status_code == 400 and "not installed" in refused.json()["detail"]
    assert api.get("/api/github/repos").status_code == 400

    monkeypatch.setattr(github, "account", lambda runner=None: github.Account(cli=True, signed_in=True, login="octocat"))
    monkeypatch.setattr(github, "repositories", lambda runner=None: [json.loads(repo("octocat/app"))])
    assert api.get("/api/github").json()["login"] == "octocat"
    listed = api.get("/api/github/repos").json()["repos"]
    assert listed[0]["full_name"] == "octocat/app" and "token" not in json.dumps(listed).lower()
    assert api.delete("/api/github/login").json()["state"] == "idle"


def test_nothing_in_the_module_asks_gh_for_the_token():
    source = Path(github.__file__).read_text(encoding="utf-8")
    assert '"auth", "token"' not in source and "auth token" not in source and "--with-token" not in source
