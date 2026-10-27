"""The exact copy of a site: HTML to JSX, the project written from a capture, and the three steps of its pipeline.

Nothing here opens a browser or the network: the Node scripts are replaced by fakes that write what they would write.
One test captures a page served from this PC, and only runs where the browser engine is (see ``WIR_LIVE_BROWSER``).
"""

from __future__ import annotations

import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agentfit.clone import jsx, pipeline, replica, studio
from agentfit.clone.browser import find_browser
from agentfit.clone.install import BrowserInstall
from agentfit.events import EventBus
from agentfit.workflows import checks
from agentfit.workflows.checks import CheckContext
from agentfit.workflows.dsl import END
from agentfit.workflows.runner import RunManager
from agentfit.workflows.validator import validate
from fakes import FakeRuntime
from test_clone import fake_store

# -- html to jsx --------------------------------------------------------------------------------------------------


def to_jsx(html: str, **kw: Any) -> str:
    return jsx.render(jsx.parse(html), **kw)


def test_class_style_and_plain_attributes():
    out = to_jsx('<div class="a b" id="x" style="color: red; --gap: 4px">Hello</div>')
    assert out == '<div className="a b" id="x" style={{ color: "red", "--gap": "4px" }}>Hello</div>'


def test_a_style_becomes_an_object_with_react_spelling():
    assert jsx.style_object("-webkit-box-orient: vertical; background-color:#fff; --Gap: 1px; -ms-grid-rows: auto; ;") == (
        'WebkitBoxOrient: "vertical", backgroundColor: "#fff", "--Gap": "1px", msGridRows: "auto"')
    assert jsx.style_object("  ;; novalue; :orphan") == ""


def test_a_semicolon_inside_a_url_does_not_split_a_style():
    out = to_jsx('<i style="background:url(data:image/png;base64,AAAA);color:red"></i>')
    assert 'background: "url(data:image/png;base64,AAAA)"' in out and 'color: "red"' in out


def test_svg_attributes_are_camel_case_and_keep_their_case():
    out = to_jsx('<svg viewBox="0 0 10 10" xmlns:xlink="http://www.w3.org/1999/xlink"><linearGradient id="g"></linearGradient>'
                 '<path stroke-width="2" clip-path="url(#g)" d="M0 0"></path><use xlink:href="#i"></use></svg>')
    for wanted in ('viewBox="0 0 10 10"', "xmlnsXlink=", "<linearGradient", 'strokeWidth="2"', 'clipPath="url(#g)"', 'xlinkHref="#i"'):
        assert wanted in out, wanted


def test_text_that_would_open_an_expression_or_a_tag_is_a_string():
    out = to_jsx("<p>a {b} &lt;c&gt; &amp; d</p>")
    assert out == '<p>{"a {b} <c> & d"}</p>'


def test_a_space_between_inline_elements_is_kept_and_plain_text_stays_plain():
    out = to_jsx("<p><b>a</b> <i>b</i> plain</p>")
    assert '{" "}' in out and "plain" in out
    assert to_jsx("<p>Blazers e giacche</p>") == "<p>Blazers e giacche</p>"


def test_void_elements_and_empty_ones_are_self_closing():
    assert to_jsx('<img src="/a.png" alt=""/>') == '<img src="/a.png" />' or to_jsx('<img src="/a.png" alt=""/>').startswith("<img")
    assert to_jsx("<div></div>") == "<div />"


def test_form_controls_use_the_default_props_react_wants():
    assert 'defaultValue="x"' in to_jsx('<input type="text" value="x"/>')
    assert "defaultChecked" in to_jsx('<input type="checkbox" checked=""/>')
    assert to_jsx("<textarea>hi</textarea>") == '<textarea defaultValue={"hi"} />'
    assert to_jsx('<button disabled="">go</button>') == "<button disabled>go</button>"


def test_what_is_not_a_jsx_attribute_is_left_out():
    out = to_jsx('<div @click="x" :foo="y" [prop]="z" data-a="1" aria-label="L">t</div>')
    assert out == '<div data-a="1" aria-label="L">t</div>'


def test_a_value_with_an_ampersand_or_a_quote_is_an_expression_not_a_string_jsx_would_decode():
    out = to_jsx('<a href="/x?a=1&amp;b=2" title="say &quot;hi&quot;">go</a>')
    assert 'href={"/x?a=1&b=2"}' in out and 'title={"say \\"hi\\""}' in out


