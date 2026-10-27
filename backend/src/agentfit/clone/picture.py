"""Building a site from a picture, with the models of this PC: a frame of code around small model calls that code checks.

    prepare -> analyze -> compose -> measure -> (refine -> measure)* -> motion -> reviewer

A frontier model can be given a whole brief (a hero that looks like this picture, with parallax, hover and scroll) and do it in one long
conversation. A model of 27B on a card of 16 GB has about 27K tokens of room, so the brief is cut into the phases it describes and
each phase is made small enough for it, with the numbers kept by code:

* **prepare**: the project (Next.js and GSAP), the skills copied into it, the picture measured by code (its colours, its bands).
* **analyze**: the model looks at the picture and writes its composition as data (regions with a place, a size, a depth and the text
  in them). What it writes is clamped and checked; when it is unusable the bands of the picture stand in.
* **compose**: the model writes one component for each region, looking at that region's crop. The place of each region comes from the
  data, not from the model, so a model that is wrong about a size cannot move the layout. A photograph is the crop itself.
* **measure**: the page is built, shown at the picture's size and compared with the picture, region by region.
* **refine**: the weakest regions are written again from the crop, the clone's own crop and the score. The new code stays only if the
  next measure is better; if not, the old code comes back.
* **motion**: GSAP, read from its own skills, adds the intro, the mouse and the hover. It stays only if the page, at rest, still matches
  the picture: the fidelity of the last frame is the rule of the brief.
* **reviewer**: an agent that writes the report for the person from the numbers.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import struct
from pathlib import Path
from typing import Any

from agentfit.clone import pipeline, replica
from agentfit.clone.pipeline import _fail, _step
from agentfit.clone.skills import SkillStore, prompt_catalog
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

log = get_logger("clone.picture")

HERE = Path(__file__).resolve().parent
PICTURE_SCRIPT = HERE / "picture.mjs"
WORKFLOW = "site-from-picture"

# The same bar as a clone: below it the run is blocked instead of completed.
TARGET = pipeline.TARGET
# A rewritten region that scores this much less than before is put back as it was.
REGRESSION = 0.005
# A region that scores below this is written again; at most this many in a round, at most this many rounds.
REFINE_BELOW = 0.85
REFINE_PER_ROUND = 3
REFINE_ROUNDS = 2
MAX_REGIONS = 10
# The motion may cost the page at rest at most this much of its score.
MOTION_TOLERANCE = 0.03

# Set by the app: the skills on this PC.
skill_store: SkillStore | None = None

RESEARCH = "docs/research/picture"
REFERENCE_DIR = "docs/reference"
KINDS = ("photo", "text", "ui", "shape")


def research(workspace: Path) -> Path:
    return workspace / RESEARCH


def region_file(workspace: Path, number: int) -> Path:
    return workspace / "src" / "components" / "regions" / f"Region{number}.tsx"


# -- the picture -----------------------------------------------------------------------------------------------------


def image_type(path: Path) -> str | None:
    """png, jpeg, webp or gif by the first bytes of the file, never by its name; None when it is not a picture."""
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"GIF8",):
        return "gif"
    return None


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        head = path.read_bytes()[:24]
        return struct.unpack(">II", head[16:24]) if head.startswith(b"\x89PNG") else None
    except (OSError, struct.error):
        return None


def data_url(path: Path) -> str:
    kind = "jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "png"
    return f"data:image/{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def image_message(text: str, *pictures: Path, system: str = "") -> list[dict[str, Any]]:
    """A chat message with pictures in it, in the OpenAI form LM Studio takes for a model that sees."""
    content: list[dict[str, Any]] = [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in pictures]
    content.append({"type": "text", "text": text})
    return ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]


def answer_of(response: dict[str, Any]) -> str:
    return str(((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def ask(ctx: CheckContext, messages: list[dict[str, Any]], *, tokens: int = 3500, context: int = 12288) -> str:
    """One call to the workflow's model: the same as the exact copy's (``pipeline.ask_model``)."""
    return pipeline.ask_model(ctx, messages, tokens=tokens, context=context)


# -- the composition -------------------------------------------------------------------------------------------------


def clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def clean_regions(raw: Any) -> list[dict[str, Any]]:
    """The regions a model wrote, made usable: numbers clamped, a kind that exists, nothing smaller than 4% of the picture."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        w, h = clamp(item.get("w"), 0, 100, 0), clamp(item.get("h"), 0, 100, 0)
        x, y = clamp(item.get("x"), 0, 100, 0), clamp(item.get("y"), 0, 100, 0)
        if w < 4 or h < 4:
            continue
        w, h = min(w, 100 - x), min(h, 100 - y)
        if w < 4 or h < 4 or any(abs(r["x"] - x) < 1 and abs(r["y"] - y) < 1 and abs(r["w"] - w) < 1 and abs(r["h"] - h) < 1 for r in out):
            continue
        kind = str(item.get("kind") or "ui").lower()
        out.append({"id": len(out) + 1, "kind": kind if kind in KINDS else "ui", "x": round(x, 2), "y": round(y, 2), "w": round(w, 2), "h": round(h, 2),
                    "z": int(clamp(item.get("z"), 0, 99, len(out) + 1)), "rotate": round(clamp(item.get("rotate"), -45, 45, 0), 1),
                    "text": str(item.get("text") or "")[:400], "note": str(item.get("note") or "")[:80]})
        if len(out) == MAX_REGIONS:
            break
    return out


def parse_composition(answer: str) -> tuple[str, list[dict[str, Any]]]:
    """The background colour and the regions of a model's answer (a JSON object, with anything around it ignored)."""
    match = re.search(r"\{.*\}", answer, flags=re.DOTALL)
    if not match:
        return "", []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return "", []
    if not isinstance(data, dict):
        return "", []
    background = str(data.get("background") or "")
    return (background if re.fullmatch(r"#[0-9a-fA-F]{6}", background) else ""), clean_regions(data.get("regions"))


