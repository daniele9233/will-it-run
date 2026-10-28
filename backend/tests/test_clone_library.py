"""The library of master prompts: Markdown files that are checked when they are loaded.

Libraries are built in ``tmp_path``; only the last tests read the folder that travels with the program.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentfit.clone import library
from agentfit.clone.library import (
    GROUPS,
    PLACEHOLDERS,
    RECIPE_MAX_CHARS,
    SHARED_HEADINGS,
    SHARED_MAX_CHARS,
    Library,
    LibraryError,
    content_hash,
    fill,
    get_library,
    load,
    parse_prompt,
)

STANDARD = ("Goal", "Stack", "States", "Verify")
WITH_MOTION = ("Goal", "Stack", "States", "Motion", "Verify")
BLOCKS_OF = {
    "pages": STANDARD, "gallery": STANDARD, "after-copy": STANDARD,
    "motion": WITH_MOTION, "surfaces": WITH_MOTION, "three-d": WITH_MOTION,
    "process": ("Goal", "Steps", "Verify"),
}
PROOF = {"builder": "Claude Code", "date": "2026-10-28", "build_ok": True, "widths": [390, 768, 1440], "dna_match": 0.71,
         "notes": "Tested inside a composed prompt with F01."}


BOM = chr(0xFEFF)  # the byte order mark, as the character a decoder that does not know it would leave in the text


def ph(name: str) -> str:
    """A placeholder as a prompt writes it."""
    return "{{" + name + "}}"


def body_of(blocks: dict[str, str]) -> str:
    return "\n\n".join(f"## {heading}\n\n{text}" for heading, text in blocks.items())


def prompt_text(prompt_id: str, slug: str, group: str, /, *, blocks: dict[str, str] | None = None, body: str | None = None, **header: Any) -> str:
    """The text of a prompt file. ``header`` replaces keys of the front matter (``None`` leaves the key out); ``blocks`` or ``body`` replace the default body."""
    fields: dict[str, Any] = {
        "id": prompt_id, "slug": slug, "group": group, "title": f"Title of {prompt_id}", "title_it": f"Titolo di {prompt_id}",
        "summary": f"What {prompt_id} gives.", "summary_it": f"Cosa dà {prompt_id}.", "size": "M", "tags": ["one", "two"],
        "libs": {"lenis": "1.3.4"}, "skills": ["gsap-core"],
    }
    fields.update(header)
    front = yaml.safe_dump({key: value for key, value in fields.items() if value is not None}, allow_unicode=True, sort_keys=False)
    if body is None:
        body = body_of(blocks if blocks is not None else {name: f"The {name} of {prompt_id}." for name in BLOCKS_OF[group]})
    return f"---\n{front}---\n\n{body}\n"


def put(path: Path, text: str, *, bom: bool = False, crlf: bool = False) -> Path:
    """A file with exactly the bytes asked for (``write_text`` would turn every line end into the platform's)."""
    data = text.replace("\n", "\r\n") if crlf else text
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data.encode("utf-8"))
    return path


def shared_text(headings: tuple[str, ...] = SHARED_HEADINGS) -> str:
    return "\n".join(f"## {heading}\n\nThe rule of {heading}.\n" for heading in headings)


def library_in(root: Path, *, bom: bool = False, crlf: bool = False) -> Path:
    """A folder with the shared rules and no proof; the prompts are added with ``write``."""
    put(root / "shared.md", shared_text(), bom=bom, crlf=crlf)
    put(root / "proofs.json", "{}\n", bom=bom, crlf=crlf)
    return root


def write(root: Path, prompt_id: str, slug: str, group: str, /, *, filename: str | None = None, bom: bool = False, crlf: bool = False, **kw: Any) -> Path:
    return put(root / (filename or f"{prompt_id}-{slug}.md"), prompt_text(prompt_id, slug, group, **kw), bom=bom, crlf=crlf)


def problems_of(text: str, filename: str) -> list[str]:
    """What ``parse_prompt`` refuses a file for."""
    with pytest.raises(LibraryError) as caught:
        parse_prompt(text, filename)
    return caught.value.problems


def load_problems(root: Path) -> list[str]:
    """What ``load`` refuses a library for."""
    with pytest.raises(LibraryError) as caught:
        load(root)
    return caught.value.problems


# -- the ten behaviours of the plan -------------------------------------------------------------------------------------


def test_a_valid_prompt_loads_with_its_blocks_in_order(tmp_path):
    library_in(tmp_path)
    write(tmp_path, "C01", "smooth-scroll", "motion", libs={"lenis": "1.3.4", "gsap": "3.13.0"}, skills=["gsap-scrolltrigger", "gsap-react"],
          tags=["lenis", "gsap"], size="L")
    write(tmp_path, "F01", "study", "process")
    loaded = load(tmp_path)
    assert isinstance(loaded, Library)
    assert [p.id for p in loaded.prompts] == ["C01", "F01"]
    first = loaded.prompts[0]
    assert (first.id, first.slug, first.group, first.size) == ("C01", "smooth-scroll", "motion", "L")
    assert (first.title, first.title_it) == ("Title of C01", "Titolo di C01")
    assert (first.summary, first.summary_it) == ("What C01 gives.", "Cosa dà C01.")
    assert first.tags == ("lenis", "gsap") and first.skills == ("gsap-scrolltrigger", "gsap-react")
    assert first.libs == {"lenis": "1.3.4", "gsap": "3.13.0"}
    assert list(first.blocks) == ["Goal", "Stack", "States", "Motion", "Verify"]
    assert first.blocks["Goal"] == "The Goal of C01."
    assert list(loaded.prompts[1].blocks) == ["Goal", "Steps", "Verify"]
    assert tuple(loaded.shared) == SHARED_HEADINGS and loaded.proofs == {}


