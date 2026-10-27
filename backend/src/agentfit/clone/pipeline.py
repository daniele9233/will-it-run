"""Cloning a site as a pipeline: capture, project, measure, and a model only where a number says it is needed.

    capture  ->  project  ->  measure  ->  (polisher  ->  measure)

* **capture** (code): Chrome opens the address like a visitor; the page as it rendered, its style rules, pictures and fonts are
  frozen into a folder, with a screenshot of the original at desktop and phone width (``capture.mjs``).
* **project** (code, and a model for the names): the capture becomes a Next.js project, one component for each section, and is
  installed and built (``replica.py``).
* **measure** (code): the built project is served and screenshotted at the same widths, and compared with the original's
  screenshots, pixel by pixel and structure by structure (``measure.mjs``). The accuracy is that number, not a judgement.
* **polisher** (a model, only when the measure finds a weak section): it looks at the original and the clone side by side and
  writes style fixes, which count only if the next measure is better.

A page that is copied from what the browser rendered does not depend on a model's ability to describe a layout: on brioni.com
the first measure is above 99%. The local models do what they are good at, which is small and checked.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentfit.clone import proc, replica
from agentfit.clone.browser import find_browser
from agentfit.clone.devserver import DevServer, DevServerError
from agentfit.clone.install import BrowserInstall, CloneSetupError
from agentfit.clone.kits import host_of, host_slug
from agentfit.logging_setup import get_logger
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

log = get_logger("clone.pipeline")

HERE = Path(__file__).resolve().parent
CAPTURE_SCRIPT = HERE / "capture.mjs"
MEASURE_SCRIPT = HERE / "measure.mjs"

WORKFLOW = "clone-site-replica"
# What the owner asked for: below this accuracy the run is blocked instead of completed.
TARGET = 0.80
# Below this a model is asked to look at the weakest sections; a page that is as good as this is left alone.
POLISH_BELOW = 0.97
POLISH_ROUNDS = 2
# A section is "weak" below this visual score.
WEAK = 0.9

VIEWPORTS = [{"name": "desktop", "width": 1440, "height": 900}, {"name": "mobile", "width": 390, "height": 844}]

# Set by the app, like ``studio.install_source``: where the browser engine is.
install_source: Callable[[], BrowserInstall] | None = None


def research(workspace: Path, host: str) -> Path:
    return workspace / "docs" / "research" / host


def capture_dir(workspace: Path, host: str) -> Path:
    return research(workspace, host) / "capture"


def report_dir(workspace: Path, host: str) -> Path:
    return research(workspace, host) / "report"


# -- helpers -------------------------------------------------------------------------------------------------------


def _step(ctx: CheckContext, step: str, state: str, detail: str = "") -> None:
    if ctx.emit:
        ctx.emit("clone.step", {"step": step, "state": state, "detail": detail})


def _fail(summary: str, verdict: str = "NOT_READY", **more: Any) -> dict[str, Any]:
    return {"passed": False, "verdict": verdict, "summary": summary, **more}


def _run_script(ctx: CheckContext, install: BrowserInstall, script: Path, config: Path, step: str, *, timeout: float) -> tuple[bool, str]:
    """Run one of the two Node scripts, following its progress. Returns (finished well, the reason when not).

    The script's result is the file it writes; its stdout only says how far it is (a line longer than the reader keeps is cut).
    """
    assert ctx.workspace is not None
    try:
        node = install.node_command()
    except CloneSetupError as exc:
        return False, str(exc)
    problem: list[str] = []

    def on_line(line: str) -> None:
        if not line.startswith('{"event"'):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if event.get("event") == "progress":
            _step(ctx, step, "running", f"{event.get('step', '')}: {event.get('detail', '')}".strip(": "))
        elif event.get("event") == "fatal":
            problem.append(str(event.get("error") or "the script stopped"))

    try:
        code, lines, timed_out = proc.run_streaming(
            [node, str(script), str(config)], ctx.workspace, cancelled=ctx.cancelled, timeout=timeout, on_line=on_line,
            env={"WIR_BROWSER_DIR": str(install.install_dir)},
        )
    except proc.Cancelled as exc:
        raise RunCancelled() from exc
    if timed_out:
        return False, f"It did not finish within {int(timeout // 60)} minutes."
    if code != 0 or problem:
        return False, problem[-1] if problem else (lines[-1] if lines else f"exit code {code}")
    return True, ""


def _npm(ctx: CheckContext, install: BrowserInstall, args: list[str], step: str, timeout: float) -> tuple[int, list[str], bool]:
    assert ctx.workspace is not None
    npm = install.npm_exe()
    if not npm:
        return -1, ["npm is not on this PC. It comes with Node.js: install the LTS from nodejs.org."], False
    seen = {"n": 0}

    def line(text: str) -> None:
        seen["n"] += 1
        if seen["n"] % 15 == 0:
            _step(ctx, step, "running", text[:120])

    try:
        return proc.run_streaming([npm, *args], ctx.workspace, cancelled=ctx.cancelled, timeout=timeout, on_line=line,
                                  env={"NEXT_TELEMETRY_DISABLED": "1"})
    except proc.Cancelled as exc:
        raise RunCancelled() from exc


def _install() -> BrowserInstall | None:
    return install_source() if install_source is not None else None


# -- capture -------------------------------------------------------------------------------------------------------


def _clone_capture(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    install = _install()
    if install is None or ctx.workspace is None:
        return _fail("The cloner is not available in this run.")
    url = str(checks.param(check, "url"))
    host = str(checks.param(check, "host") or host_of(url))
    _step(ctx, "tools", "running")
    node = install.node()
    if not node.get("ok"):
        _step(ctx, "tools", "failed", node.get("detail") or "")
        return _fail(node.get("detail") or "Node.js is needed.")
    if install.installed_version() is None:
        _step(ctx, "tools", "failed", "browser engine")
        return _fail("The browser engine is not installed yet: press Prepare on the Clone a site page.")
    browser = find_browser()
    if browser is None:
        _step(ctx, "tools", "failed", "Chrome or Edge")
        return _fail("Neither Chrome nor Edge was found on this PC: install one of them.")
    _step(ctx, "tools", "done", f"Node {node.get('version')}, {browser['name']}")

    folder = capture_dir(ctx.workspace, host)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "config.json"
    config.write_text(json.dumps({"url": url, "out": str(folder), "executable": browser["executable"], "viewports": VIEWPORTS,
                                  "locale": str(checks.param(check, "locale") or "") or None}), encoding="utf-8")
    _step(ctx, "capture", "running", url)
    ok, why = _run_script(ctx, install, CAPTURE_SCRIPT, config, "capture", timeout=600.0)
    if not ok:
        _step(ctx, "capture", "failed", why)
        return _fail(why, "CAPTURE_FAILED")
    try:
        result = json.loads((folder / "capture.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _step(ctx, "capture", "failed", str(exc))
        return _fail(f"The capture left no result: {exc}", "CAPTURE_FAILED")
    stats = result.get("stats") or {}
    sections = [{k: s.get(k) for k in ("id", "tag", "className", "heading", "text", "y", "h")} for s in result.get("sections") or []]
    summary = (f"{stats.get('sections', 0)} sections, {stats.get('nodes', 0)} elements, {stats.get('assets', 0)} files "
               f"({round(float(stats.get('assetBytes') or 0) / 1_000_000, 1)} MB), in {stats.get('seconds', 0)} s.")
    _step(ctx, "capture", "done", summary)
    return {"passed": True, "verdict": "CAPTURED", "summary": summary, "stats": stats, "sections": sections, "host": host,
            "overlays": [f"{o.get('el')} ({o.get('kind')})" for o in (result.get("overlays") or []) if o.get("viewport") == "desktop"],
            "warnings": (result.get("warnings") or [])[:6],
            "viewports": {k: {"width": v.get("width"), "pageHeight": v.get("pageHeight"), "screenshot": v.get("screenshot")}
                          for k, v in (result.get("viewports") or {}).items()}}


# -- project -------------------------------------------------------------------------------------------------------

NAMING_SYSTEM = (
    "You name React components. Answer with one JSON object and nothing else: each section number is a key, each value a PascalCase "
    "English name of two to four words that says what the section is, for example Header, HeroTravelJacket, ProductCarousel, "
    "StoreLocator, NewsletterSignup, Footer. Every section gets a different name."
)


def _naming_messages(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = "\n".join(
        f'{s.get("id")}. <{s.get("tag")}> class="{s.get("className") or ""}" heading="{str(s.get("heading") or "")[:60]}" '
        f'text="{str(s.get("text") or "")[:90]}"'
        for s in sections
    )
    return [{"role": "system", "content": NAMING_SYSTEM}, {"role": "user", "content": f"The sections of the page, top to bottom:\n{rows}"}]


def ask_model(ctx: CheckContext, messages: list[dict[str, Any]], *, tokens: int = 3500, context: int = 12288) -> str:
    """One call to the workflow's model through the gateway; the text of its answer.

    A model that thinks first can use all its tokens thinking and leave no answer (seen with Gemma 4 and Qwen3): one more try gets twice
    the room. An answer that is still empty is "" and the caller decides what that means.
    """
    if ctx.complete is None:
        raise RuntimeError("no model in this run")
    for attempt in range(2):
        response = ctx.complete("main", messages, need_context=context, max_tokens=tokens * (attempt + 1), temperature=0.2)
        text = str(((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        if text.strip():
            return text
    return ""


def _model_names(ctx: CheckContext, sections: list[dict[str, Any]]) -> dict[int, str]:
    """The model's names for the components, validated; {} when it cannot be asked or answers badly (the page does not depend on it)."""
    if ctx.complete is None or "main" not in ctx.definition.models or not sections:
        return {}
    _step(ctx, "names", "running", ctx.definition.models["main"].model_key)
    try:
        text = ask_model(ctx, _naming_messages(sections), tokens=2000, context=6144)
    except RunCancelled:
        raise
    except Exception as exc:
        log.warning("the model could not name the components: %s", exc)
        _step(ctx, "names", "failed", str(exc)[:160])
        return {}
    names = replica.name_components(text, [{"n": s.get("id"), **s} for s in sections])
    detail = f"{len(names)} of {len(sections)} named by the model"
    if not names:
        # Said, so that a model that answers in a way this cannot read is seen and not just a name that is missing.
        log.info("the model named no component; its answer began: %r", text[:200])
        detail += "; " + (f"its answer began: {text.strip()[:80]!r}" if text.strip() else "it gave no answer")
    _step(ctx, "names", "done", detail)
    return names