def regions_from_bands(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """The regions when no model's answer is usable: the horizontal bands where the picture changes, as full-width rows."""
    height = float(analysis.get("height") or 1)
    bands = sorted(analysis.get("bands") or [], key=lambda b: -b.get("h", 0))[:MAX_REGIONS]
    out: list[dict[str, Any]] = []
    for band in sorted(bands, key=lambda b: b.get("y", 0)):
        if band.get("h", 0) < 8:
            continue
        number = len(out) + 1
        out.append({"id": number, "kind": "ui", "x": 0.0, "y": round(100 * band["y"] / height, 2), "w": 100.0, "h": round(100 * band["h"] / height, 2), "z": number,
                    "rotate": 0.0, "text": "", "note": "a band of the picture"})
    return out


def box_px(region: dict[str, Any], width: int, height: int) -> dict[str, int]:
    return {"x": round(region["x"] * width / 100), "y": round(region["y"] * height / 100), "w": round(region["w"] * width / 100), "h": round(region["h"] * height / 100)}


ANALYSIS_SYSTEM = "You look at a picture of a web page or a design and write its composition as JSON, and nothing else."


def analysis_prompt(width: int, height: int, palette: list[dict[str, Any]]) -> str:
    colours = ", ".join(p["hex"] for p in palette[:6])
    return (
        f"The picture is {width}x{height} px; its main colours are {colours}. Write its composition as one JSON object: "
        '{"background": "#rrggbb", "regions": [{"kind": "photo|text|ui|shape", "x": 0, "y": 0, "w": 100, "h": 20, "z": 1, "rotate": 0, '
        '"text": "the text you can read in it", "note": "what it is, in six words"}]}. x, y, w and h are percent of the picture, so the '
        f"whole picture is x 0, y 0, w 100, h 100. Give at most {MAX_REGIONS} regions: the largest and most important parts, from the back to the "
        "front (z is the depth, 1 the farthest). A region is a photograph, a block of text, an interface part (a header, a menu, a button, a "
        "card) or a plain shape. Regions may overlap. Copy the text exactly, in the language it is written in."
    )


# -- the component of a region -----------------------------------------------------------------------------------------

COMPOSE_SYSTEM = (
    "You write one React component for a Next.js page, from a picture of one part of a design. Answer with exactly two fenced code blocks and "
    "nothing else: first ```tsx, then ```css.\n"
    "- The tsx block is `export default function Region{id}() {{ return (...); }}`. Its root is `<div className=\"r{id}\">`. No imports, no "
    "hooks, no external addresses; only HTML, CSS classes and inline SVG.\n"
    "- The component fills its box: the root is width 100% and height 100%, and everything inside is placed with flexbox, grid or absolute "
    "positions in percent, so it keeps its proportions at any size.\n"
    "- Every class starts with r{id} (r{id}-title, r{id}-card...), so it cannot clash with another region. The css block holds their rules.\n"
    "- Reproduce the visible text exactly, in its language, with the font weight, size relative to the box, colour and alignment you see. Use "
    "the colours of the palette.\n"
    "- A photograph inside the region is an <img> of {crop} with object-fit: cover."
)


def compose_prompt(region: dict[str, Any], width: int, height: int, palette: list[dict[str, Any]], brief: str) -> str:
    box = box_px(region, width, height)
    parts = [
        f"Region {region['id']} ({region['kind']}): {box['w']}x{box['h']} px at ({box['x']}, {box['y']}) in a {width}x{height} picture.",
        f"Palette: {', '.join(p['hex'] for p in palette[:6])}.",
    ]
    if region.get("note"):
        parts.append(f"It is: {region['note']}.")
    if region.get("text"):
        parts.append(f"Its text: {region['text']}")
    if brief.strip():
        parts.append(f"The site's brief: {brief.strip()[:500]}")
    parts.append("The picture is that region. Write its component.")
    return "\n".join(parts)


def clean_css(css: str) -> str:
    """A model's style rules made safe for the page: no import, no address, and the braces must balance (else nothing)."""
    css = re.sub(r"@import[^;]*;", "", css)
    css = re.sub(r"url\(\s*['\"]?(?:https?:)?//[^)]*\)", "none", css)
    return css.strip() if css.count("{") == css.count("}") else ""


def extract_blocks(answer: str) -> tuple[str, str]:
    """The tsx and the css of an answer made of fenced blocks (a model may name them tsx, jsx or typescript, or leave out the name)."""
    blocks = re.findall(r"```([A-Za-z]*)\s*\n(.*?)```", answer, flags=re.DOTALL)
    css = next((body for lang, body in blocks if lang.lower() == "css"), "")
    code = next((body for lang, body in blocks if lang.lower() in ("tsx", "jsx", "typescript", "ts", "js", "javascript", "")), "")
    if not code:
        match = re.search(r"export default function.*", answer, flags=re.DOTALL)
        code = match.group(0) if match else ""
    return code.strip(), clean_css(css)


FORBIDDEN = re.compile(r"dangerouslySetInnerHTML|<script|\beval\(|\bfetch\(|\bdocument\.|\bwindow\.|\bimport\s*\(|\brequire\(")


def valid_component(code: str, number: int) -> tuple[str | None, str]:
    """The component made safe to write, or (None, why not): no imports (they are dropped), nothing that runs or loads, one default function."""
    code = re.sub(r"^\s*import\s[^\n]*\n", "", code, flags=re.MULTILINE).strip()
    if not code or "export default function" not in code:
        return None, "the answer has no `export default function`"
    if FORBIDDEN.search(code):
        return None, "the component uses something a component of this page must not (scripts, fetch, window, document, import())"
    code = re.sub(r"export default function\s+\w+", f"export default function Region{number}", code, count=1)
    if "<" not in code or "return" not in code:
        return None, "the component renders nothing"
    return code, ""


def photo_component(number: int) -> str:
    return (f"export default function Region{number}() {{\n  return <img className=\"r{number}\" src=\"/crops/region-{number}.png\" alt=\"\" "
            "style={{ width: \"100%\", height: \"100%\", objectFit: \"cover\", display: \"block\" }} />;\n}\n")


def fallback_component(number: int, colour: str) -> str:
    return (f"export default function Region{number}() {{\n  return <div className=\"r{number}\" style={{{{ width: \"100%\", height: \"100%\", background: \"{colour}\" }}}} />;\n}}\n")


def write_component(workspace: Path, number: int, code: str) -> None:
    target = region_file(workspace, number)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(replica.NO_CHECK + code.strip() + "\n", encoding="utf-8")


def read_css(workspace: Path) -> dict[int, str]:
    """The rules of each region, kept in one file as ``/* region N */`` blocks."""
    try:
        text = (research(workspace) / "regions.css.json").read_text(encoding="utf-8")
        return {int(k): str(v) for k, v in json.loads(text).items()}
    except (OSError, ValueError):
        return {}


def write_css(workspace: Path, rules: dict[int, str]) -> None:
    folder = research(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "regions.css.json").write_text(json.dumps({str(k): v for k, v in sorted(rules.items())}), encoding="utf-8")


def stage_css(width: int, height: int, background: str, rules: dict[int, str]) -> str:
    body = "\n".join(f"/* region {n} */\n{css}" for n, css in sorted(rules.items()))
    return (f".stage {{ position: relative; width: 100%; aspect-ratio: {width} / {height}; overflow: hidden; background: {background}; }}\n"
            ".slot { position: absolute; overflow: hidden; }\n.slot > * { width: 100%; height: 100%; }\n" + body + "\n")


def write_page(workspace: Path, composition: dict[str, Any], motion: bool = False) -> None:
    regions = composition["regions"]
    width, height = int(composition["width"]), int(composition["height"])
    imports = "".join(f'import Region{r["id"]} from "@/components/regions/Region{r["id"]}";\n' for r in regions)
    slots = []
    for r in sorted(regions, key=lambda r: r["z"]):
        style = (f'left: "{r["x"]}%", top: "{r["y"]}%", width: "{r["w"]}%", height: "{r["h"]}%", zIndex: {r["z"]}'
                 + (f', transform: "rotate({r["rotate"]}deg)"' if r.get("rotate") else ""))
        slots.append(f'      <div className="slot" data-region="{r["id"]}" data-kind="{r["kind"]}" data-clone-section="{r["id"]}" style={{{{ {style} }}}}>\n'
                     f'        <Region{r["id"]} />\n      </div>')
    motion_import = 'import Motion from "@/components/Motion";\n' if motion else ""
    motion_tag = "      <Motion />\n" if motion else ""
    page = (f'{replica.NO_CHECK}import "./regions.css";\n{motion_import}{imports}\nexport default function Page() {{\n  return (\n'
            f'    <main className="stage">\n{motion_tag}' + "\n".join(slots) + "\n    </main>\n  );\n}\n")
    (workspace / "src" / "app").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "app" / "page.tsx").write_text(page, encoding="utf-8")
    (workspace / "src" / "app" / "regions.css").write_text(stage_css(width, height, composition.get("background") or "#ffffff", read_css(workspace)), encoding="utf-8")


