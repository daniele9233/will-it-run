"""The composer: from the Site DNA of a reference to one prompt, and the reason for every recipe in it. No model takes part.

The rules are a table in code. Each says what the DNA must show to recommend a recipe and carries the reason the owner reads (a ``tr()`` template with
a literal text). ``compose`` writes the opening, one ``## Site DNA`` block, the recipes, then the shared rules.

The measured values are stated once, in the Site DNA block. A recipe or a shared rule that has a placeholder is filled with a pointer to the line of the
block that holds the value (``see Site DNA, palette``), so that the nine values of F01 are not repeated in the prompt and the budget goes to recipes.
A value that was not measured reads ``UNKNOWN`` in the block, and the pointer leads there.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentfit.clone import library as lib
from agentfit.clone.dna import SiteDNA, font_text, layout_text, limit_text, palette_text
from agentfit.clone.skills import PACKS
from agentfit.i18n import tr

BUDGET_TOKENS, MAX_RECOMMENDED, ALWAYS = 12_000, 6, ("F01", "F03")
# Fewer videos than this are decoration, not a gallery of clips.
MIN_VIDEOS = 6


@dataclass(frozen=True)
class Rule:
    id: str
    prompts: tuple[str, ...]
    when: Callable[[SiteDNA], bool]
    reason: Callable[[SiteDNA], str]


def _looping_videos(dna: SiteDNA) -> bool:
    videos = dna.motion.videos
    return videos is not None and videos.count >= MIN_VIDEOS and videos.loop * 2 >= videos.count and videos.muted * 2 >= videos.count


def _lenis(dna: SiteDNA) -> bool:
    return dna.motion.smooth_scroll == "lenis" or any(found.name == "Lenis" for found in dna.libs)


RULES: tuple[Rule, ...] = (
    Rule("hover-video", ("B02",), _looping_videos,
         lambda dna: tr("{n} videos on the page, most of them muted and looping", n=dna.motion.videos.count if dna.motion.videos else 0)),
    Rule("chip-filters", ("B01",), lambda dna: dna.patterns.chip_filters is True,
         lambda dna: tr("a row of filter chips above a grid")),
    Rule("lenis", ("C01",), _lenis, lambda dna: tr("Lenis smooth scroll was found on the page")),
)


def _always_reason() -> str:
    return tr("every master prompt starts from a style guide and ends with a look-and-fix loop")


# -- the measured values ---------------------------------------------------------------------------------------------


def _numbers(values: list[float] | None) -> str | None:
    if values is None:
        return None
    return (", ".join(f"{v:g}" for v in values) + " px") if values else "none"


def _yes(value: bool | None) -> str:
    return "Unknown" if value is None else ("yes" if value else "no")


def _count(value: int | None) -> str:
    return "Unknown" if value is None else str(value)


def _motion(dna: SiteDNA) -> str:
    m = dna.motion
    videos = "Unknown" if m.videos is None else (
        f"{m.videos.count} ({m.videos.loop} looping, {m.videos.muted} muted, {m.videos.autoplay} autoplay, {m.videos.preload_none} preload none)")
    canvases = "Unknown" if m.canvases is None else (", ".join(f"{c.context} {c.width}x{c.height}" for c in m.canvases) or "none")
    scroll, hover = dna.probes.scroll, dna.probes.hover
    parts = [
        f"smooth scroll {m.smooth_scroll or 'Unknown'}", f"sticky {_count(m.sticky)}", f"view transitions {_yes(m.view_transitions)} (browser feature, not a library)",
        f"scroll snap {_yes(m.scroll_snap)}", f"backdrop blur {_count(m.backdrop)}", f"videos {videos}", f"canvases {canvases}",
        f"lottie {_count(m.lottie)}",
        "pinned elements " + ("Unknown" if scroll is None else str(scroll.pinned)),
        "parallax-like elements " + ("Unknown" if scroll is None else str(scroll.parallax_like)),
        "hover changes " + ("Unknown" if hover is None else f"{hover.changed} of {hover.sampled} ({', '.join(hover.props) or 'none'})"),
    ]
    if m.transitions:
        parts.append("transitions " + ", ".join(f"{t.duration_ms}ms {t.easing}" for t in m.transitions[:4]))
    if m.keyframes:
        parts.append("keyframes " + ", ".join(m.keyframes[:8]))
    return "; ".join(parts)


def _sections(dna: SiteDNA) -> str | None:
    if dna.sections is None:
        return None
    if not dna.sections:
        return "none found (the page has no readable sections)"
    lines = []
    for s in dna.sections:
        heading = "" if s.heading_px is None else f", heading {s.heading_px:g}px" + ("" if s.heading_vw is None else f" ({s.heading_vw:g}vw)")
        media = ", ".join(f"{n} {kind}" for kind, n in (("img", s.img), ("video", s.video), ("canvas", s.canvas), ("svg", s.svg)) if n)
        layout = layout_text(s, dna.viewport[0])
        size = f"{s.role or s.tag}, {layout}, section {s.height_px} px" if s.cards is not None else (
            f"{s.role or s.tag} {s.height_px}px" + (f", {layout}" if layout else ""))
        lines.append(f"{s.index} {size}{heading}{', ' + media if media else ''}")
    return "; ".join(lines)


def values_of(dna: SiteDNA) -> dict[str, str]:
    """The nine placeholder values, measured. A value that was not measured is ``UNKNOWN``, never a default."""
    t = dna.tokens
    palette = None if t.palette is None else "; ".join(palette_text(c) for c in t.palette)
    fonts = None if t.fonts is None else "; ".join(font_text(f) for f in t.fonts)
    libs = ", ".join(f"{found.name} ({', '.join(found.evidence)})" for found in dna.libs) or "none detected"
    found: dict[str, str | None] = {
        "host": dna.host, "palette": palette, "fonts": fonts, "type_scale": _numbers(t.type_scale), "spacing": _numbers(t.spacing),
        "motion": _motion(dna), "sections": _sections(dna), "libs": libs, "viewport": f"{dna.viewport[0]}x{dna.viewport[1]}",
    }
    return {name: found[name] or lib.UNKNOWN for name in lib.PLACEHOLDERS}


def _pointers() -> dict[str, str]:
    return {name: f"see Site DNA, {name}" for name in lib.PLACEHOLDERS}


def _dna_block(dna: SiteDNA) -> str:
    lines = [f"## Site DNA (measured on {dna.captured_at[:10]})", "", f"Reference: {dna.url}"]
    lines += [f"- {name}: {value}" for name, value in values_of(dna).items()]
    if dna.limits:
        lines.append("- limits: " + "; ".join(limit_text(x.code, x.count) for x in dna.limits))
    return "\n".join(lines)


# -- the recommendation ------------------------------------------------------------------------------------------------


def recommend(dna: SiteDNA, library: lib.Library | None = None) -> list[dict[str, str]]:
    """The recipes the DNA calls for, each with its reason: the rules that match in the order of ``RULES`` (at most ``MAX_RECOMMENDED``, and only
    prompts the library has), then ``ALWAYS``."""
    found = library if library is not None else lib.get_library()
    chosen: list[dict[str, str]] = []
    for rule in RULES:
        if not rule.when(dna):
            continue
        why = rule.reason(dna)
        for prompt_id in rule.prompts:
            if found.get(prompt_id) is not None and all(c["id"] != prompt_id for c in chosen) and len(chosen) < MAX_RECOMMENDED:
                chosen.append({"id": prompt_id, "why": why})
    return [*chosen, *({"id": prompt_id, "why": _always_reason()} for prompt_id in ALWAYS)]


def _tokens(text: str) -> int:
    return -(-len(text) // 4)


def compose(dna: SiteDNA, picked: list[str] | None = None, *, library: lib.Library | None = None) -> dict[str, Any]:
    """One prompt for the DNA. ``picked=None`` is the recommendation; otherwise exactly those recipes plus ``ALWAYS``. Over the budget the optional
    recipes at the end of the order are dropped and named; ``ALWAYS`` never are, and ``over_budget`` says when that was not enough."""
    found = library if library is not None else lib.get_library()
    by_rule = {c["id"]: c["why"] for c in recommend(dna, found)}
    if picked is None:
        optional = [prompt_id for prompt_id in by_rule if prompt_id not in ALWAYS]
        empty = not optional
    else:
        unknown = [prompt_id for prompt_id in picked if found.get(prompt_id) is None]
        if unknown:
            raise ValueError(f"unknown prompts: {', '.join(unknown)}")
        optional = [prompt_id for prompt_id in dict.fromkeys(picked) if prompt_id not in ALWAYS]
        empty = False
    reasons = {prompt_id: by_rule.get(prompt_id, tr("you picked it")) for prompt_id in optional}
    reasons.update({prompt_id: _always_reason() for prompt_id in ALWAYS})
    values, block, dropped = _pointers(), _dna_block(dna), []

    def build(ids: list[str]) -> str:
        order = [ALWAYS[0], *ids, *ALWAYS[1:]]
        recipes = [lib.render_recipe(prompt, values) for prompt_id in order if (prompt := found.get(prompt_id)) is not None]
        return "\n\n".join([lib.OPENING, block, "## Recipes", *recipes, lib.render_shared(found.shared, values)])

    text = build(optional)
    while _tokens(text) > BUDGET_TOKENS and optional:
        dropped.append(optional.pop())
        text = build(optional)
    used = [ALWAYS[0], *optional, *ALWAYS[1:]]
    return {
        "prompt": text, "used": used, "reasons": [{"id": i, "why": reasons[i]} for i in used], "dropped": dropped,
        "tokens": _tokens(text), "over_budget": _tokens(text) > BUDGET_TOKENS, "recommended_empty": empty,
    }


# -- saving it -----------------------------------------------------------------------------------------------------------


def save(project: Path, host: str, text: str) -> Path:
    """Write the prompt to ``<project>/docs/prompts/<host>-master.md`` and return the path. A file already there is never touched: the next is ``-2``,
    ``-3``, ... The file is UTF-8 with LF line endings, whatever the system's."""
    folder = project / "docs" / "prompts"
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9.-]+", "-", host).strip(".-") or "site"
    data = text.replace("\r\n", "\n").replace("\r", "\n")
    number = 1
    while True:
        path = folder / (f"{name}-master.md" if number == 1 else f"{name}-master-{number}.md")
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:  # "x": the file is created or this fails, so no race overwrites another
                handle.write(data)
            return path
        except FileExistsError:
            number += 1


def needed_packs(prompt_ids: list[str], library: lib.Library | None = None) -> list[str]:
    """The ids of the packs that own the skills the prompts cite, once each and in the order met. A prompt cites skills of any pack: the design
    packs and the picture ones (F03 cites ``playwright-cli``). A prompt the library lacks, or a skill no pack has, adds nothing."""
    found = library if library is not None else lib.get_library()
    out: list[str] = []
    for prompt_id in prompt_ids:
        prompt = found.get(prompt_id)
        for skill in prompt.skills if prompt is not None else ():
            pack = next((p.id for p in PACKS if skill in p.skills), None)
            if pack is not None and pack not in out:
                out.append(pack)
    return out