def test_every_problem_is_reported_not_only_the_first(tmp_path):
    library_in(tmp_path)
    write(tmp_path, "B01", "one", "gallery", blocks={**dict.fromkeys(STANDARD, "x"), "Notes": "a block the template does not have"})
    write(tmp_path, "C01", "two", "motion", skills=["nope"])
    write(tmp_path, "F01", "three", "process", body=f"## Goal\n\nUse {ph('colour')}.\n\n## Steps\n\nx\n\n## Verify\n\nx")
    problems = load_problems(tmp_path)
    assert len(problems) == 3
    assert [line.split(":")[0] for line in problems] == ["B01-one.md", "C01-two.md", "F01-three.md"]
    with pytest.raises(LibraryError) as caught:
        load(tmp_path)
    assert str(caught.value).splitlines() == problems  # one problem to a line, and nothing else in the message
    # a single file with two faults reports both
    both = prompt_text("C01", "x", "motion", skills=["nope"], blocks={**dict.fromkeys(WITH_MOTION, "x"), "Goal": ph("colour")})
    assert len(problems_of(both, "C01-x.md")) == 2


def test_the_id_must_match_the_file_name_and_the_group_letter():
    good = prompt_text("C01", "x", "motion")
    assert parse_prompt(good, "C01-x.md").id == "C01"
    # the file is named <id>-<slug>.md, whatever folder the path names
    assert parse_prompt(good, str(Path("some") / "folder" / "C01-x.md")).id == "C01"
    for wrong in ("C01-other.md", "C02-x.md", "x.md", "C01-x.txt"):
        found = problems_of(good, wrong)
        assert len(found) == 1 and "C01-x.md" in found[0], wrong
    # C is the letter of motion, not of pages
    found = problems_of(prompt_text("C01", "x", "pages"), "C01-x.md")
    assert len(found) == 1 and "motion" in found[0] and "pages" in found[0]
    # the id is a letter from A to G and two digits
    for bad_id in ("H01", "C1", "C001", "c01", "01C"):
        found = problems_of(prompt_text(bad_id, "x", "motion"), f"{bad_id}-x.md")
        assert len(found) == 1 and bad_id in found[0], bad_id
    # every letter has its group
    for letter, group in GROUPS.items():
        assert parse_prompt(prompt_text(f"{letter}05", "x", group), f"{letter}05-x.md").group == group
    found = problems_of(prompt_text("C01", "x", "videos", blocks=dict.fromkeys(WITH_MOTION, "x")), "C01-x.md")
    assert len(found) == 1 and "videos" in found[0]


def test_required_blocks_depend_on_the_group():
    # a process prompt needs Goal, Steps and Verify, and no States
    process = prompt_text("F01", "x", "process")
    assert list(parse_prompt(process, "F01-x.md").blocks) == ["Goal", "Steps", "Verify"]
    found = problems_of(prompt_text("F01", "x", "process", blocks=dict.fromkeys(STANDARD, "x")), "F01-x.md")
    assert len(found) == 1 and "Steps" in found[0]
    # every other group needs Goal, Stack, States and Verify
    for missing in STANDARD:
        blocks = {name: "x" for name in STANDARD if name != missing}
        found = problems_of(prompt_text("B01", "x", "gallery", blocks=blocks), "B01-x.md")
        assert len(found) == 1 and missing in found[0], missing
    # Motion is needed by motion, surfaces and three-d, and by A01 to A04
    without_motion = dict.fromkeys(STANDARD, "x")
    for prompt_id, group in (("C01", "motion"), ("D01", "surfaces"), ("E01", "three-d"), ("A01", "pages"), ("A04", "pages")):
        found = problems_of(prompt_text(prompt_id, "x", group, blocks=without_motion), f"{prompt_id}-x.md")
        assert len(found) == 1 and "Motion" in found[0], prompt_id
    # and may be left out elsewhere
    for prompt_id, group in (("A05", "pages"), ("B01", "gallery"), ("G01", "after-copy")):
        assert "Motion" not in parse_prompt(prompt_text(prompt_id, "x", group, blocks=without_motion), f"{prompt_id}-x.md").blocks
    # Hard lines is never required, and a heading the template does not have is refused, spelled differently or not
    with_hard_lines = {**without_motion, "Hard lines": "x"}
    assert list(parse_prompt(prompt_text("B01", "x", "gallery", blocks=with_hard_lines), "B01-x.md").blocks) == [*STANDARD, "Hard lines"]
    for heading in ("Notes", "Hard Lines", "goal"):
        found = problems_of(prompt_text("B01", "x", "gallery", blocks={**without_motion, heading: "x"}), "B01-x.md")
        assert len(found) == 1 and heading in found[0], heading


