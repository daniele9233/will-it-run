"""The composer: which recipes a Site DNA calls for and why, the prompt it writes, the budget, and the route.

The four DNAs are the ones measured on real sites (``fixtures/dna``). The library is the one that travels with the program, read through the module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.clone import compose, library
from agentfit.clone.dna import Patterns, SiteDNA, analyze
from agentfit.clone.library import PLACEHOLDERS, UNKNOWN
from agentfit.clone.skills import SkillStore
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.i18n import set_default_language
from test_clone_picture import make_studio
from test_clone_skills import git_that_serves_every_pack
from test_clone_study import RAW, client_of, write_dna

FIXTURES = Path(__file__).parent / "fixtures" / "dna"
SITES = ["recent.design", "footer.design", "inspora.design", "sceneai.art"]


def load_dna(name: str) -> SiteDNA:
    return SiteDNA.model_validate(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def reasons_of(result: dict) -> dict[str, str]:
    return {item["id"]: item["why"] for item in result["reasons"]}


def bare_dna() -> SiteDNA:
    """A DNA that no rule matches."""
    found = analyze(json.loads(RAW.read_text(encoding="utf-8-sig")))
    motion = found.motion.model_copy(update={"videos": None, "smooth_scroll": "none"})
    return found.model_copy(update={"patterns": Patterns(), "motion": motion, "libs": []})


def test_recent_design_recommends_hover_video_and_chip_filters_with_their_reasons():
    dna = load_dna("recent.design")
    result = compose.compose(dna)
    assert result["used"] == ["F01", "B02", "B01", "F03"] and result["recommended_empty"] is False
    reasons = reasons_of(result)
    assert reasons["B02"] == f"{dna.motion.videos.count} videos on the page, most of them muted and looping"
    assert reasons["B01"] == "a row of filter chips above a grid"


def test_sceneai_recommends_the_lenis_recipe_because_lenis_was_found():
    dna = load_dna("sceneai.art")
    assert dna.motion.smooth_scroll == "lenis"
    result = compose.compose(dna)
    assert "C01" in result["used"] and reasons_of(result)["C01"] == "Lenis smooth scroll was found on the page"
    assert "C01" not in compose.compose(load_dna("recent.design"))["used"]  # no Lenis there


def test_a_dna_that_matches_no_rule_recommends_only_the_always_on_prompts_and_says_so():
    dna = bare_dna()
    assert not any(rule.when(dna) for rule in compose.RULES)
    result = compose.compose(dna)
    assert result["recommended_empty"] is True and result["used"] == list(compose.ALWAYS)
    assert [item["id"] for item in compose.recommend(dna)] == list(compose.ALWAYS)


def test_the_always_on_prompts_are_in_every_composition():
    for site in SITES:
        assert {"F01", "F03"} <= set(compose.compose(load_dna(site))["used"])
    picked = compose.compose(load_dna("recent.design"), ["C01"])
    assert picked["used"] == ["F01", "C01", "F03"] and picked["recommended_empty"] is False
    assert reasons_of(picked)["C01"] == "you picked it"
    assert compose.compose(load_dna("recent.design"), ["F03", "B01"])["used"] == ["F01", "B01", "F03"]


def test_a_pick_the_library_does_not_have_is_refused():
    with pytest.raises(ValueError, match="Z99"):
        compose.compose(load_dna("recent.design"), ["Z99"])


def test_unknown_values_render_as_unknown_not_as_defaults():
    dna = bare_dna()
    tokens = dna.tokens.model_copy(update={"palette": None, "fonts": None, "type_scale": None, "spacing": None})
    bare = dna.model_copy(update={"tokens": tokens, "sections": None})
    values = compose.values_of(bare)
    assert set(values) == set(PLACEHOLDERS)
    assert [values[k] for k in ("palette", "fonts", "type_scale", "spacing", "sections")] == [UNKNOWN] * 5
    block = compose.compose(bare)["prompt"].split("## Recipes")[0]
    assert f"- palette: {UNKNOWN}" in block and f"- sections: {UNKNOWN}" in block


def test_the_dna_is_stated_once_and_the_recipes_point_to_it():
    dna = load_dna("recent.design")
    text = compose.compose(dna)["prompt"]
    values = compose.values_of(dna)
    assert text.count("## Site DNA") == 1 and "{{" not in text
    for name in ("palette", "fonts", "motion", "sections"):
        assert text.count(values[name]) == 1, name
    assert "Palette: see Site DNA, palette." in text and "Host: see Site DNA, host." in text  # F01 step 1


def test_a_composition_over_the_budget_drops_the_lowest_priority_recipes_and_says_which(monkeypatch):
    dna = load_dna("recent.design")
    monkeypatch.setattr(compose, "BUDGET_TOKENS", compose.compose(dna, ["B02"])["tokens"])
    result = compose.compose(dna)
    assert result["dropped"] == ["B01"] and result["used"] == ["F01", "B02", "F03"]
    assert result["over_budget"] is False and result["tokens"] <= compose.BUDGET_TOKENS
    assert "### B01" not in result["prompt"] and "### B02" in result["prompt"]


def test_over_budget_is_reported_when_nothing_more_can_be_dropped(monkeypatch):
    monkeypatch.setattr(compose, "BUDGET_TOKENS", 100)
    result = compose.compose(load_dna("recent.design"))
    assert result["dropped"] == ["B01", "B02"] and result["used"] == list(compose.ALWAYS) and result["over_budget"] is True


def test_every_rule_names_a_prompt_that_exists_in_the_library():
    found = library.get_library()
    assert compose.RULES and all(found.get(prompt_id) is not None for rule in compose.RULES for prompt_id in rule.prompts)
    assert all(found.get(prompt_id) is not None for prompt_id in compose.ALWAYS)


def test_a_rule_for_a_prompt_the_library_lacks_recommends_nothing(monkeypatch):
    rule = compose.Rule("x", ("Z99",), lambda dna: True, lambda dna: "never shown")
    monkeypatch.setattr(compose, "RULES", (rule,))
    assert [item["id"] for item in compose.recommend(load_dna("recent.design"))] == list(compose.ALWAYS)


def test_every_reason_has_an_italian_pair():
    english = {site: compose.recommend(load_dna(site)) for site in SITES}
    set_default_language("it")
    try:
        italian = {site: compose.recommend(load_dna(site)) for site in SITES}
        picked = compose.compose(load_dna("recent.design"), ["C01"])
    finally:
        set_default_language("en")
    seen = set()
    for site in SITES:
        for en, it in zip(english[site], italian[site], strict=True):
            assert it["why"] != en["why"], en["why"]
            seen.add(en["why"])
    assert len(seen) >= 4  # the three rules and the always-on reason
    assert reasons_of(picked)["C01"] == "l'hai scelto tu"


def test_the_four_compositions_fit_the_budget():
    for site in SITES:
        result = compose.compose(load_dna(site))
        assert result["tokens"] <= compose.BUDGET_TOKENS and result["over_budget"] is False and result["dropped"] == []


def test_the_dna_block_holds_the_measured_values():
    dna = load_dna("recent.design")
    block = compose.compose(dna)["prompt"].split("## Recipes")[0]
    assert dna.motion.videos is not None and dna.tokens.palette
    assert f"videos {dna.motion.videos.count} (" in block  # the count the fixture measured, not the 20 counted by hand
    assert "Departure Mono" in block and all(color.hex in block for color in dna.tokens.palette)
    assert dna.captured_at[:10] in block and dna.host in block


def test_the_compose_route_returns_the_composition(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    client = client_of(study, monkeypatch)
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    assert client.post(f"/api/clone/{entry['id']}/compose", json={"picked": None}).status_code == 404  # no DNA yet
    written = write_dna(study, entry)
    reply = client.post(f"/api/clone/{entry['id']}/compose", json={"picked": None})
    assert reply.status_code == 200 and reply.json() == compose.compose(written)
    picked = client.post(f"/api/clone/{entry['id']}/compose", json={"picked": ["C01"]}).json()
    assert picked["used"] == ["F01", "C01", "F03"]
    refused = client.post(f"/api/clone/{entry['id']}/compose", json={"picked": ["Z99"]}, headers={"x-willitrun-lang": "it"})
    assert refused.status_code == 400 and refused.json()["detail"] == "Questi master prompt non esistono: Z99"
    assert client.post("/api/clone/unknown/compose", json={}).status_code == 404

# -- saving into the project --------------------------------------------------------------------------------------------


def test_saving_writes_the_prompt_under_docs_prompts(tmp_path):
    path = compose.save(tmp_path, "example.com", "line one\r\nline two\n")
    assert path == tmp_path / "docs/prompts/example.com-master.md"
    assert path.read_bytes() == b"line one\nline two\n"


def test_saving_twice_never_overwrites(tmp_path):
    first = compose.save(tmp_path, "example.com", "one")
    second = compose.save(tmp_path, "example.com", "two")
    third = compose.save(tmp_path, "example.com", "three")
    assert [p.name for p in (first, second, third)] == ["example.com-master.md", "example.com-master-2.md", "example.com-master-3.md"]
    assert first.read_text(encoding="utf-8") == "one" and second.read_text(encoding="utf-8") == "two"


def test_a_host_is_made_safe_as_a_file_name(tmp_path):
    assert compose.save(tmp_path, "../a b:c", "x").parent == tmp_path / "docs/prompts"
    assert compose.save(tmp_path, "", "x").name == "site-master.md"


def test_needed_packs_come_from_any_pack_once_each_in_the_order_met():
    assert compose.needed_packs(["F01", "B02", "F03", "C01"]) == ["motionsites", "playwright", "gsap"]
    assert compose.needed_packs(["F03", "F03", "Z99"]) == ["playwright"]


def save_setup(db, tmp_path, monkeypatch, packs):
    """A studio with one finished study of example.com, a skill store holding ``packs``, and a client whose service uses both."""
    study, manager = make_studio(db, tmp_path)
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    svc = AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())
    svc._clone = study
    store = SkillStore(tmp_path / "store", git=git_that_serves_every_pack([]))
    store.install(packs)
    svc._skills = store
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    return study, entry, TestClient(create_app(svc))


def clone_of(study, entry, host="example.com", project=True, when="2026-01-01T00:00:00"):
    folder = study.home(entry["id"])
    folder.mkdir(parents=True, exist_ok=True)
    if project:
        (folder / "package.json").write_text("{}", encoding="utf-8")
    study.db.kv_set(study.namespace, entry["id"], {**entry, "host": host, "workspace": str(folder), "updated_at": when})
    return folder


def test_the_route_copies_the_cited_skills_to_both_folders_of_the_chosen_project(db, tmp_path, monkeypatch):
    study, entry, client = save_setup(db, tmp_path, monkeypatch, ["motionsites", "playwright"])
    own = study.workspace(entry)
    reply = client.post(f"/api/clone/{entry['id']}/prompts/save", json={"text": "# prompt\n", "used": ["F01", "B02", "F03"], "target": entry["id"]})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["file"] == "docs/prompts/example.com-master.md" and body["folder"] == str(own)
    assert body["skills"] == {"copied": ["motionsites-web-builder", "playwright-cli"], "skipped": [], "missing": [], "failed": []}
    assert (own / body["file"]).read_text(encoding="utf-8") == "# prompt\n"
    for name in ("motionsites-web-builder", "playwright-cli"):
        for folder in (".skills", ".claude/skills"):
            assert (own / folder / name / "SKILL.md").is_file()


def test_a_skill_the_person_already_has_is_reported_skipped_through_the_route(db, tmp_path, monkeypatch):
    study, entry, client = save_setup(db, tmp_path, monkeypatch, ["playwright"])
    own = study.workspace(entry) / ".claude/skills/playwright-cli"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("mine", encoding="utf-8")
    body = client.post(f"/api/clone/{entry['id']}/prompts/save", json={"text": "x", "used": ["F03"], "target": entry["id"]}).json()
    assert body["skills"]["skipped"] == ["playwright-cli"] and body["skills"]["copied"] == []
    assert (own / "SKILL.md").read_text(encoding="utf-8") == "mine" and [p.name for p in own.iterdir()] == ["SKILL.md"]


def test_a_skill_whose_pack_is_not_installed_is_reported_missing(db, tmp_path, monkeypatch):
    study, entry, client = save_setup(db, tmp_path, monkeypatch, ["playwright"])
    body = client.post(f"/api/clone/{entry['id']}/prompts/save", json={"text": "x", "used": ["F03", "B02"], "target": entry["id"]}).json()
    assert body["skills"]["copied"] == ["playwright-cli"] and body["skills"]["missing"] == ["motionsites-web-builder"]
    assert not (study.workspace(entry) / ".claude/skills/motionsites-web-builder").exists()


def test_the_default_target_is_the_latest_clone_of_the_same_host(db, tmp_path, monkeypatch):
    study, entry, client = save_setup(db, tmp_path, monkeypatch, ["playwright"])
    older, _ = study.start_study("https://example.com/")
    newer, _ = study.start_study("https://example.com/")
    other, _ = study.start_study("https://other.org/")
    bare, _ = study.start_study("https://example.com/")
    old_folder = clone_of(study, older, when="2026-01-01T00:00:00")
    new_folder = clone_of(study, newer, when="2026-03-01T00:00:00")
    clone_of(study, other, host="other.org", when="2026-05-01T00:00:00")
    clone_of(study, bare, project=False, when="2026-06-01T00:00:00")
    body = client.post(f"/api/clone/{entry['id']}/prompts/save", json={"text": "x", "used": []}).json()
    assert body["folder"] == str(new_folder) and (new_folder / body["file"]).is_file() and not (old_folder / "docs").exists()
    # With no project of that site at all the study's own folder receives it.
    for e in (older, newer):
        study.db.kv_set(study.namespace, e["id"], {**study.db.kv_get(study.namespace, e["id"]), "host": "gone.example"})
    alone = client.post(f"/api/clone/{entry['id']}/prompts/save", json={"text": "x", "used": []}).json()
    assert alone["folder"] == str(study.workspace(entry))


def test_saving_refuses_empty_text_unknown_prompts_and_unknown_projects(db, tmp_path, monkeypatch):
    _study, entry, client = save_setup(db, tmp_path, monkeypatch, ["playwright"])
    url = f"/api/clone/{entry['id']}/prompts/save"
    assert client.post(url, json={"text": "  ", "used": []}).status_code == 400
    assert client.post(url, json={"text": "x", "used": ["Z99"]}).status_code == 400
    assert client.post(url, json={"text": "x", "used": [], "target": "nope"}).status_code == 404
    assert client.post("/api/clone/nope/prompts/save", json={"text": "x", "used": []}).status_code == 404


def test_the_dna_block_states_the_columns_the_role_colours_and_the_declared_only_fonts():
    raw = json.loads(RAW.read_text(encoding="utf-8-sig"))
    raw["styles"]["font_faces"] = ["Departure Mono"]
    raw["styles"]["border_colors"] = {"#f8f8f8": 90, "#383838": 10}
    values = compose.values_of(analyze(raw))
    assert "gallery 1400px" in values["sections"] and "4 columns at 1440px" in values["sections"]
    assert "#f8f8f8" in values["palette"] and "(45.8%), text, line, " in values["palette"] and "#888888" in values["palette"] and "muted" in values["palette"]
    assert "Departure Mono (declared, not seen in visible text: optional)" in values["fonts"]
    assert "view transitions yes (browser feature, not a library)" in values["motion"]
    assert "View Transitions" not in values["libs"]


def test_a_gallery_with_no_measured_columns_reads_unknown_in_the_block():
    raw = json.loads(RAW.read_text(encoding="utf-8-sig"))
    raw["dom"]["sections"][2]["columns"] = None
    assert "gallery 1400px" in compose.values_of(analyze(raw))["sections"]
    assert "columns Unknown" in compose.values_of(analyze(raw))["sections"]


def test_the_dna_block_reads_the_size_of_a_gallery_and_flags_the_contrast_of_a_grey_text():
    raw = json.loads(RAW.read_text(encoding="utf-8-sig"))
    raw["dom"]["sections"][2].update({"cards": 80, "card_height": 230, "gap": 16})
    values = compose.values_of(analyze(raw))
    assert "3 gallery, 80 cards in 4 columns, rows ~230 px tall, gap 16 px, section 1400 px, heading 48px (3.3vw), 12 img" in values["sections"]
    assert "#888888 " in values["palette"] and "muted, " in values["palette"] and ":1 on the background: " in values["palette"]
    raw["styles"]["backgrounds"] = {"#f8f8f8": 900000}
    raw["styles"]["text_colors"] = {"#080808": 6000, "#808080": 2500}
    assert "#808080" in compose.values_of(analyze(raw))["palette"]
    assert ", not usable as body text as measured" in compose.values_of(analyze(raw))["palette"]
    raw["dom"]["sections"][2]["cards"] = None
    assert "3 gallery 1400px, 4 columns at 1440px" in compose.values_of(analyze(raw))["sections"]


def test_the_block_writes_the_palette_and_fonts_with_the_helpers_of_the_style_guide():
    found = analyze(json.loads(RAW.read_text(encoding="utf-8-sig")))
    from agentfit.clone import dna as dna_module
    values = compose.values_of(found)
    assert values["palette"] == "; ".join(dna_module.palette_text(c) for c in found.tokens.palette or [])
    assert values["fonts"] == "; ".join(dna_module.font_text(f) for f in found.tokens.fonts or [])
    guide = dna_module.style_guide(found)
    assert all(f"- {dna_module.palette_text(c)}" in guide for c in found.tokens.palette or [])
