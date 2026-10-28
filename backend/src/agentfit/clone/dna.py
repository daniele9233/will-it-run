"""Site DNA: what a live page measured (``dna.mjs``) read as facts, a style guide and a comparison.

``dna.mjs`` only collects numbers; every interpretation is here. A value that was not
measured stays ``None``: it is never replaced by one that looks like data.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from agentfit.clone import pipeline
from agentfit.clone.kits import host_of
from agentfit.clone.pipeline import _fail, _step
from agentfit.workflows import checks
from agentfit.workflows.checks import CheckContext, CheckSpec
from agentfit.workflows.dsl import END, START, CheckDef, EdgeDef, WorkflowDef, WorkflowMeta

HERE = Path(__file__).resolve().parent
DNA_SCRIPT = HERE / "dna.mjs"
WORKFLOW = "clone-site-study"

_SIGNATURES = Path(__file__).with_name("dna_signatures.json")


def _read_globals() -> dict[str, str]:
    data = json.loads(_SIGNATURES.read_text(encoding="utf-8-sig"))
    return {str(k): str(v) for k, v in data.get("globals", {}).items()}


GLOBAL_LIBS: dict[str, str] = _read_globals()

LIB_NAMES: dict[str, str] = {
    "gsap": "GSAP", "lenis": "Lenis", "locomotive": "Locomotive Scroll", "three": "Three.js", "ogl": "OGL",
    "pixi": "PixiJS", "p5": "p5.js", "paper": "Paper.js", "matter": "Matter.js", "rapier": "Rapier",
    "tsparticles": "tsParticles", "lottie": "Lottie", "swiper": "Swiper", "framer-motion": "Framer Motion",
    "glsl": "GLSL shaders", "webgpu": "WebGPU", "jquery": "jQuery",
}

# Signature ids that are features of the browser, not libraries a builder installs: they are kept in ``Motion``, never in ``libs``.
PLATFORM_FEATURES = frozenset({"view-transitions"})

_NOT_COVERED = ["layout", "text", "the feel of the motion"]
_PALETTE_MAX = 8
_ACCENT_CHROMA = 25.0
_ACCENT_SHARE = 0.005
_MERGE_APART = 5.0  # text and border colours closer than this are the same colour
_INK_SHARE = 0.15  # a stronger text colour with this part of all the text is the ink
_MUTED_SHARE = 0.02  # a second text colour must carry this part of the characters
_MUTED_APART = 10.0  # and be this far (CIELAB) from the main text colour, so a neighbouring bucket of the same colour is not "muted"


class DetectedLib(BaseModel):
    name: str
    evidence: list[str]


PaletteRole = Literal["background", "text", "muted", "line", "accent"]


class PaletteColor(BaseModel):
    """A colour and the job the page gives it. ``role`` is the main one; ``also`` lists others the same colour does (a page that draws its borders in
    its text colour has one colour for ``text`` and ``line``). ``share`` is the part of the visible area and of the text it covers."""

    hex: str
    share: float
    role: PaletteRole | None = None
    also: list[PaletteRole] = []
    contrast: float | None = None  # WCAG contrast ratio against the background colour, for the text, muted and line roles; None for any other colour


class FontUse(BaseModel):
    family: str
    share: float
    weights: list[int]


class Tokens(BaseModel):
    palette: list[PaletteColor] | None = None
    fonts: list[FontUse] | None = None
    type_scale: list[float] | None = None
    spacing: list[float] | None = None
    radii: list[float] | None = None
    shadows: list[str] | None = None
    container_px: int | None = None
    breakpoints: list[int] | None = None


SectionRole = Literal["header", "hero", "gallery", "filters", "content", "footer"]


class Section(BaseModel):
    index: int
    tag: str
    role: SectionRole | None = None
    height_px: int
    heading_px: float | None = None
    heading_vw: float | None = None
    img: int = 0
    video: int = 0
    canvas: int = 0
    svg: int = 0
    columns: int | None = None  # cards side by side in the first row at the viewport; None when no repeated card could be measured
    cards: int | None = None  # repeated cards the section holds (all columns); None when none could be measured
    card_height_px: int | None = None  # average height of those cards
    gap_px: int | None = None  # space between neighbouring cards: across the first row, else down a column; None when not measurable


class Videos(BaseModel):
    count: int
    loop: int
    muted: int
    autoplay: int
    preload_none: int
    playing_without_autoplay: int


class Canvas(BaseModel):
    context: Literal["webgl", "webgl2", "2d", "unknown"]
    width: int
    height: int
    covers_viewport: bool
    in_card: bool | None = None


class Transition(BaseModel):
    duration_ms: int
    easing: str
    count: int


class Motion(BaseModel):
    transitions: list[Transition] | None = None
    keyframes: list[str] | None = None
    sticky: int | None = None
    fixed: int | None = None
    scroll_snap: bool | None = None
    scroll_timeline: bool | None = None
    view_transitions: bool | None = None
    smooth_scroll: Literal["lenis", "native", "none"] | None = None
    backdrop: int | None = None
    blur: int | None = None
    mask: int | None = None
    clip_path: int | None = None
    blend: int | None = None
    videos: Videos | None = None
    canvases: list[Canvas] | None = None
    lottie: int | None = None


class Patterns(BaseModel):
    chip_filters: bool | None = None
    dialog: bool | None = None
    carousel: bool | None = None
    masonry: bool | None = None
    sticky_header: bool | None = None
    newsletter_form: bool | None = None


class ScrollProbe(BaseModel):
    sampled: int
    parallax_like: int
    pinned: int


class HoverProbe(BaseModel):
    sampled: int
    changed: int
    plays_video: int
    props: list[str]


class Probes(BaseModel):
    scroll: ScrollProbe | None = None
    hover: HoverProbe | None = None


class Limit(BaseModel):
    code: str
    count: int | None = None


class SiteDNA(BaseModel):
    schema_version: Literal[1] = 1
    url: str
    host: str
    captured_at: str
    viewport: tuple[int, int]
    framework: str | None = None
    libs: list[DetectedLib]
    tokens: Tokens
    sections: list[Section] | None = None
    motion: Motion
    patterns: Patterns
    probes: Probes
    limits: list[Limit]


# ---------------------------------------------------------------- helpers

def _dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _num_map(value: Any) -> dict[str, float] | None:
    """A ``{key: number}`` map, or ``None`` when it is missing or empty."""
    d = _dict(value)
    if not d:
        return None
    out = {str(k): float(v) for k, v in d.items() if _is_num(v)}
    return out or None


def _lab(hex_: str) -> tuple[float, float, float] | None:
    h = hex_.lstrip("#")
    try:
        rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else None
    except ValueError:
        rgb = None
    if rgb is None:
        return None

    def lin(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _chroma(hex_: str) -> float:
    lab = _lab(hex_)
    return math.hypot(lab[1], lab[2]) if lab else 0.0


def _distance(a: str, b: str) -> float | None:
    la, lb = _lab(a), _lab(b)
    if la is None or lb is None:
        return None
    return math.dist(la, lb)


def _fractions(m: dict[str, float] | None) -> dict[str, float]:
    if not m:
        return {}
    total = sum(m.values())
    return {k: v / total for k, v in m.items()} if total > 0 else {}


def _merge_near(m: dict[str, float] | None) -> dict[str, float] | None:
    """Colours that sit in neighbouring 5-bit buckets are one colour (the same grey over two backgrounds): each joins the most used one within
    ``_MERGE_APART`` (CIELAB), which names the group."""
    if not m:
        return m
    kept: dict[str, float] = {}
    for hex_, n in sorted(m.items(), key=lambda kv: (-kv[1], kv[0])):
        near = next((k for k in kept if (_distance(hex_, k) or 99.0) < _MERGE_APART), None)
        kept[near or hex_] = kept.get(near or hex_, 0.0) + n
    return kept


def _ink(text: dict[str, float] | None, background: str | None) -> str | None:
    """The main text colour: the most used one (a colour equal to the background is not text). When the most used is itself a dimmed colour (a grey
    caption that outnumbers the black of the headings) a plain colour that carries at least ``_INK_SHARE`` of all the text characters and stands out more
    against the background is the main one, and the dimmed colour becomes ``muted``."""
    if not text:
        return None
    rest = {k: v for k, v in text.items() if k != background}
    if not rest:
        return None
    leader = max(rest, key=lambda k: (rest[k], k))
    total = sum(rest.values())
    lb = _lab(background) if background else None
    lead = _lab(leader)
    if lb is None or lead is None:
        return leader
    best, best_gap = leader, abs(lead[0] - lb[0])
    for hex_, chars in rest.items():
        lab = _lab(hex_)
        if lab is None or chars < _INK_SHARE * total or _chroma(hex_) >= _ACCENT_CHROMA:
            continue
        gap = abs(lab[0] - lb[0])
        if gap > best_gap:
            best, best_gap = hex_, gap
    return best


def _muted(
    text: dict[str, float] | None, taken: set[str | None], background: str | None, text_color: str | None,
) -> str | None:
    """The second text colour: the most used one that is not the main text, not an accent and sits nearer the background than the text does
    (a lighter grey on a light page, a dimmer white on a dark one). Opacity is already in the colour: the probe measures text as it looks."""
    if not text or background is None or text_color is None:
        return None
    lb, lt = _lab(background), _lab(text_color)
    if lb is None or lt is None:
        return None
    total = sum(text.values())
    best: str | None = None
    for hex_, chars in sorted(text.items(), key=lambda kv: (-kv[1], kv[0])):
        if hex_ in taken or chars / total < _MUTED_SHARE or _chroma(hex_) >= _ACCENT_CHROMA:
            continue
        lab = _lab(hex_)
        if lab is None or (_distance(hex_, text_color) or 0.0) < _MUTED_APART:
            continue
        if abs(lab[0] - lb[0]) < abs(lt[0] - lb[0]):
            best = hex_
            break
    return best


def _luminance(hex_: str) -> float | None:
    """WCAG relative luminance of ``#rrggbb``; None when it is not a hex colour."""
    if len(hex_) != 7 or hex_[0] != "#":
        return None
    try:
        r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except ValueError:
        return None

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(a: str, b: str) -> float | None:
    """The WCAG 2 contrast ratio of two ``#rrggbb`` colours (1 to 21), rounded to two decimals; None when either is not a hex colour."""
    la, lb = _luminance(a), _luminance(b)
    if la is None or lb is None:
        return None
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


