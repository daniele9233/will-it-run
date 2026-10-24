"""The cloning kit: Mahanaicoach/ai-site-cloner, fetched from GitHub at a pinned commit.

It is what the person does by hand in opencode:

    git clone https://github.com/Mahanaicoach/ai-site-cloner.git .
    npm install
    /clone-website https://some-site

The repository (MIT) is a Next.js 16 project plus the Node scripts that measure a site with
Playwright (``scripts/extract/page.mjs``, ``diff.mjs``, ``compare.mjs``...) and the
``clone-website`` skill that tells a coding agent how to run them: crawl the pages, ask the
person which to clone, extract, write a spec per section, build, and loop a scored pixel diff.

Nothing of it is copied into this program. It is fetched on the user's PC with Git at one exact
commit (Git checks the content against the commit's hash, so a changed repository can never
reach a run) and kept under the app's data folder; its files are copied into the folder the
clone is built in, as the clone would have put them. What the skill says is not rewritten: the
agent is given it as it is (``prompts.py``), and what the app adds is a short note on the tools
it provides in place of opencode's.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentfit import paths
from agentfit.logging_setup import get_logger, log_event

log = get_logger("clone.kits")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FETCH_TIMEOUT = 600.0

GitRunner = Callable[[list[str], Path | None, float], "subprocess.CompletedProcess[str]"]


class KitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Kit:
    id: str
    name: str
    author: str
    repo: str
    commit: str
    license: str
    summary: str


KIT = Kit(
    id="cloner",
    name="ai-site-cloner",
    author="Mahanaicoach",
    repo="https://github.com/Mahanaicoach/ai-site-cloner",
    commit="ad6189bf33945a4431491827c4fde4382f0b4407",
    license="MIT",
    summary="A Next.js 16 project and the scripts that measure a site with Playwright (every computed style, the responsive layout at "
            "phone, tablet and desktop, the assets), a spec for each section, and a pixel-diff QA that decides when each section is done.",
)
KITS = {KIT.id: KIT}

# What the project is made of; the rest of the repository (Docker files, CI, the configuration of other coding tools, the demo
# clones and their images) is not part of a clone.
KEEP = (
    ".claude", "src", "public", "scripts", "AGENTS.md", "CLAUDE.md", "BRAND.md", "components.json", "eslint.config.mjs", "next.config.ts",
    "package.json", "package-lock.json", "postcss.config.mjs", "tsconfig.json", ".gitignore", ".nvmrc",
)

SKILL = ".claude/skills/clone-website/SKILL.md"
# A folder that already is a project of the kit has these.
MARKERS = ("scripts/extract/page.mjs", SKILL)


def _git(args: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
    # Never wait for a password or open a sign-in window: fail and say so.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
    )


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no message from git"


def strip_frontmatter(text: str) -> str:
    """The body of a Markdown file with a ``---`` header (the skill has one)."""
    match = re.match(r"^---\s*\n.*?\n---\s*\n", text, flags=re.DOTALL)
    return text[match.end():].lstrip("\n") if match else text


class KitStore:
    def __init__(self, root: Path | None = None, git: GitRunner = _git) -> None:
        self.root = Path(root) if root else paths.local_dir() / "tools" / "clone-kits"
        self._git = git

    def folder(self) -> Path:
        return self.root / KIT.id / KIT.commit[:12]

    def ready(self) -> bool:
        return (self.folder() / ".kit-ready").is_file()

    def describe(self) -> dict[str, Any]:
        return {"id": KIT.id, "name": KIT.name, "author": KIT.author, "repo": KIT.repo, "commit": KIT.commit[:12], "license": KIT.license,
                "summary": KIT.summary, "ready": self.ready()}

    def ensure(self, emit: Callable[[str], None] | None = None) -> Path:
        """The kit's folder, fetched at its pinned commit when it is not on this PC yet."""
        folder = self.folder()
        if self.ready():
            return folder
        if shutil.which("git") is None and self._git is _git:
            raise KitError("Git is not installed on this PC. Install Git for Windows: the cloning kit is fetched with it.")
        say = emit or (lambda _line: None)
        say(f"Fetching {KIT.name} ({KIT.repo.removeprefix('https://github.com/')} @ {KIT.commit[:12]})")
        remove_tree(folder)
        folder.mkdir(parents=True, exist_ok=True)
        steps = [
            ["init", "-q"],
            ["remote", "add", "origin", KIT.repo + ".git"],
            ["fetch", "--depth", "1", "-q", "origin", KIT.commit],
            ["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"],
        ]
        try:
            for args in steps:
                result = self._git(args, folder, FETCH_TIMEOUT)
                if result.returncode != 0:
                    raise KitError(f"Could not fetch {KIT.name}: {_last_line(result.stderr or result.stdout)}")
            head = self._git(["rev-parse", "HEAD"], folder, 60).stdout.strip()
        except (subprocess.TimeoutExpired, OSError) as exc:
            remove_tree(folder)
            raise KitError(f"Could not fetch {KIT.name}: {exc}") from exc
        except KitError:
            remove_tree(folder)
            raise
        if head != KIT.commit:
            remove_tree(folder)
            raise KitError(f"{KIT.name} came back as commit {head[:12]}, not the pinned {KIT.commit[:12]}: it was not used.")
        (folder / ".kit-ready").write_text(KIT.commit + "\n", encoding="utf-8")
        log_event(log, "cloning kit fetched", commit=KIT.commit[:12])
        say(f"{KIT.name} is ready (commit {KIT.commit[:12]})")
        return folder

    def read(self, relative: str) -> str:
        try:
            return (self.folder() / relative).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise KitError(f"{relative} is missing from {KIT.name}: {exc}") from exc

    def skill(self) -> str:
        """The clone-website skill, as its author wrote it (without its header)."""
        return strip_frontmatter(self.read(SKILL))

    def scaffold(self, destination: Path) -> list[str]:
        """Copy the project into ``destination``, never over a file that is already there. Returns what was copied."""
        source = self.folder()
        copied: list[str] = []
        for name in KEEP:
            origin = source / name
            if not origin.exists():
                continue
            if origin.is_dir():
                for path in sorted(origin.rglob("*")):
                    if path.is_file():
                        relative = path.relative_to(source)
                        if (destination / relative).exists():
                            continue
                        (destination / relative).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, destination / relative)
                        copied.append(str(relative).replace("\\", "/"))
            elif not (destination / name).exists():
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, destination / name)
                copied.append(name)
        (destination / "docs" / "research").mkdir(parents=True, exist_ok=True)
        return copied


