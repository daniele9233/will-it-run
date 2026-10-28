"""Agent skills: folders with a ``SKILL.md`` that tell a model how to do one kind of work, fetched from GitHub.

A skill is what a person installs for a coding agent in the terminal: GSAP's own instructions for animating, a design
brief that keeps a page from looking templated, Vercel's rules for React and for interfaces, Three.js scenes, driving a browser
to test a page. They are the same files here, in the layout a coding agent expects, so what a step quotes from them is what
the author wrote.

Nothing of them is shipped with this program. Each pack is fetched on the PC with Git at one exact commit (Git checks the
content against the commit's hash, so a repository that changed can never reach a run) and kept, with the licence of its
author, under the app's data folder as ``<owner>__<repo>@<commit>``. A run copies the skills it wants into its own project
(``.skills/<name>/``), for whoever continues the work there. The model of a step is not given a whole skill, which would fill
a small model's context: it is given the few sentences of one that bear on the choice the step asks of it (``excerpt``).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agentfit import paths
from agentfit.clone.kits import (
    FETCH_TIMEOUT,
    GitRunner,
    KitError,
    _git,
    _last_line,
    remove_tree,
    strip_frontmatter,
)
from agentfit.logging_setup import get_logger, log_event

log = get_logger("clone.skills")

SKILLS_FOLDER = ".skills"
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")


class SkillsError(KitError):
    pass


@dataclass(frozen=True)
class SkillPack:
    id: str
    repo: str
    commit: str
    license: str
    summary: str
    skills: tuple[str, ...]
    # The folder of the repository the skills are in.
    root: str = "skills"

    @property
    def owner_repo(self) -> str:
        return self.repo.removeprefix("https://github.com/")

    @property
    def folder_name(self) -> str:
        return f"{self.owner_repo.replace('/', '__')}@{self.commit[:12]}"


PACKS: tuple[SkillPack, ...] = (
    SkillPack(
        "gsap", "https://github.com/greensock/gsap-skills", "aed9cfd3277740755f6bfc1155c7aa645403b760", "MIT",
        "GSAP, from its authors: tweens, timelines, ScrollTrigger, plugins, utilities, React and framework use, performance.",
        ("gsap-core", "gsap-timeline", "gsap-scrolltrigger", "gsap-plugins", "gsap-utils", "gsap-react", "gsap-performance", "gsap-frameworks"),
    ),
    SkillPack(
        "frontend-design", "https://github.com/anthropics/skills", "dbd4588f9e1033efb41dad4bef2f7947c8993d44", "Apache-2.0",
        "Anthropic's brief for distinctive visual design: a direction, typography and layout that do not read as a template.",
        ("frontend-design",),
    ),
    SkillPack(
        "vercel", "https://github.com/vercel-labs/agent-skills", "6ec269328bc400712ee225a173a14b6b29128df7", "MIT",
        "Vercel's rules for React and Next.js code, and its guidelines for the quality of a web interface.",
        ("react-best-practices", "web-design-guidelines"),
    ),
    SkillPack(
        "threejs", "https://github.com/cesartevisual/threejs-skills", "84f9bcb4bea1f7a28d57fe7f0af78f7f9c3dc467", "MIT",
        "Three.js: scenes, materials, lighting, assets, animation, interaction, React Three Fiber, HTML overlays, performance, testing.",
        ("threejs-web", "threejs-scene", "threejs-materials", "threejs-lighting", "threejs-assets", "threejs-animation", "threejs-interaction",
         "threejs-r3f", "threejs-html", "threejs-performance", "threejs-testing"),
    ),
    SkillPack(
        "playwright", "https://github.com/microsoft/playwright-cli", "b1a5903c5b633b8c9f843ad7b629c2a78437040c", "Apache-2.0",
        "Microsoft's skill for driving a browser from the command line to look at a page and test it.",
        ("playwright-cli",),
    ),
)
PACK_BY_ID = {p.id: p for p in PACKS}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    pack: str
    folder: Path

    def view(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "pack": self.pack}


def describe(skill_file: Path) -> tuple[str, str]:
    """The name and the one-line description in the header of a SKILL.md (the folder's name when the header says none)."""
    try:
        text = skill_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return skill_file.parent.name, ""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    header: Any = {}
    if match:
        try:
            header = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            header = {}
    if not isinstance(header, dict):
        header = {}
    description = re.sub(r"\s+", " ", str(header.get("description") or "")).strip()
    return str(header.get("name") or skill_file.parent.name), description


class SkillStore:
    def __init__(self, root: Path | None = None, git: GitRunner = _git) -> None:
        self.root = Path(root) if root else paths.local_dir() / "tools" / "skills"
        self._git = git
        self._lock = threading.Lock()
        self._job: dict[str, Any] | None = None

    def folder(self, pack: SkillPack) -> Path:
        return self.root / pack.folder_name

    def ready(self, pack: SkillPack) -> bool:
        base = self.folder(pack) / pack.root
        return (self.folder(pack) / ".pack-ready").is_file() and all((base / name / "SKILL.md").is_file() for name in pack.skills)

    # -- what is installed -----------------------------------------------------------
    def skills(self, pack: SkillPack) -> list[Skill]:
        """The skills of a pack that are on this PC, with their descriptions."""
        out: list[Skill] = []
        base = self.folder(pack) / pack.root
        for name in pack.skills:
            file = base / name / "SKILL.md"
            if file.is_file():
                title, description = describe(file)
                out.append(Skill(title or name, description, pack.id, file.parent))
        return out

    def catalog(self) -> list[Skill]:
        return [s for pack in PACKS if self.ready(pack) for s in self.skills(pack)]

    def status(self) -> dict[str, Any]:
        with self._lock:
            job = dict(self._job) if self._job else None
        return {
            "packs": [
                {"id": p.id, "repo": p.repo, "commit": p.commit[:12], "license": p.license, "summary": p.summary, "ready": self.ready(p),
                 "skills": [s.view() for s in self.skills(p)] if self.ready(p) else [{"name": n, "description": "", "pack": p.id} for n in p.skills]}
                for p in PACKS
            ],
            "ready": all(self.ready(p) for p in PACKS), "job": job,
        }

    # -- fetching ----------------------------------------------------------------------
    def ensure(self, pack: SkillPack, emit: Callable[[str], None] | None = None) -> Path:
        """The pack's folder, fetched at its pinned commit when it is not on this PC yet."""
        target = self.folder(pack)
        if self.ready(pack):
            return target
        if shutil.which("git") is None and self._git is _git:
            raise SkillsError("Git is not installed on this PC. Install Git for Windows: the skills are fetched with it.")
        say = emit or (lambda _line: None)
        say(f"Fetching {pack.owner_repo} @ {pack.commit[:12]}")
        self.root.mkdir(parents=True, exist_ok=True)
        # Into a folder of its own first: a fetch that fails or comes back as another commit leaves nothing under the final name.
        work = Path(tempfile.mkdtemp(prefix="skills-", dir=self.root))
        try:
            for args in (["init", "-q"], ["remote", "add", "origin", pack.repo + ".git"], ["fetch", "--depth", "1", "-q", "origin", pack.commit],
                         ["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"]):
                result = self._git(args, work, FETCH_TIMEOUT)
                if result.returncode != 0:
                    raise SkillsError(f"Could not fetch {pack.owner_repo}: {_last_line(result.stderr or result.stdout)}")
            head = self._git(["rev-parse", "HEAD"], work, 60).stdout.strip()
            if head != pack.commit:
                raise SkillsError(f"{pack.owner_repo} came back as commit {head[:12]}, not the pinned {pack.commit[:12]}: it was not used.")
            missing = [n for n in pack.skills if not (work / pack.root / n / "SKILL.md").is_file()]
            if missing:
                raise SkillsError(f"{pack.owner_repo} has no {', '.join(missing)} at that commit.")
            remove_tree(target)
            for name in pack.skills:
                shutil.copytree(work / pack.root / name, target / pack.root / name)
            for licence in LICENSE_NAMES:
                if (work / licence).is_file():
                    shutil.copy2(work / licence, target / licence)
                    break
            (target / ".pack-ready").write_text(pack.commit + "\n", encoding="utf-8")
        except (subprocess.TimeoutExpired, OSError) as exc:
            remove_tree(target)
            raise SkillsError(f"Could not fetch {pack.owner_repo}: {exc}") from exc
        finally:
            remove_tree(work)
        log_event(log, "skill pack fetched", pack=pack.id, commit=pack.commit[:12])
        say(f"{pack.owner_repo} is ready ({len(pack.skills)} skills)")
        return target

    def install(self, ids: list[str] | None = None, emit: Callable[[str], None] | None = None) -> list[str]:
        """Fetch the packs (all of them by default). Returns the ids of the ones that are on this PC afterwards."""
        wanted = [PACK_BY_ID[i] for i in ids] if ids else list(PACKS)
        for pack in wanted:
            self.ensure(pack, emit)
        return [p.id for p in wanted if self.ready(p)]

    def install_in_background(self, ids: list[str] | None = None, on_done: Callable[[], None] | None = None) -> dict[str, Any]:
        """The same, in a thread: a job whose lines and result ``status()`` reports."""
        with self._lock:
            if self._job is not None and self._job["state"] == "running":
                return dict(self._job)
            job: dict[str, Any] = {"state": "running", "lines": [], "error": None}
            self._job = job

        def line(text: str) -> None:
            job["lines"] = [*job["lines"][-30:], text]

        def work() -> None:
            try:
                self.install(ids, line)
                job["state"] = "done"
            except (KitError, OSError, KeyError) as exc:
                job["state"], job["error"] = "failed", str(exc)
            if on_done is not None:
                on_done()

        threading.Thread(target=work, name="skills-install", daemon=True).start()
        return dict(job)

    # -- using them ------------------------------------------------------------------
    def find(self, name: str) -> Skill | None:
        return next((s for s in self.catalog() if s.name == name), None)

    def copy_into(self, workspace: Path, names: list[str] | None = None) -> list[Skill]:
        """Copy skills into ``<workspace>/.skills/<name>/`` for the model to read. All installed ones by default. Returns what was copied."""
        copied: list[Skill] = []
        for skill in self.catalog():
            if names is not None and skill.name not in names:
                continue
            destination = workspace / SKILLS_FOLDER / skill.name
            remove_tree(destination)
            shutil.copytree(skill.folder, destination)
            copied.append(skill)
        return copied

    def excerpt(self, name: str, phrases: tuple[str, ...], limit: int = 800) -> str:
        """The sentences of an installed skill that contain one of ``phrases``, one to a line and never more than ``limit`` characters:
        what a step puts in front of a small model. Empty when the skill is not on this PC or has none of them."""
        skill = self.find(name)
        if skill is None:
            return ""
        return pick((skill.folder / "SKILL.md").read_text(encoding="utf-8-sig", errors="replace"), phrases, limit)


def units(text: str) -> list[str]:
    """The paragraphs and bullets of a skill's text, each on one line. Its header, headings, tables and code are left out."""
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append(" ".join(current))
            current.clear()

    fenced = False
    for line in strip_frontmatter(text).splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            fenced = not fenced
        elif fenced or not stripped or stripped.startswith(("#", "|")):
            flush()
        else:
            if re.match(r"([-*]|\d+\.)\s", stripped):
                flush()
            current.append(stripped)
    flush()
    return out


def pick(text: str, phrases: tuple[str, ...], limit: int) -> str:
    """The units of ``text`` that contain one of ``phrases`` (from the start of a word, so ``ease`` finds ``eases`` and not ``release``), in
    their order and as long as they fit: a unit is whole or left out, and the first that does not fit ends the excerpt."""
    wanted = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + ")", flags=re.IGNORECASE) if phrases else None
    lines: list[str] = []
    size = 0
    for unit in units(text):
        if wanted is None or not wanted.search(unit):
            continue
        if size + len(unit) + 1 > limit:
            break
        lines.append(unit)
        size += len(unit) + 1
    return "\n".join(lines)
