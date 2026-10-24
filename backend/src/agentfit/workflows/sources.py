"""Where a run works: a folder of the user's, or a copy of a GitHub repository.

A run normally gets an empty workspace of its own. It can instead be pointed at a folder
that already has a project in it, or at a GitHub repository, which is cloned into Will It
Run's own data folder so the agents work on a copy.

Nothing here handles credentials. Cloning uses the Git that is installed, with every
prompt turned off. When the user is signed in to GitHub through the GitHub CLI, Git is
told to take the credential from it for that one command (see ``agentfit.github``), so a
private repository of theirs clones; otherwise it works only if Git already has access,
and fails with a clear message when it does not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentfit import github, paths

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CLONE_TIMEOUT = 600.0

# https://github.com/<owner>/<repository>, with an optional ".git" or trailing slash. No
# user:password@ part, no other host, no query: the address is passed to git as it is.
_GITHUB = re.compile(r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

GitRunner = Callable[[list[str], Path | None, float], "subprocess.CompletedProcess[str]"]


class SourceError(Exception):
    pass


@dataclass(frozen=True)
class Checkout:
    path: Path
    owner: str
    repo: str
    branch: str | None
    # True when this call made the clone, False when an earlier copy was found.
    cloned: bool
    note: str


def parse_github(url: str) -> tuple[str, str]:
    """(owner, repository) of a GitHub address, or SourceError."""
    match = _GITHUB.match(url.strip())
    if not match or match["repo"] in (".", ".."):
        raise SourceError("That is not a GitHub repository address. It looks like https://github.com/owner/repository.")
    return match["owner"], match["repo"]


def _run_git(args: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
    # Never wait for a password or open a sign-in window: fail and say so.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
    )


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no message from git"


def checkout(
    url: str,
    branch: str | None = None,
    *,
    base: Path | None = None,
    runner: GitRunner = _run_git,
    credentials: Callable[[], list[str]] = github.git_credential_args,
) -> Checkout:
    """Clone the repository, or reuse the copy an earlier run made.

    An existing copy is updated only when that cannot lose anything: no local changes and a
    fast-forward. Otherwise it is used as it is, and the note says so. ``credentials``
    gives the Git options that let the commands talking to GitHub use the user's sign-in.
    """
    owner, repo = parse_github(url)
    branch = (branch or "").strip() or None
    if branch and not _BRANCH.match(branch):
        raise SourceError("That branch name is not valid.")
    if runner is _run_git and shutil.which("git") is None:
        raise SourceError("Git is not installed on this PC. Install Git for Windows, or choose a folder instead.")
    name = f"{owner}__{repo}".lower() + (f"@{re.sub(r'[^A-Za-z0-9._-]', '-', branch)}" if branch else "")
    target = (base or paths.workspaces_dir() / "repos") / name
    address = f"https://github.com/{owner}/{repo}.git"
    auth = credentials()

    if (target / ".git").exists():
        try:
            status = runner(["status", "--porcelain"], target, 60)
            if status.returncode != 0:
                return Checkout(target, owner, repo, branch, False, "Using the copy from an earlier run. Its state could not be read.")
            if status.stdout.strip():
                return Checkout(target, owner, repo, branch, False, "Using the copy from an earlier run, with the changes made in it. Not updated from GitHub.")
            pull = runner([*auth, "pull", "--ff-only"], target, CLONE_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            return Checkout(target, owner, repo, branch, False, "Using the copy from an earlier run. GitHub could not be reached to update it.")
        note = "Updated the copy from an earlier run." if pull.returncode == 0 else "Using the copy from an earlier run. It could not be updated from GitHub."
        return Checkout(target, owner, repo, branch, False, note)

    if target.exists() and any(target.iterdir()):
        raise SourceError(f"{target} exists and is not a copy of this repository. Remove it and try again.")
    target.parent.mkdir(parents=True, exist_ok=True)
    args = [*auth, "clone", "--depth", "1", "--single-branch", *(["--branch", branch] if branch else []), "--", address, str(target)]
    try:
        result = runner(args, None, CLONE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(target, ignore_errors=True)  # only what this call started to create
        raise SourceError(f"Cloning took longer than {CLONE_TIMEOUT / 60:.0f} minutes and was stopped.") from exc
    except OSError as exc:
        raise SourceError(f"Git could not be started: {exc}") from exc
    if result.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise SourceError(
            f"Could not clone {owner}/{repo}: {_last_line(result.stderr)}. "
            "For a private repository, sign in to GitHub first."
        )
    return Checkout(target, owner, repo, branch, True, f"Cloned {owner}/{repo}" + (f" ({branch})" if branch else "") + ".")


@dataclass(frozen=True)
class Place:
    """Where a run works and what it may read, resolved from what the user asked for."""

    workspace: str | None
    evidence_dir: str | None
    protect_existing: bool
    # Shown with the run: {"kind": "none" | "folder" | "github", ...}
    source: dict[str, str | None]


def resolve(
    *,
    folder: str | None = None,
    repo_url: str | None = None,
    repo_branch: str | None = None,
    evidence_dir: str | None = None,
    protect_existing: bool = True,
    takes_evidence: bool = False,
    clone: Callable[..., Checkout] = checkout,
) -> Place:
    """Turn a folder or a GitHub address into the run's workspace.

    A workflow that builds an evidence dossier reads the same place as its documents,
    unless a separate documents folder was given.
    """
    if repo_url and repo_url.strip():
        copy = clone(repo_url, repo_branch)
        place = str(copy.path)
        return Place(
            workspace=place, evidence_dir=evidence_dir or (place if takes_evidence else None), protect_existing=False,
            source={"kind": "github", "url": f"https://github.com/{copy.owner}/{copy.repo}", "branch": copy.branch, "path": place, "note": copy.note},
        )
    if folder and folder.strip():
        place = str(user_folder(folder))
        return Place(
            workspace=place, evidence_dir=evidence_dir or (place if takes_evidence else None), protect_existing=protect_existing,
            source={"kind": "folder", "path": place},
        )
    return Place(workspace=None, evidence_dir=evidence_dir, protect_existing=False, source={"kind": "none"})


def earlier_work(task: str, result: dict[str, object] | None, limit: int = 2400) -> str:
    """What the previous run of a session was asked and what it delivered, for the next request."""
    final = (result or {}).get("final")
    text = ""
    if isinstance(final, dict):
        for key in ("markdown", "answer", "summary", "objective"):
            if isinstance(final.get(key), str) and final[key]:
                text = str(final[key])
                break
    lines = ["## Earlier in this session", f"The previous request was: {task.strip()[:600]}"]
    lines.append(f"It ended with:\n{text[:limit]}" if text else "It produced no final result.")
    return "\n".join(lines)


def user_folder(raw: str) -> Path:
    """A folder the user chose to work in. Refuses a whole drive and Windows' own folders."""
    text = raw.strip().strip('"')
    if not text:
        raise SourceError("Choose a folder.")
    folder = Path(text).expanduser()
    if not folder.is_absolute():
        raise SourceError("Give the full path of the folder, starting with the drive letter.")
    folder = folder.resolve()
    if not folder.is_dir():
        raise SourceError(f"The folder does not exist: {folder}")
    if folder.parent == folder:
        raise SourceError("Choose a project folder, not a whole drive.")
    # Windows reads these names in any case.
    protected = [os.environ.get(name) for name in ("SYSTEMROOT", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA")]
    for item in protected:
        if item and (folder == Path(item).resolve() or Path(item).resolve() in folder.parents):
            raise SourceError("Choose a project folder, not a folder that belongs to Windows or to installed programs.")
    home = os.environ.get("USERPROFILE")
    if home and folder == Path(home).resolve():
        raise SourceError("Choose a project folder inside your user folder, not the user folder itself.")
    return folder