def test_an_unknown_placeholder_or_an_oversized_recipe_is_refused():
    blocks = dict.fromkeys(STANDARD, "x")
    # every placeholder of the closed set is welcome
    everything = {**blocks, "Goal": " ".join(ph(name) for name in PLACEHOLDERS)}
    goal = parse_prompt(prompt_text("B01", "x", "gallery", blocks=everything), "B01-x.md").blocks["Goal"]
    assert all(ph(name) in goal for name in PLACEHOLDERS)
    # a name outside the set is refused wherever it is, and each is named once
    stray = {**blocks, "Verify": f"Check {ph('colour')} and {ph('host')} and {ph('Host')} and {ph('colour')}.", "Goal": ph("palette")}
    found = problems_of(prompt_text("B01", "x", "gallery", blocks=stray), "B01-x.md")
    assert len(found) == 1 and ph("colour") in found[0] and ph("Host") in found[0]
    assert found[0].count(ph("colour")) == 1 and ph("host") not in found[0] and ph("palette") not in found[0]
    # the size of the body: the limit itself loads, one character more does not
    pad = RECIPE_MAX_CHARS - len(body_of(blocks))
    exact = {**blocks, "Goal": "x" * (1 + pad)}
    assert len(body_of(exact)) == RECIPE_MAX_CHARS
    parse_prompt(prompt_text("B01", "x", "gallery", blocks=exact), "B01-x.md")
    over = {**blocks, "Goal": "x" * (2 + pad)}
    found = problems_of(prompt_text("B01", "x", "gallery", blocks=over), "B01-x.md")
    assert len(found) == 1 and str(RECIPE_MAX_CHARS + 1) in found[0]


def test_a_skill_no_pack_has_and_a_template_version_are_refused():
    names = ["gsap-scrolltrigger", "gsap-react", "taste-skill", "motionsites-web-builder", "impeccable"]  # folder names of the packs, design ones too
    good = parse_prompt(prompt_text("C01", "x", "motion", skills=names, libs={"lenis": "1.3.4", "gsap": "3.13.0"}), "C01-x.md")
    assert good.skills == tuple(names) and good.libs == {"lenis": "1.3.4", "gsap": "3.13.0"}
    # a skill is cited by the folder name of its pack, not by the name in its header (taste-skill's header says design-taste-frontend)
    for wrong in (["nope"], ["gsap-react", "nope"], ["design-taste-frontend"], ["GSAP-core"]):
        found = problems_of(prompt_text("C01", "x", "motion", skills=wrong), "C01-x.md")
        assert len(found) == 1 and wrong[-1] in found[0], wrong
    assert "gsap-react" not in problems_of(prompt_text("C01", "x", "motion", skills=["gsap-react", "nope"]), "C01-x.md")[0]
    # a version is looked up and written down: not a template, not a range, not empty, not a number YAML would round
    for version in ("<version>", "<version looked up when written>", ">=1.0", "latest>", "", None, 1.3):
        found = problems_of(prompt_text("C01", "x", "motion", libs={"lenis": version}), "C01-x.md")
        assert len(found) == 1 and "lenis" in found[0], version
    assert len(problems_of(prompt_text("C01", "x", "motion", libs={"lenis": "<v>", "gsap": ""}), "C01-x.md")) == 2


def test_files_with_a_bom_and_windows_line_endings_load_and_hash_the_same(tmp_path):
    blocks = {"Goal": "Line one.\nLine two.", "Stack": "```ts\nconst a = 1;\n\nconst b = 2;\n```", "States": "x", "Motion": "x", "Verify": "x"}
    folders = {"plain": {}, "windows": {"bom": True, "crlf": True}}
    for name, flags in folders.items():
        folder = tmp_path / name
        folder.mkdir()
        library_in(folder, **flags)
        write(folder, "C01", "smooth-scroll", "motion", blocks=blocks, **flags)
        write(folder, "F01", "study", "process", **flags)
        put(folder / "proofs.json", json.dumps({"C01": PROOF}, indent=2), **flags)
    raw = (tmp_path / "windows" / "C01-smooth-scroll.md").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    assert b"\r" not in (tmp_path / "plain" / "C01-smooth-scroll.md").read_bytes()
    windows, plain = load(tmp_path / "windows"), load(tmp_path / "plain")
    assert windows == plain
    assert windows.prompts[0].blocks["Stack"] == blocks["Stack"] and windows.proofs == {"C01": PROOF}
    assert content_hash(tmp_path / "windows") == content_hash(tmp_path / "plain")
    # text handed straight to the parser is read the same way
    text = prompt_text("C01", "x", "motion")
    assert parse_prompt(BOM + text.replace("\n", "\r\n"), "C01-x.md") == parse_prompt(text, "C01-x.md")