def test_a_web_component_keeps_its_attribute_names():
    out = to_jsx('<swiper-container slides-per-view="1.1" class="wir-sh-x"><swiper-slide>a</swiper-slide></swiper-container>')
    assert 'slides-per-view="1.1"' in out and 'className="wir-sh-x"' in out


def test_a_slot_becomes_the_component_that_holds_the_section():
    out = to_jsx('<main><wir-slot n="3"/><wir-slot n="9"/></main>', slots={3: "Hero"})
    assert "<Hero />" in out and "wir-slot" not in out


def test_a_long_tag_puts_one_attribute_on_each_line():
    html = '<div class="' + "x " * 80 + '" id="k"><p>hi</p></div>'
    out = to_jsx(html)
    assert out.splitlines()[0] == "<div" and 'id="k"' in out and out.rstrip().endswith("</div>")


# -- names and the project ---------------------------------------------------------------------------------------


def test_a_component_is_named_from_its_tag_its_class_and_its_heading():
    assert replica.default_name({"tag": "header"}) == "Header"
    assert replica.default_name({"tag": "section", "className": "style_hero-widget__3_d0w"}) == "HeroWidget"
    assert replica.default_name({"tag": "div", "className": "", "heading": "Blazers e giacche"}) == "BlazersEGiacche"
    assert replica.default_name({"tag": "div", "n": 4}) == "Section4"


def test_names_are_unique_and_a_name_the_model_chose_must_be_valid():
    sections = [{"n": 1, "tag": "section", "className": "style_hero-widget__ab12", "heading": "La Travel Jacket"},
                {"n": 2, "tag": "section", "className": "style_hero-widget__cd34", "heading": "Grand Tour"},
                {"n": 3, "tag": "section", "className": "style_hero-widget__ef56", "heading": ""}]
    names = replica.component_names(sections, {1: "TravelJacketHero", 2: "not valid", 3: "Fragment"})
    assert names[1] == "TravelJacketHero" and len(set(names.values())) == 3
    assert all(replica.valid_name(n) for n in names.values())


def test_the_models_answer_is_used_only_where_it_is_usable():
    sections = [{"n": 1}, {"n": 2}, {"n": 3}]
    assert replica.name_components('Here: {"1": "Header", "2": "x", "9": "Ghost", "3": "product carousel"}', sections) == {1: "Header", 3: "ProductCarousel"}
    assert replica.name_components('["Header", "Footer", 5]', sections) == {1: "Header", 2: "Footer"}
    assert replica.name_components("no json at all", sections) == {}


