"""Cloning a site: an address in, a project on this PC out.

The person gives an address, a model and, if they want, a folder or a GitHub repository to build
in. The clone is what they would do by hand in opencode with ai-site-cloner (``kits.py``): the kit's
project is put in the folder and its packages are installed, then an agent follows the kit's
``clone-website`` skill, which crawls the site, asks the person which pages to clone, measures
every section with the kit's scripts, writes a spec for each, builds, and loops a scored pixel diff.

It is a workflow like any other (live graph, models through the gateway, Stop, the session log)
with two deterministic steps around the agent, code and not a model:

    prepare  ->  Cloner (the skill)  ->  final check (npm run check, and something of the site written)  ->  back to the Cloner once

The agent may ask the person one thing: which pages to clone (``ask_user``, ``asking.py``).
"""

from __future__ import annotations

import contextlib
import json
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agentfit import paths
from agentfit.clone import asking, proc, prompts, tools
from agentfit.clone.devserver import DevServer, DevServerError
from agentfit.clone.install import BrowserInstall
from agentfit.clone.kits import (
    KIT,
    KitError,
    KitStore,
    has_package,
    host_of,
    host_slug,
    is_cloner_project,
    is_empty,
)
from agentfit.db import Database, utcnow
from agentfit.logging_setup import get_logger, log_event
from agentfit.workflows import checks, schemas
from agentfit.workflows.checks import CheckContext, CheckSpec
from agentfit.workflows.compiler import RunCancelled
from agentfit.workflows.dsl import (
    END,
    START,
    AgentDef,
    CheckDef,
    EdgeDef,
    ModelSlot,
    WorkflowDef,
    WorkflowMeta,
)
from agentfit.workflows.runner import TERMINAL, RunManager, RunRecord

log = get_logger("clone")

NAMESPACE = "clonesite"
WORKFLOW = "clone-site"
# Passes after the first. Each is a fresh conversation (the manifest says where to resume) and is allowed only while the work moved.
FIX_ROUNDS = 8
# Rounds in a row without a change in the project after which the Cloner has only its tools that write, and what it is told to write.
# A fresh pass looks around first, and a 27B does it piece by piece (brioni.com: twenty rounds to list the folders, read the
# manifest, the layout, the styles and the first outline of the tokens; the first question comes after nine), so it is more than that.
# Four more rounds without a file and its tools go.
FOCUS_ROUNDS = 20
FOCUS_HINT = (
    "Start with the foundation if it is not done: src/app/layout.tsx with the site's fonts (next/font/local, the files are in "
    "public/fonts) and src/app/globals.css with its colours and type, both from tokens.json. Then the shared header and footer, "
    "then the next section of the manifest that is not built, as a component in src/components/sites/. A first version that "
    "compiles is better than none."
)

# Set by the app: where the kit and the browser engine are.
kit_store: KitStore | None = None
install_source: Callable[[], BrowserInstall] | None = None

FILES = ["read_file", "list_dir", "write_file", "edit_file", "delete_file"]
JUDGMENT = ["browser_navigate", "browser_resize", "browser_take_screenshot", "browser_snapshot", "browser_evaluate", "browser_click",
            "browser_hover", "browser_scroll", "browser_wait_for", "browser_console_messages", "download_asset"]
SOURCE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".css", ".mdx"}


class CloneError(Exception):
    pass


def normalize_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise CloneError("Write the address of a website.")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or not host or ("." not in host and host != "localhost"):
        raise CloneError("That is not the address of a website.")
    return text


def site_title(url: str) -> str:
    return host_of(url)


# -- the workflow ----------------------------------------------------------------------------