_CONTRAST_ROLES = ("text", "muted", "line")


def _palette(styles: dict[str, Any]) -> list[PaletteColor] | None:
    bg = _num_map(styles.get("backgrounds"))
    text = _merge_near(_num_map(styles.get("text_colors")))
    if bg is None and text is None:
        return None
    sources = [f for f in (_fractions(bg), _fractions(text)) if f]
    share: dict[str, float] = {}
    for f in sources:
        for hex_, v in f.items():
            share[hex_] = share.get(hex_, 0.0) + v / len(sources)
    background = max(bg, key=lambda k: bg[k]) if bg else None
    text_color = _ink(text, background)
    accents = [
        h for h in share
        if h not in (background, text_color) and _chroma(h) >= _ACCENT_CHROMA and share[h] >= _ACCENT_SHARE
    ]
    accent = max(accents, key=_chroma) if accents else None
    muted = _muted(text, {background, text_color, accent}, background, text_color)
    borders = _merge_near(_num_map(styles.get("border_colors")))
    line = None
    if borders:
        visible = {k: v for k, v in borders.items() if k != background}
        line = max(visible, key=lambda k: (visible[k], k)) if visible else None
    roles: dict[str, list[PaletteRole]] = {}
    for hex_, role in ((background, "background"), (text_color, "text"), (muted, "muted"), (line, "line"), (accent, "accent")):
        if hex_ is not None:
            roles.setdefault(hex_, []).append(role)  # type: ignore[arg-type]
    keep = list(roles)
    for h in sorted(share, key=lambda k: (-share[k], k)):
        if len(keep) >= _PALETTE_MAX:
            break
        if h not in keep:
            keep.append(h)
    keep.sort(key=lambda k: (-share.get(k, 0.0), k))
    return [
        PaletteColor(
            hex=h, share=round(share.get(h, 0.0), 4), role=roles[h][0] if h in roles else None, also=roles.get(h, [])[1:],
            contrast=contrast_ratio(h, background) if background and h != background and any(r in _CONTRAST_ROLES for r in roles.get(h, [])) else None,
        )
        for h in keep
    ]


