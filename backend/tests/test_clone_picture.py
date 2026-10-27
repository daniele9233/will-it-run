"""A site from a picture: what a model's answer is made into, how the steps keep or undo what it wrote, and the run's start.

No model, browser or build runs here: the model is a function that answers with what a model would, and the steps that open
Chrome or npm are replaced by functions that write what they would write.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from agentfit import paths
from agentfit.clone import picture, pipeline, studio
from agentfit.clone.install import BrowserInstall
from agentfit.clone.skills import Skill
from agentfit.events import EventBus
from agentfit.workflows import checks
from agentfit.workflows.checks import CheckContext
from agentfit.workflows.runner import RunManager
from agentfit.workflows.validator import validate
from fakes import FakeRuntime
from test_clone import fake_store
from test_clone_replica import FakeEngine

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360f8cfc000000301010018dd8db00000000049454e44ae426082")

# -- the picture ------------------------------------------------------------------------------------------------------


def test_a_picture_is_known_by_its_first_bytes_not_by_its_name(tmp_path):
    for name, head, kind in (("a.png", PNG, "png"), ("b.png", b"\xff\xd8\xff\xe0abc", "jpeg"), ("c.jpg", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"), ("d.gif", b"GIF89a....", "gif")):
        (tmp_path / name).write_bytes(head)
        assert picture.image_type(tmp_path / name) == kind
    (tmp_path / "e.png").write_text("<html>not a picture</html>", encoding="utf-8")
    assert picture.image_type(tmp_path / "e.png") is None and picture.image_type(tmp_path / "missing.png") is None
    assert picture.png_size(tmp_path / "a.png") == (1, 1) and picture.png_size(tmp_path / "e.png") is None


def test_a_message_with_a_picture_has_the_picture_before_the_words(tmp_path):
    (tmp_path / "p.png").write_bytes(PNG)
    messages = picture.image_message("What is it?", tmp_path / "p.png", system="Be brief.")
    assert messages[0] == {"role": "system", "content": "Be brief."}
    content = messages[1]["content"]
    assert content[0]["type"] == "image_url" and content[0]["image_url"]["url"].startswith("data:image/png;base64,") and content[-1] == {"type": "text", "text": "What is it?"}


# -- the composition a model writes -----------------------------------------------------------------------------------


def test_the_regions_of_a_model_are_made_usable():
    raw = [
        {"kind": "photo", "x": 0, "y": 0, "w": 50, "h": 100, "z": 1},
        {"kind": "Text", "x": 60, "y": -5, "w": 150, "h": 20, "z": "3", "rotate": 90, "text": "Hello " * 100, "note": "a title"},
        {"kind": "text", "x": 10, "y": 10, "w": 2, "h": 50},
        {"kind": "photo", "x": 0, "y": 0, "w": 50, "h": 100},
        {"kind": "mystery", "x": 70, "y": 70, "w": 20, "h": 20},
        "nonsense",
    ]
    regions = picture.clean_regions(raw)
    assert [r["id"] for r in regions] == [1, 2, 3] and [r["kind"] for r in regions] == ["photo", "text", "ui"]
    assert regions[1]["y"] == 0 and regions[1]["x"] + regions[1]["w"] == 100 and regions[1]["rotate"] == 45 and regions[1]["z"] == 3 and len(regions[1]["text"]) == 400
    assert len(picture.clean_regions([{"x": i, "y": 0, "w": 5, "h": 5} for i in range(30)])) == picture.MAX_REGIONS
    assert picture.clean_regions("not a list") == []


def test_the_composition_is_read_from_an_answer_with_words_around_it():
    answer = 'Here you are:\n{"background": "#112233", "regions": [{"kind": "ui", "x": 0, "y": 0, "w": 100, "h": 10}]}\nDone.'
    assert picture.parse_composition(answer) == ("#112233", picture.clean_regions([{"kind": "ui", "x": 0, "y": 0, "w": 100, "h": 10}]))
    assert picture.parse_composition('{"background": "blue", "regions": []}') == ("", [])
    assert picture.parse_composition("no json") == ("", []) and picture.parse_composition("{broken") == ("", [])


def test_the_bands_of_the_picture_stand_in_for_the_regions():
    regions = picture.regions_from_bands({"height": 1000, "bands": [{"y": 500, "h": 500}, {"y": 0, "h": 300}, {"y": 300, "h": 4}, {"y": 304, "h": 196}]})
    assert [(r["y"], r["h"]) for r in regions] == [(0.0, 30.0), (30.4, 19.6), (50.0, 50.0)] and all(r["x"] == 0 and r["w"] == 100 for r in regions)
    assert [r["id"] for r in regions] == [1, 2, 3]


def test_the_code_and_the_style_of_an_answer_are_found_whatever_the_blocks_are_called():
    tsx = "export default function Region1() { return <div className=\"r1\">Hi</div>; }"
    assert picture.extract_blocks(f"```tsx\n{tsx}\n```\n```css\n.r1 {{ color: red; }}\n```") == (tsx, ".r1 { color: red; }")
    assert picture.extract_blocks(f"```jsx\n{tsx}\n```")[0] == tsx
    assert picture.extract_blocks(f"Sure!\n```\n{tsx}\n```")[0] == tsx
    assert picture.extract_blocks(f"Sure: {tsx}")[0].startswith("export default function")
    assert picture.extract_blocks("nothing") == ("", "")


def test_style_rules_a_page_may_not_carry_are_cleaned_and_unbalanced_ones_dropped():
    css = '@import url("x.css");\n.a { background: url(https://x.test/a.png); }\n.b { color: red; }'
    cleaned = picture.clean_css(css)
    assert "@import" not in cleaned and "x.test" not in cleaned and ".b { color: red; }" in cleaned
    assert picture.clean_css(".a { color: red;") == ""


@pytest.mark.parametrize("code, ok", [
    ("import x from 'y';\nexport default function Foo() { return <div className=\"r2\">a</div>; }", True),
    ("export default function Foo() { return <div onClick={() => window.alert(1)} />; }", False),
    ("export default function Foo() { return <div dangerouslySetInnerHTML={{__html: 'x'}} />; }", False),
    ("export default function Foo() { fetch('/x'); return <div />; }", False),
    ("const x = 1;", False),
    ("export default function Foo() { return null; }", False),
])
def test_a_component_is_written_only_if_it_is_safe_and_renders_something(code, ok):
    result, why = picture.valid_component(code, 2)
    assert (result is not None) is ok and (ok or why)
    if ok:
        assert result.startswith("export default function Region2") and "import" not in result


# -- the page ---------------------------------------------------------------------------------------------------------------------


COMPOSITION = {"width": 1000, "height": 500, "background": "#fafafa", "source": "model", "regions": [
    {"id": 1, "kind": "photo", "x": 0, "y": 0, "w": 40, "h": 100, "z": 1, "rotate": 0, "text": "", "note": "a photo"},
    {"id": 2, "kind": "text", "x": 45, "y": 10, "w": 50, "h": 20, "z": 2, "rotate": -3, "text": "Hello", "note": "title"}]}


def test_the_page_puts_each_region_where_the_composition_says_and_in_depth_order(tmp_path):
    picture.write_css(tmp_path, {2: ".r2 { color: red; }"})
    picture.write_page(tmp_path, {**COMPOSITION, "regions": list(reversed(COMPOSITION["regions"]))})
    page = (tmp_path / "src/app/page.tsx").read_text(encoding="utf-8")
    assert page.index('data-region="1"') < page.index('data-region="2"')
    assert 'left: "45%", top: "10%", width: "50%", height: "20%", zIndex: 2, transform: "rotate(-3deg)"' in page
    assert 'data-clone-section="2"' in page and '<Region2 />' in page and "<Motion" not in page
    css = (tmp_path / "src/app/regions.css").read_text(encoding="utf-8")
    assert "aspect-ratio: 1000 / 500" in css and "background: #fafafa" in css and "/* region 2 */\n.r2 { color: red; }" in css
    picture.write_page(tmp_path, COMPOSITION, motion=True)
    assert 'import Motion from "@/components/Motion";' in (tmp_path / "src/app/page.tsx").read_text(encoding="utf-8")


def test_the_skeleton_is_a_nextjs_project_with_gsap(tmp_path):
    picture.write_skeleton(tmp_path, 'The "Site"', "it")
    package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["gsap"] and package["dependencies"]["next"] and package["scripts"]["build"] == "next build"
    assert "The 'Site'" in (tmp_path / "src/app/layout.tsx").read_text(encoding="utf-8") and 'lang="it"' in (tmp_path / "src/app/layout.tsx").read_text(encoding="utf-8")


@pytest.mark.parametrize("sees, vision_key", [(True, ""), (False, "eyes")])
def test_the_workflow_is_valid_and_its_loops_are_bounded(sees, vision_key):
    definition = picture.definition(model_key="m", sees=sees, vision_key=vision_key, brief="b", reference="docs/reference/reference.png")
    assert validate(definition, require_models=True).ok
    assert definition.models["main"].model_key == (vision_key or "m") and definition.models["main"].kind == "vision"
    rewrite = next(e for e in definition.edges if e.target == "refine")
    assert rewrite.max_loops == picture.REFINE_ROUNDS and rewrite.on_exhausted == "motion"
    for kind in ("prepare", "analyze", "compose", "measure", "refine", "motion"):
        assert f"picture_{kind}" in checks.CHECKS and not checks.CHECKS[f"picture_{kind}"].listed


# -- the steps ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path, monkeypatch):
    install = FakeEngine(tmp_path / "engine")
    monkeypatch.setattr(pipeline, "install_source", lambda: install)
    monkeypatch.setattr(pipeline, "find_browser", lambda: {"name": "Chrome", "executable": "chrome.exe"})
    return install


def make_ctx(workspace: Path, outputs: dict[str, Any] | None = None, complete: Any = None) -> CheckContext:
    events: list[tuple[str, dict]] = []
    definition = picture.definition(model_key="m", sees=True, brief="Un sito con animazioni e parallax", reference="docs/reference/reference.png")
    ctx = CheckContext(task="t", outputs=outputs or {}, ledger=[], history=[], definition=definition, workspace=workspace,
                       emit=lambda kind, payload: events.append((kind, payload)), cancelled=lambda: False, complete=complete)
    ctx.events = events  # type: ignore[attr-defined]
    return ctx


def check_of(name: str, **params: Any) -> Any:
    check = next(c for c in picture.definition(model_key="m", sees=True).checks if c.id == name)
    return check.model_copy(update={"params": {**check.params, **params}})


def answer(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


ANALYSIS = {"width": 1000, "height": 500, "palette": [{"hex": "#ffffff", "share": 0.5}, {"hex": "#000000", "share": 0.2}],
            "bands": [{"y": 0, "h": 250, "mean": "#ffffff"}, {"y": 250, "h": 250, "mean": "#eeeeee"}]}


def fake_script(calls: list[dict] | None = None):
    """``picture.mjs`` as a function: it writes the files it would write."""
    def run(ctx, config, name):
        if calls is not None:
            calls.append(config)
        folder = Path(config["out"])
        folder.mkdir(parents=True, exist_ok=True)
        if config["cmd"] == "analyze":
            (folder / "analysis.json").write_text(json.dumps(ANALYSIS), encoding="utf-8")
        else:
            for region in config["regions"]:
                (folder / f"region-{region['id']}.png").write_bytes(PNG)
                (folder / f"region-{region['id']}.jpg").write_bytes(PNG)
        return True, ""
    return run


class Skills:
    """A skill store that holds one skill, so that a run copies it."""

    def __init__(self, root: Path) -> None:
        self.skill = Skill("gsap-core", "GSAP core", "gsap", root)

    def catalog(self) -> list[Skill]:
        return [self.skill]

    def copy_into(self, workspace: Path, names: list[str] | None = None) -> list[Skill]:
        (workspace / ".skills" / "gsap-core").mkdir(parents=True, exist_ok=True)
        (workspace / ".skills" / "gsap-core" / "SKILL.md").write_text("# gsap", encoding="utf-8")
        return [self.skill]

    def body(self, name: str, limit: int = 6000) -> str:
        return f"The guide of {name}."


def prepared(workspace: Path) -> dict[str, Any]:
    (workspace / "docs/reference").mkdir(parents=True, exist_ok=True)
    (workspace / "docs/reference/reference.png").write_bytes(PNG)
    return {"passed": True, "reference": "docs/reference/reference.png", "palette": ANALYSIS["palette"]}


def test_prepare_measures_the_picture_writes_the_project_copies_the_skills_and_installs(tmp_path, engine, monkeypatch):
    prepared(tmp_path)
    monkeypatch.setattr(picture, "_script", fake_script())
    monkeypatch.setattr(picture, "skill_store", Skills(tmp_path))
    ran: list[list[str]] = []
    monkeypatch.setattr(pipeline, "_npm", lambda ctx, install, args, step, timeout: (ran.append(args) or (0, [], False)))
    out = picture._prepare(check_of("prepare", reference="docs/reference/reference.png", title="Site", language="it"), make_ctx(tmp_path))
    assert out["passed"] is True and out["width"] == 1000 and out["skills"] == ["gsap-core"] and out["bands"] == 2
    assert (tmp_path / "package.json").is_file() and (tmp_path / ".skills/gsap-core/SKILL.md").is_file() and ran == [["install", "--no-fund", "--no-audit", "--loglevel=error"]]


def test_prepare_refuses_what_is_not_a_picture(tmp_path, engine):
    (tmp_path / "docs/reference").mkdir(parents=True)
    (tmp_path / "docs/reference/reference.png").write_text("<html>", encoding="utf-8")
    out = picture._prepare(check_of("prepare", reference="docs/reference/reference.png"), make_ctx(tmp_path))
    assert out["passed"] is False and out["verdict"] == "NO_PICTURE"
    assert picture._prepare(check_of("prepare", reference=""), make_ctx(tmp_path))["verdict"] == "NO_PICTURE"


def analyze_ctx(tmp_path: Path, complete: Any) -> CheckContext:
    ctx = make_ctx(tmp_path, {"prepare": prepared(tmp_path)}, complete)
    (picture.research(tmp_path)).mkdir(parents=True, exist_ok=True)
    (picture.research(tmp_path) / "analysis.json").write_text(json.dumps(ANALYSIS), encoding="utf-8")
    return ctx


def test_analyze_takes_the_regions_the_model_wrote_and_cuts_the_picture_into_them(tmp_path, engine, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(picture, "_script", fake_script(calls))
    sent: list[Any] = []

    def complete(slot, messages, **options):
        sent.append((slot, messages))
        return answer('{"background": "#fafafa", "regions": [{"kind": "photo", "x": 0, "y": 0, "w": 40, "h": 100}, {"kind": "text", "x": 45, "y": 10, "w": 50, "h": 20, "text": "Hi"}]}')

    out = picture._analyze(check_of("analyze"), analyze_ctx(tmp_path, complete))
    assert out["passed"] is True and out["source"] == "model" and len(out["regions"]) == 2 and out["background"] == "#fafafa"
    assert sent[0][1][1]["content"][0]["type"] == "image_url" and "1000x500" in sent[0][1][1]["content"][-1]["text"]
    assert calls[-1]["regions"] == [{"id": 1, "x": 0, "y": 0, "w": 400, "h": 500}, {"id": 2, "x": 450, "y": 50, "w": 500, "h": 100}]
    assert (tmp_path / "public/crops/region-2.png").is_file() and json.loads((picture.research(tmp_path) / "composition.json").read_text(encoding="utf-8"))["width"] == 1000


@pytest.mark.parametrize("complete", [lambda slot, messages, **o: answer("I cannot see pictures."), None])
def test_analyze_falls_back_on_the_bands_of_the_picture_when_the_model_does_not_help(tmp_path, engine, monkeypatch, complete):
    monkeypatch.setattr(picture, "_script", fake_script())
    out = picture._analyze(check_of("analyze"), analyze_ctx(tmp_path, complete))
    assert out["passed"] is True and out["source"] == "bands" and len(out["regions"]) == 2 and "bands" in out["summary"]


def compose_ctx(tmp_path: Path, complete: Any) -> CheckContext:
    prepared(tmp_path)
    folder = picture.research(tmp_path)
    (folder / "crops").mkdir(parents=True, exist_ok=True)
    (folder / "composition.json").write_text(json.dumps(COMPOSITION), encoding="utf-8")
    for n in (1, 2):
        (folder / "crops" / f"region-{n}.jpg").write_bytes(PNG)
    return make_ctx(tmp_path, {"prepare": {"passed": True, "reference": "docs/reference/reference.png", "palette": ANALYSIS["palette"]}, "analyze": {"passed": True}}, complete)


GOOD = ("```tsx\nexport default function Region2() { return <div className=\"r2\"><h1 className=\"r2-title\">Hello</h1></div>; }\n```\n"
        "```css\n.r2 { width: 100%; height: 100%; }\n.r2-title { color: red; }\n```")


def test_compose_has_the_model_write_each_region_and_the_picture_is_the_photo(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(picture, "_build", lambda ctx, install: (True, []))
    asked: list[Any] = []
    out = picture._compose(check_of("compose", brief="un sito"), compose_ctx(tmp_path, lambda slot, messages, **o: asked.append(messages) or answer(GOOD)))
    assert out["passed"] is True and out["model_regions"] == 1 and [m["how"] for m in out["regions"]] == ["photo", "model"]
    assert len(asked) == 1 and "Region 2 (text)" in asked[0][1]["content"][-1]["text"] and "un sito" in asked[0][1]["content"][-1]["text"]
    assert "Hello" in picture.region_file(tmp_path, 2).read_text(encoding="utf-8") and "/crops/region-1.png" in picture.region_file(tmp_path, 1).read_text(encoding="utf-8")
    assert ".r2-title" in (tmp_path / "src/app/regions.css").read_text(encoding="utf-8")


def test_a_region_the_model_cannot_write_is_the_pictures_own_crop_and_the_report_says_so(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(picture, "_build", lambda ctx, install: (True, []))
    out = picture._compose(check_of("compose"), compose_ctx(tmp_path, lambda slot, messages, **o: answer("Sorry, I can't do that.")))
    assert out["passed"] is True and out["model_regions"] == 0 and out["regions"][1]["how"] == "crop"
    assert "/crops/region-2.png" in picture.region_file(tmp_path, 2).read_text(encoding="utf-8")


def test_a_component_that_does_not_compile_is_asked_for_again_then_replaced_by_the_crop(tmp_path, engine, monkeypatch):
    builds = iter([(False, ["Error", "./src/components/regions/Region2.tsx:3", "Unexpected token"]), (False, ["./src/components/regions/Region2.tsx:3"]), (True, [])])
    monkeypatch.setattr(picture, "_build", lambda ctx, install: next(builds))
    asked: list[Any] = []
    out = picture._compose(check_of("compose"), compose_ctx(tmp_path, lambda slot, messages, **o: asked.append(messages) or answer(GOOD)))
    assert out["passed"] is True and out["regions"][1]["how"] == "crop"
    assert len(asked) == 2 and "The compiler said" in asked[1][1]["content"][-1]["text"] and "Unexpected token" in asked[1][1]["content"][-1]["text"]
    assert "/crops/region-2.png" in picture.region_file(tmp_path, 2).read_text(encoding="utf-8")


def test_style_rules_the_compiler_refuses_are_dropped_and_a_page_that_never_builds_is_reported(tmp_path, engine, monkeypatch):
    builds = iter([(False, ["Parsing css source code failed", "./src/app/regions.css:4"]), (True, [])])
    monkeypatch.setattr(picture, "_build", lambda ctx, install: next(builds))
    out = picture._compose(check_of("compose"), compose_ctx(tmp_path, lambda slot, messages, **o: answer(GOOD)))
    assert out["passed"] is True and picture.read_css(tmp_path) == {}
    monkeypatch.setattr(picture, "_build", lambda ctx, install: (False, ["Error: boom"]))
    broken = picture._compose(check_of("compose"), compose_ctx(tmp_path / "b", lambda slot, messages, **o: answer(GOOD)))
    assert broken["passed"] is False and broken["verdict"] == "BUILD_FAILED"


def report(accuracy: float, regions: dict[int, float]) -> dict[str, Any]:
    return {"accuracy": accuracy, "formula": "f", "viewports": {"reference": {
        "width": 1000, "visual": accuracy, "pixels": accuracy, "ssim": accuracy, "layout": 1, "text": None, "referenceHeight": 500, "cloneHeight": 500,
        "diff": "diff-reference.png", "original": "original-reference.jpg", "preview": "clone-reference.jpg", "errors": [], "failed": [],
        "sections": [{"id": i, "name": f"r{i}", "visual": v, "layout": 1} for i, v in regions.items()]}}}


def measure_ctx(tmp_path: Path, previous: dict | None = None) -> CheckContext:
    ctx = compose_ctx(tmp_path, lambda slot, messages, **o: answer(GOOD))
    ctx.outputs["compose"] = {"regions": [{"id": 1, "kind": "photo", "how": "photo"}, {"id": 2, "kind": "text", "how": "model"}]}
    if previous:
        ctx.outputs["measure"] = previous
    return ctx


def test_the_measure_separates_what_the_model_built_from_the_pictures_own_crops(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(picture, "_measure_once", lambda ctx, label, settle_ms=800: report(0.9, {1: 1.0, 2: 0.6}))
    out = picture._measure(check_of("measure"), measure_ctx(tmp_path))
    assert out["passed"] is True and out["accuracy"] == 0.9 and out["built_accuracy"] == 0.6 and out["copied_share"] == 0.4
    assert out["weak"] == [{"id": 2, "name": "r2", "visual": 0.6}] and out["should_refine"] is True and out["round"] == 1
    assert "60.0% over the regions the model wrote" in out["summary"] and out["viewports"]["reference"]["diff"] == "diff-reference.png"


def rewritten(tmp_path: Path, before: float) -> CheckContext:
    """A run in its second round: region 2 was rewritten, and had scored ``before`` in the round before."""
    ctx = measure_ctx(tmp_path, {"round": 1, "built_accuracy": before, "sections": {"reference": [{"id": 1, "visual": 1.0}, {"id": 2, "visual": before}]}})
    ctx.outputs["refine"] = {"changed": [2]}
    backup = picture.research(tmp_path) / "backup"
    (backup / "regions").mkdir(parents=True)
    (backup / "regions" / "Region2.tsx").write_text("// the old code\nexport default function Region2() { return <div />; }", encoding="utf-8")
    (backup / "regions.css.json").write_text(json.dumps({"2": ".r2 { color: blue; }"}), encoding="utf-8")
    picture.write_component(tmp_path, 2, "export default function Region2() { return <div>the new code</div>; }")
    picture.write_css(tmp_path, {2: ".r2 { color: red; }"})
    return ctx


def test_a_region_the_rewrite_made_worse_gets_its_old_code_back(tmp_path, engine, monkeypatch):
    ctx = rewritten(tmp_path, 0.8)
    reports = iter([report(0.7, {1: 1.0, 2: 0.4}), report(0.85, {1: 1.0, 2: 0.8})])
    monkeypatch.setattr(picture, "_measure_once", lambda c, label, settle_ms=800: next(reports))
    builds: list[int] = []
    monkeypatch.setattr(picture, "_build", lambda c, install: builds.append(1) or (True, []))
    out = picture._measure(check_of("measure"), ctx)
    assert out["reverted"] is True and out["undone"] == [2] and out["round"] == 2 and out["built_accuracy"] == 0.8 and builds == [1]
    assert "the old code" in picture.region_file(tmp_path, 2).read_text(encoding="utf-8") and picture.read_css(tmp_path) == {2: ".r2 { color: blue; }"}
    assert "rewrite of region 2 made it worse and was undone" in out["summary"]


def test_a_region_the_rewrite_made_better_stays(tmp_path, engine, monkeypatch):
    ctx = rewritten(tmp_path, 0.5)
    monkeypatch.setattr(picture, "_measure_once", lambda c, label, settle_ms=800: report(0.9, {1: 1.0, 2: 0.7}))
    monkeypatch.setattr(picture, "_build", lambda c, install: (_ for _ in ()).throw(AssertionError("nothing to put back: it must not build again")))
    out = picture._measure(check_of("measure"), ctx)
    assert out["reverted"] is False and out["undone"] == [] and "the new code" in picture.region_file(tmp_path, 2).read_text(encoding="utf-8")
    assert picture.read_css(tmp_path) == {2: ".r2 { color: red; }"} and out["should_refine"] is True


def test_refine_writes_the_weakest_regions_again_and_keeps_the_old_code_to_come_back_to(tmp_path, engine, monkeypatch):
    ctx = measure_ctx(tmp_path)
    ctx.outputs["measure"] = {"weak": [{"id": 2, "name": "r2", "visual": 0.5}], "round": 1}
    picture.write_component(tmp_path, 2, "export default function Region2() { return <div>old</div>; }")
    monkeypatch.setattr(picture, "_script", fake_script())
    (picture.research(tmp_path) / "report").mkdir(parents=True, exist_ok=True)
    (picture.research(tmp_path) / "crops" / "region-clone-2.jpg").write_bytes(PNG)
    monkeypatch.setattr(picture, "_build", lambda c, install: (True, []))
    sent: list[Any] = []
    ctx.complete = lambda slot, messages, **o: sent.append(messages) or answer(GOOD)
    out = picture._refine(check_of("refine"), ctx)
    assert out["changed"] == [2] and out["verdict"] == "REFINED" and "Hello" in picture.region_file(tmp_path, 2).read_text(encoding="utf-8")
    assert "old" in (picture.research(tmp_path) / "backup/regions/Region2.tsx").read_text(encoding="utf-8")
    user = sent[0][1]["content"]
    assert len([c for c in user if c["type"] == "image_url"]) == 2 and "matches 50%" in user[-1]["text"] and "<div>old</div>" in user[-1]["text"]


def test_a_rewrite_that_does_not_compile_is_undone_at_once(tmp_path, engine, monkeypatch):
    ctx = measure_ctx(tmp_path)
    ctx.outputs["measure"] = {"weak": [{"id": 2, "name": "r2", "visual": 0.5}]}
    picture.write_component(tmp_path, 2, "export default function Region2() { return <div>old</div>; }")
    monkeypatch.setattr(picture, "_script", fake_script())
    builds = iter([(False, ["Error"]), (True, [])])
    monkeypatch.setattr(picture, "_build", lambda c, install: next(builds))
    out = picture._refine(check_of("refine"), ctx)
    assert out["changed"] == [] and "old" in picture.region_file(tmp_path, 2).read_text(encoding="utf-8")


def motion_ctx(tmp_path: Path, brief: str, answer_text: str) -> CheckContext:
    ctx = compose_ctx(tmp_path, lambda slot, messages, **o: answer(answer_text))
    ctx.outputs["measure"] = {"accuracy": 0.9}
    return ctx


MOTION = '{"intro": {"style": "depth", "duration": 2.5, "stagger": 0.08, "ease": "power3.out", "distance": 40}, "parallax": {"amount": 18}, "hover": {"lift": 8, "scale": 1.02}}'


def test_the_numbers_of_a_models_answer_are_clamped_to_what_the_component_allows():
    config = picture.motion_config('Sure!\n{"intro": {"style": "bounce", "duration": 99, "stagger": -1, "ease": "elastic.out", "distance": 500}, '
                                   '"parallax": {"amount": "lots"}, "hover": {"lift": 50, "scale": 3}}')
    assert config == {"intro": {"style": "depth", "duration": 4, "stagger": 0, "ease": "power3.out", "distance": 120}, "parallax": {"amount": 16},
                      "hover": {"lift": 20, "scale": 1.08}}
    assert picture.motion_config(MOTION)["intro"] == {"style": "depth", "duration": 2.5, "stagger": 0.08, "ease": "power3.out", "distance": 40}
    assert picture.motion_config("no numbers") is None and picture.motion_config("{broken") is None and picture.motion_config("[1]") is None
    assert picture.motion_config("{}")["parallax"] == {"amount": 16}


def test_the_component_ends_every_animation_by_clearing_what_it_set_and_does_nothing_for_reduced_motion():
    code = picture.motion_component(picture.motion_config(MOTION))
    assert code.startswith('"use client";') and '"duration": 2.5' in code and "__CONFIG__" not in code
    assert 'clearProps: "opacity,transform"' in code and "gsap.context" in code and "context.revert()" in code
    assert "prefers-reduced-motion" in code and 'bodyOf = (box' in code and "gsap.delayedCall" in code


def test_motion_stays_only_if_the_page_at_rest_still_matches_the_picture(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(picture, "_build", lambda c, install: (True, []))
    seen: list[int] = []
    monkeypatch.setattr(picture, "_measure_once", lambda c, label, settle_ms=800: seen.append(settle_ms) or report(0.89, {1: 1.0}))
    out = picture._motion(check_of("motion", brief="con animazioni e parallax"), motion_ctx(tmp_path, "x", MOTION))
    # The page is measured when the intro is over (2.5 s, two regions 0.08 s apart) and a second and a half after.
    assert out["verdict"] == "ADDED" and out["kept"] is True and seen == [4160] and out["config"]["parallax"]["amount"] == 18
    motion = (tmp_path / "src/components/Motion.tsx").read_text(encoding="utf-8")
    assert '"use client"' in motion and "gsap.from" in motion and "import Motion" in (tmp_path / "src/app/page.tsx").read_text(encoding="utf-8")
    assert "a depth intro of 2.5 s, parallax 18 px" in out["summary"]


def test_motion_that_moves_the_page_at_rest_is_taken_out_again(tmp_path, engine, monkeypatch):
    builds: list[int] = []
    monkeypatch.setattr(picture, "_build", lambda c, install: builds.append(1) or (True, []))
    monkeypatch.setattr(picture, "_measure_once", lambda c, label, settle_ms=800: report(0.6, {1: 0.6}))
    out = picture._motion(check_of("motion", brief="intro e parallax"), motion_ctx(tmp_path, "x", MOTION))
    assert out["verdict"] == "REVERTED" and not (tmp_path / "src/components/Motion.tsx").exists() and "<Motion" not in (tmp_path / "src/app/page.tsx").read_text(encoding="utf-8")
    assert builds == [1, 1] and "it moved the page at rest" in out["summary"]


def test_motion_is_skipped_when_the_brief_does_not_ask_for_it_or_the_answer_has_no_numbers(tmp_path, engine, monkeypatch):
    assert picture._motion(check_of("motion", brief="Un sito semplice."), motion_ctx(tmp_path, "x", MOTION))["verdict"] == "SKIPPED"
    assert picture._motion(check_of("motion", brief="con animazioni"), motion_ctx(tmp_path / "b", "x", "I would use GSAP."))["verdict"] == "SKIPPED"
    assert not (tmp_path / "src/components/Motion.tsx").exists()


# -- the start ------------------------------------------------------------------------------------------------------------------


def make_studio(db, tmp_path):
    manager = RunManager(db, FakeRuntime({"*": ["ok"]}), bus=EventBus(), checkpoint_path=":memory:")
    install = BrowserInstall(tmp_path / "engine", which=lambda name: None, bus=EventBus())
    return studio.CloneStudio(db, manager, fake_store(tmp_path / "kits"), install), manager


def reference(data: bytes = PNG, name: str = "template.png") -> dict[str, str]:
    return {"name": name, "data": base64.b64encode(data).decode("ascii")}


def test_a_site_from_a_picture_keeps_the_picture_and_the_brief_in_its_own_folder(db, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "install_source", None)
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_picture(reference(), brief="Un sito elegante\ncon animazioni", model_key="m", sees=True, language="it")
    manager.wait(record.id, 30)
    root = study.workspace(entry)
    assert entry["mode"] == "picture" and entry["title"] == "Un sito elegante" and (root / "docs/reference/reference.png").read_bytes() == PNG
    assert db.kv_get("run_definition", record.id)["workflow"]["id"] == picture.WORKFLOW and "scrivi il rapporto in italiano" in record.task
    assert study.get(entry["id"])["shots"] == ["docs/reference/reference.png"] and study.list()[0]["mode"] == "picture"
    again, second = study.start("", model_key="m", sees=True, study_id=entry["id"])
    manager.wait(second.id, 30)
    assert again["mode"] == "picture" and again["brief"] == "Un sito elegante\ncon animazioni" and again["reference"] == entry["reference"]


def test_a_picture_needs_a_model_that_sees_and_must_be_a_picture(db, tmp_path):
    study, _ = make_studio(db, tmp_path)
    before = sorted(p.name for p in paths.workspaces_dir().glob("clone-*"))
    with pytest.raises(studio.CloneError, match="can see"):
        study.start_picture(reference(), model_key="m", sees=False)
    with pytest.raises(studio.CloneError, match="not a picture"):
        study.start_picture(reference(b"<html>nothing</html>"), model_key="m", sees=True)
    with pytest.raises(studio.CloneError, match="at most 25 MB"):
        study.start_picture({"name": "a.png", "data": ""}, model_key="m", sees=True)
    # What was refused leaves nothing behind: no clone in the list, no folder of its own.
    assert study.list() == [] and sorted(p.name for p in paths.workspaces_dir().glob("clone-*")) == before