def test_the_hash_ignores_proofs_and_changes_when_a_prompt_changes(tmp_path):
    library_in(tmp_path)
    write(tmp_path, "C01", "x", "motion")
    write(tmp_path, "F01", "y", "process")
    before = content_hash(tmp_path)
    assert len(before) == 64 and int(before, 16) >= 0
    assert content_hash(tmp_path) == before
    # a proof is outside the hash
    put(tmp_path / "proofs.json", json.dumps({"C01": PROOF}))
    assert load(tmp_path).proofs == {"C01": PROOF}
    assert content_hash(tmp_path) == before
    # a prompt that changes, a shared rule that changes, a file that is renamed, a file that is added: each gives another hash
    seen = {before}
    write(tmp_path, "C01", "x", "motion", title="Another title")
    seen.add(content_hash(tmp_path))
    put(tmp_path / "shared.md", shared_text().replace("The rule of Gates.", "A different rule."))
    seen.add(content_hash(tmp_path))
    (tmp_path / "F01-y.md").rename(tmp_path / "F01-z.md")
    seen.add(content_hash(tmp_path))
    write(tmp_path, "B01", "w", "gallery")
    seen.add(content_hash(tmp_path))
    assert len(seen) == 5
    # only the names and the text count, not the order the files were written in
    first, second = tmp_path / "first", tmp_path / "second"
    for folder, order in ((first, ("C01", "F01")), (second, ("F01", "C01"))):
        folder.mkdir()
        library_in(folder)
        for prompt_id in order:
            write(folder, prompt_id, "x", "motion" if prompt_id == "C01" else "process")
    assert content_hash(first) == content_hash(second)


def test_view_gives_the_italian_text_when_asked():
    prompt = parse_prompt(prompt_text("C01", "x", "motion", tags=["a", "b"], libs={"lenis": "1.3.4"}, skills=["gsap-core"], size="S"), "C01-x.md")
    shown = prompt.view("it")
    assert shown["title"] == prompt.title_it == "Titolo di C01"
    assert shown["summary"] == prompt.summary_it == "Cosa dà C01."
    assert shown == {"id": "C01", "group": "motion", "title": "Titolo di C01", "summary": "Cosa dà C01.", "tags": ["a", "b"], "size": "S",
                     "skills": ["gsap-core"], "libs": {"lenis": "1.3.4"}}
    # English by default, and for a language there is no text in
    assert prompt.view()["title"] == prompt.view("en")["title"] == prompt.view("fr")["title"] == "Title of C01"
    assert prompt.view()["summary"] == "What C01 gives."
    # what is shown is a copy that can go to JSON, and changing it changes nothing in the prompt
    json.dumps(shown)
    shown["libs"]["lenis"] = "0"
    shown["tags"].append("c")
    assert prompt.libs == {"lenis": "1.3.4"} and prompt.tags == ("a", "b")


def test_the_shipped_library_loads():
    assert Path(library.__file__).resolve().parent / "prompt_library" == library.LIBRARY_FOLDER
    shipped = load()  # the folder that travels with the program: no error (it may hold no prompt yet)
    assert isinstance(shipped, Library) and tuple(shipped.shared) == SHARED_HEADINGS
    assert all(GROUPS[p.id[0]] == p.group for p in shipped.prompts)
    assert len(content_hash()) == 64


# -- the rest of the rules ----------------------------------------------------------------------------------------------


def test_a_library_error_made_from_one_text_is_one_problem_not_one_for_each_letter():
    error = LibraryError("the library is not where it should be")
    assert error.problems == ["the library is not where it should be"] and str(error) == "the library is not where it should be"
    many = LibraryError(["first", "second\nthird"])  # a message of several lines is kept to one line
    assert many.problems == ["first", "second third"] and str(many).splitlines() == ["first", "second third"]
    assert isinstance(many, ValueError)


def test_the_front_matter_has_its_keys_and_the_optional_ones_may_be_left_out():
    bare = parse_prompt(prompt_text("B01", "x", "gallery", tags=None, libs=None, skills=None), "B01-x.md")
    assert (bare.tags, bare.libs, bare.skills) == ((), {}, ())
    for key in ("id", "slug", "group", "title", "title_it", "summary", "summary_it", "size"):
        found = problems_of(prompt_text("B01", "x", "gallery", **{key: None}), "B01-x.md")
        assert len(found) == 1 and key in found[0], key
    found = problems_of(prompt_text("B01", "x", "gallery", title=None, summary_it=None), "B01-x.md")
    assert len(found) == 1 and "title" in found[0] and "summary_it" in found[0]


@pytest.mark.parametrize(("change", "mention"), [
    ({"title": ""}, "title"), ({"title_it": 5}, "title_it"), ({"summary": ["a"]}, "summary"), ({"size": "XL"}, "size"), ({"size": "m"}, "size"),
    ({"tags": "lenis"}, "tags"), ({"tags": [1, 2]}, "tags"), ({"skills": "gsap-core"}, "skills"), ({"libs": ["lenis"]}, "libs"),
    ({"skils": ["gsap-core"]}, "skils"),
])
def test_a_front_matter_value_of_the_wrong_kind_or_a_key_nobody_asked_for_is_refused(change, mention):
    found = problems_of(prompt_text("B01", "x", "gallery", **change), "B01-x.md")
    assert len(found) == 1 and mention in found[0]