def _declared_fonts(value: Any, used: set[str]) -> list[FontUse]:
    """Families the page declares with @font-face but shows no text in: share 0.0, no weights seen."""
    if not isinstance(value, list):
        return []
    out: list[FontUse] = []
    for name in value:
        family = str(name).strip()
        if not family or family.lower().endswith(" fallback") or family.lower() in used:
            continue
        used.add(family.lower())
        out.append(FontUse(family=family, share=0.0, weights=[]))
    return out[:4]


def _fonts(value: Any, declared: Any = None) -> list[FontUse] | None:
    d = _dict(value)
    if not d:
        return None
    used = {k: v for k, v in d.items() if isinstance(v, dict) and _is_num(v.get("chars"))}
    total = sum(float(v["chars"]) for v in used.values())
    if not used or total <= 0:
        return None
    out: list[FontUse] = []
    for family in sorted(used, key=lambda k: -float(used[k]["chars"]))[:4]:
        weights = _num_map(used[family].get("weights")) or {}
        top = sorted(weights, key=lambda k: -weights[k])[:3]
        out.append(FontUse(
            family=family, share=round(float(used[family]["chars"]) / total, 4),
            weights=[int(float(w)) for w in top],
        ))
    out += _declared_fonts(declared, {f.family.lower() for f in out})
    return out