# -- the project -----------------------------------------------------------------------------------------------------


def project_package(slug: str) -> str:
    data = json.loads(replica._package(slug))
    data["dependencies"]["gsap"] = "3.13.0"
    return json.dumps(data, indent=2) + "\n"


GLOBALS = "html, body { margin: 0; padding: 0; background: #fff; }\n*, *::before, *::after { box-sizing: border-box; }\nbody { font-family: system-ui, -apple-system, \"Segoe UI\", sans-serif; }\n"
LAYOUT = (replica.NO_CHECK + 'import type { ReactNode } from "react";\nimport "./globals.css";\n\nexport const metadata = { title: "{title}" };\n\n'
          "export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {\n"
          "  return (\n    <html lang=\"{lang}\">\n      <body>{children}</body>\n    </html>\n  );\n}\n")


def write_skeleton(workspace: Path, title: str, language: str) -> list[str]:
    files = {
        "package.json": project_package("picture-site"), "tsconfig.json": replica.TSCONFIG, "next.config.mjs": replica.NEXT_CONFIG, ".gitignore": replica.GITIGNORE,
        "src/app/globals.css": GLOBALS, "src/app/layout.tsx": LAYOUT.replace("{title}", title.replace('"', "'")[:60] or "Site").replace("{lang}", language or "en"),
        "src/app/page.tsx": replica.NO_CHECK + "export default function Page() {\n  return <main />;\n}\n",
    }
    for relative, text in files.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    return list(files)