def _clone_project(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    install = _install()
    if install is None or ctx.workspace is None:
        return _fail("The cloner is not available in this run.")
    url = str(checks.param(check, "url"))
    host = str(checks.param(check, "host") or host_of(url))
    capture = ctx.outputs.get("capture") or {}
    folder = capture_dir(ctx.workspace, host)
    sections = capture.get("sections") or []
    names = _model_names(ctx, sections)
    _step(ctx, "project", "running", "writing the files")
    try:
        written = replica.write_project(folder, ctx.workspace, slug=host_slug(url), names=names)
    except replica.ReplicaError as exc:
        _step(ctx, "project", "failed", str(exc))
        return _fail(str(exc), "PROJECT_FAILED")
    _step(ctx, "project", "done", f"{len(written['components'])} components, {written['assets']} files in public/clone")
    if ctx.created is not None:
        ctx.created.update((ctx.workspace / name).resolve() for name in written["files"])

    if not (ctx.workspace / "node_modules").is_dir():
        _step(ctx, "install", "running", "npm install")
        code, lines, timed_out = _npm(ctx, install, ["install", "--no-fund", "--no-audit", "--loglevel=error"], "install", 1500.0)
        if code != 0 or timed_out:
            _step(ctx, "install", "failed", "did not finish" if timed_out else f"exit code {code}")
            return _fail("npm install failed:\n" + "\n".join(lines[-12:]), "INSTALL_FAILED")
        _step(ctx, "install", "done")
    _step(ctx, "build", "running", "npm run build")
    code, lines, timed_out = _npm(ctx, install, ["run", "build"], "build", 900.0)
    if code != 0 or timed_out:
        _step(ctx, "build", "failed", "did not finish" if timed_out else f"exit code {code}")
        return _fail("The project did not build:\n" + "\n".join(lines[-20:]), "BUILD_FAILED", components=written["components"])
    _step(ctx, "build", "done")
    return {"passed": True, "verdict": "BUILT", "components": written["components"], "assets": written["assets"],
            "named_by_model": len(names), "summary": f"{len(written['components'])} components written, {written['assets']} files, built."}


# -- measure -------------------------------------------------------------------------------------------------------


def _measure_config(workspace: Path, host: str, address: str, executable: str, names: dict[int, str]) -> dict[str, Any]:
    folder = capture_dir(workspace, host)
    capture = json.loads((folder / "capture.json").read_text(encoding="utf-8"))
    bundle = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
    viewports = capture.get("viewports") or {}
    return {
        "url": address.rstrip("/") + "/", "out": str(report_dir(workspace, host)), "executable": executable,
        "viewports": [{"name": k, "width": v["width"], "height": v["height"]} for k, v in viewports.items()],
        "reference": {k: str(folder / v["screenshot"]) for k, v in viewports.items()},
        "boxes": {k: v.get("boxes") or {} for k, v in viewports.items()},
        "pageHeights": {k: v.get("pageHeight") for k, v in viewports.items()},
        "sections": [{"id": s["id"], "name": names.get(int(s["id"]), str(s.get("heading") or s.get("tag")))} for s in capture.get("sections") or []],
        "referenceText": bundle.get("text") or "", "textViewport": "desktop",
    }


def _weak(report: dict[str, Any], boxes: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The sections under ``WEAK``, weakest first, with where each is in the original (``y`` and ``h``, for a model that looks at a part)."""
    out = []
    for name, view in (report.get("viewports") or {}).items():
        for section in view.get("sections") or []:
            if section.get("visual", 1) < WEAK:
                box = ((boxes or {}).get(name) or {}).get(str(section["id"])) or {}
                out.append({"viewport": name, "id": section["id"], "name": section.get("name"), "visual": section["visual"], "layout": section.get("layout"),
                            "y": box.get("y"), "h": box.get("h")})
    return sorted(out, key=lambda w: w["visual"])


def _percent(value: Any) -> str:
    return f"{float(value) * 100:.1f}%" if isinstance(value, (int, float)) else "unknown"


def _clone_measure(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    install = _install()
    if install is None or ctx.workspace is None:
        return _fail("The cloner is not available in this run.")
    url = str(checks.param(check, "url"))
    host = str(checks.param(check, "host") or host_of(url))
    npm = install.npm_exe()
    browser = find_browser()
    if not npm or browser is None:
        return _fail("npm and Chrome or Edge are needed to measure the clone.")
    previous = ctx.outputs.get(check.id) or {}
    rounds = int(previous.get("round") or 0) + 1
    components = (ctx.outputs.get("project") or {}).get("components") or []
    names = {int(c["n"]): str(c["name"]) for c in components}
    _step(ctx, "measure", "running", "serving the clone")
    server = DevServer(ctx.workspace, npm)
    config: dict[str, Any] = {}
    try:
        address = server.start(cancelled=ctx.cancelled, production=True, build=False)
        folder = report_dir(ctx.workspace, host)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "config.json"
        config = _measure_config(ctx.workspace, host, address, browser["executable"], names)
        path.write_text(json.dumps(config), encoding="utf-8")
        ok, why = _run_script(ctx, install, MEASURE_SCRIPT, path, "measure", timeout=600.0)
    except DevServerError as exc:
        _step(ctx, "measure", "failed", str(exc)[:200])
        return _fail(f"The clone could not be served: {exc}", "SERVE_FAILED")
    except (OSError, ValueError, KeyError) as exc:
        _step(ctx, "measure", "failed", str(exc)[:200])
        return _fail(f"The measure could not be prepared: {exc}", "MEASURE_FAILED")
    finally:
        server.stop()
    if not ok:
        _step(ctx, "measure", "failed", why)
        return _fail(why, "MEASURE_FAILED")
    try:
        report = json.loads((report_dir(ctx.workspace, host) / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _fail(f"The measure left no report: {exc}", "MEASURE_FAILED")
    accuracy = report.get("accuracy")
    weak = _weak(report, config.get("boxes"))
    # Where the full-size pictures are, for a model that looks at the part of the page a weak section is in.
    pictures = {name: {"original": f"docs/research/{host}/capture/{Path(path).name}", "clone": f"docs/research/{host}/report/clone-{name}.png"}
                for name, path in (config.get("reference") or {}).items()}
    views = {
        name: {k: v.get(k) for k in ("width", "visual", "pixels", "ssim", "layout", "text", "referenceHeight", "cloneHeight", "screenshot", "diff",
                                     "original", "preview")}
        | {"errors": (v.get("errors") or [])[:5], "failed": (v.get("failed") or [])[:5]}
        for name, v in (report.get("viewports") or {}).items()
    }
    detail = ", ".join(f"{name} {_percent(v.get('visual'))}" for name, v in views.items())
    passed = isinstance(accuracy, (int, float)) and accuracy >= TARGET
    more = bool(weak) and not (isinstance(accuracy, (int, float)) and accuracy >= POLISH_BELOW)
    summary = f"Accuracy {_percent(accuracy)} ({detail}); the owner's target is {_percent(TARGET)}."
    if weak:
        summary += " Weakest: " + "; ".join(f"{w['name']} on {w['viewport']} {_percent(w['visual'])}" for w in weak[:4]) + "."
    _step(ctx, "measure", "done" if passed else "failed", f"{_percent(accuracy)}")
    return {"passed": passed, "verdict": "MATCHES" if passed and not weak else "WEAK_SECTIONS" if passed else "BELOW_TARGET", "accuracy": accuracy,
            "viewports": views, "weak": weak[:8], "pictures": pictures, "round": rounds, "should_continue": more, "target": TARGET,
            "formula": report.get("formula"), "summary": summary, "sections": {name: [{k: s.get(k) for k in ("id", "name", "visual", "layout")} for s in v.get("sections") or []]
                                             for name, v in (report.get("viewports") or {}).items()}}


# -- the workflow --------------------------------------------------------------------------------------------------

POLISHER_PROMPT = (
    "The clone of a website is a Next.js project made from a copy of the original page. A measure compared it with the original and "
    "found sections that differ; its result lists them (viewport, id, name, score, and y and h: where the section is in the original) "
    "and `pictures`, the full-size screenshots of the original and of the clone at each width. You fix the sections with style rules "
    "only, written to public/clone/fixes.css (the page loads it after the original's styles), and a fix counts only if the next "
    "measure is better.\n"
    "1. Take the weakest section. With `look`, look at the original (path = pictures.<viewport>.original, from_y = y, height = h) and "
    "then at the clone (path = pictures.<viewport>.clone, the same from_y and height), and say in one line what differs.\n"
    "2. Write or extend public/clone/fixes.css with rules that fix it. Select the section by [data-clone-section=\"N\"]; read the "
    "section's component in src/components/sites/ only if you need a class name.\n"
    "3. Do the same for the next section, at most three in all.\n"
    "Change nothing else. Do not rewrite components. Finish with one line per fix."
)


def definition(*, url: str, model_key: str, sees: bool, vision_key: str = "", locale: str = "") -> WorkflowDef:
    host = host_of(url)
    models = {"main": ModelSlot(label="Namer and polisher", kind="vision" if sees else "", model_key=model_key, auto_tune=True)}
    if vision_key and not sees:
        models["vision"] = ModelSlot(label="Vision", kind="vision", model_key=vision_key, auto_tune=True)
    params = {"url": url, "host": host, "locale": locale}
    polisher = AgentDef(
        id="polisher", role="Polisher", model="main", goal="Fix, with style rules, the sections of the clone that differ from the original.",
        system_prompt=POLISHER_PROMPT, tools=["read_file", "list_dir", "write_file", "edit_file", *(["look"] if sees or vision_key else [])],
        output_schema=schemas.TEXT, max_tool_rounds=14, revision_tool_rounds=14, temperature=0.3, reads=["measure"],
    )
    edges = [
        EdgeDef(source=START, target="capture"),
        EdgeDef(source="capture", target="project", when="passed == true"), EdgeDef(source="capture", target=END),
        EdgeDef(source="project", target="measure", when="passed == true"), EdgeDef(source="project", target=END),
        EdgeDef(source="measure", target="polisher", when="should_continue == true", label="fix", max_loops=POLISH_ROUNDS, on_exhausted=END),
        EdgeDef(source="measure", target=END),
        EdgeDef(source="polisher", target="measure"),
    ]
    return WorkflowDef(
        workflow=WorkflowMeta(id=WORKFLOW, name="Clone a site: exact copy", category="clone", max_steps=30,
                              description="Copies the page as a browser rendered it into a Next.js project and measures it against the original."),
        models=models, agents=[polisher],
        checks=[CheckDef(id="capture", kind="clone_capture", label="Capture", params=params),
                CheckDef(id="project", kind="clone_project", label="Project", params=params),
                CheckDef(id="measure", kind="clone_measure", label="Measure", params=params)],
        edges=edges,
        layout={"capture": {"x": 0.0, "y": 0.0}, "project": {"x": 220.0, "y": 0.0}, "measure": {"x": 440.0, "y": 0.0}, "polisher": {"x": 440.0, "y": 120.0}},
    )


def register() -> None:
    checks.CHECKS["clone_capture"] = CheckSpec(
        kind="clone_capture", label="Capture",
        description="Opens the page in Chrome, lets it settle, removes banners and pop-ups, takes the reference screenshots and freezes the rendered "
                    "page with its styles, pictures and fonts.",
        run=_clone_capture, defaults={"url": "", "host": "", "locale": ""}, listed=False,
    )
    checks.CHECKS["clone_project"] = CheckSpec(
        kind="clone_project", label="Project",
        description="Turns the capture into a Next.js project, one component for each section (a model may name them), installs and builds it.",
        run=_clone_project, defaults={"url": "", "host": "", "locale": ""}, listed=False,
    )
    checks.CHECKS["clone_measure"] = CheckSpec(
        kind="clone_measure", label="Measure",
        description="Serves the built clone and compares its screenshots with the original's, over the page and over each section.",
        run=_clone_measure, defaults={"url": "", "host": "", "locale": ""}, listed=False,
    )


def has_fixes(workspace: Path) -> bool:
    with contextlib.suppress(OSError):
        return (workspace / "public" / "clone" / "fixes.css").is_file()
    return False


def tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


register()