def _top_keys(m: dict[str, float] | None, n: int) -> list[str] | None:
    if m is None:
        return None
    return sorted(m, key=lambda k: (-m[k], k))[:n]


def _numeric(keys: list[str]) -> list[float]:
    out: list[float] = []
    for k in keys:
        try:
            out.append(float(k))
        except ValueError:
            continue
    return sorted(out)


def _type_scale(value: Any) -> list[float] | None:
    m = _num_map(value)
    if m is None:
        return None
    total = sum(m.values())
    sizes = [k for k, v in m.items() if total > 0 and v / total >= 0.005]
    return _numeric(sizes)[:8]


def _tokens(styles: dict[str, Any] | None) -> Tokens:
    if styles is None:
        return Tokens()
    spacing = _top_keys(_num_map(styles.get("spacings")), 8)
    radii = _top_keys(_num_map(styles.get("radii")), 8)
    container = styles.get("container_px")
    breakpoints = styles.get("breakpoints")
    return Tokens(
        palette=_palette(styles),
        fonts=_fonts(styles.get("fonts"), styles.get("font_faces")),
        type_scale=_type_scale(styles.get("sizes")),
        spacing=_numeric(spacing) if spacing is not None else None,
        radii=_numeric(radii) if radii is not None else None,
        shadows=_top_keys(_num_map(styles.get("shadows")), 5),
        container_px=int(container) if _is_num(container) and container else None,
        breakpoints=sorted(int(b) for b in breakpoints) if isinstance(breakpoints, list) else None,
    )


def _libs(raw: dict[str, Any]) -> list[DetectedLib]:
    found: dict[str, list[str]] = {}
    for lib_id, labels in (_dict(raw.get("signature_hits")) or {}).items():
        if labels and lib_id not in PLATFORM_FEATURES:
            found.setdefault(lib_id, []).extend(str(x) for x in labels)
    names = raw.get("globals")
    for name in names if isinstance(names, list) else []:
        lib_id = GLOBAL_LIBS.get(str(name))
        if lib_id is not None and lib_id not in PLATFORM_FEATURES:
            found.setdefault(lib_id, []).append(f"window.{name}")
    return [DetectedLib(name=LIB_NAMES.get(i, i), evidence=ev) for i, ev in found.items()]


_FILTER_BAR_MAX_PX = 120  # a bar shorter than this that holds chips, buttons or inputs and no picture is a filter bar
_HERO_MAX_VIEWPORTS = 1.5  # a hero that is only a video or a canvas must fit in this many screens


def _section_role(s: dict[str, Any], *, first_content: bool, viewport_h: int, above_grid: bool = False) -> SectionRole:
    """The job of a section, by rule. The order matters: a short bar of controls is ``filters`` and a repeating grid is a ``gallery`` before either
    can be taken for the ``hero``, which is only ever the first content section of the page."""
    tag = str(s.get("tag", ""))
    if tag == "header":
        return "header"
    if tag == "footer":
        return "footer"
    height = int(s.get("height", 0))
    media = int(s.get("img", 0)) + int(s.get("video", 0)) + int(s.get("canvas", 0))
    # A row of chips is a filter bar only with evidence (a control, a pressed state, a filter attribute) or when it sits directly above a grid:
    # a thin row of links alone is a menu, and stays ``content``.
    chips = int(s.get("chips", 0)) >= 3 and (bool(s.get("chips_evidence")) or above_grid)
    if height < _FILTER_BAR_MAX_PX and media == 0 and (chips or int(s.get("controls", 0)) >= 3):
        return "filters"
    if int(s.get("repeat", 0)) >= 6:
        return "gallery"
    heading = s.get("heading_px")
    if first_content and (
        (_is_num(heading) and heading >= 32)
        or ((s.get("video", 0) or s.get("canvas", 0)) and height <= _HERO_MAX_VIEWPORTS * viewport_h)
    ):
        return "hero"
    return "content"