def _script(ctx: CheckContext, config: dict[str, Any], name: str) -> tuple[bool, str]:
    """Run ``picture.mjs`` with a config; (True, "") when it finished well."""
    install = pipeline._install()
    browser = pipeline.find_browser()
    if install is None or ctx.workspace is None or browser is None:
        return False, "The browser engine, or Chrome or Edge, is not available."
    folder = research(ctx.workspace)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.config.json"
    path.write_text(json.dumps({**config, "executable": browser["executable"]}), encoding="utf-8")
    return pipeline._run_script(ctx, install, PICTURE_SCRIPT, path, "picture", timeout=300.0)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _prepare(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    install = pipeline._install()
    if install is None or ctx.workspace is None:
        return _fail("The builder is not available in this run.")
    workspace = ctx.workspace
    relative = str(checks.param(check, "reference") or "")
    reference = workspace / relative
    kind = image_type(reference) if relative else None
    if kind is None:
        return _fail("The reference is not a picture (a PNG, JPEG, WebP or GIF file).", "NO_PICTURE")
    _step(ctx, "tools", "running")
    node = install.node()
    if not node.get("ok") or install.installed_version() is None or pipeline.find_browser() is None:
        why = node.get("detail") or "The browser engine is not installed yet: press Prepare on the Clone a site page."
        _step(ctx, "tools", "failed", why)
        return _fail(why)
    _step(ctx, "tools", "done", f"Node {node.get('version')}")

    _step(ctx, "reference", "running", relative)
    ok, why = _script(ctx, {"cmd": "analyze", "image": str(reference), "out": str(research(workspace))}, "analyze")
    analysis = read_json(research(workspace) / "analysis.json")
    if not ok or not isinstance(analysis, dict):
        _step(ctx, "reference", "failed", why)
        return _fail(f"The picture could not be read: {why}", "NO_PICTURE")
    _step(ctx, "reference", "done", f"{analysis['width']}x{analysis['height']}, {len(analysis['palette'])} colours, {len(analysis['bands'])} bands")

    title = str(checks.param(check, "title") or "")
    write_skeleton(workspace, title, str(checks.param(check, "language") or "en"))
    skills: list[str] = []
    store = skill_store
    if store is not None:
        wanted = checks.param(check, "skills")
        try:
            if not store.catalog():
                _step(ctx, "skills", "running", "fetching the skill packs")
                store.install(emit=lambda line: _step(ctx, "skills", "running", line))
            skills = [s.name for s in store.copy_into(workspace, list(wanted) if wanted else None)]
            _step(ctx, "skills", "done", f"{len(skills)} skills in .skills/")
        except (OSError, ValueError, RuntimeError) as exc:
            _step(ctx, "skills", "failed", str(exc)[:160])
    if not (workspace / "node_modules").is_dir():
        _step(ctx, "install", "running", "npm install")
        code, lines, timed_out = pipeline._npm(ctx, install, ["install", "--no-fund", "--no-audit", "--loglevel=error"], "install", 1500.0)
        if code != 0 or timed_out:
            _step(ctx, "install", "failed", "did not finish" if timed_out else f"exit code {code}")
            return _fail("npm install failed:\n" + "\n".join(lines[-12:]), "INSTALL_FAILED")
        _step(ctx, "install", "done")
    return {"passed": True, "verdict": "READY", "width": analysis["width"], "height": analysis["height"], "palette": analysis["palette"], "bands": len(analysis["bands"]),
            "skills": skills, "reference": relative, "summary": f"Picture {analysis['width']}x{analysis['height']}, {len(skills)} skills copied, packages installed."}


def _analyze(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    workspace = ctx.workspace
    prepared = ctx.outputs.get("prepare") or {}
    if workspace is None or not prepared.get("passed"):
        return _fail("There is no prepared picture.")
    analysis = read_json(research(workspace) / "analysis.json")
    if not isinstance(analysis, dict) or not analysis.get("width"):
        return _fail("The picture's analysis is missing: run the first step again.")
    width, height = int(analysis["width"]), int(analysis["height"])
    reference = workspace / str(prepared["reference"])
    background, regions, source = "", [], "bands"
    _step(ctx, "analyze", "running", "the model looks at the picture")
    try:
        answer = ask(ctx, image_message(analysis_prompt(width, height, analysis["palette"]), reference, system=ANALYSIS_SYSTEM), tokens=3000, context=14336)
        background, regions = parse_composition(answer)
        source = "model" if len(regions) >= 2 else "bands"
    except RunCancelled:
        raise
    except Exception as exc:
        log.warning("the model could not analyze the picture: %s", exc)
        _step(ctx, "analyze", "failed", str(exc)[:160])
    if source == "bands":
        regions = regions_from_bands(analysis)
    if not regions:
        regions = [{"id": 1, "kind": "ui", "x": 0.0, "y": 0.0, "w": 100.0, "h": 100.0, "z": 1, "rotate": 0.0, "text": "", "note": "the whole picture"}]
    background = background or (analysis["palette"][0]["hex"] if analysis.get("palette") else "#ffffff")
    composition = {"width": width, "height": height, "background": background, "regions": regions, "source": source}
    (research(workspace) / "composition.json").write_text(json.dumps(composition, indent=1), encoding="utf-8")
    pieces = [{"id": r["id"], **box_px(r, width, height)} for r in regions]
    ok, why = _script(ctx, {"cmd": "crops", "image": str(reference), "regions": pieces, "out": str(research(workspace) / "crops"), "previewSide": 768}, "crops")
    if not ok:
        return _fail(f"The regions could not be cut out of the picture: {why}")
    crops = workspace / "public" / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    for r in regions:
        shutil.copy2(research(workspace) / "crops" / f"region-{r['id']}.png", crops / f"region-{r['id']}.png")
    _step(ctx, "analyze", "done", f"{len(regions)} regions ({'by the model' if source == 'model' else 'from the bands of the picture'})")
    return {"passed": True, "verdict": "ANALYZED", "source": source, "regions": regions, "background": background,
            "summary": f"{len(regions)} regions, found {'by the model' if source == 'model' else 'from the bands of the picture'}."}


# -- compose ---------------------------------------------------------------------------------------------------------


def _generate(ctx: CheckContext, region: dict[str, Any], composition: dict[str, Any], palette: list[dict[str, Any]], brief: str, crop: Path,
              hint: str = "") -> tuple[str, str] | None:
    """(tsx, css) for one region from its crop, or None when the model's answer is not usable."""
    system = COMPOSE_SYSTEM.replace("{id}", str(region["id"])).replace("{crop}", f"/crops/region-{region['id']}.png")
    text = compose_prompt(region, composition["width"], composition["height"], palette, brief) + (f"\n{hint}" if hint else "")
    answer = ask(ctx, image_message(text, crop, system=system), tokens=3500, context=12288)
    code, css = extract_blocks(answer)
    code, why = valid_component(code, region["id"])
    if code is None:
        log.info("region %s: %s", region["id"], why)
        return None
    return code, css


def _build(ctx: CheckContext, install: Any) -> tuple[bool, list[str]]:
    code, lines, timed_out = pipeline._npm(ctx, install, ["run", "build"], "build", 900.0)
    return code == 0 and not timed_out, lines


def _failing_regions(lines: list[str]) -> list[int]:
    return sorted({int(m.group(1)) for line in lines for m in re.finditer(r"Region(\d+)\.tsx", line)})


def _compose(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    install = pipeline._install()
    workspace = ctx.workspace
    analyzed = ctx.outputs.get("analyze") or {}
    prepared = ctx.outputs.get("prepare") or {}
    if install is None or workspace is None or not analyzed.get("passed"):
        return _fail("There is no composition to write.")
    composition = read_json(research(workspace) / "composition.json")
    palette = prepared.get("palette") or []
    brief = str(checks.param(check, "brief") or "")
    rules: dict[int, str] = {}
    made: list[dict[str, Any]] = []
    for region in composition["regions"]:
        number = region["id"]
        crop = research(workspace) / "crops" / f"region-{number}.jpg"
        how = "photo"
        if region["kind"] != "photo":
            _step(ctx, "compose", "running", f"region {number} of {len(composition['regions'])}")
            try:
                result = _generate(ctx, region, composition, palette, brief, crop) if ctx.complete is not None else None
            except RunCancelled:
                raise
            except Exception as exc:
                log.warning("region %s: %s", number, exc)
                result = None
            if result is None:
                write_component(workspace, number, photo_component(number))
                how = "crop"  # the model's answer was unusable: the region is the picture's own crop, and the report says so
            else:
                write_component(workspace, number, result[0])
                rules[number] = result[1]
                how = "model"
        else:
            write_component(workspace, number, photo_component(number))
        made.append({"id": number, "kind": region["kind"], "how": how})
    write_css(workspace, rules)
    write_page(workspace, composition)
    # A component that does not compile is asked for again once with the compiler's words, then replaced by the picture's own crop.
    ok, lines = _build(ctx, install)
    for attempt in range(2):
        if ok:
            break
        bad = _failing_regions(lines)
        _step(ctx, "build", "running", f"fixing region(s) {', '.join(map(str, bad)) or 'the style rules'}")
        if not bad:
            if any("regions.css" in line for line in lines) and rules:
                # The compiler does not take the models' style rules: the page is built without them rather than not at all.
                rules.clear()
                write_css(workspace, rules)
                write_page(workspace, composition)
                ok, lines = _build(ctx, install)
            break
        for number in bad:
            region = next((r for r in composition["regions"] if r["id"] == number), None)
            if region is None:
                continue
            fixed = None
            if attempt == 0 and ctx.complete is not None and region["kind"] != "photo":
                hint = "The compiler said:\n" + "\n".join(lines[-12:])[-900:] + "\nWrite the component again so that it compiles."
                try:
                    fixed = _generate(ctx, region, composition, palette, brief, research(workspace) / "crops" / f"region-{number}.jpg", hint)
                except RunCancelled:
                    raise
                except Exception:
                    fixed = None
            if fixed is None:
                write_component(workspace, number, photo_component(number))
                rules.pop(number, None)
                for item in made:
                    if item["id"] == number:
                        item["how"] = "crop"
            else:
                write_component(workspace, number, fixed[0])
                rules[number] = fixed[1]
        write_css(workspace, rules)
        write_page(workspace, composition)
        ok, lines = _build(ctx, install)
    if not ok:
        _step(ctx, "build", "failed", "the page does not build")
        return _fail("The page did not build:\n" + "\n".join(lines[-20:]), "BUILD_FAILED", regions=made)
    area = sum(r["w"] * r["h"] for r in composition["regions"] if r["kind"] == "photo" or next(m for m in made if m["id"] == r["id"])["how"] == "crop")
    _step(ctx, "build", "done")
    return {"passed": True, "verdict": "COMPOSED", "regions": made, "model_regions": sum(1 for m in made if m["how"] == "model"),
            "summary": f"{sum(1 for m in made if m['how'] == 'model')} of {len(made)} regions written by the model; "
                       f"the others are the picture's own crops ({round(min(area / 100, 100))}% of its area at most)."}


# -- measure and refine ----------------------------------------------------------------------------------------------


def _measure_once(ctx: CheckContext, label: str, settle_ms: int = 800) -> dict[str, Any] | None:
    """Serve the built page, compare it with the picture, and return the report (None, with the reason in the step, when it cannot).

    ``settle_ms`` is how long the page is given after it loaded: a page with an intro is measured when the intro is over."""
    install, workspace = pipeline._install(), ctx.workspace
    browser = pipeline.find_browser()
    composition = read_json(research(workspace) / "composition.json") if workspace else None
    prepared = ctx.outputs.get("prepare") or {}
    if install is None or workspace is None or browser is None or not composition:
        return None
    npm = install.npm_exe()
    if not npm:
        return None
    out = research(workspace) / "report"
    out.mkdir(parents=True, exist_ok=True)
    width, height = int(composition["width"]), int(composition["height"])
    server = pipeline.DevServer(workspace, npm)
    try:
        address = server.start(cancelled=ctx.cancelled, production=True, build=False)
        regions = composition["regions"]
        config = {
            "url": address.rstrip("/") + "/", "out": str(out), "executable": browser["executable"],
            "viewports": [{"name": "reference", "width": width, "height": height}], "reference": {"reference": str(workspace / str(prepared["reference"]))},
            "boxes": {"reference": {str(r["id"]): box_px(r, width, height) for r in regions}}, "pageHeights": {"reference": height},
            "sections": [{"id": r["id"], "name": f"{r['kind']}: {r['note'] or r['id']}"[:40]} for r in regions], "referenceText": "", "settleMs": settle_ms,
        }
        path = out / f"config-{label}.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        ok, why = pipeline._run_script(ctx, install, pipeline.MEASURE_SCRIPT, path, "measure", timeout=600.0)
    except pipeline.DevServerError as exc:
        _step(ctx, "measure", "failed", str(exc)[:200])
        return None
    finally:
        server.stop()
    if not ok:
        _step(ctx, "measure", "failed", why)
        return None
    return read_json(out / "report.json")


def _summarize(report: dict[str, Any], composition: dict[str, Any], made: list[dict[str, Any]]) -> dict[str, Any]:
    view = (report.get("viewports") or {}).get("reference") or {}
    sections = {s["id"]: s for s in view.get("sections") or []}
    copied = {m["id"] for m in made if m["how"] in ("photo", "crop")}
    built = [sections[r["id"]]["visual"] for r in composition["regions"] if r["id"] in sections and r["id"] not in copied and r["kind"] != "photo"]
    area = sum(r["w"] * r["h"] for r in composition["regions"] if r["id"] in copied or r["kind"] == "photo") / 100
    weak = sorted(({"id": s["id"], "name": s["name"], "visual": s["visual"]} for s in sections.values() if s["id"] not in copied and s["visual"] < REFINE_BELOW),
                  key=lambda w: w["visual"])
    return {"accuracy": report.get("accuracy"), "built_accuracy": round(sum(built) / len(built), 4) if built else None, "copied_share": round(min(area, 100) / 100, 3),
            "weak": weak, "view": view}


def _measure(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    workspace = ctx.workspace
    if workspace is None:
        return _fail("There is no project to measure.")
    composition = read_json(research(workspace) / "composition.json")
    made = (ctx.outputs.get("compose") or {}).get("regions") or []
    previous = ctx.outputs.get(check.id) or {}
    rounds = int(previous.get("round") or 0) + 1
    _step(ctx, "measure", "running", "serving the page")
    report = _measure_once(ctx, f"round{rounds}")
    if report is None:
        return _fail("The page could not be measured.", "MEASURE_FAILED")
    found = _summarize(report, composition, made)
    undone: list[int] = []
    # A rewrite counts only for the regions it made better: where a region scores less than it did, the code it replaced comes back.
    backup = research(workspace) / "backup"
    changed = [int(n) for n in (ctx.outputs.get("refine") or {}).get("changed") or []]
    if rounds > 1 and changed and (backup / "regions").is_dir():
        before = {int(s["id"]): float(s["visual"]) for s in (previous.get("sections") or {}).get("reference") or []}
        now = {int(s["id"]): float(s["visual"]) for s in found["view"].get("sections") or []}
        undone = [n for n in changed if n in before and n in now and now[n] < before[n] - REGRESSION]
        saved_css = read_json(backup / "regions.css.json")
        if undone and isinstance(saved_css, dict):
            rules = read_css(workspace)
            for number in undone:
                shutil.copy2(backup / "regions" / f"Region{number}.tsx", region_file(workspace, number))
                if str(number) in saved_css:
                    rules[number] = str(saved_css[str(number)])
            write_css(workspace, rules)
            write_page(workspace, composition)
            install = pipeline._install()
            if install is not None and _build(ctx, install)[0]:
                report = _measure_once(ctx, f"round{rounds}-back") or report
                found = _summarize(report, composition, made)
            else:
                undone = []
    reverted = bool(undone)
    accuracy = found["accuracy"]
    passed = isinstance(accuracy, (int, float)) and accuracy >= TARGET
    more = bool(found["weak"]) and rounds <= REFINE_ROUNDS and ctx.complete is not None
    view = found["view"]
    _step(ctx, "measure", "done" if passed else "failed", pipeline._percent(accuracy))
    summary = (f"Accuracy {pipeline._percent(accuracy)} over the whole picture; {pipeline._percent(found['built_accuracy'])} over the regions the model wrote "
               f"({pipeline._percent(found['copied_share'])} of the picture is its own crops)."
               + (f" The rewrite of region {', '.join(map(str, undone))} made it worse and was undone." if reverted else ""))
    return {"passed": passed, "verdict": "MATCHES" if passed else "BELOW_TARGET", "accuracy": accuracy, "built_accuracy": found["built_accuracy"],
            "copied_share": found["copied_share"], "round": rounds, "should_refine": more, "weak": found["weak"][:8], "reverted": reverted, "undone": undone, "target": TARGET,
            "formula": report.get("formula"), "summary": summary,
            "viewports": {"reference": {k: view.get(k) for k in ("width", "visual", "pixels", "ssim", "layout", "text", "referenceHeight", "cloneHeight", "diff", "original",
                                                                    "preview")} | {"errors": (view.get("errors") or [])[:5], "failed": (view.get("failed") or [])[:5]}},
            "sections": {"reference": [{k: s.get(k) for k in ("id", "name", "visual", "layout")} for s in view.get("sections") or []]}}


REFINE_SYSTEM = (
    "You improve one React component of a page so that it matches a picture better. You are shown the picture of the region and a picture of what "
    "the component renders now, with how close it is. Answer with exactly two fenced code blocks and nothing else: first ```tsx, then ```css, "
    "under the same rules as before (root <div className=\"r{id}\">, no imports, classes that start with r{id}, only HTML, CSS and inline SVG). "
    "Fix what differs: the text, its size and weight, the colours, the spacing, the alignment."
)


def _refine(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    workspace = ctx.workspace
    measured = ctx.outputs.get("measure") or {}
    if workspace is None or ctx.complete is None:
        return _fail("There is nothing to improve.")
    composition = read_json(research(workspace) / "composition.json")
    palette = (ctx.outputs.get("prepare") or {}).get("palette") or []
    brief = str(checks.param(check, "brief") or "")
    # What is there now is kept, to come back to if the rewrite is not better.
    backup = research(workspace) / "backup"
    shutil.rmtree(backup, ignore_errors=True)
    (backup / "regions").mkdir(parents=True)
    for file in (workspace / "src" / "components" / "regions").glob("Region*.tsx"):
        shutil.copy2(file, backup / "regions" / file.name)
    css = read_css(workspace)
    (backup / "regions.css.json").write_text(json.dumps({str(k): v for k, v in css.items()}), encoding="utf-8")
    shot = research(workspace) / "report" / "clone-reference.png"
    changed: list[int] = []
    for weak in (measured.get("weak") or [])[:REFINE_PER_ROUND]:
        region = next((r for r in composition["regions"] if r["id"] == weak["id"]), None)
        if region is None:
            continue
        _step(ctx, "refine", "running", f"region {region['id']} ({pipeline._percent(weak['visual'])})")
        # picture.mjs names a crop region-<id>: the clone's own is region-clone-<id>.
        here = research(workspace) / "crops" / f"region-clone-{region['id']}.jpg"
        crop_ok, _ = _script(ctx, {"cmd": "crops", "image": str(shot), "regions": [{"id": f"clone-{region['id']}", **box_px(region, composition["width"], composition["height"])}],
                                   "out": str(research(workspace) / "crops"), "previewSide": 768}, "clone-crop")
        pictures = [research(workspace) / "crops" / f"region-{region['id']}.jpg"] + ([here] if crop_ok and here.is_file() else [])
        code = region_file(workspace, region["id"]).read_text(encoding="utf-8")[:3500]
        text = (compose_prompt(region, composition["width"], composition["height"], palette, brief)
                + f"\nThe first picture is the original region; the second is what the component renders now. It matches {round(weak['visual'] * 100)}%.\n"
                  f"The component now:\n```tsx\n{code}\n```\nThe rules now:\n```css\n{css.get(region['id'], '')[:1800]}\n```")
        try:
            answer = ask(ctx, image_message(text, *pictures, system=REFINE_SYSTEM.replace("{id}", str(region["id"]))), tokens=3500, context=14336)
        except RunCancelled:
            raise
        except Exception as exc:
            log.warning("region %s: %s", region["id"], exc)
            continue
        new_code, new_css = extract_blocks(answer)
        new_code, why = valid_component(new_code, region["id"])
        if new_code is None:
            log.info("region %s: refine refused: %s", region["id"], why)
            continue
        write_component(workspace, region["id"], new_code)
        css[region["id"]] = new_css
        changed.append(region["id"])
    if changed:
        write_css(workspace, css)
        write_page(workspace, composition)
        install = pipeline._install()
        ok = install is not None and _build(ctx, install)[0]
        if not ok:
            # It does not compile: the old code comes back at once.
            for file in (backup / "regions").glob("Region*.tsx"):
                shutil.copy2(file, workspace / "src" / "components" / "regions" / file.name)
            write_css(workspace, {int(k): str(v) for k, v in (read_json(backup / "regions.css.json") or {}).items()})
            write_page(workspace, composition)
            if install is not None:
                _build(ctx, install)
            changed = []
    _step(ctx, "refine", "done", f"{len(changed)} region(s) rewritten")
    return {"passed": True, "verdict": "REFINED" if changed else "UNCHANGED", "changed": changed, "summary": f"{len(changed)} region(s) rewritten, to be measured."}


# -- motion ----------------------------------------------------------------------------------------------------------

MOTION_SYSTEM = (
    "You choose the motion of a page that already matches a picture. The motion is made by a fixed GSAP component that only takes numbers, so "
    "the last frame of every animation is the page as it is, and you cannot break it. Answer with one JSON object and nothing else:\n"
    '{"intro": {"style": "depth", "duration": 2.5, "stagger": 0.08, "ease": "power3.out", "distance": 40}, '
    '"parallax": {"amount": 18}, "hover": {"lift": 8, "scale": 1.02}}\n'
    "- intro.style: depth = the regions come forward from different depths (the nearer ones, with a higher z, travel more), rise = they rise into "
    "place, fade = they fade in. duration 1 to 4 seconds, stagger 0 to 0.3, distance 0 to 120 px, ease one of power2.out, power3.out, power4.out, "
    "expo.out, sine.out (no bounce, no elastic).\n"
    "- parallax.amount: how far, in px, the nearest region follows the mouse (0 to 40, 0 for none); farther regions follow less.\n"
    "- hover.lift: the depth, in px, of the shadow a region gets when the pointer is over it (0 to 20); hover.scale: 1 to 1.08.\n"
    "Follow the brief: set to 0 (and scale to 1) what it does not ask for."
)

EASES = ("power2.out", "power3.out", "power4.out", "expo.out", "sine.out")
INTRO_STYLES = ("depth", "rise", "fade")

# The component the numbers go in. What it animates is the content of each region (the first child of its [data-region] box), so the box keeps
# its place and its rotation, and every animation ends by clearing what it set: the page at rest is the page that was measured. It follows the
# GSAP skills it was written from: transforms and opacity only, one gsap.context that is reverted when the component goes, nothing that runs
# before the intro is over, and nothing at all for a visitor who asks for reduced motion.
MOTION_TEMPLATE = """"use client";
import { useEffect } from "react";
import gsap from "gsap";

const CONFIG = __CONFIG__;

export default function Motion() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const boxes = gsap.utils.toArray<HTMLElement>("[data-region]");
    const depth = (box: HTMLElement) => Number(box.style.zIndex || 1);
    const top = Math.max(1, ...boxes.map(depth));
    const bodyOf = (box: HTMLElement) => box.firstElementChild as HTMLElement | null;
    const cleanups: Array<() => void> = [];
    const context = gsap.context(() => {
      const { style, duration, stagger, ease, distance } = CONFIG.intro;
      boxes.forEach((box, index) => {
        const body = bodyOf(box);
        if (!body) return;
        const near = depth(box) / top;
        const from: gsap.TweenVars = { opacity: 0 };
        if (style === "depth") Object.assign(from, { scale: 1 - 0.08 * near, y: distance * near });
        if (style === "rise") Object.assign(from, { y: distance });
        gsap.from(body, { ...from, duration, ease, delay: index * stagger, clearProps: "opacity,transform" });
      });
      // Nothing answers the pointer before the intro is over.
      gsap.delayedCall(duration + stagger * boxes.length, () => {
        if (CONFIG.parallax.amount > 0) {
          const move = (event: PointerEvent) => {
            const nx = event.clientX / window.innerWidth - 0.5;
            const ny = event.clientY / window.innerHeight - 0.5;
            boxes.forEach((box) => {
              const body = bodyOf(box);
              const reach = CONFIG.parallax.amount * (depth(box) / top);
              if (body) gsap.to(body, { x: -nx * reach, y: -ny * reach, duration: 0.9, ease: "power2.out", overwrite: "auto" });
            });
          };
          window.addEventListener("pointermove", move);
          cleanups.push(() => window.removeEventListener("pointermove", move));
        }
        if (CONFIG.hover.lift > 0 || CONFIG.hover.scale > 1) {
          boxes.forEach((box) => {
            const body = bodyOf(box);
            if (!body) return;
            const shadow = (px: number, alpha: number) => `drop-shadow(0px ${px}px ${px * 2}px rgba(0,0,0,${alpha}))`;
            const over = () => gsap.to(body, { scale: CONFIG.hover.scale, filter: shadow(CONFIG.hover.lift, 0.35), duration: 0.35, ease: "power2.out" });
            const out = () => gsap.to(body, { scale: 1, filter: shadow(0, 0), duration: 0.35, ease: "power2.out", onComplete: () => gsap.set(body, { clearProps: "scale,filter" }) });
            box.addEventListener("pointerenter", over);
            box.addEventListener("pointerleave", out);
            cleanups.push(() => {
              box.removeEventListener("pointerenter", over);
              box.removeEventListener("pointerleave", out);
            });
          });
        }
      });
    });
    return () => {
      cleanups.forEach((undo) => undo());
      context.revert();
    };
  }, []);
  return null;
}
"""


def motion_config(answer: str) -> dict[str, Any] | None:
    """The numbers of a model's answer, clamped to what the component allows; None when there is no JSON object in it."""
    match = re.search(r"\{.*\}", answer, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    intro, parallax, hover = (data[k] if isinstance(data.get(k), dict) else {} for k in ("intro", "parallax", "hover"))
    style, ease = str(intro.get("style") or "depth"), str(intro.get("ease") or "power3.out")
    return {
        "intro": {"style": style if style in INTRO_STYLES else "depth", "duration": round(clamp(intro.get("duration"), 1, 4, 2.5), 2),
                  "stagger": round(clamp(intro.get("stagger"), 0, 0.3, 0.08), 2), "ease": ease if ease in EASES else "power3.out",
                  "distance": round(clamp(intro.get("distance"), 0, 120, 40))},
        "parallax": {"amount": round(clamp(parallax.get("amount"), 0, 40, 16))},
        "hover": {"lift": round(clamp(hover.get("lift"), 0, 20, 8)), "scale": round(clamp(hover.get("scale"), 1, 1.08, 1.02), 3)},
    }


def motion_component(config: dict[str, Any]) -> str:
    return MOTION_TEMPLATE.replace("__CONFIG__", json.dumps(config, indent=2).replace("\n", "\n"))


def _motion(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    workspace = ctx.workspace
    install = pipeline._install()
    measured = ctx.outputs.get("measure") or {}
    if workspace is None or install is None or ctx.complete is None:
        return {"passed": True, "verdict": "SKIPPED", "summary": "No motion was added: there is no model to choose it."}
    brief = str(checks.param(check, "brief") or "")
    if not brief.strip() or not re.search(r"anim|motion|parallax|hover|scroll|intro|movimento|animaz|effett", brief, flags=re.IGNORECASE):
        return {"passed": True, "verdict": "SKIPPED", "summary": "The brief asks for no motion."}
    composition = read_json(research(workspace) / "composition.json")
    regions = "\n".join(f"- region {r['id']}: {r['kind']}, z {r['z']}, {r['note']}" for r in composition["regions"])
    _step(ctx, "motion", "running", "the model chooses the motion")
    try:
        answer = ask(ctx, [{"role": "system", "content": MOTION_SYSTEM}, {"role": "user", "content": f"The brief: {brief.strip()[:900]}\n\nThe regions:\n{regions}"}],
                     tokens=2500, context=8192)
    except RunCancelled:
        raise
    except Exception as exc:
        _step(ctx, "motion", "failed", str(exc)[:160])
        return {"passed": True, "verdict": "SKIPPED", "summary": f"No motion was added: {exc}"}
    config = motion_config(answer)
    if config is None:
        _step(ctx, "motion", "failed", "the answer was not numbers")
        return {"passed": True, "verdict": "SKIPPED", "summary": "No motion was added: the model's answer was not usable."}
    target = workspace / "src" / "components" / "Motion.tsx"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(replica.NO_CHECK + motion_component(config), encoding="utf-8")
    write_page(workspace, composition, motion=True)
    ok, _log = _build(ctx, install)
    intro = config["intro"]
    # The page is measured when the intro is over, and a second and a half after.
    settle = int((intro["duration"] + intro["stagger"] * len(composition["regions"]) + 1.5) * 1000)
    after = _measure_once(ctx, "motion", settle_ms=settle) if ok else None
    before = measured.get("accuracy")
    now = after.get("accuracy") if isinstance(after, dict) else None
    kept = bool(ok and isinstance(now, (int, float)) and isinstance(before, (int, float)) and now >= before - MOTION_TOLERANCE)
    if not kept:
        # The page at rest must stay the picture: the motion goes, and the page is built again as it was.
        target.unlink(missing_ok=True)
        write_page(workspace, composition, motion=False)
        _build(ctx, install)
    why = "the build failed" if not ok else "it moved the page at rest" if not kept else ""
    _step(ctx, "motion", "done" if kept else "failed", "kept" if kept else why)
    return {"passed": True, "verdict": "ADDED" if kept else "REVERTED", "kept": kept, "config": config, "accuracy_before": before, "accuracy_after": now,
            "summary": (f"GSAP motion added: a {intro['style']} intro of {intro['duration']} s, parallax {config['parallax']['amount']} px, hover lift {config['hover']['lift']} px. "
                        f"The page at rest still matches ({pipeline._percent(now)} against {pipeline._percent(before)})." if kept
                        else f"The motion was chosen but not kept: {why}.")}


# -- the workflow ----------------------------------------------------------------------------------------------------

REVIEWER_PROMPT = (
    "You write the report of a site that was built from a picture, for the person who asked for it, in their language. Use only the numbers and "
    "facts in the steps you were given, and quote the numbers exactly. Say: what was built, the accuracy and how it was measured (the share of "
    "the picture that is the picture's own crops is not built by the model), which regions are weak, whether motion was added and kept, and what "
    "to do next. Short: a few lines."
)


def definition(*, model_key: str, sees: bool, vision_key: str = "", brief: str = "", reference: str = "", title: str = "", language: str = "en",
               skills: list[str] | None = None) -> WorkflowDef:
    models = {"main": ModelSlot(label="Designer", kind="vision" if sees else "", model_key=model_key, auto_tune=True)}
    if vision_key and not sees:
        # A model that cannot see cannot read a picture: the one that can does the whole job.
        models["main"] = ModelSlot(label="Designer", kind="vision", model_key=vision_key, auto_tune=True)
    params = {"brief": brief, "reference": reference, "title": title, "language": language, "skills": skills}
    reviewer = AgentDef(
        id="reviewer", role="Reviewer", model="main", goal="Report on the site that was built from the picture.", system_prompt=REVIEWER_PROMPT, tools=[],
        output_schema=schemas.TEXT, max_tool_rounds=2, temperature=0.3, reads=["measure", "compose", "motion"],
    )
    edges = [
        EdgeDef(source=START, target="prepare"),
        EdgeDef(source="prepare", target="analyze", when="passed == true"), EdgeDef(source="prepare", target=END),
        EdgeDef(source="analyze", target="compose", when="passed == true"), EdgeDef(source="analyze", target=END),
        EdgeDef(source="compose", target="measure", when="passed == true"), EdgeDef(source="compose", target=END),
        EdgeDef(source="measure", target="refine", when="should_refine == true", label="rewrite", max_loops=REFINE_ROUNDS, on_exhausted="motion"),
        EdgeDef(source="measure", target="motion"),
        EdgeDef(source="refine", target="measure"),
        EdgeDef(source="motion", target="reviewer"),
        EdgeDef(source="reviewer", target=END),
    ]
    return WorkflowDef(
        workflow=WorkflowMeta(id=WORKFLOW, name="Build a site from a picture", category="clone", max_steps=40,
                              description="Cuts a reference picture into regions, has a model write each one, measures the page against the picture and adds motion."),
        models=models, agents=[reviewer],
        checks=[CheckDef(id=i, kind=f"picture_{i}", label=i.title(), params=params) for i in ("prepare", "analyze", "compose", "measure", "refine", "motion")],
        edges=edges,
        layout={"prepare": {"x": 0.0, "y": 0.0}, "analyze": {"x": 200.0, "y": 0.0}, "compose": {"x": 400.0, "y": 0.0}, "measure": {"x": 600.0, "y": 0.0},
                "refine": {"x": 600.0, "y": 120.0}, "motion": {"x": 800.0, "y": 0.0}, "reviewer": {"x": 1000.0, "y": 0.0}},
    )


def register() -> None:
    table = {
        "prepare": (_prepare, "Prepare", "Copies the picture into the project, measures its colours and bands, writes the Next.js and GSAP project, copies the skills and installs the packages."),
        "analyze": (_analyze, "Analyze", "A model that sees writes the picture's composition as regions; the picture is cut into them."),
        "compose": (_compose, "Compose", "A model writes one component for each region from its crop; the places come from the composition."),
        "measure": (_measure, "Measure", "Builds and shows the page at the picture's size and compares it with the picture, region by region."),
        "refine": (_refine, "Refine", "The weakest regions are written again from the crop and the score; the old code comes back if it is not better."),
        "motion": (_motion, "Motion", "A model chooses the numbers of a GSAP animation (the intro, the parallax, the hover); it stays only if the page at rest still matches."),
    }
    for name, (run, label, description) in table.items():
        checks.CHECKS[f"picture_{name}"] = CheckSpec(kind=f"picture_{name}", label=label, description=description, run=run,
                                                     defaults={"brief": "", "reference": "", "title": "", "language": "en", "skills": None}, listed=False)


def skills_prompt(store: SkillStore | None) -> str:
    return prompt_catalog(store.catalog()) if store is not None else ""


register()
