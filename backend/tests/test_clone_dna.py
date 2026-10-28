"""Site DNA: a raw probe read as facts, a style guide and a comparison. No browser, no network."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from agentfit.clone import dna, pipeline
from agentfit.clone.dna import GLOBAL_LIBS, SiteDNA, analyze, dna_match, limit_text, style_guide
from agentfit.events import EventBus
from agentfit.workflows import checks
from agentfit.workflows.checks import CheckContext
from agentfit.workflows.runner import RunManager
from agentfit.workflows.validator import validate
from fakes import FakeRuntime
from test_clone_replica import FakeEngine

RAW_PATH = Path(__file__).parent / "fixtures" / "dna" / "raw_example.json"


def raw_example() -> dict[str, Any]:
    return json.loads(RAW_PATH.read_text(encoding="utf-8-sig"))


def test_analyze_gives_the_palette_roles_by_rule() -> None:
    dna = analyze(raw_example())
    assert dna.host == "www.example.test"
    palette = {c.hex: c for c in dna.tokens.palette or []}
    assert palette["#0a0a0a"].role == "background"
    assert palette["#f8f8f8"].role == "text"
    assert palette["#ff4d1c"].role == "accent"
    assert all(0 <= c.share <= 1 for c in palette.values())
    assert dna.tokens.type_scale == [14.0, 16.0, 48.0, 96.0]  # 13px holds under 0.5% of the characters
    assert [f.family for f in dna.tokens.fonts or []] == ["Inter", "Playfair Display"]
    assert (dna.tokens.fonts or [])[0].weights == [400, 600, 700]
    assert len(dna.tokens.spacing or []) == 8
    assert dna.tokens.spacing == sorted(dna.tokens.spacing or [])
    assert [s.role for s in dna.sections or []] == ["header", "hero", "gallery", "content", "footer"]
    assert dna.motion.smooth_scroll == "lenis"
    assert dna.motion.view_transitions is True
    assert dna.motion.videos is not None and dna.motion.videos.autoplay == 1


def test_text_muted_and_line_colours_are_palette_roles_measured_from_the_page() -> None:
    palette = {c.hex: c for c in analyze(raw_example()).tokens.palette or []}
    assert palette["#f8f8f8"].role == "text"  # the most used text colour
    assert palette["#888888"].role == "muted"  # the next one, nearer the background than the text is
    assert palette["#383838"].role == "line"  # the most used border colour that is not the background
    assert "#808080" not in palette  # a neighbouring bucket of the muted grey is merged into it
    assert palette["#0a0a0a"].role == "background" and palette["#0a0a0a"].also == []


def test_a_page_that_draws_its_borders_in_its_text_colour_gives_one_colour_two_roles() -> None:
    raw = raw_example()
    raw["styles"]["border_colors"] = {"#f8f8f8": 90, "#383838": 10}
    palette = {c.hex: c for c in analyze(raw).tokens.palette or []}
    assert palette["#f8f8f8"].role == "text" and palette["#f8f8f8"].also == ["line"]
    assert "#f8f8f8 (" in style_guide(analyze(raw)) and ", text, line" in style_guide(analyze(raw))


def test_without_text_or_border_measures_the_muted_and_line_roles_are_none() -> None:
    raw = raw_example()
    raw["styles"]["border_colors"] = None
    raw["styles"]["text_colors"] = {"#f8f8f8": 6000}
    roles = {c.role for c in analyze(raw).tokens.palette or []}
    assert "muted" not in roles and "line" not in roles
    old = raw_example()
    del old["styles"]["border_colors"]  # a raw probe written before the colours were measured
    assert "line" not in {c.role for c in analyze(old).tokens.palette or []}


def test_a_muted_colour_must_sit_nearer_the_background_than_the_text_does() -> None:
    raw = raw_example()
    raw["styles"]["text_colors"] = {"#f8f8f8": 6000, "#0a0a0a": 500, "#e0e0e0": 4000, "#303030": 3000}
    muted = [c.hex for c in analyze(raw).tokens.palette or [] if c.role == "muted"]
    assert muted == ["#303030"]  # on a dark page the dimmer grey is the muted one; #e0e0e0 is brighter than a muted text can be


def test_a_dimmed_caption_that_outnumbers_the_ink_is_muted_and_the_ink_is_the_text() -> None:
    # recent.design: black at 49% (#808080) is the most used text colour, the real black of the headings is the second.
    raw = raw_example()
    raw["styles"]["backgrounds"] = {"#f8f8f8": 9000, "#f0f0f0": 3000}
    raw["styles"]["text_colors"] = {"#808080": 307, "#080808": 165, "#b8b8b8": 21, "#f8f8f8": 139}
    roles = {c.hex: c.role for c in analyze(raw).tokens.palette or []}
    assert roles["#080808"] == "text" and roles["#808080"] == "muted" and roles["#b8b8b8"] is None
    raw["styles"]["text_colors"] = {"#808080": 307, "#080808": 40}  # a little black does not take the text role from the main colour
    roles = {c.hex: c.role for c in analyze(raw).tokens.palette or []}
    assert roles["#808080"] == "text" and roles["#080808"] != "text"


def test_the_same_grey_in_two_neighbouring_buckets_is_one_colour_not_a_text_and_a_muted() -> None:
    raw = raw_example()
    raw["styles"]["backgrounds"] = {"#f8f8f8": 9000}
    raw["styles"]["text_colors"] = {"#808080": 300, "#787878": 250, "#080808": 260, "#b8b8b8": 30}
    raw["styles"]["border_colors"] = {"#e0e0e0": 200, "#d8d8d8": 40}
    palette = {c.hex: c for c in analyze(raw).tokens.palette or []}
    assert palette["#080808"].role == "text" and palette["#808080"].role == "muted"
    assert "#787878" not in palette and "#d8d8d8" not in palette
    assert palette["#e0e0e0"].role == "line"


def section(**kw: Any) -> dict[str, Any]:
    base = {"index": 1, "tag": "div", "height": 700, "heading_px": None, "heading_vw": None, "img": 0, "video": 0, "canvas": 0,
            "svg": 0, "repeat": 0, "columns": None, "controls": 0, "chips": 0, "first_video_autoplay": False}
    return {**base, **kw}


def roles_of(*sections: dict[str, Any]) -> list[str | None]:
    raw = raw_example()
    raw["dom"]["sections"] = [{**s, "index": i + 1} for i, s in enumerate(sections)]
    return [s.role for s in analyze(raw).sections or []]


def test_a_grid_with_many_repeats_is_a_gallery_not_the_hero_and_a_short_bar_of_chips_is_filters() -> None:
    # The shape of recent.design: a thin header, a 60px bar of chips, then 4600px of cards with videos and a small heading.
    assert roles_of(
        section(tag="header", height=28), section(height=60, chips=8, repeat=8, svg=1),
        section(height=4605, img=99, video=85, repeat=17, heading_px=13, columns=3),
    ) == ["header", "filters", "gallery"]


def test_the_hero_is_the_first_content_section_with_a_big_heading_or_a_screen_sized_video() -> None:
    assert roles_of(section(tag="header", height=80), section(heading_px=96, height=900), section(heading_px=96)) == ["header", "hero", "content"]
    assert roles_of(section(height=800, video=1), section(height=800, video=1)) == ["hero", "content"]  # only the first can be one
    assert roles_of(section(height=4000, video=3)) == ["content"]  # a video section taller than 1.5 screens is not a hero
    assert roles_of(section(height=600, heading_px=20), section(heading_px=96)) == ["content", "content"]  # a big heading lower down is not one
    assert roles_of(section(height=900, canvas=1), section(height=300)) == ["hero", "content"]


def test_a_short_bar_with_a_picture_or_few_controls_is_not_a_filter_bar() -> None:
    assert roles_of(section(height=100, controls=2)) == ["content"]
    assert roles_of(section(height=100, controls=5, img=2)) == ["content"]
    assert roles_of(section(height=200, controls=5)) == ["content"]
    assert roles_of(section(height=100, controls=3)) == ["filters"]


def test_the_columns_of_a_section_are_carried_and_unknown_stays_none() -> None:
    sections = analyze(raw_example()).sections or []
    assert [s.columns for s in sections] == [None, None, 4, None, None]
    text = style_guide(analyze(raw_example()))
    assert "Section 3: gallery, 1400px high, heading 48.0px, 4 columns at 1440px" in text
    raw = raw_example()
    raw["dom"]["sections"][2]["columns"] = None
    assert "Section 3: gallery, 1400px high, heading 48.0px, columns Unknown" in style_guide(analyze(raw))


def test_view_transitions_is_a_measured_browser_feature_and_not_a_library() -> None:
    parsed = analyze(raw_example())
    assert parsed.motion.view_transitions is True
    assert "View Transitions" not in {lib.name for lib in parsed.libs}
    assert "- View transitions: yes (a feature of the browser, not a library)" in style_guide(parsed)
    assert "View Transitions" not in style_guide(parsed).split("## Libraries")[1]
    other = raw_example()
    del other["signature_hits"]["view-transitions"]
    other["styles"]["counts"]["view_transition_rules"] = 2  # a stylesheet that names it counts too
    assert analyze(other).motion.view_transitions is True
    other["styles"]["counts"]["view_transition_rules"] = 0
    assert analyze(other).motion.view_transitions is False
    other["styles"]["counts"] = None
    other["signature_hits"] = None
    assert analyze(other).motion.view_transitions is None


def test_two_sites_that_differ_only_in_view_transitions_still_share_all_their_libraries() -> None:
    a = analyze(raw_example())
    other = raw_example()
    del other["signature_hits"]["view-transitions"]
    assert dna_match(a, analyze(other))["parts"]["libs"] == 1.0


def test_an_unmeasured_field_stays_none_and_is_not_defaulted() -> None:
    raw = raw_example()
    raw["styles"]["fonts"] = None
    raw["styles"]["container_px"] = None
    raw["probes"]["hover"] = None
    dna = analyze(raw)
    assert dna.tokens.fonts is None
    assert dna.tokens.container_px is None
    assert dna.probes.hover is None


def test_a_page_with_no_sections_leaves_them_empty_and_says_so() -> None:
    raw = raw_example()
    raw["dom"]["sections"] = []
    dna = analyze(raw)
    assert dna.sections == []
    assert [x.code for x in dna.limits].count("no_sections") == 1
    assert "no readable sections" in style_guide(dna)


def test_a_signature_hit_becomes_a_detected_library_with_its_evidence() -> None:
    dna = analyze(raw_example())
    libs = {lib.name: lib.evidence for lib in dna.libs}
    assert libs["GSAP"] == ["a.js", "main.js", "window.gsap"]
    assert libs["Lenis"] == ["window.Lenis"]
    assert libs["jQuery"] == ["window.jQuery"]
    assert GLOBAL_LIBS["jQuery"] == "jquery"


def test_the_style_guide_says_unknown_for_what_was_not_measured() -> None:
    raw = raw_example()
    raw["styles"]["fonts"] = None
    raw["styles"]["container_px"] = None
    raw["dom"]["videos"] = None
    text = style_guide(analyze(raw))
    assert "- Fonts: Unknown" in text
    assert "- Container (px): Unknown" in text
    assert "- Videos: Unknown" in text
    for heading in ("Palette", "Type", "Spacing", "Layout", "Motion", "Libraries", "Patterns", "Limits"):
        assert f"## {heading}" in text


def test_dna_match_is_one_for_the_same_dna_and_lower_for_a_different_palette() -> None:
    a = analyze(raw_example())
    assert dna_match(a, a)["overall"] == 1.0
    other = raw_example()
    other["styles"]["backgrounds"] = {"#2a6fe0": 800000, "#fcf0a8": 200000}
    other["styles"]["text_colors"] = {"#fcf0a8": 6000}
    b = analyze(other)
    match = dna_match(a, b)
    assert match["parts"]["palette"] < 0.5
    assert match["overall"] < 1.0


def test_dna_match_says_what_it_does_not_cover() -> None:
    a = analyze(raw_example())
    result = dna_match(a, a)
    assert result["not_covered"] == ["layout", "text", "the feel of the motion"]
    bare = copy.deepcopy(raw_example())
    bare["styles"] = None
    bare["dom"] = None
    empty = analyze(bare)
    assert dna_match(empty, empty)["parts"]["palette"] is None


@pytest.mark.parametrize(
    "code",
    ["script_unreadable", "no_sections", "scroll_probe_skipped", "hover_probe_skipped",
     "stylesheet_unreadable", "visit_slow"],
)
def test_limit_text_exists_for_every_limit_code(code: str) -> None:
    text = limit_text(code, 3)
    assert text and not text.startswith("limit ")
    assert "{n}" not in text


def test_the_dna_is_a_valid_model_round_trip() -> None:
    dna = analyze(raw_example())
    assert SiteDNA.model_validate_json(dna.model_dump_json()) == dna


# -- the study as a run ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path, monkeypatch):
    install = FakeEngine(tmp_path / "engine")
    monkeypatch.setattr(pipeline, "install_source", lambda: install)
    monkeypatch.setattr(pipeline, "find_browser", lambda: {"name": "Chrome", "executable": "chrome.exe"})
    return install


def study_ctx(workspace: Path) -> CheckContext:
    return CheckContext(task="t", outputs={}, ledger=[], history=[], definition=dna.definition(url="https://www.example.test/"),
                        workspace=workspace, emit=lambda kind, payload: None, cancelled=lambda: False, complete=None)


def test_a_workflow_with_a_check_and_no_agent_is_valid_and_one_with_neither_is_not() -> None:
    definition = dna.definition(url="https://www.example.test/")
    assert definition.agents == [] and validate(definition, require_models=True).ok
    empty = definition.model_copy(update={"checks": [], "edges": []})
    assert "A workflow needs at least one agent or check." in [i.message for i in validate(empty).errors]


def test_a_study_writes_the_dna_the_style_guide_and_the_thumbnail(tmp_path, engine, monkeypatch) -> None:
    def fake(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
        out = Path(config["out"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "dna-raw.json").write_text(RAW_PATH.read_text(encoding="utf-8-sig"), encoding="utf-8")
        (out / "thumb.jpg").write_bytes(b"1")
        return True, ""

    monkeypatch.setattr(dna, "_script", fake)
    check = dna.definition(url="https://www.example.test/").checks[0]
    result = dna._clone_dna(check, study_ctx(tmp_path))
    folder = dna.dna_dir(tmp_path, "example.test")
    assert result["passed"] is True and result["verdict"] == "STUDIED" and result["dna"]["host"] == "www.example.test"
    assert SiteDNA.model_validate_json((folder / "dna.json").read_text(encoding="utf-8")).host == "www.example.test"
    assert (folder / "style_guide.md").read_text(encoding="utf-8").startswith("# Style guide:")
    assert (folder / "thumb.jpg").exists()


def test_a_study_that_the_site_refuses_ends_with_what_the_site_answered(tmp_path, engine, monkeypatch) -> None:
    monkeypatch.setattr(dna, "_script", lambda ctx, config: (False, "HTTP 403 Forbidden"))
    check = dna.definition(url="https://www.example.test/").checks[0]
    result = dna._clone_dna(check, study_ctx(tmp_path))
    assert result["passed"] is False and result["verdict"] == "STUDY_FAILED" and "403" in result["summary"]
    assert not (dna.dna_dir(tmp_path, "example.test") / "dna.json").exists()


def test_the_study_check_is_registered_and_not_listed() -> None:
    assert "clone_dna" in checks.CHECKS and not checks.CHECKS["clone_dna"].listed


def test_a_study_runs_with_no_agent_and_no_model(db, tmp_path, engine, monkeypatch) -> None:
    def fake(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
        out = Path(config["out"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "dna-raw.json").write_text(RAW_PATH.read_text(encoding="utf-8-sig"), encoding="utf-8")
        return True, ""

    monkeypatch.setattr(dna, "_script", fake)
    manager = RunManager(db, FakeRuntime({"*": ["ok"]}), bus=EventBus(), checkpoint_path=":memory:")
    record = manager.start(dna.definition(url="https://www.example.test/"), "study", workspace=str(tmp_path / "ws"))
    manager.wait(record.id, 30)
    assert manager.get(record.id).status == "completed"
    assert (dna.dna_dir(tmp_path / "ws", "example.test") / "dna.json").exists()


def test_a_font_the_page_declares_but_shows_no_text_in_is_listed_with_a_zero_share() -> None:
    raw = raw_example()
    raw["styles"]["font_faces"] = ["Departure Mono", "Inter Fallback", next(iter(raw["styles"]["fonts"])).upper()]
    fonts = analyze(raw).tokens.fonts
    assert fonts is not None
    declared = [f for f in fonts if f.share == 0.0]
    assert [f.family for f in declared] == ["Departure Mono"]
    assert declared[0].weights == []
    text = style_guide(analyze(raw))
    assert "- Departure Mono (declared, not seen in visible text: optional)" in text
    assert "Departure Mono (0" not in text


def test_contrast_ratio_is_the_wcag_ratio_and_none_for_what_is_not_a_hex_colour() -> None:
    assert dna.contrast_ratio("#000000", "#ffffff") == 21.0
    assert dna.contrast_ratio("#ffffff", "#ffffff") == 1.0
    assert dna.contrast_ratio("#808080", "#f8f8f8") == 3.72  # the builder's measured case: 3.7:1
    assert dna.contrast_ratio("#808080", "#ffffff") == 3.95  # the textbook value for mid grey on white
    assert dna.contrast_ratio("red", "#ffffff") is None and dna.contrast_ratio("#fff", "#ffffff") is None


def test_the_text_muted_and_line_colours_carry_their_contrast_on_the_background_and_no_other_does() -> None:
    palette = {c.hex: c for c in analyze(raw_example()).tokens.palette or []}
    assert palette["#0a0a0a"].contrast is None and palette["#ff4d1c"].contrast is None  # background and accent: no ratio
    assert palette["#f8f8f8"].contrast == dna.contrast_ratio("#f8f8f8", "#0a0a0a")
    assert palette["#888888"].contrast == dna.contrast_ratio("#888888", "#0a0a0a")
    assert palette["#383838"].contrast == dna.contrast_ratio("#383838", "#0a0a0a")
    text = style_guide(analyze(raw_example()))
    assert "#f8f8f8" in text and ":1 on the background: meets 4.5:1" in text
    assert ":1 on the background: below 3:1" in text  # the dark border line


def test_a_grey_text_below_4_5_to_1_is_flagged_with_the_measured_number_and_nothing_more() -> None:
    raw = raw_example()
    raw["styles"]["backgrounds"] = {"#f8f8f8": 900000}
    raw["styles"]["text_colors"] = {"#080808": 6000, "#808080": 2500}
    raw["styles"]["border_colors"] = {"#e0e0e0": 10}
    palette = {c.hex: c for c in analyze(raw).tokens.palette or []}
    muted = palette["#808080"]
    assert muted.role == "muted" and muted.contrast == dna.contrast_ratio("#808080", "#f8f8f8")
    assert 3.0 <= muted.contrast < 4.5
    assert dna.contrast_text(muted) == f"{int(muted.contrast * 10) / 10:.1f}:1 on the background: below 4.5:1, not usable as body text as measured; meets 3:1 (large text)"
    assert palette["#e0e0e0"].role == "line" and dna.contrast_text(palette["#e0e0e0"]).endswith(": below 3:1")
    assert "#808080 (" in style_guide(analyze(raw)) and ", muted, " in style_guide(analyze(raw))
    low = muted.model_copy(update={"contrast": 2.5})
    assert dna.contrast_text(low) == "2.5:1 on the background: below 4.5:1 and below 3:1, not usable as text as measured"
    assert dna.contrast_text(muted.model_copy(update={"contrast": 4.49})).startswith("4.4:1 ")  # cut, never rounded up to the threshold
    assert dna.contrast_text(muted.model_copy(update={"contrast": None})) == ""


def test_without_a_background_no_colour_has_a_contrast() -> None:
    raw = raw_example()
    raw["styles"]["backgrounds"] = None
    assert all(c.contrast is None for c in analyze(raw).tokens.palette or [])


def test_a_gallery_carries_its_cards_their_height_and_the_gap_and_the_style_guide_reads_them() -> None:
    raw = raw_example()
    gallery = raw["dom"]["sections"][2]
    gallery.update({"cards": 80, "card_height": 230, "gap": 16})
    section = (analyze(raw).sections or [])[2]
    assert (section.cards, section.card_height_px, section.gap_px, section.columns) == (80, 230, 16, 4)
    assert "Section 3: gallery, 1400px high, heading 48.0px, 80 cards in 4 columns, rows ~230 px tall, gap 16 px" in style_guide(analyze(raw))
    gallery.update({"gap": None, "card_height": None})
    section = (analyze(raw).sections or [])[2]
    assert section.card_height_px is None and section.gap_px is None
    assert "80 cards in 4 columns\n" in style_guide(analyze(raw))  # nothing invented for what was not measured
    other = (analyze(raw_example()).sections or [])
    assert all(s.cards is None and s.card_height_px is None and s.gap_px is None for s in other)  # a probe with no such fields: unknown
    assert dna.layout_text(other[0], 1440) == ""


def test_a_top_menu_of_links_is_not_a_filter_bar_and_a_row_with_evidence_or_above_a_grid_is() -> None:
    menu = section(height=60, chips=5, chips_evidence=False)  # a nav div of five links: the probe counted the row, nothing shows it filters
    assert roles_of(menu, section(height=900, heading_px=64)) == ["content", "hero"]
    assert roles_of(section(height=60, chips=5, chips_evidence=True), section(height=900)) == ["filters", "content"]  # buttons, tabs, aria-pressed
    assert roles_of(menu, section(height=3000, img=20, repeat=20)) == ["filters", "gallery"]  # directly above a repeated grid
    assert roles_of(section(height=60, chips=5), section(height=900)) == ["content", "content"]  # a probe with no evidence field: not filters
    assert roles_of(section(height=60, controls=3)) == ["filters"]  # three real controls need no further evidence


def test_view_transitions_is_unknown_unless_both_the_scripts_and_the_rules_were_read() -> None:
    from agentfit.clone.dna import _view_transitions
    assert _view_transitions(None, None) is None
    assert _view_transitions(None, 0) is None  # scripts unreadable: a missing rule proves nothing
    assert _view_transitions({}, None) is None  # rules unreadable
    assert _view_transitions(None, 2) is True and _view_transitions({"view-transitions": ["a.js"]}, None) is True
    assert _view_transitions({}, 0) is False and _view_transitions({"gsap": ["a.js"]}, 0) is False
    other = raw_example()
    other["signature_hits"] = None
    other["styles"]["counts"]["view_transition_rules"] = 0
    assert analyze(other).motion.view_transitions is None


def test_a_thin_strip_of_links_above_the_hero_does_not_take_the_hero_s_place() -> None:
    menu = section(height=60, chips=4, chips_evidence=False)
    assert roles_of(menu, section(height=900, heading_px=64)) == ["content", "hero"]
    assert roles_of(section(tag="header", height=80), menu, section(height=900, video=1)) == ["header", "content", "hero"]
    assert roles_of(section(height=300, heading_px=20), section(height=900, heading_px=64)) == ["content", "content"]  # a tall block still counts
    assert roles_of(section(height=60, img=1), section(height=900, heading_px=64)) == ["content", "content"]  # a strip with a picture counts