@pytest.mark.parametrize("text", ["{body}", "\n---\nid: B01\n---\n{body}", "--- \nid: B01\n---\n{body}", "---\nid: B01\n"])
def test_a_file_without_a_front_matter_block_is_refused(text):
    found = problems_of(text.format(body=body_of(dict.fromkeys(STANDARD, "x"))), "B01-x.md")
    assert len(found) == 1 and "front matter" in found[0]


def test_a_file_that_is_only_a_header_is_told_which_blocks_it_lacks():
    header_only = prompt_text("B01", "x", "gallery", body="").rstrip("\n")
    assert header_only.endswith("\n---")
    found = problems_of(header_only, "B01-x.md")
    assert len(found) == 1 and "missing blocks" in found[0] and "Goal" in found[0]


def test_front_matter_that_is_not_yaml_is_refused_with_the_line_of_the_file():
    found = problems_of(f"---\nid: B01\ntitle: Study: grammar\n---\n{body_of(dict.fromkeys(STANDARD, 'x'))}", "B01-x.md")
    assert len(found) == 1 and "YAML" in found[0] and "line 3" in found[0]


def test_front_matter_that_is_not_a_set_of_keys_is_refused():
    found = problems_of(f"---\n- id\n- B01\n---\n{body_of(dict.fromkeys(STANDARD, 'x'))}", "B01-x.md")
    assert len(found) == 1 and "key: value" in found[0]


def test_text_before_the_first_heading_is_refused_because_it_would_be_lost():
    found = problems_of(prompt_text("B01", "x", "gallery", body=f"An intro nobody would see.\n\n{body_of(dict.fromkeys(STANDARD, 'x'))}"), "B01-x.md")
    assert len(found) == 1 and "before the first" in found[0]


def test_a_block_written_twice_is_refused_because_one_would_overwrite_the_other():
    found = problems_of(prompt_text("B01", "x", "gallery", body=f"{body_of(dict.fromkeys(STANDARD, 'x'))}\n\n## Verify\n\nAgain."), "B01-x.md")
    assert len(found) == 1 and "Verify" in found[0] and "more than once" in found[0]


def test_a_block_with_no_text_is_refused_even_when_it_is_required():
    found = problems_of(prompt_text("B01", "x", "gallery", blocks={**dict.fromkeys(STANDARD, "x"), "Goal": ""}), "B01-x.md")
    assert len(found) == 1 and "Goal" in found[0] and "empty" in found[0]


def test_a_heading_inside_a_code_fence_is_text_of_its_block_not_a_new_block():
    # the skeleton of a style guide written in a fence has ## lines of its own
    fenced = "```md\n## Colours\n\n- background\n\n## Type\n```\n\n~~~\n## Not a block either\n~~~\n\n### A smaller heading"
    prompt = parse_prompt(prompt_text("F01", "x", "process", blocks={"Goal": "g", "Steps": fenced, "Verify": "v"}), "F01-x.md")
    assert list(prompt.blocks) == ["Goal", "Steps", "Verify"]
    assert "## Type" in prompt.blocks["Steps"] and "## Not a block either" in prompt.blocks["Steps"]
    assert prompt.blocks["Steps"].endswith("### A smaller heading")


def test_double_braces_that_are_not_a_bare_name_are_code_not_placeholders():
    jsx = "```tsx\n<div style={{ opacity }} />\n<div style={{ opacity: 0 }} />\n```"
    prompt = parse_prompt(prompt_text("B01", "x", "gallery", blocks={**dict.fromkeys(STANDARD, "x"), "Stack": jsx}), "B01-x.md")
    assert prompt.blocks["Stack"] == jsx


# Double braces that are not a placeholder of the set: a space, a hyphen, a dot, nothing at all, a JSX style object. Written in prose they would reach the
# builder as they are, because only a bare {{name}} is filled.
NOT_PLACEHOLDERS = ["{{ palette }}", "{{palette }}", "{{type-scale}}", "{{type scale}}", "{{host.name}}", "{{}}", "{{ opacity: 0 }}"]
# Bare names outside the set: refused wherever they stand.
UNKNOWN_NAMES = ["{{colour}}", "{{Host}}"]


def stack_of(code: str) -> str:
    """A gallery prompt whose Stack block is ``code``."""
    return prompt_text("B01", "x", "gallery", blocks={**dict.fromkeys(STANDARD, "x"), "Stack": code})


@pytest.mark.parametrize("text", [*NOT_PLACEHOLDERS, *UNKNOWN_NAMES])
def test_double_braces_that_are_not_exactly_a_placeholder_of_the_set_are_refused_outside_a_code_block(text):
    found = problems_of(prompt_text("B01", "x", "gallery", blocks={**dict.fromkeys(STANDARD, "x"), "Goal": f"Use {text} for the colours."}), "B01-x.md")
    assert len(found) == 1
    assert found[0].startswith("B01-x.md:") and text in found[0]
    assert "use exactly {{name}} from the closed set, or move the example into a code block" in found[0]
    assert "host, palette, fonts" in found[0]  # the set is named, so the author need not look for it