def _sections(dom: dict[str, Any] | None, viewport_h: int = 900) -> list[Section] | None:
    if dom is None or not isinstance(dom.get("sections"), list):
        return None
    out: list[Section] = []
    first_content = True
    raw_sections = dom["sections"]
    for i, s in enumerate(raw_sections):
        tag = str(s.get("tag", ""))
        above_grid = i + 1 < len(raw_sections) and int(raw_sections[i + 1].get("repeat", 0)) >= 6
        role = _section_role(s, first_content=first_content, viewport_h=viewport_h, above_grid=above_grid)
        strip = role == "content" and int(s.get("height", 0)) < _FILTER_BAR_MAX_PX and not (
            int(s.get("img", 0)) + int(s.get("video", 0)) + int(s.get("canvas", 0)))  # a thin menu strip is not the top content of the page
        if role not in ("header", "footer", "filters") and not strip:
            first_content = False  # whatever comes after the first content section is not at the top
        columns, cards, card_h, gap = s.get("columns"), s.get("cards"), s.get("card_height"), s.get("gap")
        out.append(Section(
            index=int(s.get("index", len(out) + 1)), tag=tag, role=role, height_px=int(s.get("height", 0)),
            heading_px=s.get("heading_px"), heading_vw=s.get("heading_vw"), img=int(s.get("img", 0)),
            video=int(s.get("video", 0)), canvas=int(s.get("canvas", 0)), svg=int(s.get("svg", 0)),
            columns=int(columns) if _is_num(columns) and columns > 0 else None,
            cards=int(cards) if _is_num(cards) and cards > 0 else None,
            card_height_px=int(card_h) if _is_num(card_h) and card_h > 0 else None,
            gap_px=int(gap) if _is_num(gap) and gap >= 0 else None,
        ))
    return out


def _view_transitions(hits: dict[str, Any] | None, rules: int | None) -> bool | None:
    """A browser feature, measured two ways: a script that calls it, or a style rule that names it. Found by either: yes. Absent from both: no, but only
    when both were read; if either source was unreadable, absence proves nothing: unknown."""
    if (hits or {}).get("view-transitions") or (rules or 0) > 0:
        return True
    return None if hits is None or rules is None else False


def _motion(raw: dict[str, Any]) -> Motion:
    styles = _dict(raw.get("styles"))
    dom = _dict(raw.get("dom"))
    hits = _dict(raw.get("signature_hits"))
    counts = _dict(styles.get("counts")) if styles else None

    def count(key: str) -> int | None:
        v = counts.get(key) if counts else None
        return int(v) if _is_num(v) else None

    def flag(key: str) -> bool | None:
        n = count(key)
        return None if n is None else n > 0

    transitions = None
    if styles and isinstance(styles.get("transitions"), list):
        transitions = [
            Transition(duration_ms=t["duration_ms"], easing=t["easing"], count=t["count"])
            for t in styles["transitions"]
        ]
    smooth = _dict(dom.get("smooth_scroll")) if dom else None
    smooth_scroll: Literal["lenis", "native", "none"] | None = None
    if smooth is not None or hits is not None:
        if (smooth or {}).get("lenis_class") or (hits or {}).get("lenis"):
            smooth_scroll = "lenis"
        elif (smooth or {}).get("css_smooth"):
            smooth_scroll = "native"
        else:
            smooth_scroll = "none"
    videos = None
    if dom and isinstance(dom.get("videos"), list):
        vs = dom["videos"]
        videos = Videos(
            count=len(vs), loop=sum(1 for v in vs if v.get("loop")), muted=sum(1 for v in vs if v.get("muted")),
            autoplay=sum(1 for v in vs if v.get("autoplay")),
            preload_none=sum(1 for v in vs if v.get("preload") == "none"),
            playing_without_autoplay=sum(1 for v in vs if v.get("playing_without_autoplay")),
        )
    canvases = None
    if dom and isinstance(dom.get("canvases"), list):
        canvases = [Canvas.model_validate(c) for c in dom["canvases"]]
    lottie = dom.get("lottie") if dom else None
    keyframes = styles.get("keyframes") if styles else None
    return Motion(
        transitions=transitions,
        keyframes=[str(k) for k in keyframes] if isinstance(keyframes, list) else None,
        sticky=count("sticky"), fixed=count("fixed"), scroll_snap=flag("scroll_snap"),
        scroll_timeline=flag("scroll_timeline"),
        view_transitions=_view_transitions(hits, count("view_transition_rules")),
        smooth_scroll=smooth_scroll, backdrop=count("backdrop"), blur=count("blur"), mask=count("mask"),
        clip_path=count("clip_path"), blend=count("blend"), videos=videos, canvases=canvases,
        lottie=int(lottie) if _is_num(lottie) else None,
    )


def _probes(raw: dict[str, Any]) -> Probes:
    probes = _dict(raw.get("probes")) or {}
    scroll, hover = _dict(probes.get("scroll")), _dict(probes.get("hover"))
    return Probes(
        scroll=ScrollProbe.model_validate(scroll) if scroll else None,
        hover=HoverProbe.model_validate(hover) if hover else None,
    )