def definition(*, store: KitStore, url: str, model_key: str, sees: bool, vision_key: str = "", extra: str = "") -> WorkflowDef:
    """The workflow of one clone. ``sees``: the model can see; else ``vision_key`` is a model that can (or empty: none)."""
    host = host_of(url)
    models = {"main": ModelSlot(label="Cloner", kind="vision" if sees else "", model_key=model_key, auto_tune=True)}
    if vision_key and not sees:
        models["vision"] = ModelSlot(label="Vision", kind="vision", model_key=vision_key, auto_tune=True)
    look = sees or bool(vision_key)
    cloner = AgentDef(
        id="cloner", role="Cloner", model="main", goal="Clone the site with the kit's clone-website skill.",
        system_prompt=prompts.skill_prompt(store, url=url, host=host, sees=look, extra=extra),
        tools=[*FILES, *tools.PROJECT_TOOLS, *JUDGMENT, *(["look"] if look else [])], output_schema=schemas.TEXT,
        # A whole site is dozens of scripts, a spec and a component for each section, builds and diffs: too long for one conversation
        # on a local model, whose context (about 30K tokens for a 27B on a 16 GB card, 12K of it the skill) fills with its own history
        # and trims what it read, so it reads the same files again (brioni.com: 117 reads against 14 edits). So the work is passes of
        # 150 rounds, each a fresh conversation that resumes from the kit's manifest, and the final check sends it back while the work moves.
        # The kit works through its scripts, so a round in which they changed anything in the folder is progress.
        # After FOCUS_ROUNDS in a row in which nothing changed it keeps only the tools that write (brioni.com: 110 rounds of reading,
        # twenty of them the same 9 KB file, and not one file written). The temperature is not near zero: Qwen3 warns that greedy
        # decoding "can lead to endless repetitions", which is what a 27B did there.
        max_tool_rounds=150, revision_tool_rounds=150, idle_watches_workspace=True, temperature=0.6,
        focus_after_idle_rounds=FOCUS_ROUNDS, focus_hint=FOCUS_HINT,
    )
    prepare = CheckDef(id="prepare", kind="clone_prepare", label="Preparation", params={"url": url})
    final = CheckDef(id="final", kind="clone_build", label="Final check", params={"script": "check", "host": host})
    edges = [
        EdgeDef(source=START, target="prepare"),
        EdgeDef(source="prepare", target="cloner", when="passed == true", label="ready"), EdgeDef(source="prepare", target=END),
        EdgeDef(source="cloner", target="final"),
        EdgeDef(source="final", target="cloner", when="should_continue == true", label="fix", max_loops=FIX_ROUNDS, on_exhausted=END),
        EdgeDef(source="final", target=END),
    ]
    return WorkflowDef(
        workflow=WorkflowMeta(id=WORKFLOW, name=f"Clone a site: {KIT.name}", description=KIT.summary, max_steps=40, category="clone"),
        models=models, agents=[cloner], checks=[prepare, final], edges=edges,
        layout={"prepare": {"x": 0.0, "y": 0.0}, "cloner": {"x": 220.0, "y": 0.0}, "final": {"x": 440.0, "y": 0.0}},
    )


# -- the deterministic steps ---------------------------------------------------------------


def _step(ctx: CheckContext, step: str, state: str, detail: str = "") -> None:
    if ctx.emit:
        ctx.emit("clone.step", {"step": step, "state": state, "detail": detail})


def _fail(summary: str, **more: Any) -> dict[str, Any]:
    return {"passed": False, "verdict": "NOT_READY", "summary": summary, **more}


def _install_tool(ctx: CheckContext, argv: list[str], step: str) -> tuple[int, list[str]]:
    assert ctx.workspace is not None
    count = {"n": 0}

    def line(text: str) -> None:
        count["n"] += 1
        if count["n"] % 20 == 0:
            _step(ctx, step, "running", text[:120])

    try:
        code, lines, timed_out = proc.run_streaming(argv, ctx.workspace, cancelled=ctx.cancelled, timeout=1500.0, on_line=line,
                                                    env={"NEXT_TELEMETRY_DISABLED": "1"})
    except proc.Cancelled as exc:
        raise RunCancelled() from exc
    return (-1 if timed_out else code), lines