def test_an_inline_code_span_is_not_a_code_block():
    found = problems_of(stack_of("Write `style={{ y: 0 }}` on it."), "B01-x.md")
    assert len(found) == 1 and "{{ y: 0 }}" in found[0] and "move the example into a code block" in found[0]


def test_every_wrong_double_brace_text_is_named_once_in_one_problem_and_the_good_ones_not_at_all():
    prose = "Use " + ph("host") + ", {{ palette }}, {{type-scale}} and " + ph("palette") + "; again {{ palette }}."
    found = problems_of(stack_of(prose), "B01-x.md")
    assert len(found) == 1
    assert found[0].count("{{ palette }}") == 1 and found[0].index("{{ palette }}") < found[0].index("{{type-scale}}")
    assert ph("host") not in found[0] and ph("palette") not in found[0]


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_the_same_double_braces_load_inside_a_code_block(fence):
    code = f"{fence}tsx\n" + "\n".join(f"<div style={text} />" for text in NOT_PLACEHOLDERS) + f"\n{fence}"
    assert parse_prompt(stack_of(code), "B01-x.md").blocks["Stack"] == code


def test_a_bare_name_outside_the_set_is_refused_inside_a_code_block_too():
    found = problems_of(stack_of("```tsx\n<div style={{ opacity }} data-a={{colour}} data-b={{Host}} />\n```"), "B01-x.md")
    assert len(found) == 1 and "{{colour}}" in found[0] and "{{Host}}" in found[0] and "{{ opacity }}" not in found[0]
    assert "move the example" not in found[0]  # it is in a code block already: only the set can fix it


def test_a_placeholder_of_the_set_is_welcome_in_a_code_block_and_wherever_prose_puts_it():
    prose = "\n".join([
        f"- Use {ph('host')}, then {ph('palette')}{ph('fonts')}, `{ph('motion')}` and **{ph('type_scale')}**.",
        f'({ph("spacing")}) "{ph("sections")}" / {ph("libs")}: {ph("viewport")}.',
        ph("host"),
    ])
    code = "```tsx\nconst host = '" + ph("host") + "';\n<div style={{ x: '" + ph("viewport") + "' }} />\n```"
    blocks = parse_prompt(prompt_text("B01", "x", "gallery", blocks={**dict.fromkeys(STANDARD, "x"), "Goal": prose, "Stack": code}), "B01-x.md").blocks
    assert blocks["Goal"] == prose and blocks["Stack"] == code


def test_the_shared_rules_refuse_a_wrong_double_brace_in_prose_and_allow_it_in_a_code_block(tmp_path):
    library_in(tmp_path)
    put(tmp_path / "shared.md", shared_text().replace("The rule of Gates.", "Study {{ palette }} first."))
    found = load_problems(tmp_path)
    assert len(found) == 1 and found[0].startswith("shared.md:") and "{{ palette }}" in found[0] and "move the example into a code block" in found[0]
    put(tmp_path / "shared.md", shared_text().replace("The rule of Gates.", "```tsx\n<i style={{ y: 0 }} />\n```"))
    assert tuple(load(tmp_path).shared) == SHARED_HEADINGS


def test_two_files_with_one_id_are_refused(tmp_path):
    library_in(tmp_path)
    write(tmp_path, "C01", "first", "motion")
    write(tmp_path, "C01", "second", "motion")
    write(tmp_path, "C02", "third", "motion")
    found = load_problems(tmp_path)
    assert len(found) == 1 and "C01-first.md" in found[0] and "C01-second.md" in found[0] and "C02" not in found[0]


def test_a_file_that_is_not_a_prompt_and_one_that_cannot_be_read_are_reported(tmp_path):
    library_in(tmp_path)
    put(tmp_path / "notes.md", "just some notes\n")
    (tmp_path / "B01-bytes.md").write_bytes(b"---\ntitle: \xff\xfe\n---\n")
    (tmp_path / "readme.txt").write_text("other kinds of file are left alone", encoding="utf-8")
    found = load_problems(tmp_path)
    assert len(found) == 2
    assert found[0].startswith("B01-bytes.md:") and "UTF-8" in found[0]
    assert found[1].startswith("notes.md:") and "front matter" in found[1]


@pytest.mark.parametrize("headings", [
    SHARED_HEADINGS[:-1], (*SHARED_HEADINGS, "Notes"), SHARED_HEADINGS[::-1], ("Gates", "Assets", "Verify", "Hard lines", "Deliver"),
    ("Gates", "Assets", "Hard Lines", "Verify", "Deliver"),
])
def test_the_shared_rules_need_exactly_their_five_headings_in_order(tmp_path, headings):
    library_in(tmp_path)
    put(tmp_path / "shared.md", shared_text(headings))
    found = load_problems(tmp_path)
    assert len(found) == 1 and found[0].startswith("shared.md:") and "in this order" in found[0]