def analyze(raw: dict[str, Any]) -> SiteDNA:
    """Read a raw probe (``dna-raw.json``) as a ``SiteDNA``. Nothing unmeasured is filled in."""
    dom = _dict(raw.get("dom"))
    sections = _sections(dom, int(raw.get("viewport", [1440, 900])[1]))
    limits = [Limit(code=str(x["code"]), count=x.get("count")) for x in raw.get("limits") or []]
    if sections is not None and not sections and not any(x.code == "no_sections" for x in limits):
        limits.append(Limit(code="no_sections", count=None))
    patterns = (_dict(dom.get("patterns")) if dom else None) or {}
    where = str(raw.get("final_url") or raw.get("url") or "")
    return SiteDNA(
        url=str(raw.get("url") or where), host=urlparse(where).netloc, captured_at=str(raw["captured_at"]),
        viewport=tuple(raw["viewport"]), framework=raw.get("framework") or None, libs=_libs(raw),
        tokens=_tokens(_dict(raw.get("styles"))), sections=sections, motion=_motion(raw),
        patterns=Patterns(**{k: v for k, v in patterns.items() if k in Patterns.model_fields}),
        probes=_probes(raw), limits=limits,
    )


# ---------------------------------------------------------------- limits and style guide

_LIMIT_TEXT: dict[str, str] = {
    "script_unreadable": "{n} script(s) could not be read, so libraries only they use may be missing",
    "no_sections": "the page has no readable sections (it may be a canvas, an iframe or a single block)",
    "scroll_probe_skipped": "the scroll behaviour was not tested",
    "hover_probe_skipped": "the hover behaviour was not tested",
    "stylesheet_unreadable": "{n} stylesheet(s) could not be read, so some styles are missing",
    "visit_slow": "the page was slow to settle, so late content may be missing",
}


def limit_text(code: str, count: int | None) -> str:
    """English words for a limit code (models read them)."""
    text = _LIMIT_TEXT.get(code, f"limit {code}")
    return text.replace("{n}", "some" if count is None else str(count))


def _unknown(value: Any) -> str:
    return "Unknown" if value is None else str(value)


def _join(values: list[Any] | None) -> str:
    if values is None:
        return "Unknown"
    return ", ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in values) or "none"


def _yes(value: bool | None) -> str:
    return "Unknown" if value is None else ("yes" if value else "no")


def role_text(c: PaletteColor) -> str:
    """``, text, line``: the jobs of a palette colour, or nothing when it has none."""
    return "".join(f", {r}" for r in ([c.role] if c.role else []) + c.also)


def contrast_text(c: PaletteColor) -> str:
    """``3.7:1 on the background: below 4.5:1, ...``: the measured ratio and the WCAG thresholds it falls under, nothing more. Empty when not measured.
    Text and muted are read against 4.5:1 (3:1 for large text), a line against 3:1. The ratio is cut, not rounded, to one decimal so that it never
    reads as the threshold it is below."""
    if c.contrast is None:
        return ""
    shown = f"{math.floor(round(c.contrast * 10, 6)) / 10:.1f}:1 on the background"
    if c.role == "line" and "text" not in c.also and "muted" not in c.also:
        return f"{shown}: " + ("meets 3:1" if c.contrast >= 3 else "below 3:1")
    if c.contrast >= 4.5:
        return f"{shown}: meets 4.5:1"
    if c.contrast >= 3:
        return f"{shown}: below 4.5:1, not usable as body text as measured; meets 3:1 (large text)"
    return f"{shown}: below 4.5:1 and below 3:1, not usable as text as measured"


def layout_text(s: Section, viewport_w: int) -> str:
    """``80 cards in 4 columns, rows ~230 px tall, gap 16 px``: the size of the repeated cards of a section, as far as it was measured; empty
    when no card was (a gallery then says ``columns Unknown``)."""
    parts: list[str] = []
    if s.cards is not None:
        parts.append(f"{s.cards} cards" + (f" in {s.columns} column{'s' if s.columns != 1 else ''}" if s.columns is not None else ""))
    elif s.columns is not None:
        parts.append(f"{s.columns} column{'s' if s.columns != 1 else ''} at {viewport_w}px")
    elif s.role == "gallery":
        parts.append("columns Unknown")
    if s.card_height_px is not None:
        parts.append(f"rows ~{s.card_height_px} px tall")
    if s.gap_px is not None:
        parts.append(f"gap {s.gap_px} px")
    return ", ".join(parts)


def share_text(c: PaletteColor) -> str:
    """``12.5%``; a colour with no area and no text of its own (only borders use it) says so instead of a false ``0%``."""
    return "borders only" if c.share == 0.0 and "line" in ([c.role] if c.role else []) + c.also else f"{round(c.share * 100, 1)}%"


def palette_text(c: PaletteColor) -> str:
    """One palette colour as the style guide and the composer's Site DNA block both write it: ``#808080 (38.3%), muted, 3.7:1 on the background: ...``."""
    return f"{c.hex} ({share_text(c)}){role_text(c)}" + (f", {contrast_text(c)}" if c.contrast is not None else "")