def _clone_prepare(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    """Everything the clone needs before the agent starts: the kit, the project, its packages, Playwright's Chromium."""
    if kit_store is None or install_source is None:
        return _fail("The cloner is not available in this run.")
    store, install = kit_store, install_source()
    workspace = ctx.workspace
    assert workspace is not None

    _step(ctx, "tools", "running")
    node = install.node()
    npm = install.npm_exe()
    if not node.get("ok"):
        _step(ctx, "tools", "failed", node.get("detail") or "")
        return _fail(node.get("detail") or "Node.js is needed.")
    if not npm:
        _step(ctx, "tools", "failed", "npm")
        return _fail("npm is not on this PC. It comes with Node.js: install the LTS from nodejs.org.")
    _step(ctx, "tools", "done", f"Node {node.get('version')}")

    try:
        _step(ctx, "kit", "running")
        store.ensure(emit=lambda text: _step(ctx, "kit", "running", text))
        _step(ctx, "kit", "done", f"{KIT.name} @ {KIT.commit[:12]}")
    except KitError as exc:
        _step(ctx, "kit", "failed", str(exc))
        return _fail(str(exc))

    _step(ctx, "project", "running")
    scaffolded: list[str] = []
    if is_cloner_project(workspace):
        _step(ctx, "project", "done", "the folder already is a project of the kit")
    elif has_package(workspace):
        message = "The folder already holds another project: choose an empty folder, or a project made with ai-site-cloner."
        _step(ctx, "project", "failed", message)
        return _fail(message)
    else:
        scaffolded = store.scaffold(workspace)
        if ctx.created is not None:
            ctx.created.update((workspace / name).resolve() for name in scaffolded)
        _step(ctx, "project", "done", f"{len(scaffolded)} files")

    if not (workspace / "node_modules").is_dir():
        _step(ctx, "install", "running", "npm install")
        code, lines = _install_tool(ctx, [npm, "install", "--no-fund", "--no-audit", "--loglevel=error"], "install")
        if code != 0:
            _step(ctx, "install", "failed", f"exit code {code}")
            return _fail(f"npm install failed (exit code {code}):\n" + "\n".join(lines[-15:]))
        _step(ctx, "install", "done")

    # The kit's scripts launch Playwright's own Chromium, and install it on the first run if it is missing; doing it here shows the
    # progress (about 170 MB, once) and keeps a download out of the agent's first command.
    _step(ctx, "browser", "running", "npm run setup")
    code, lines = _install_tool(ctx, [npm, "run", "setup"], "browser")
    if code != 0:
        _step(ctx, "browser", "failed", f"exit code {code}")
        return _fail(f"Playwright's Chromium could not be installed (exit code {code}):\n" + "\n".join(lines[-15:]))
    _step(ctx, "browser", "done")
    return {"passed": True, "verdict": "READY", "scaffolded": len(scaffolded), "node": node.get("version"),
            "summary": f"{KIT.name} ready" + (f"; {len(scaffolded)} files of the kit copied" if scaffolded else "") + "."}


def _nothing_written(ctx: CheckContext) -> str | None:
    """Why the project still holds nothing of the site, or None. The kit's own page builds fine, so a passing build proves nothing about
    an agent that wrote no file (seen: a whole team done in a minute and a "completed" run with the template's blank page)."""
    root = ctx.workspace
    if root is None or not int((ctx.outputs.get("prepare") or {}).get("scaffolded") or 0):
        return None  # a project of the person's own: its pages are theirs, there is no page of the kit to compare with
    sites = root / "src" / "components" / "sites"
    # A source file with something in it: an empty file of a folder's name does not count.
    if sites.is_dir() and any(p.is_file() and p.suffix in SOURCE_SUFFIXES and p.stat().st_size > 100 for p in sites.rglob("*")):
        return None
    app = root / "src" / "app"
    if app.is_dir() and any(p.name == "page.tsx" and p.parent != app and p.stat().st_size > 100 for p in app.rglob("page.tsx")):
        return None  # a page of another route
    page, original = app / "page.tsx", (kit_store.folder() / "src" / "app" / "page.tsx") if kit_store else None
    if page.is_file() and original is not None and original.is_file() and page.read_text(encoding="utf-8", errors="replace") != original.read_text(encoding="utf-8", errors="replace"):
        return None
    return ("the project still holds nothing of the site: src/components/sites/ has no source file and src/app/page.tsx is the kit's own. "
            "Follow the skill: extract, write the specs and build the pages.")


MANIFEST = "docs/research/manifest.json"


def _unfinished(root: Path | None) -> list[str]:
    """What the kit's own manifest still lists as not done, as ``route: section (stage)``. The skill is resumable: each section moves
    discovered, extracted, specd, built, merged, qa_passed, and a page is done when all its sections are qa_passed. Empty when there is
    no manifest (the skill was not followed, or the folder is a project of the person's own): the other gates judge that."""
    if root is None:
        return []
    try:
        pages = json.loads((root / MANIFEST).read_text(encoding="utf-8")).get("pages") or []
    except (OSError, ValueError, AttributeError):
        return []
    left: list[str] = []
    for page in pages:
        if not isinstance(page, dict) or page.get("status") == "done":
            continue
        sections = [s for s in page.get("sections") or [] if isinstance(s, dict) and s.get("stage") != "qa_passed"]
        if not sections:
            left.append(f"{page.get('route', '?')}: no section yet" if not page.get("sections") else f"{page.get('route', '?')}: not marked done")
        left += [f"{page.get('route', '?')}: {s.get('name', '?')} ({s.get('stage', '?')})" for s in sections]
    return left


def _progress(root: Path | None) -> dict[str, int]:
    """What the work amounts to now: the kit's sections by stage, the specs written and the size of the project's own source."""
    out: dict[str, int] = {}
    if root is None:
        return out
    try:
        pages = json.loads((root / MANIFEST).read_text(encoding="utf-8")).get("pages") or []
    except (OSError, ValueError, AttributeError):
        pages = []
    for page in pages:
        for section in (page.get("sections") or []) if isinstance(page, dict) else []:
            if isinstance(section, dict):
                key = f"stage:{section.get('stage')}"
                out[key] = out.get(key, 0) + 1
    specs = root / "docs" / "research" / "components"
    out["specs"] = sum(1 for _ in specs.rglob("*.spec.md")) if specs.is_dir() else 0
    src = root / "src"
    files = size = 0
    for path in src.rglob("*") if src.is_dir() else ():
        if path.is_file() and path.suffix in SOURCE_SUFFIXES:
            files += 1
            size += path.stat().st_size
    out["source_files"], out["source_bytes"] = files, size
    return out


def _clone_build(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    """``npm run check`` (or another script): did the project build? The log goes back to the agent that wrote it."""
    if install_source is None or ctx.workspace is None:
        return {"passed": False, "built": False, "summary": "The cloner is not available in this run."}
    workspace = ctx.workspace
    script = str(checks.param(check, "script") or "check")
    package = workspace / "package.json"
    if not package.is_file() or f'"{script}"' not in package.read_text(encoding="utf-8", errors="replace"):
        return {"passed": False, "built": False, "summary": f'The project has no "{script}" script, so nothing could be checked.'}
    npm = install_source().npm_exe()
    if not npm:
        return {"passed": False, "built": False, "summary": "npm is not on this PC."}
    # Another pass is worth it only if the work moved since the last check: a pass that changed nothing would be the same pass again.
    progress = _progress(workspace)
    more = (ctx.outputs.get(check.id) or {}).get("progress") != progress
    _step(ctx, check.id, "running", f"npm run {script}")
    try:
        code, lines, timed_out = proc.run_streaming([npm, "run", script], workspace, cancelled=ctx.cancelled, timeout=900.0,
                                                    env={"NEXT_TELEMETRY_DISABLED": "1"})
    except proc.Cancelled as exc:
        raise RunCancelled() from exc
    tail = lines[-45:]
    if code == 0 and not timed_out:
        empty = _nothing_written(ctx)
        if empty:
            _step(ctx, check.id, "failed", "nothing of the site written")
            return {"passed": False, "built": True, "verdict": "NOTHING_WRITTEN", "summary": f"npm run {script} passed, but {empty}", "log": tail[-8:],
                    "progress": progress, "should_continue": more}
        left = _unfinished(workspace)
        if left:
            _step(ctx, check.id, "failed", f"{len(left)} section(s) not qa_passed")
            shown = "; ".join(left[:12]) + (f"; and {len(left) - 12} more" if len(left) > 12 else "")
            return {"passed": False, "built": True, "verdict": "UNFINISHED", "log": tail[-8:], "progress": progress, "should_continue": more,
                    "summary": f"npm run {script} passed, but the kit's manifest ({MANIFEST}) says the work is not finished: {shown}. "
                               "Resume as the skill says: node scripts/manifest.mjs status, build and repair what is not done, compare each section "
                               "with node scripts/diff.mjs, and mark it done only when it matches: "
                               "node scripts/manifest.mjs set --route <route> --section <name> --stage qa_passed."}
        _step(ctx, check.id, "done", f"npm run {script} passed")
        return {"passed": True, "built": True, "verdict": "BUILT", "summary": f"npm run {script} passed.", "log": tail[-8:],
                "progress": progress, "should_continue": False}
    why = "did not finish in 15 minutes" if timed_out else f"failed (exit code {code})"
    _step(ctx, check.id, "failed", why)
    return {"passed": False, "built": False, "verdict": "BUILD_FAILED", "progress": progress, "should_continue": more,
            "summary": f"npm run {script} {why}. Fix the errors in the log, run npm run typecheck to confirm, and finish.", "log": tail}


def register() -> None:
    tools.register()
    checks.CHECKS["clone_prepare"] = CheckSpec(
        kind="clone_prepare", label="Preparation",
        description="Fetches the cloning kit at its pinned commit, puts its project in the folder (unless the folder already is one), installs its packages "
                    "and Playwright's Chromium.",
        run=_clone_prepare, defaults={"url": ""}, listed=False,
    )
    checks.CHECKS["clone_build"] = CheckSpec(
        kind="clone_build", label="Build check",
        description="Runs the project's check script (lint, typecheck, production build). A failure is sent back, with the log, to the agent that wrote the code.",
        run=_clone_build, defaults={"script": "check", "host": "site"}, listed=False,
    )


# -- the studies ------------------------------------------------------------------------------


def _slim(event: dict[str, Any]) -> dict[str, Any]:
    """A tool call as the page shows it: which tool, which file, command or address, how it went. Not its output."""
    if event["type"] != "tool.call":
        return event
    p = event["payload"]
    arguments = p.get("arguments") or {}
    what = None
    if isinstance(arguments, dict):
        what = arguments.get("command") or arguments.get("path") or arguments.get("filename") or arguments.get("url") or arguments.get("question")
    return {**event, "payload": {"node": p.get("node"), "tool": p.get("tool"), "status": p.get("status"), "path": str(what)[:100] if what else None}}


_SERVED = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
           ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


class CloneStudio:
    namespace = NAMESPACE

    def __init__(self, db: Database, runs: RunManager, store: KitStore, install: BrowserInstall) -> None:
        self.db = db
        self.runs = runs
        self.store = store
        self.install = install
        self._previews: dict[str, DevServer] = {}
        self._lock = threading.Lock()

    def home(self, study_id: str) -> Path:
        return paths.workspaces_dir() / f"clone-{study_id}"

    def project(self, study_id: str) -> str:
        return f"{self.namespace}:{study_id}"

    def _entry(self, study_id: str) -> dict[str, Any]:
        entry = self.db.kv_get(self.namespace, study_id)
        if not isinstance(entry, dict):
            raise CloneError("Clone not found")
        return entry

    def _runs(self, study_id: str) -> list[RunRecord]:
        rows = self.db.query("SELECT id FROM workflow_run WHERE project = ? ORDER BY started_at, rowid", (self.project(study_id),))
        return [self.runs.get(r["id"]) for r in rows]

    def workspace(self, entry: dict[str, Any]) -> Path:
        return Path(entry.get("workspace") or self.home(entry["id"]))

    # -- listing -------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        out = []
        for entry in self.db.kv_list(self.namespace).values():
            runs = self._runs(entry["id"])
            last = runs[-1] if runs else None
            shots = self._shots(entry, limit=1)
            out.append({**entry, "status": last.status if last else "none", "active": any(r.status not in TERMINAL for r in runs),
                        "thumbnail": shots[0] if shots else None})
        return sorted(out, key=lambda e: e.get("updated_at") or "", reverse=True)

    def _shots(self, entry: dict[str, Any], limit: int = 60) -> list[str]:
        """Screenshots the kit's scripts and the agent saved, as paths relative to the project. The full-size picture is left out when
        it has a ``-review.png`` twin (the half-size one the skill looks at): the page shows the small ones."""
        root = self.workspace(entry)
        found: list[str] = []
        for folder in ("docs/research", "docs/design-references"):
            base = root / folder
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.png")):
                if ".cache" in path.parts:
                    continue
                if not path.stem.endswith("-review") and path.with_name(path.stem + "-review.png").is_file():
                    continue
                found.append(str(path.relative_to(root)).replace("\\", "/"))
        return found[:limit]

    # -- starting --------------------------------------------------------------
    def target(self, place: str | None, slug: str, home: Path) -> Path:
        """Where the project is: the folder the person chose when it is empty or already a project of the kit, a folder of its own inside it when not."""
        if not place:
            return home
        folder = Path(place)
        if is_empty(folder) or is_cloner_project(folder):
            return folder
        return folder / slug

    def start(self, url: str, *, model_key: str, sees: bool, vision_key: str = "", language: str = "en", workspace: str | None = None,
              protect_existing: bool = True, source: dict[str, Any] | None = None, approval: str = "ask", extra: str = "",
              study_id: str | None = None) -> tuple[dict[str, Any], RunRecord]:
        """Start a clone, or a new run of one (``study_id``): the agent carries on in the project the earlier run left, from the kit's manifest."""
        if study_id:
            entry = self._entry(study_id)
            if any(r.status not in TERMINAL for r in self._runs(study_id)):
                raise CloneError("This clone is still working: stop it first.")
            url = entry["url"]
            target = self.workspace(entry)
        else:
            url = normalize_url(url)
            study_id = uuid.uuid4().hex[:12]
            slug = host_slug(url)
            target = self.target(workspace, slug, self.home(study_id))
            entry = {"id": study_id, "url": url, "title": site_title(url), "slug": slug, "host": host_of(url), "created_at": utcnow()}
        try:
            # The agent is given what the kit's skill says, so the kit is on this PC before the run is built.
            self.store.ensure()
        except KitError as exc:
            raise CloneError(str(exc)) from exc
        target.mkdir(parents=True, exist_ok=True)
        target = proc.resolve_true_case(target)
        definition_ = definition(store=self.store, url=url, model_key=model_key, sees=sees, vision_key=vision_key, extra=extra)
        again = bool(self._runs(study_id))
        task = ("Improve the clone of " if again else "Clone this website: ") + url
        if extra.strip():
            task += f"\nExtra instructions from the person: {extra.strip()}"
        if language == "it":
            # Small models answer in the language of their instructions (English) unless told plainly.
            task += "\n(scrivi le risposte e fai le domande in italiano)"
        record = self.runs.start(definition_, task, title=f"{entry['title']}", project=self.project(study_id), workspace=str(target),
                                 protect_existing=protect_existing, source=source or {"kind": "none"}, approval=approval)
        # Built for this clone, not saved with the workflows: the live graph of the session reads it from here.
        self.db.kv_set("run_definition", record.id, definition_.model_dump(mode="json", by_alias=True))
        entry = {**entry, "model_key": model_key, "vision_key": vision_key, "sees": sees, "source": source or {"kind": "none"},
                 "workspace": str(target), "updated_at": utcnow()}
        self.db.kv_set(self.namespace, study_id, entry)
        log_event(log, "site clone started", study=study_id, run=record.id)
        return entry, record

    # -- reading ---------------------------------------------------------------
    def get(self, study_id: str) -> dict[str, Any]:
        entry = self._entry(study_id)
        runs = []
        for record in self._runs(study_id):
            events = [_slim(e) for e in self.runs.events(record.id)
                      if e["type"].startswith("clone.") or e["type"] in ("node.started", "node.completed", "node.failed", "node.retry", "node.stalled",
                                                                         "run.completed", "run.failed")
                      or (e["type"] == "tool.call" and e["payload"].get("status") != "running")
                      or (e["type"] == "model.switch" and e["payload"].get("phase") in ("loading", "ready", "failed"))]
            outputs = ((record.result or {}).get("outputs") or {}) if isinstance(record.result, dict) else {}
            runs.append({
                "id": record.id, "status": record.status, "error": record.error, "task": record.task, "started_at": record.started_at,
                "finished_at": record.finished_at, "events": events,
                "outputs": {k: v for k, v in outputs.items() if isinstance(v, dict)},
                "pending_approvals": [a.model_dump() for a in record.pending_approvals],
            })
        root = self.workspace(entry)
        active = self.active_run(study_id)
        return {**entry, "runs": runs, "active": active is not None, "shots": self._shots(entry), "project": (root / "package.json").is_file(),
                "preview": self._preview_url(study_id), "ask": asking.pending(active) if active else None, "ask_run": active}

    def file(self, study_id: str, relative: str) -> Path:
        """A file of the clone's project (a screenshot), never outside it."""
        root = self.workspace(self._entry(study_id)).resolve()
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise CloneError("Not a file of this clone.")
        if target.suffix.lower() not in _SERVED or not target.is_file():
            raise CloneError("File not found.")
        return target

    @staticmethod
    def media(path: Path) -> str:
        return _SERVED.get(path.suffix.lower(), "application/octet-stream")

    def answer(self, study_id: str, text: str) -> bool:
        """The person's answer to the question the agent asked. False when nothing is waiting."""
        self._entry(study_id)
        run_id = self.active_run(study_id)
        return bool(run_id) and asking.answer(str(run_id), text)

    # -- the result ----------------------------------------------------------------
    def _preview_url(self, study_id: str) -> str | None:
        server = self._previews.get(study_id)
        return server.url if server is not None else None

    def preview(self, study_id: str) -> str:
        """The clone served on http://127.0.0.1:<port>/ (its dev server), for as long as the app runs."""
        entry = self._entry(study_id)
        root = self.workspace(entry)
        if not (root / "package.json").is_file():
            raise CloneError("There is no project to show yet.")
        npm = self.install.npm_exe()
        if not npm:
            raise CloneError("npm is not on this PC. It comes with Node.js: install the LTS from nodejs.org.")
        with self._lock:
            server = self._previews.get(study_id)
            if server is None or server.folder != root:
                server = self._previews[study_id] = DevServer(root, npm)
        try:
            url = server.start()
        except DevServerError as exc:
            raise CloneError(str(exc)) from exc
        # The app's link opener only allows 127.0.0.1 addresses that have a path, so the slash is never left out.
        return url.rstrip("/") + "/"

    def active_run(self, study_id: str) -> str | None:
        return next((r.id for r in reversed(self._runs(study_id)) if r.status not in TERMINAL), None)

    def delete(self, study_id: str) -> None:
        entry = self._entry(study_id)
        runs = self._runs(study_id)
        if any(r.status not in TERMINAL for r in runs):
            raise CloneError("Stop the clone before deleting it.")
        with self._lock:
            server = self._previews.pop(study_id, None)
        if server is not None:
            server.stop()
        for record in runs:
            self.runs.delete(record.id)
            self.db.kv_delete("run_definition", record.id)
        self.db.kv_delete(self.namespace, study_id)
        # Its own folder goes with it. A folder or repository of the user's is never touched.
        if entry.get("workspace") and proc.resolve_true_case(entry["workspace"]) == proc.resolve_true_case(self.home(study_id)):
            with contextlib.suppress(OSError):
                shutil.rmtree(self.home(study_id), ignore_errors=True)


register()