def is_cloner_project(folder: Path) -> bool:
    """A folder that already is a project of the kit: the clone is made in it as it is."""
    return all((folder / marker).is_file() for marker in MARKERS)


def has_package(folder: Path) -> bool:
    return (folder / "package.json").is_file()


def is_empty(folder: Path) -> bool:
    try:
        return not any(entry.name != ".git" for entry in folder.iterdir())
    except OSError:
        return True


def host_of(url: str) -> str:
    """``https://www.Example.com/a`` -> ``example.com``: the name the kit's scripts give a site's folders (``docs/research/<host>/``)."""
    host = re.sub(r"^[a-z]+://", "", url.strip().lower()).split("/")[0].split("?")[0].split("#")[0]
    host = re.sub(r":\d+$", "", host).removeprefix("www.")
    return host or "site"


def host_slug(url: str) -> str:
    """The same name made safe for a folder of ours (``example-com``)."""
    return re.sub(r"[^a-z0-9]+", "-", host_of(url)).strip("-") or "site"


def remove_tree(path: Path) -> None:
    """``rmtree`` that also removes Git's read-only object files."""
    def onerror(func: Callable[..., Any], target: str, _exc: Any) -> None:
        with contextlib.suppress(OSError):
            os.chmod(target, 0o700)
            func(target)

    if path.exists():
        shutil.rmtree(path, onerror=onerror)
