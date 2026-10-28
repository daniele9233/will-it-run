"""The DNA collector: its pure helpers under Node, and the whole script against a local page (opt-in, a real browser)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentfit.clone.browser import find_browser

CLONE = Path(__file__).resolve().parents[1] / "src" / "agentfit" / "clone"
PAGE = Path(__file__).parent / "fixtures" / "dna" / "page"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")
needs_browser = pytest.mark.skipif(
    not os.environ.get("WIR_LIVE_BROWSER") or find_browser() is None or NODE is None,
    reason="set WIR_LIVE_BROWSER to the folder with playwright-core to collect a page in a real browser",
)


def _node(code: str) -> object:
    lib = (CLONE / "dna_lib.mjs").as_uri()
    sigs = json.dumps(json.loads((CLONE / "dna_signatures.json").read_text(encoding="utf-8-sig"))["signatures"])
    script = f'import * as lib from "{lib}"; const S = {sigs}; console.log(JSON.stringify({code}));'
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_the_signature_table_matches_what_it_should_and_nothing_else():
    hit = {
        "gsap.registerPlugin(ScrollTrigger)": "gsap",
        "const l = new Lenis({": "lenis",
        "new THREE.WebGLRenderer BufferGeometry": "three",
        "@dimforge/rapier3d": "rapier",
        "document.startViewTransition(": "view-transitions",
        "gl_FragColor = vec4(1.0);": "glsl",
    }
    for text, lib_id in hit.items():
        assert lib_id in _node(f"lib.matchSignatures({json.dumps(text)}, S)"), text
    miss = {
        'window.Webflow.require("lottie")': "lottie",  # an integration hook of a runtime is not a Lottie on the page
        "browserapierrors.xhr": "rapier",
        "we render in three dimensions": "three",
        "the lenis of the eye": "lenis",
        "registerPlugin(": "gsap",
    }
    for text, lib_id in miss.items():
        assert lib_id not in _node(f"lib.matchSignatures({json.dumps(text)}, S)"), text


@needs_node
def test_quantize_and_durations():
    assert _node("lib.quantizeHex(255, 0, 0)") == "#f80000"
    assert _node('lib.parseDurationsMs("0.2s, 150ms")') == [200, 150]


def _serve(directory: Path, handler: type = SimpleHTTPRequestHandler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run(url: str, out: Path) -> subprocess.CompletedProcess[str]:
    out.mkdir(parents=True, exist_ok=True)
    config = out / "config.json"
    config.write_text(json.dumps({"url": url, "out": str(out), "executable": find_browser()["executable"]}),
                      encoding="utf-8")
    return subprocess.run([NODE, str(CLONE / "dna.mjs"), str(config)], capture_output=True, text=True, timeout=240,
                          env={**os.environ, "WIR_BROWSER_DIR": os.environ["WIR_LIVE_BROWSER"]})


@needs_browser
def test_the_collector_reports_a_local_fixture_page(tmp_path):
    server = _serve(PAGE)
    try:
        done = _run(f"http://127.0.0.1:{server.server_address[1]}/index.html", tmp_path / "out")
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    raw = json.loads((tmp_path / "out" / "dna-raw.json").read_text(encoding="utf-8"))
    assert raw["raw_version"] == 1 and raw["viewport"] == [1440, 900]
    video = raw["dom"]["videos"][0]
    assert video["loop"] and video["muted"]
    assert raw["dom"]["canvases"][0]["context"].startswith("webgl")
    assert raw["dom"]["patterns"]["chip_filters"] and raw["dom"]["patterns"]["sticky_header"]
    assert raw["dom"]["smooth_scroll"]["lenis_class"]
    assert "gsap" in raw["signature_hits"]
    assert "marquee-x" in raw["styles"]["keyframes"]
    assert raw["styles"]["counts"]["backdrop"] >= 1
    assert raw["styles"]["counts"]["scroll_snap"] >= 1  # a rule counts although no element on the page uses the class
    assert raw["framework"] == "next"  # the app router has no __NEXT_DATA__: next-route-announcer says it
    assert isinstance(raw["styles"]["font_faces"], list)
    assert raw["dom"]["sections"] and raw["probes"]["hover"]["sampled"] >= 1
    assert (tmp_path / "out" / "thumb.jpg").is_file()
    # header, a menu of four links, hero, video block, grid of ten cards (five tracks), three column divs of three cards each, footer
    assert [sec["columns"] for sec in raw["dom"]["sections"]] == [None, None, None, None, 5, 3, None]
    assert next(sec for sec in raw["dom"]["sections"] if sec["tag"] == "header")["columns"] is None
    # Size of the cards: ten cards in five tracks 16px apart; nine cards in three column divs (the cards, not the columns, are counted).
    grids = [(sec["columns"], sec["cards"], sec["gap"]) for sec in raw["dom"]["sections"]]
    assert grids[4] == (5, 10, 16) and grids[5][:2] == (3, 9)
    assert all(sec["card_height"] > 0 for sec in raw["dom"]["sections"] if sec["cards"])
    assert all(sec["cards"] is None and sec["card_height"] is None and sec["gap"] is None for sec in raw["dom"]["sections"] if sec["columns"] is None)
    menu = next(sec for sec in raw["dom"]["sections"] if sec["tag"] == "div" and sec["height"] < 120)
    assert menu["chips"] == 4 and menu["chips_evidence"] is False  # four links in a row: counted, but nothing shows they filter
    from agentfit.clone.dna import analyze
    assert [sec.role for sec in analyze(raw).sections or []][1] == "content"  # a menu, not a filter bar
    assert [sec.role for sec in analyze(raw).sections or []][2] == "hero"  # and it does not take the hero's place
    assert any(sec["chips"] == 4 and sec["chips_evidence"] is True for sec in raw["dom"]["sections"])  # the four buttons of the filter bar
    assert any(sec["chips"] == 4 for sec in raw["dom"]["sections"])  # the four chips of the filter bar
    text = raw["styles"]["text_colors"]
    assert any(0x98 <= int(k[1:3], 16) <= 0xb8 and k[1:3] == k[3:5] == k[5:7] for k in text), text  # oklch(0.7 0 0) read as a grey
    assert "#888888" in raw["styles"]["border_colors"]  # the 1px #888 border of the chips
    steps = [json.loads(line).get("step") for line in done.stdout.splitlines() if '"progress"' in line]
    assert steps == ["visit", "scroll", "styles", "scripts", "probes", "write"]


@needs_browser
def test_a_rule_that_sets_scroll_snap_to_none_is_not_counted(tmp_path):
    page = tmp_path / "page"
    page.mkdir()
    (page / "index.html").write_text(
        "<!doctype html><title>t</title><style>.a{scroll-snap-type: none}.b{scroll-snap-type:none}"
        ".c{scroll-snap-type:  NONE}</style><main><section style='height:900px'><h1>x</h1></section></main>",
        encoding="utf-8")
    server = _serve(page)
    try:
        done = _run(f"http://127.0.0.1:{server.server_address[1]}/index.html", tmp_path / "out")
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    raw = json.loads((tmp_path / "out" / "dna-raw.json").read_text(encoding="utf-8"))
    assert raw["styles"]["counts"]["scroll_snap"] == 0


class _Forbidden(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"nope"
        self.send_response(403, "Forbidden")
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@needs_browser
def test_a_page_that_answers_403_ends_with_a_fatal_event_naming_the_status(tmp_path):
    server = _serve(PAGE, _Forbidden)
    try:
        done = _run(f"http://127.0.0.1:{server.server_address[1]}/", tmp_path / "out")
    finally:
        server.shutdown()
    assert done.returncode != 0
    fatal = [json.loads(x) for x in done.stdout.splitlines() if '"fatal"' in x]
    assert fatal and "403" in fatal[-1]["error"] and "Forbidden" in fatal[-1]["error"]
    assert not (tmp_path / "out" / "dna-raw.json").exists()


@needs_node
def test_a_time_budget_counts_down_on_its_clock_and_never_goes_below_zero():
    code = ("(() => { let t = 1000; const b = lib.makeBudget(500, () => t); const a = [b.left(), b.over()];"
            " t += 200; a.push(b.left(), b.over()); t += 900; a.push(b.left(), b.over()); return a; })()")
    assert _node(code) == [500, False, 300, False, 0, True]


@needs_node
def test_a_hover_probe_that_did_not_finish_is_skipped_and_a_page_without_cards_is_a_measurement():
    measured = {"sampled": 2, "changed": 1, "plays_video": 0, "props": ["transform"]}
    assert _node("lib.settleHover(null, null)") == {"value": None, "skipped": True}
    assert _node("lib.settleHover(3, null)") == {"value": None, "skipped": True}
    assert _node("lib.settleHover(0, null)") == {
        "value": {"sampled": 0, "changed": 0, "plays_video": 0, "props": []}, "skipped": False}
    assert _node(f"lib.settleHover(2, {json.dumps(measured)})") == {"value": measured, "skipped": False}