def test_a_shared_heading_written_twice_is_refused(tmp_path):
    library_in(tmp_path)
    put(tmp_path / "shared.md", shared_text(("Gates", "Gates", *SHARED_HEADINGS[1:])))
    found = load_problems(tmp_path)
    assert len(found) == 1 and found[0].startswith("shared.md:") and "Gates" in found[0] and "more than once" in found[0]


def test_the_shared_rules_fit_the_limit_and_not_one_character_more(tmp_path):
    library_in(tmp_path)
    room = SHARED_MAX_CHARS - len(shared_text().strip())

    def with_extra(characters: int) -> str:
        return shared_text().replace("The rule of Gates.", "The rule of Gates." + "x" * characters)

    assert len(with_extra(room).strip()) == SHARED_MAX_CHARS
    put(tmp_path / "shared.md", with_extra(room))
    assert tuple(load(tmp_path).shared) == SHARED_HEADINGS
    put(tmp_path / "shared.md", with_extra(room + 1))
    found = load_problems(tmp_path)
    assert len(found) == 1 and found[0].startswith("shared.md:") and str(SHARED_MAX_CHARS + 1) in found[0]


def test_the_closed_set_of_placeholders_holds_in_the_shared_rules_too(tmp_path):
    library_in(tmp_path)
    put(tmp_path / "shared.md", shared_text().replace("The rule of Gates.", f"Use {ph('host')} and {ph('colour')}."))
    found = load_problems(tmp_path)
    assert len(found) == 1 and found[0].startswith("shared.md:") and ph("colour") in found[0] and ph("host") not in found[0]


def test_a_library_without_its_shared_rules_is_refused(tmp_path):
    library_in(tmp_path)
    (tmp_path / "shared.md").unlink()
    found = load_problems(tmp_path)
    assert len(found) == 1 and "shared.md" in found[0] and "missing" in found[0]


def test_a_proof_must_belong_to_a_prompt_and_carry_its_keys(tmp_path):
    library_in(tmp_path)
    write(tmp_path, "C01", "x", "motion")
    proofs = tmp_path / "proofs.json"

    def refused(data: str) -> str:
        put(proofs, data)
        found = load_problems(tmp_path)
        assert len(found) == 1 and found[0].startswith("proofs.json:"), found
        return found[0]

    put(proofs, json.dumps({"C01": PROOF}))
    assert load(tmp_path).proofs == {"C01": PROOF}
    assert "C09" in refused(json.dumps({"C09": PROOF}))
    assert "notes" in refused(json.dumps({"C01": {k: v for k, v in PROOF.items() if k != "notes"}}))
    assert "score" in refused(json.dumps({"C01": {**PROOF, "score": 9}}))
    assert "C01" in refused(json.dumps({"C01": "tested"}))
    assert "object" in refused(json.dumps(["C01"]))
    refused("{not json")


def test_without_a_proofs_file_nothing_has_been_tried_yet(tmp_path):
    library_in(tmp_path)
    (tmp_path / "proofs.json").unlink()
    assert load(tmp_path).proofs == {}


def test_get_finds_a_prompt_by_its_id(tmp_path):
    library_in(tmp_path)
    write(tmp_path, "C01", "x", "motion")
    write(tmp_path, "B01", "y", "gallery")
    loaded = load(tmp_path)
    assert loaded.get("B01") is loaded.prompts[0] and loaded.get("C01") is loaded.prompts[1]
    assert loaded.get("C99") is None and loaded.get("") is None


def test_get_library_reads_the_default_folder_once(tmp_path, monkeypatch):
    library_in(tmp_path)
    write(tmp_path, "C01", "x", "motion")
    monkeypatch.setattr(library, "LIBRARY_FOLDER", tmp_path)
    get_library.cache_clear()
    try:
        first = get_library()
        assert [p.id for p in first.prompts] == ["C01"]
        write(tmp_path, "B01", "y", "gallery")  # added after the first read: the library is read once
        assert get_library() is first
        assert [p.id for p in load().prompts] == ["B01", "C01"]  # a plain load() reads the default folder again
    finally:
        get_library.cache_clear()


def test_the_shared_rules_say_what_every_master_prompt_has_to_say():
    shared = load().shared
    said = {
        "Gates": ("docs/style_guide.md", "docs/spec.md", "approve", "cannot ask a person", "docs/prompts/assumptions.md", "still", "screenshot", "full build", "Verify"),
        "Assets": ("real assets", "[PLACEHOLDER:", "product screens", "metrics", "logos", "names", "testimonials", "hotlink", "system stack", "chosen, not measured"),
        "Hard lines": ("grammar", "never its text, logos, photographs or videos", "centred title over a soft gradient", "purple-to-blue gradient",
                       "fading in on load", "label above three icon cards", "card inside a card", "`transform` and `opacity` only",
                       "prefers-reduced-motion", "shift the layout"),
        "Verify": ("build passes", "console", "390, 768 and 1440 px", "horizontal overflow", "did not check"),
        "Deliver": ("README", "verified"),
    }
    assert tuple(said) == SHARED_HEADINGS
    for heading, fragments in said.items():
        for fragment in fragments:
            assert fragment in shared[heading], (heading, fragment)
    # the text is the same for every prompt: no placeholder of its own, no code
    everything = "".join(shared.values())
    assert "{{" not in everything and "```" not in everything


