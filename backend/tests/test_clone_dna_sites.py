"""The DNA of four real reference sites, measured on 2026-10-28, against the hand measurements of the design (section 5.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentfit.clone.dna import SiteDNA

FIXTURES = Path(__file__).parent / "fixtures" / "dna"
SITES = ["recent.design", "footer.design", "inspora.design", "sceneai.art"]


def _load(name: str) -> SiteDNA:
    return SiteDNA.model_validate(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def _libs(dna: SiteDNA) -> set[str]:
    return {lib.name.lower() for lib in dna.libs or []}


def _fonts(dna: SiteDNA) -> set[str]:
    return {f.family.lower() for f in dna.tokens.fonts or []}


def test_the_four_reference_dnas_hold_the_facts_measured_by_hand() -> None:
    recent, footer, inspora, scene = (_load(n) for n in SITES)
    # The DOM holds at least the videos seen by hand (the pages load more as they scroll).
    assert recent.motion.videos is not None and recent.motion.videos.count >= 20
    assert recent.motion.videos.loop == recent.motion.videos.count == recent.motion.videos.muted
    assert recent.motion.view_transitions is True
    assert {"inter", "departure mono"} <= _fonts(recent)
    assert not ({"gsap", "lenis"} & _libs(recent)) and recent.motion.smooth_scroll == "none"

    # Hand fact "Lottie" was wrong: no Lottie library or element, only Webflow's runtime hook for one.
    assert footer.framework == "webflow" and "jquery" in _libs(footer) and "lottie" not in _libs(footer)
    assert footer.motion.lottie == 0
    assert footer.motion.videos is not None and footer.motion.videos.count == 0
    assert footer.patterns.chip_filters is True

    assert inspora.framework == "next" and "gsap" in _libs(inspora)
    assert inspora.motion.videos is not None and inspora.motion.videos.count >= 15
    assert inspora.motion.scroll_snap is True and inspora.patterns.chip_filters is True

    assert scene.framework == "next" and scene.motion.smooth_scroll == "lenis"
    assert scene.motion.videos is not None and scene.motion.videos.count >= 10
    assert "geist" in _fonts(scene)

    for dna in (recent, footer, inspora, scene):
        assert dna.motion.canvases == []


def _role(dna: SiteDNA, role: str) -> str | None:
    return next((c.hex for c in dna.tokens.palette or [] if c.role == role), None)


def _section_roles(dna: SiteDNA) -> list[str | None]:
    return [s.role for s in dna.sections or []]


def test_the_sections_are_labelled_by_what_they_are_and_the_galleries_have_their_columns() -> None:
    recent, footer, inspora, scene = (_load(n) for n in SITES)
    # recent.design was "header, gallery, hero" before: the 60px bar of chips is the filters and the grid of 99 pictures is the gallery.
    assert _section_roles(recent) == ["header", "filters", "gallery"]
    assert _section_roles(footer) == ["header", "hero", "gallery", "footer", "content"]
    assert _section_roles(inspora) == ["header", "gallery"]
    assert _section_roles(scene) == ["gallery", "footer"]  # a single big block with a 48px heading and 46 clips repeating: a gallery first
    # Columns at 1440 wide, measured on the live pages (recent.design is four column divs of cards, 276px each beside a 268px sidebar).
    assert [s.columns for s in recent.sections or []] == [None, None, 4]
    assert [s.columns for s in footer.sections or []] == [None, None, 3, None, None]
    assert [s.columns for s in inspora.sections or []] == [None, 4]
    assert [s.columns for s in scene.sections or []] == [4, 3]
    for dna in (recent, footer, inspora, scene):
        assert all(s.columns is not None for s in dna.sections or [] if s.role == "gallery")


def test_the_palette_roles_of_the_four_pages_are_the_colours_a_person_sees() -> None:
    recent, footer, inspora, scene = (_load(n) for n in SITES)
    assert (_role(recent, "background"), _role(recent, "text"), _role(recent, "muted")) == ("#f8f8f8", "#080808", "#808080")
    assert (_role(footer, "background"), _role(footer, "text"), _role(footer, "muted")) == ("#f8f8f8", "#202020", "#707070")
    assert (_role(scene, "background"), _role(scene, "text"), _role(scene, "muted")) == ("#101010", "#f8f8f8", "#a8a8a8")
    assert _role(inspora, "background") == "#f8f8f8" and _role(inspora, "muted") == "#787878"
    assert _role(inspora, "text") == "#101010"  # as stored; the live page can give #202020 (two near inks: the run decides which counts more)
    for dna in (recent, footer, inspora, scene):
        assert _role(dna, "line") is not None
    assert _role(recent, "line") == "#e0e0e0" and _role(scene, "line") == "#383838"


def test_view_transitions_is_a_measured_feature_and_no_page_lists_it_as_a_library() -> None:
    recent, footer, inspora, scene = (_load(n) for n in SITES)
    assert [d.motion.view_transitions for d in (recent, footer, inspora, scene)] == [True, False, True, True]
    for dna in (recent, footer, inspora, scene):
        assert "view transitions" not in _libs(dna)
    assert _libs(recent) == set() and _libs(inspora) == {"gsap"}


def test_a_font_declared_and_not_seen_is_listed_with_no_share() -> None:
    recent = _load("recent.design")
    declared = [f for f in recent.tokens.fonts or [] if f.share == 0.0]
    assert [f.family for f in declared] == ["Departure Mono"]


def _strings(value: Any, key: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _strings(v, k)]
    if isinstance(value, list):
        return [p for v in value for p in _strings(v, key)]
    return [(key, value)] if isinstance(value, str) else []


@pytest.mark.parametrize("name", SITES)
def test_the_fixtures_hold_no_text_and_no_media_addresses(name: str) -> None:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    for key, text in _strings(data):
        if key == "url":
            continue
        assert not text.lower().endswith((".mp4", ".webm", ".png", ".jpg", ".jpeg", ".webp", ".svg")), (key, text)
        assert not text.startswith("http"), (key, text)


def _contrast(dna: SiteDNA, role: str) -> float | None:
    return next((c.contrast for c in dna.tokens.palette or [] if c.role == role), None)


def test_the_contrast_of_the_grey_text_of_the_four_pages_is_the_number_a_checker_gives() -> None:
    recent, footer, inspora, scene = (_load(n) for n in SITES)
    # The muted greys against the background (two runs agreed to the second decimal on all four pages): recent.design's #808080 on #f8f8f8 is the case a builder met.
    assert [_contrast(d, "muted") for d in (recent, footer, inspora, scene)] == [3.72, 4.66, 4.16, 8.0]
    assert recent.tokens.palette is not None
    assert next(c for c in recent.tokens.palette if c.role == "muted").hex == "#808080"
    assert [_contrast(d, "text") for d in (recent, footer, inspora, scene)] == [18.86, 15.34, 17.92, 17.92]
    for dna in (recent, footer, inspora, scene):
        assert _contrast(dna, "background") is None and _contrast(dna, "accent") is None
        assert (_contrast(dna, "line") or 99) < 3  # a hairline border is far under 3:1 on every one of the four pages


def test_the_gallery_of_each_page_says_how_many_cards_how_tall_and_how_far_apart() -> None:
    recent, footer, inspora, scene = (_load(n) for n in SITES)
    gallery = {n: next(s for s in d.sections or [] if s.role == "gallery") for n, d in zip(SITES, (recent, footer, inspora, scene), strict=True)}
    # The fixtures are frozen runs. recent.design loads cards as it is scrolled (a second live run counted 60, section 2758 px), the stored one has 99.
    assert [(g.cards, g.columns, g.card_height_px, g.gap_px) for g in gallery.values()] == [
        (99, 4, 173, 12), (23, 3, 336, 40), (48, 4, 227, 12), (36, 4, 284, 14)]
    assert all((g.cards or 0) >= g.columns for g in gallery.values() if g.columns)  # a card count never falls under the columns it fills
    for dna in (recent, footer, inspora, scene):
        assert all(s.cards is None for s in dna.sections or [] if s.columns is None)