def font_text(f: FontUse) -> str:
    """One font, without the bullet. A family the page declares (@font-face) but sets no visible text in is optional: say so, with no invented share."""
    if f.share == 0.0 and not f.weights:
        return f"{f.family} (declared, not seen in visible text: optional)"
    return f"{f.family} ({round(f.share * 100, 1)}%), weights {_join(f.weights)}"


def style_guide(dna: SiteDNA) -> str:
    """The measured facts as Markdown. What was not measured reads ``Unknown``."""
    t, m, p = dna.tokens, dna.motion, dna.patterns
    lines = [f"# Style guide: {dna.host}", "", "## Palette", ""]
    if t.palette is None:
        lines.append("- Unknown")
    else:
        for c in t.palette:
            lines.append(f"- {palette_text(c)}")
    lines += ["", "## Type", ""]
    if t.fonts is None:
        lines.append("- Fonts: Unknown")
    else:
        lines += [f"- {font_text(f)}" for f in t.fonts]
    lines += [
        f"- Sizes (px): {_join(t.type_scale)}", "", "## Spacing", "",
        f"- Spacing values (px): {_join(t.spacing)}", f"- Radii (px): {_join(t.radii)}",
        f"- Shadows: {_join(t.shadows)}", "", "## Layout", "",
        f"- Container (px): {_unknown(t.container_px)}", f"- Breakpoints (px): {_join(t.breakpoints)}",
    ]
    if dna.sections is None:
        lines.append("- Sections: Unknown")
    elif not dna.sections:
        lines.append("- Sections: none found")
    else:
        for s in dna.sections:
            lines.append(
                f"- Section {s.index}: {s.role or s.tag}, {s.height_px}px high, heading {_unknown(s.heading_px)}px"
                + (", " + layout_text(s, dna.viewport[0]) if layout_text(s, dna.viewport[0]) else "")
            )
    videos = "Unknown" if m.videos is None else (
        f"{m.videos.count} ({m.videos.autoplay} autoplay, {m.videos.loop} looping)")
    canvases = "Unknown" if m.canvases is None else (
        ", ".join(f"{c.context} {c.width}x{c.height}" for c in m.canvases) or "none")
    lines += [
        "", "## Motion", "",
        f"- Smooth scroll: {_unknown(m.smooth_scroll)}", f"- Sticky elements: {_unknown(m.sticky)}",
        f"- View transitions: {_yes(m.view_transitions)} (a feature of the browser, not a library)", f"- Scroll snap: {_yes(m.scroll_snap)}",
        f"- Backdrop blur elements: {_unknown(m.backdrop)}", f"- Videos: {videos}", f"- Canvases: {canvases}",
        "", "## Libraries", "",
    ]
    lines += [f"- {lib.name} ({', '.join(lib.evidence)})" for lib in dna.libs] or ["- none detected"]
    lines += [
        "", "## Patterns", "", f"- Chip filters: {_yes(p.chip_filters)}", f"- Dialog: {_yes(p.dialog)}",
        f"- Carousel: {_yes(p.carousel)}", f"- Masonry: {_yes(p.masonry)}",
        f"- Sticky header: {_yes(p.sticky_header)}", f"- Newsletter form: {_yes(p.newsletter_form)}",
        "", "## Limits", "",
    ]
    lines += [f"- {limit_text(x.code, x.count)}" for x in dna.limits] or ["- none"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- match

def _palette_score(a: SiteDNA, b: SiteDNA) -> float | None:
    pa, pb = a.tokens.palette, b.tokens.palette
    if not pa or not pb:
        return None
    top_a = [c.hex for c in sorted(pa, key=lambda c: -c.share)[:5]]
    top_b = [c.hex for c in sorted(pb, key=lambda c: -c.share)[:5]]
    pairs: list[tuple[float, int, int]] = []
    for i, x in enumerate(top_a):
        for j, y in enumerate(top_b):
            d = _distance(x, y)
            if d is not None:
                pairs.append((d, i, j))
    used_a: set[int] = set()
    used_b: set[int] = set()
    distances: list[float] = []
    for d, i, j in sorted(pairs):
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            distances.append(d)
    if not distances:
        return None
    return 1 - min(1.0, (sum(distances) / len(distances)) / 40)


def _jaccard(x: set[str], y: set[str]) -> float | None:
    union = x | y
    return len(x & y) / len(union) if union else None


def _features(d: SiteDNA) -> set[str]:
    m = d.motion
    present = {
        "view_transitions": m.view_transitions is True,
        "scroll_snap": m.scroll_snap is True,
        "smooth_scroll": m.smooth_scroll not in (None, "none"),
        "sticky": (m.sticky or 0) > 0,
        "backdrop": (m.backdrop or 0) > 0,
        "videos": m.videos is not None and m.videos.count > 0,
        "canvases": bool(m.canvases),
        "lottie": (m.lottie or 0) > 0,
    }
    return {k for k, v in present.items() if v}


def dna_match(a: SiteDNA, b: SiteDNA) -> dict[str, Any]:
    """How much ``b`` (the copy) resembles ``a`` (the reference), part by part."""
    fonts_a, fonts_b = a.tokens.fonts, b.tokens.fonts
    features_a = _features(a)
    sections: float | None = None
    if a.sections and b.sections:
        sections = min(len(a.sections), len(b.sections)) / max(len(a.sections), len(b.sections))
    raw_parts: dict[str, float | None] = {
        "palette": _palette_score(a, b),
        "fonts": None if fonts_a is None or fonts_b is None else _jaccard(
            {f.family.lower() for f in fonts_a}, {f.family.lower() for f in fonts_b}),
        "libs": _jaccard({lib.name.lower() for lib in a.libs}, {lib.name.lower() for lib in b.libs}),
        "motion": len(features_a & _features(b)) / len(features_a) if features_a else None,
        "sections": sections,
    }
    parts = {k: None if v is None else round(v, 4) for k, v in raw_parts.items()}
    known = [v for v in parts.values() if v is not None]
    return {
        "overall": round(sum(known) / len(known), 4) if known else None,
        "parts": parts,
        "not_covered": list(_NOT_COVERED),
    }


# ---------------------------------------------------------------- the study as a run

def dna_dir(workspace: Path, host: str) -> Path:
    return pipeline.research(workspace, host) / "dna"


def _script(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
    """Run ``dna.mjs`` with a config; (True, "") when it finished well. The seam the tests replace."""
    install = pipeline._install()
    browser = pipeline.find_browser()
    if install is None or ctx.workspace is None or browser is None:
        return False, "The browser engine, or Chrome or Edge, is not available."
    out = Path(config["out"])
    out.mkdir(parents=True, exist_ok=True)
    path = out / "dna.config.json"
    path.write_text(json.dumps({**config, "executable": browser["executable"]}), encoding="utf-8")
    return pipeline._run_script(ctx, install, DNA_SCRIPT, path, "study", timeout=300.0)


def _clone_dna(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    if pipeline._install() is None or ctx.workspace is None:
        return _fail("The study is not available in this run.", "STUDY_FAILED")
    url = str(checks.param(check, "url"))
    host = str(checks.param(check, "host") or host_of(url))
    folder = dna_dir(ctx.workspace, host)
    _step(ctx, "study", "running", url)
    ok, why = _script(ctx, {"url": url, "out": str(folder), "locale": str(checks.param(check, "locale") or "")})
    if not ok:
        _step(ctx, "study", "failed", why)
        return _fail(f"The site could not be studied: {why}", "STUDY_FAILED")
    try:
        raw = json.loads((folder / "dna-raw.json").read_text(encoding="utf-8-sig"))
        found = analyze(raw)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _step(ctx, "study", "failed", str(exc)[:160])
        return _fail(f"The probe's answer could not be read: {exc}", "STUDY_FAILED")
    (folder / "dna.json").write_text(found.model_dump_json(indent=2), encoding="utf-8")
    (folder / "style_guide.md").write_text(style_guide(found), encoding="utf-8", newline="\n")
    sections = "unknown" if found.sections is None else str(len(found.sections))
    summary = f"Studied {found.host}: {len(found.libs)} libraries, {sections} sections, {len(found.limits)} limits."
    _step(ctx, "study", "done", summary)
    return {"passed": True, "verdict": "STUDIED", "summary": summary, "dna": found.model_dump(mode="json")}


def definition(*, url: str) -> WorkflowDef:
    params = {"url": url, "host": host_of(url), "locale": ""}
    return WorkflowDef(
        workflow=WorkflowMeta(id=WORKFLOW, name="Study a site", category="clone", max_steps=4,
                              description="Opens a page, measures its colours, type, libraries and motion, and writes the Site DNA."),
        models={}, agents=[],
        checks=[CheckDef(id="dna", kind="clone_dna", label="Study", params=params)],
        edges=[EdgeDef(source=START, target="dna"), EdgeDef(source="dna", target=END)],
        layout={"dna": {"x": 0.0, "y": 0.0}},
    )


def register() -> None:
    checks.CHECKS["clone_dna"] = CheckSpec(
        kind="clone_dna", label="Study",
        description="Opens the page in Chrome, measures its styles, scripts, structure, scroll and hover, and writes the Site DNA and a style guide.",
        run=_clone_dna, defaults={"url": "", "host": "", "locale": ""}, listed=False,
    )


register()