# -- the prompts that travel with the program ---------------------------------------------------------------------------

# The hash of shared.md and of every prompt file that ships (content_hash() prints it). Changing a prompt changes it: once the library has
# been released, bump LIBRARY_VERSION as well, then paste the new value here.
PINNED = "de2cb5c6576ea51c392fea86f6a7e82df5b45b586da46f1614a78ebcbc783369"

REPO = Path(__file__).resolve().parents[2]
# The compiler of the desktop package: the code blocks of a prompt must parse as what a builder will compile.
TYPESCRIPT = REPO / "desktop" / "node_modules" / "typescript"
SCRIPT_LANGUAGES = ("ts", "tsx", "js", "jsx")
# A line that opens or closes a code fence: three backticks or more, or three tildes or more, after an indent.
FENCE_LINE = re.compile(r"\s*(`{3,}|~{3,})")
# Reads [{name, language, code}] on its input and writes, for each block, what TypeScript says about its syntax.
TRANSPILE = """\
const ts = require(process.argv[2]);
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  const report = JSON.parse(input).map(({ name, language, code }) => {
    const { diagnostics } = ts.transpileModule(code, {
      fileName: `block.${language}`,
      reportDiagnostics: true,
      compilerOptions: { jsx: ts.JsxEmit.Preserve, target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext },
    });
    return { name, errors: (diagnostics || []).map((d) => ts.flattenDiagnosticMessageText(d.messageText, ' ')) };
  });
  process.stdout.write(JSON.stringify(report));
});
"""


def fenced_parts(text: str) -> tuple[str, list[tuple[str, str]]]:
    """``text`` split as the validator splits it: the prose with every code fence taken out, and each fenced block as (language, code).
    A fence opens on a line whose first three characters after the indent are three backticks or three tildes, and closes on the next
    line that starts the same way; the language is the first word after the marker."""
    prose: list[str] = []
    blocks: list[tuple[str, str]] = []
    fence, language, code = "", "", []
    for line in text.split("\n"):
        marker = line.lstrip()[:3]
        if fence:
            if marker == fence:
                blocks.append((language, "\n".join(code)))
                fence = ""
            else:
                code.append(line)
        elif marker in ("```", "~~~"):
            fence, code = marker, []
            language = (line.lstrip()[3:].split() or [""])[0].lower()
        else:
            prose.append(line)
    return "\n".join(prose), blocks


def test_the_shipped_library_has_the_pinned_hash_and_the_expected_ids():
    assert {p.id for p in load().prompts} == {"B01", "B02", "C01", "F01", "F03"}
    assert content_hash() == PINNED, f"a prompt file or shared.md changed: if that was meant, pin {content_hash()}"


@pytest.mark.skipif(shutil.which("node") is None or not TYPESCRIPT.is_dir(), reason="Node.js or the desktop package's TypeScript is not installed")
def test_the_code_blocks_of_the_library_parse(tmp_path):
    shipped = load()
    texts = [(f"{p.id} {heading}", text) for p in shipped.prompts for heading, text in p.blocks.items()]
    texts += [(f"shared.md {heading}", text) for heading, text in shipped.shared.items()]
    blocks = [{"name": f"{name} #{index}", "language": language, "code": code}
              for name, text in texts for index, (language, code) in enumerate(fenced_parts(text)[1], 1) if language in SCRIPT_LANGUAGES]
    assert blocks, "the prompts have code, so finding none means the fences were not read"
    script = tmp_path / "transpile.cjs"
    script.write_text(TRANSPILE, encoding="utf-8")
    done = subprocess.run([str(shutil.which("node")), str(script), str(TYPESCRIPT)], input=json.dumps(blocks), capture_output=True, text=True,
                          encoding="utf-8", timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert [item["name"] for item in report] == [block["name"] for block in blocks]
    assert {item["name"]: item["errors"] for item in report if item["errors"]} == {}


def test_filling_every_placeholder_leaves_no_double_braces_in_prose():
    # The validator takes {{host} (one brace short) for prose: filled, it would reach the builder as it is.
    every_value = dict.fromkeys(PLACEHOLDERS, "X")
    for prompt in load().prompts:
        for heading, text in prompt.blocks.items():
            prose, _ = fenced_parts(fill(text, every_value))
            assert "{{" not in prose and "}}" not in prose, f"{prompt.id} {heading}: a double brace that no value fills"


def test_every_code_fence_in_the_shipped_library_is_closed():
    # A fence left open turns the check of the double braces in prose off for the rest of the file.
    files = sorted(library.LIBRARY_FOLDER.glob("*.md"))
    assert library.SHARED_FILE in [path.name for path in files]
    for path in files:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        fences = [number for number, line in enumerate(lines, 1) if FENCE_LINE.match(line)]
        assert len(fences) % 2 == 0, f"{path.name}: a code fence is opened and never closed (the fence lines are {fences})"
