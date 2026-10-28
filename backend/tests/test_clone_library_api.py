"""The text of a master prompt on its own, and the routes that list the library and read one prompt.

Libraries are built in ``tmp_path`` with the helpers of ``test_clone_library``. The routes are handed one in place of the library that travels with the
program, the way the program asks for it: ``library.get_library()``, through the module.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.clone import library
from agentfit.clone.library import (
    LIBRARY_VERSION,
    OPENING,
    PLACEHOLDERS,
    SHARED_HEADINGS,
    UNKNOWN,
    Library,
    assemble,
    fill,
    load,
    render_recipe,
    render_shared,
)
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from test_clone_library import PROOF, WITH_MOTION, library_in, ph, put, shared_text, write

# The language of the process follows the PC it runs on: a request that reads the language says which one.
ENGLISH, ITALIAN = {"x-willitrun-lang": "en"}, {"x-willitrun-lang": "it"}
EVERYTHING = " ".join(ph(name) for name in PLACEHOLDERS)


def with_placeholders(root: Path) -> Library:
    """A library whose prompt C01 asks for every value of the set in its Goal, and whose Gates ask for the host."""
    library_in(root)
    put(root / "shared.md", shared_text().replace("The rule of Gates.", f"Study {ph('host')} first."))
    blocks = {"Goal": f"Match {EVERYTHING}.", "Stack": "The stack.", "States": "The states.", "Motion": "The motion.", "Verify": "The checks."}
    write(root, "C01", "smooth-scroll", "motion", blocks=blocks)
    return load(root)


# -- the text of one prompt ---------------------------------------------------------------------------------------------


def test_the_standalone_prompt_holds_the_recipe_then_the_shared_rules_with_every_placeholder_unknown(tmp_path):
    loaded = with_placeholders(tmp_path)
    text = assemble(loaded.get("C01"), loaded.shared)
    opening, _, rest = text.partition("\n\n")
    assert opening == OPENING and "\n" not in OPENING and not OPENING.startswith("#")  # one line to open with, then the recipe
    headings = [line for line in rest.splitlines() if line.startswith("#")]
    assert headings == ["### C01 Title of C01", "#### Goal", "#### Stack", "#### States", "#### Motion", "#### Verify",
                        "## Shared rules", "### Gates", "### Assets", "### Hard lines", "### Verify", "### Deliver"]
    for said in ("The stack.", "The states.", "The motion.", "The checks.", "The rule of Assets.", "The rule of Hard lines.", "The rule of Deliver."):
        assert said in text, said
    # no placeholder is left, and each says it is unknown: the nine of the recipe and the one of the Gates
    assert "{{" not in text and text.count(UNKNOWN) == len(PLACEHOLDERS) + 1
    assert f"Match {' '.join([UNKNOWN] * len(PLACEHOLDERS))}." in text and f"Study {UNKNOWN} first." in text


def test_the_opening_line_sends_the_builder_to_the_gates_before_any_work():
    # Gate 1 asks for the style guide and the spec, then a stop, before any page code: the opening must not read as "build, then the rules".
    assert OPENING == ("Follow the recipe below and the shared rules after it; the gates in the shared rules say in what order to work. "
                       "Take the grammar of the reference, never its content.")


def test_a_value_fills_its_placeholder(tmp_path):
    assert fill("Study {{host}} and {{host}}.", {"host": "recent.design"}) == "Study recent.design and recent.design."
    loaded = with_placeholders(tmp_path)
    text = assemble(loaded.get("C01"), loaded.shared, {"host": "recent.design", "palette": "#0a0a0a"})
    assert "Match recent.design #0a0a0a " + " ".join([UNKNOWN] * (len(PLACEHOLDERS) - 2)) + "." in text  # host and palette are the first two
    assert "Study recent.design first." in text  # the shared rules take the values too
    assert text.count(UNKNOWN) == len(PLACEHOLDERS) - 2


@pytest.mark.parametrize("values", [None, {}, {"host": ""}, {"palette": "#000000"}])
def test_a_placeholder_with_no_value_or_an_empty_one_is_unknown_never_a_default(values):
    assert fill("Host: {{host}}.", values) == f"Host: {UNKNOWN}."


def test_a_value_is_put_in_as_it_is_and_is_never_read_as_a_template():
    value = r"C:\sites\1 \g<0> {{palette}} {{ x }}"
    assert fill("{{host}} / {{palette}}", {"host": value, "palette": "red"}) == f"{value} / red"


def test_double_braces_that_are_not_a_bare_name_are_left_alone_by_fill():
    code = "<div style={{ opacity: 0 }} data-a={{ palette }} data-b={{type-scale}} data-c={{}} />"
    assert fill(code, {"palette": "red", "type": "x"}) == code


def test_a_recipe_is_its_title_then_each_block_under_its_heading_in_the_order_of_the_file(tmp_path):
    library_in(tmp_path)
    blocks = {"Goal": f"Match {ph('host')}.", "Stack": "s", "States": "t", "Motion": "m", "Hard lines": "h", "Verify": "v"}
    write(tmp_path, "C01", "x", "motion", title="Smooth scroll", title_it="Scroll fluido", blocks=blocks)
    prompt = load(tmp_path).get("C01")
    assert render_recipe(prompt, {"host": "a.b"}) == (
        "### C01 Smooth scroll\n\n#### Goal\n\nMatch a.b.\n\n#### Stack\n\ns\n\n#### States\n\nt\n\n#### Motion\n\nm\n\n#### Hard lines\n\nh\n\n#### Verify\n\nv"
    )
    assert render_recipe(prompt).startswith(f"### C01 Smooth scroll\n\n#### Goal\n\nMatch {UNKNOWN}.\n\n")  # the title is the English one: the text goes to a model


def test_the_shared_rules_come_under_one_heading_with_the_gates_first_and_take_the_values(tmp_path):
    loaded = with_placeholders(tmp_path)
    text = render_shared(loaded.shared, {"host": "a.b"})
    assert text.startswith("## Shared rules\n\n### Gates\n\nStudy a.b first.\n\n### Assets\n\nThe rule of Assets.\n\n")
    assert [line for line in text.splitlines() if line.startswith("#")] == ["## Shared rules", *(f"### {heading}" for heading in SHARED_HEADINGS)]
    assert text.endswith("### Deliver\n\nThe rule of Deliver.")
    assert f"Study {UNKNOWN} first." in render_shared(loaded.shared)


def test_assembling_a_prompt_changes_nothing_in_the_library(tmp_path):
    loaded = with_placeholders(tmp_path)
    before = copy.deepcopy((loaded.get("C01").blocks, loaded.shared))
    first = assemble(loaded.get("C01"), loaded.shared, {"host": "a.b"})
    assert assemble(loaded.get("C01"), loaded.shared) != first
    assert (loaded.get("C01").blocks, loaded.shared) == before  # the placeholders are still in the blocks: the text was filled, not the library
    assert assemble(loaded.get("C01"), loaded.shared, {"host": "a.b"}) == first


# -- the routes -------------------------------------------------------------------------------------------------------------


@pytest.fixture
def loaded(tmp_path, monkeypatch) -> Library:
    """Three prompts, one of them tried with a builder, handed to the routes in place of the library that travels with the program."""
    library_in(tmp_path)
    write(tmp_path, "B01", "grid", "gallery")
    write(tmp_path, "C01", "smooth-scroll", "motion", title="Smooth scroll", title_it="Scroll fluido", summary="Lenis with GSAP.",
          summary_it="Lenis con GSAP.", tags=["lenis", "gsap"], size="L", libs={"lenis": "1.3.4"}, skills=["gsap-core"],
          blocks={**dict.fromkeys(WITH_MOTION, "x"), "Goal": f"Match {ph('host')}."})
    write(tmp_path, "F01", "study", "process")
    put(tmp_path / "proofs.json", json.dumps({"C01": PROOF}))
    found = load(tmp_path)
    monkeypatch.setattr(library, "get_library", lambda: found)
    return found


@pytest.fixture
def client(loaded, monkeypatch) -> TestClient:
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    return TestClient(create_app(AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())))


def test_the_list_route_is_not_taken_by_the_study_route(client):
    reply = client.get("/api/clone/prompts")
    assert reply.status_code == 200
    assert reply.json()["version"] == LIBRARY_VERSION and [prompt["id"] for prompt in reply.json()["prompts"]] == ["B01", "C01", "F01"]


def test_the_list_gives_each_prompt_without_its_text_and_with_its_proof_when_it_has_one(client):
    prompts = client.get("/api/clone/prompts", headers=ENGLISH).json()["prompts"]
    assert [prompt["proof"] for prompt in prompts] == [None, PROOF, None]
    assert prompts[1] == {"id": "C01", "group": "motion", "title": "Smooth scroll", "summary": "Lenis with GSAP.", "tags": ["lenis", "gsap"], "size": "L",
                          "skills": ["gsap-core"], "libs": {"lenis": "1.3.4"}, "proof": PROOF}


def test_the_list_is_in_italian_when_the_ui_asks(client):
    italian = client.get("/api/clone/prompts", headers=ITALIAN).json()["prompts"]
    assert [prompt["title"] for prompt in italian] == ["Titolo di B01", "Scroll fluido", "Titolo di F01"]
    assert italian[1]["summary"] == "Lenis con GSAP."
    english = client.get("/api/clone/prompts", headers=ENGLISH).json()["prompts"]
    assert [prompt["title"] for prompt in english] == ["Title of B01", "Smooth scroll", "Title of F01"]


def test_what_the_list_returns_shares_nothing_with_the_library(client, loaded):
    endpoint = next(route.endpoint for route in client.app.routes if getattr(route, "path", "") == "/api/clone/prompts")
    listed = endpoint()
    listed["prompts"][1]["proof"]["widths"].append(1)
    listed["prompts"][1]["libs"]["lenis"] = "0"
    assert loaded.proofs["C01"]["widths"] == PROOF["widths"] and loaded.get("C01").libs == {"lenis": "1.3.4"}


def test_a_prompt_comes_with_its_view_and_its_standalone_text(client, loaded):
    reply = client.get("/api/clone/prompts/C01", headers=ENGLISH)
    assert reply.status_code == 200
    data = reply.json()
    assert data == {**loaded.get("C01").view("en"), "text": assemble(loaded.get("C01"), loaded.shared)}
    assert "### C01 Smooth scroll" in data["text"] and f"Match {UNKNOWN}." in data["text"] and "{{" not in data["text"]


def test_the_text_of_a_prompt_stays_in_english_when_the_ui_is_italian(client):
    data = client.get("/api/clone/prompts/C01", headers=ITALIAN).json()
    assert data["title"] == "Scroll fluido" and data["summary"] == "Lenis con GSAP."
    assert "### C01 Smooth scroll" in data["text"] and "Scroll fluido" not in data["text"] and "Lenis con GSAP" not in data["text"]  # it goes to a model


def test_an_unknown_prompt_is_a_404(client):
    reply = client.get("/api/clone/prompts/Z99", headers=ENGLISH)
    assert reply.status_code == 404 and reply.json()["detail"] == "This master prompt does not exist."


def test_every_prompt_route_is_behind_the_token(client, monkeypatch):
    monkeypatch.setenv("AGENTFIT_TOKEN", "s3cret")
    for path in ("/api/clone/prompts", "/api/clone/prompts/C01", "/api/clone/prompts/Z99"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/clone/prompts", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/clone/prompts/C01", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_the_list_route_serves_the_library_that_travels_with_the_program(monkeypatch):
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    client = TestClient(create_app(AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())))
    data = client.get("/api/clone/prompts", headers=ENGLISH).json()
    assert data["version"] == LIBRARY_VERSION and [prompt["id"] for prompt in data["prompts"]] == [prompt.id for prompt in load().prompts]