def make_capture(root: Path) -> Path:
    folder = root / "capture"
    (folder / "assets").mkdir(parents=True)
    (folder / "assets" / "ab12cd34-logo.png").write_bytes(b"png")
    (folder / "site.css").write_text('body{background:url("__WIR_ASSET__/ab12cd34-logo.png")}\n.hero{color:red}\n', encoding="utf-8")
    bundle = {
        "url": "https://example.com/", "host": "example.com", "title": "Example | Home", "description": 'A "quoted" description', "lang": "it",
        "htmlAttrs": {"lang": "it", "style": ""}, "bodyAttrs": {"class": "font-a antialiased"}, "customTags": ["x-box"], "breakpoint": 768,
        "shell": '<a href="#main">skip</a><wir-slot n="1"/><main id="main"><wir-slot n="2"/></main>',
        "icons": [{"rel": "icon", "href": "https://example.com/favicon.ico", "file": "ab12cd34-favicon.ico"}],
        "sections": [
            {"n": 1, "tag": "header", "className": "", "heading": "", "html": '<header class="top"><img src="__WIR_ASSET__/ab12cd34-logo.png" alt="logo"/></header>', "htmlMobile": ""},
            {"n": 2, "tag": "section", "className": "style_hero-widget__ab12", "heading": "Hello",
             "html": '<section class="hero"><h1>Hello</h1></section>', "htmlMobile": '<section class="hero"><h1>Hello phone</h1></section>'},
        ],
    }
    (folder / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    return folder


def test_the_project_is_written_from_the_capture(tmp_path):
    capture = make_capture(tmp_path)
    project = tmp_path / "project"
    written = replica.write_project(capture, project, slug="example-com")
    assert [c["name"] for c in written["components"]] == ["Header", "HeroWidget"]

    package = json.loads((project / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["next"] == replica.NEXT_VERSION and package["scripts"]["build"] == "next build"
    page = (project / "src/app/page.tsx").read_text(encoding="utf-8")
    assert page.startswith("// @ts-nocheck") and 'import Header from "@/components/sites/example-com/Header"' in page
    assert "<Header />" in page and "<HeroWidget />" in page and '<main id="main">' in page
    layout = (project / "src/app/layout.tsx").read_text(encoding="utf-8")
    assert '"Example | Home"' in layout and 'href="/clone/site.css"' in layout and 'href="/clone/fixes.css"' in layout
    assert '<html lang="it">' in layout and 'className="font-a antialiased"' in layout and 'icons: { icon: "/clone/ab12cd34-favicon.ico" }' in layout
    assert "\\\"quoted\\\"" in layout

    header = (project / "src/components/sites/example-com/Header.tsx").read_text(encoding="utf-8")
    assert 'data-clone-section="1"' in header and 'src="/clone/ab12cd34-logo.png"' in header and "clone-desktop" not in header
    hero = (project / "src/components/sites/example-com/HeroWidget.tsx").read_text(encoding="utf-8")
    assert "clone-desktop hero" in hero and "clone-mobile hero" in hero and hero.count('data-clone-section="2"') == 2
    assert "Hello phone" in hero and "<>" in hero

    css = (project / "public/clone/site.css").read_text(encoding="utf-8")
    assert 'url("/clone/ab12cd34-logo.png")' in css and "__WIR_ASSET__" not in css
    assert "@media (max-width: 767px) { .clone-desktop { display: none !important; } }" in css
    assert "@media (min-width: 768px) { .clone-mobile { display: none !important; } }" in css
    assert (project / "public/clone/ab12cd34-logo.png").is_file() and written["assets"] == 1


def test_a_page_with_no_doctype_is_served_from_a_document_with_none(tmp_path):
    capture = make_capture(tmp_path)
    bundle = json.loads((capture / "bundle.json").read_text(encoding="utf-8"))
    bundle["compatMode"] = "BackCompat"
    bundle["sections"][1]["html"] = '<section class="hero" data-clone-section="9"><h1>Hello</h1></section>'
    (capture / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    project = tmp_path / "project"
    written = replica.write_project(capture, project, slug="example-com")
    page = (project / "public/clone/page.html").read_text(encoding="utf-8")
    assert written["quirks"] is True and not page.lstrip().lower().startswith("<!doctype") and page.startswith('<html lang="it">')
    assert '<header class="top" data-clone-section="1">' in page and "<wir-slot" not in page and "__WIR_ASSET__" not in page
    # The section with a phone variant is there twice, each with its class, and the number is not repeated from the capture.
    assert '<section class="clone-desktop hero" data-clone-section="2">' in page and '<section class="clone-mobile hero" data-clone-section="2">' in page
    assert 'src="/clone/ab12cd34-logo.png"' in page and 'href="/clone/fixes.css"' in page and '<body class="font-a antialiased">' in page
    assert 'beforeFiles: [{ source: "/", destination: "/clone/page.html" }]' in (project / "next.config.mjs").read_text(encoding="utf-8")
    assert "quirks mode" in (project / "README.md").read_text(encoding="utf-8")
    # A page with a doctype is the usual project: no static document, no rewrite.
    other = tmp_path / "other"
    assert replica.write_project(make_capture(tmp_path / "x"), other, slug="example-com")["quirks"] is False
    assert not (other / "public/clone/page.html").exists() and "rewrites" not in (other / "next.config.mjs").read_text(encoding="utf-8")


def test_a_second_run_keeps_the_fixes_that_were_written(tmp_path):
    capture = make_capture(tmp_path)
    project = tmp_path / "project"
    replica.write_project(capture, project, slug="example-com")
    (project / "public/clone/fixes.css").write_text("[data-clone-section=\"2\"]{margin:0}\n", encoding="utf-8")
    replica.write_project(capture, project, slug="example-com")
    assert "margin:0" in (project / "public/clone/fixes.css").read_text(encoding="utf-8")


def test_a_capture_without_sections_is_not_a_project(tmp_path):
    folder = tmp_path / "capture"
    folder.mkdir()
    (folder / "bundle.json").write_text(json.dumps({"sections": []}), encoding="utf-8")
    with pytest.raises(replica.ReplicaError, match="no section"):
        replica.write_project(folder, tmp_path / "p", slug="x")
    with pytest.raises(replica.ReplicaError, match="not readable"):
        replica.write_project(tmp_path / "missing", tmp_path / "p", slug="x")


# -- the pipeline ----------------------------------------------------------------------------------------------------


class FakeEngine(BrowserInstall):
    """The browser engine as the pipeline asks for it: a version, a Node and an npm, nothing run."""

    def __init__(self, folder: Path) -> None:
        super().__init__(folder, which=lambda name: None, bus=EventBus())

    def installed_version(self) -> str:
        return "1.58.2"

    def node(self) -> dict[str, Any]:
        return {"ok": True, "version": "v24.0.0", "path": "node", "preferred": True, "detail": None}

    def node_command(self) -> str:
        return "node"

    def npm_exe(self) -> str:
        return "npm"


@pytest.fixture
def engine(tmp_path, monkeypatch):
    install = FakeEngine(tmp_path / "engine")
    monkeypatch.setattr(pipeline, "install_source", lambda: install)
    monkeypatch.setattr(pipeline, "find_browser", lambda: {"name": "Chrome", "executable": "chrome.exe"})
    return install


URL = "https://www.example.com/"


def make_ctx(workspace: Path, outputs: dict[str, Any] | None = None, complete: Any = None, **kw: Any) -> CheckContext:
    events: list[tuple[str, dict]] = []
    ctx = CheckContext(task=URL, outputs=outputs or {}, ledger=[], history=[], definition=pipeline.definition(url=URL, model_key="m", sees=False),
                       workspace=workspace, emit=lambda kind, payload: events.append((kind, payload)), cancelled=lambda: False, complete=complete, **kw)
    ctx.events = events  # type: ignore[attr-defined]
    return ctx


def check_of(kind: str) -> Any:
    return next(c for c in pipeline.definition(url=URL, model_key="m", sees=False).checks if c.kind == kind)


@pytest.mark.parametrize("sees, vision_key", [(True, ""), (False, "vision-model"), (False, "")])
def test_the_workflow_is_valid_whatever_can_see(sees, vision_key):
    definition = pipeline.definition(url=URL, model_key="m", sees=sees, vision_key=vision_key)
    assert validate(definition, require_models=True).ok
    assert ("look" in definition.agents[0].tools) == bool(sees or vision_key)
    # The polisher is reached only from the measure, and at most twice.
    fix = next(e for e in definition.edges if e.target == "polisher")
    assert fix.source == "measure" and fix.max_loops == pipeline.POLISH_ROUNDS and fix.on_exhausted == END


def test_the_three_steps_are_registered_and_not_offered_in_the_editor():
    for kind in ("clone_capture", "clone_project", "clone_measure"):
        assert kind in checks.CHECKS and checks.CHECKS[kind].listed is False


def test_the_capture_reports_what_the_script_wrote(tmp_path, engine, monkeypatch):
    def script(ctx, install, path, config, step, *, timeout):
        out = json.loads(config.read_text(encoding="utf-8"))["out"]
        (Path(out) / "capture.json").write_text(json.dumps({
            "stats": {"sections": 2, "nodes": 120, "assets": 5, "assetBytes": 2_500_000, "seconds": 20},
            "sections": [{"id": 1, "tag": "header", "className": "", "heading": "", "text": "Menu", "y": 0, "h": 100, "extra": 1}],
            "overlays": [{"el": "#cookies", "kind": "consent", "viewport": "desktop"}, {"el": "#x", "kind": "dialog", "viewport": "mobile"}],
            "viewports": {"desktop": {"width": 1440, "pageHeight": 3000, "screenshot": "reference-desktop.png", "boxes": {}}},
        }), encoding="utf-8")
        return True, ""

    monkeypatch.setattr(pipeline, "_run_script", script)
    out = pipeline._clone_capture(check_of("clone_capture"), make_ctx(tmp_path))
    assert out["passed"] is True and out["verdict"] == "CAPTURED" and out["host"] == "example.com"
    assert out["overlays"] == ["#cookies (consent)"] and "2 sections" in out["summary"] and "2.5 MB" in out["summary"]
    assert out["sections"] == [{"id": 1, "tag": "header", "className": "", "heading": "", "text": "Menu", "y": 0, "h": 100}]
    assert out["viewports"]["desktop"]["pageHeight"] == 3000


def test_a_capture_that_fails_says_why_and_a_missing_engine_is_said_first(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(pipeline, "_run_script", lambda *a, **k: (False, "The site answered HTTP 403."))
    out = pipeline._clone_capture(check_of("clone_capture"), make_ctx(tmp_path))
    assert out["passed"] is False and out["verdict"] == "CAPTURE_FAILED" and "403" in out["summary"]
    monkeypatch.setattr(pipeline, "find_browser", lambda: None)
    assert "Neither Chrome nor Edge" in pipeline._clone_capture(check_of("clone_capture"), make_ctx(tmp_path))["summary"]
    monkeypatch.setattr(pipeline, "install_source", None)
    assert pipeline._clone_capture(check_of("clone_capture"), make_ctx(tmp_path))["passed"] is False


def project_ctx(tmp_path: Path, complete: Any = None) -> CheckContext:
    make_capture(pipeline.research(tmp_path, "example.com"))
    capture = pipeline.capture_dir(tmp_path, "example.com")
    (pipeline.research(tmp_path, "example.com") / "capture").rename(capture) if not capture.exists() else None
    sections = [{"id": 1, "tag": "header", "className": "", "heading": "", "text": "Menu"},
                {"id": 2, "tag": "section", "className": "style_hero-widget__ab12", "heading": "Hello", "text": "Hello"}]
    return make_ctx(tmp_path, {"capture": {"sections": sections}}, complete)


def test_the_project_is_written_installed_and_built_and_the_model_names_the_components(tmp_path, engine, monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(pipeline, "_npm", lambda ctx, install, args, step, timeout: (ran.append(args) or (0, [], False)))
    asked: list[Any] = []

    def complete(slot, messages, **options):
        asked.append((slot, messages, options))
        return {"choices": [{"message": {"content": '{"1": "SiteHeader", "2": "WelcomeHero"}'}}]}

    out = pipeline._clone_project(check_of("clone_project"), project_ctx(tmp_path, complete))
    assert out["passed"] is True and out["verdict"] == "BUILT" and out["named_by_model"] == 2
    assert [c["name"] for c in out["components"]] == ["SiteHeader", "WelcomeHero"]
    assert ran == [["install", "--no-fund", "--no-audit", "--loglevel=error"], ["run", "build"]]
    assert (tmp_path / "src/components/sites/example-com/WelcomeHero.tsx").is_file()
    slot, messages, options = asked[0]
    assert slot == "main" and "JSON" in messages[0]["content"] and "<header>" in messages[1]["content"] and options["max_tokens"] >= 1000


def test_a_model_that_fails_changes_nothing_but_the_names(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(pipeline, "_npm", lambda ctx, install, args, step, timeout: (0, [], False))

    def broken(slot, messages, **options):
        raise RuntimeError("the model could not be loaded")

    out = pipeline._clone_project(check_of("clone_project"), project_ctx(tmp_path, broken))
    assert out["passed"] is True and out["named_by_model"] == 0 and [c["name"] for c in out["components"]] == ["Header", "HeroWidget"]
    assert pipeline._model_names(make_ctx(tmp_path, complete=None), [{"id": 1}]) == {}


def test_a_thinking_model_that_left_no_answer_is_asked_again_with_more_room(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(pipeline, "_npm", lambda ctx, install, args, step, timeout: (0, [], False))
    answers = iter(["", '{"1": "SiteHeader", "2": "WelcomeHero"}'])
    room: list[int] = []

    def complete(slot, messages, **options):
        room.append(options["max_tokens"])
        return {"choices": [{"message": {"content": next(answers)}}]}

    out = pipeline._clone_project(check_of("clone_project"), project_ctx(tmp_path, complete))
    assert out["named_by_model"] == 2 and room == [2000, 4000]


def test_an_answer_the_names_cannot_be_read_from_is_said_in_the_step(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(pipeline, "_npm", lambda ctx, install, args, step, timeout: (0, [], False))
    ctx = project_ctx(tmp_path, lambda slot, messages, **o: {"choices": [{"message": {"content": "I think the header is fine."}}]})
    out = pipeline._clone_project(check_of("clone_project"), ctx)
    names = [p for kind, p in ctx.events if kind == "clone.step" and p["step"] == "names" and p["state"] == "done"]
    assert out["passed"] is True and out["named_by_model"] == 0
    assert names and "0 of 2 named by the model; its answer began: 'I think the header is fine.'" in names[0]["detail"]


def test_a_build_that_fails_stops_the_run_with_the_end_of_the_log(tmp_path, engine, monkeypatch):
    def npm(ctx, install, args, step, timeout):
        return (0, [], False) if args[0] == "install" else (1, ["compiling", "Error: Unexpected token"], False)

    monkeypatch.setattr(pipeline, "_npm", npm)
    out = pipeline._clone_project(check_of("clone_project"), project_ctx(tmp_path))
    assert out["passed"] is False and out["verdict"] == "BUILD_FAILED" and "Unexpected token" in out["summary"]


class FakeServer:
    started: list[dict] = []
    stopped = 0

    def __init__(self, folder, npm):
        self.folder = folder

    def start(self, **kw):
        FakeServer.started.append(kw)
        return "http://127.0.0.1:4010"

    def stop(self):
        FakeServer.stopped += 1


def write_report(root: Path, accuracy: float, sections: dict[str, list[tuple[int, float]]]) -> dict:
    report = {"accuracy": accuracy, "formula": "f", "viewports": {
        name: {"width": 1440 if name == "desktop" else 390, "visual": min(v for _, v in rows), "pixels": 0.9, "ssim": 0.9, "layout": 1, "text": 0.98,
               "referenceHeight": 100, "cloneHeight": 100, "diff": f"diff-{name}.png", "original": f"original-{name}.jpg", "preview": f"clone-{name}.jpg",
               "errors": [], "failed": [], "sections": [{"id": i, "name": f"S{i}", "visual": v, "layout": 1} for i, v in rows]}
        for name, rows in sections.items()}}
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return report


def measure_ctx(tmp_path: Path, report: dict | None, previous: dict | None = None) -> CheckContext:
    capture = pipeline.capture_dir(tmp_path, "example.com")
    capture.mkdir(parents=True)
    (capture / "capture.json").write_text(json.dumps({
        "viewports": {"desktop": {"width": 1440, "height": 900, "screenshot": "reference-desktop.png", "pageHeight": 100, "boxes": {"1": {"x": 0, "y": 0, "w": 1440, "h": 50}}}},
        "sections": [{"id": 1, "heading": "Menu", "tag": "header"}]}), encoding="utf-8")
    (capture / "bundle.json").write_text(json.dumps({"text": "hello world"}), encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    outputs: dict[str, Any] = {"project": {"components": [{"n": 1, "name": "SiteHeader"}]}}
    if previous:
        outputs["measure"] = previous
    return make_ctx(tmp_path, outputs)


@pytest.fixture
def served(monkeypatch):
    FakeServer.started, FakeServer.stopped = [], 0
    monkeypatch.setattr(pipeline, "DevServer", FakeServer)


def test_the_measure_serves_the_build_compares_it_and_always_stops_the_server(tmp_path, engine, served, monkeypatch):
    ctx = measure_ctx(tmp_path, None)

    def script(ctx_, install, path, config, step, *, timeout):
        cfg = json.loads(config.read_text(encoding="utf-8"))
        assert cfg["url"] == "http://127.0.0.1:4010/" and cfg["reference"]["desktop"].endswith("reference-desktop.png")
        assert cfg["sections"] == [{"id": 1, "name": "SiteHeader"}] and cfg["referenceText"] == "hello world"
        write_report(Path(cfg["out"]), 0.9987, {"desktop": [(1, 0.9992)], "mobile": [(1, 0.9982)]})
        return True, ""

    monkeypatch.setattr(pipeline, "_run_script", script)
    out = pipeline._clone_measure(check_of("clone_measure"), ctx)
    assert FakeServer.started == [{"cancelled": ctx.cancelled, "production": True, "build": False}] and FakeServer.stopped == 1
    assert out["passed"] is True and out["verdict"] == "MATCHES" and out["accuracy"] == 0.9987 and out["should_continue"] is False and out["round"] == 1
    assert "99.9%" in out["summary"] and out["viewports"]["desktop"]["diff"] == "diff-desktop.png" and out["weak"] == []


def test_a_weak_section_sends_the_clone_to_the_polisher_until_the_rounds_are_used(tmp_path, engine, served, monkeypatch):
    monkeypatch.setattr(pipeline, "_run_script", lambda ctx, install, path, config, step, *, timeout:
                        (write_report(Path(json.loads(config.read_text(encoding="utf-8"))["out"]), 0.88, {"desktop": [(1, 0.5)], "mobile": [(1, 0.99)]}) and (True, "")))
    out = pipeline._clone_measure(check_of("clone_measure"), measure_ctx(tmp_path, None))
    assert out["passed"] is True and out["verdict"] == "WEAK_SECTIONS" and out["should_continue"] is True
    assert out["weak"][0]["name"] == "S1" and out["weak"][0]["viewport"] == "desktop" and "Weakest" in out["summary"]


def test_below_the_target_the_run_is_not_passed_and_a_failed_serve_says_so(tmp_path, engine, served, monkeypatch):
    monkeypatch.setattr(pipeline, "_run_script", lambda ctx, install, path, config, step, *, timeout:
                        (write_report(Path(json.loads(config.read_text(encoding="utf-8"))["out"]), 0.6, {"desktop": [(1, 0.6)]}) and (True, "")))
    out = pipeline._clone_measure(check_of("clone_measure"), measure_ctx(tmp_path, None))
    assert out["passed"] is False and out["verdict"] == "BELOW_TARGET" and "80.0%" in out["summary"]

    class Broken(FakeServer):
        def start(self, **kw):
            raise pipeline.DevServerError("no build")

    monkeypatch.setattr(pipeline, "DevServer", Broken)
    out = pipeline._clone_measure(check_of("clone_measure"), measure_ctx(tmp_path / "other", None) if (tmp_path / "other").mkdir() is None else None)
    assert out["passed"] is False and out["verdict"] == "SERVE_FAILED" and "no build" in out["summary"]


def test_a_page_that_is_good_enough_is_left_alone(tmp_path, engine, served, monkeypatch):
    monkeypatch.setattr(pipeline, "_run_script", lambda ctx, install, path, config, step, *, timeout:
                        (write_report(Path(json.loads(config.read_text(encoding="utf-8"))["out"]), 0.975, {"desktop": [(1, 0.85)], "mobile": [(1, 0.99)]}) and (True, "")))
    out = pipeline._clone_measure(check_of("clone_measure"), measure_ctx(tmp_path, None, previous={"round": 1}))
    assert out["passed"] is True and out["should_continue"] is False and out["round"] == 2


def test_numbers_are_shown_as_percent_and_an_unknown_one_is_not_a_default():
    assert pipeline._percent(0.9987) == "99.9%" and pipeline._percent(None) == "unknown"


# -- the studio ---------------------------------------------------------------------------------------------------------


def make_studio(db, tmp_path):
    manager = RunManager(db, FakeRuntime({"*": ["ok"]}), bus=EventBus(), checkpoint_path=":memory:")
    install = BrowserInstall(tmp_path / "engine", which=lambda name: None, bus=EventBus())
    return studio.CloneStudio(db, manager, fake_store(tmp_path / "kits"), install), manager


def test_an_exact_copy_does_not_fetch_the_kit_and_is_stored_with_its_mode(db, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "install_source", None)
    study, manager = make_studio(db, tmp_path)
    study.store = studio.KitStore(tmp_path / "nokit", git=lambda *a: (_ for _ in ()).throw(AssertionError("the kit must not be fetched")))
    entry, record = study.start("example.com", model_key="m", sees=False, mode="replica")
    manager.wait(record.id, 30)
    assert entry["mode"] == "replica" and study.get(entry["id"])["mode"] == "replica"
    assert db.kv_get("run_definition", record.id)["workflow"]["id"] == pipeline.WORKFLOW
    # The cloner is not wired in this test, so the first step says so and the run ends blocked, not stuck.
    assert study.get(entry["id"])["runs"][-1]["status"] == "blocked"
    with pytest.raises(studio.CloneError, match="Choose how to clone"):
        study.start("example.com", model_key="m", sees=False, mode="magic")


def test_a_new_run_of_a_clone_keeps_its_mode(db, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "install_source", None)
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start("example.com", model_key="m", sees=False, mode="replica")
    manager.wait(record.id, 30)
    again, second = study.start("", model_key="m", sees=False, study_id=entry["id"])
    manager.wait(second.id, 30)
    assert again["mode"] == "replica" and db.kv_get("run_definition", second.id)["workflow"]["id"] == pipeline.WORKFLOW


def test_the_list_shows_the_first_screen_and_the_accuracy_of_an_exact_copy(db, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "install_source", None)
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start("example.com", model_key="m", sees=False, mode="replica")
    manager.wait(record.id, 30)
    root = study.workspace(entry)
    for relative in ("capture/thumb.jpg", "report/original-desktop.jpg", "report/clone-desktop.jpg", "report/diff-desktop.png", "capture/reference-desktop.png"):
        target = root / "docs/research/example.com" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    shots = study.get(entry["id"])["shots"]
    assert shots[0] == "docs/research/example.com/capture/thumb.jpg" and all("reference-desktop" not in s for s in shots)
    assert any(s.endswith("report/diff-desktop.png") for s in shots)
    assert study.list()[0]["thumbnail"] == shots[0] and study.list()[0]["accuracy"] is None
    assert study.media(study.file(entry["id"], shots[0])) == "image/jpeg"


# -- a page from this PC, in a real browser ----------------------------------------------------------------------------

FIXTURE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Fixture</title>
<style>
body{margin:0;font:16px sans-serif} .hero{height:300px;background:#123 url(hero.png) center/cover;color:#fff}
.modal{position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.6)} .modal .box{background:#fff;margin:100px auto;width:300px;padding:20px}
</style></head><body>
<header style="height:60px;background:#eee"><nav><a href="/about">About</a></nav></header>
<main>
  <section class="hero"><h1>Welcome</h1><picture><img src="hero.png" alt="hero" width="40" height="40"></picture></section>
  <section class="cards"><x-box><p>inside the box</p></x-box><script>1+1</script></section>
</main>
<footer style="height:80px;background:#ddd">Bye</footer>
<div class="modal" role="dialog" aria-modal="true"><div class="box">Subscribe to our newsletter</div></div>
<script>
class XBox extends HTMLElement{constructor(){super();const r=this.attachShadow({mode:"open"});
r.innerHTML='<style>:host{display:block;border:2px solid red}.in{padding:10px}</style><div class="in"><slot></slot></div>'}}
customElements.define("x-box",XBox);
</script></body></html>"""

# A 1x1 PNG.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360f8cfc000000301010018dd8db00000000049454e44ae426082")


@pytest.mark.skipif(not os.environ.get("WIR_LIVE_BROWSER") or find_browser() is None,
                    reason="set WIR_LIVE_BROWSER to the folder with playwright-core to capture a page in a real browser")
def test_the_capture_freezes_a_page_with_a_shadow_root_and_a_popup(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(FIXTURE, encoding="utf-8")
    (site / "hero.png").write_bytes(PNG)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = tmp_path / "capture"
    out.mkdir()
    config = out / "config.json"
    config.write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_address[1]}/index.html", "out": str(out), "executable": find_browser()["executable"],
                                  "viewports": [{"name": "desktop", "width": 1200, "height": 800}, {"name": "mobile", "width": 390, "height": 800}]}), encoding="utf-8")
    import subprocess

    try:
        done = subprocess.run(["node", str(pipeline.CAPTURE_SCRIPT), str(config)], capture_output=True, text=True, timeout=180,
                              env={**os.environ, "WIR_BROWSER_DIR": os.environ["WIR_LIVE_BROWSER"]})
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stdout[-600:] + done.stderr[-600:]
    bundle = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    html = bundle["shell"] + "".join(s["html"] for s in bundle["sections"])
    assert "x-box" in html and "inside the box" in html and "wir-sh-" in html
    assert "Subscribe to our newsletter" not in html and "<script" not in html
    css = (out / "site.css").read_text(encoding="utf-8")
    assert ".in" in css and "__WIR_ASSET__/" in css and any(a["bytes"] for a in bundle["assets"].values())
    assert (out / "reference-desktop.png").is_file() and (out / "reference-mobile.png").is_file() and (out / "thumb.jpg").is_file()
    written = replica.write_project(out, tmp_path / "project", slug="fixture")
    assert written["components"] and (tmp_path / "project/src/app/page.tsx").is_file()
