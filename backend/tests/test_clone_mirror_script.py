"""The mirror recorder (``mirror.mjs``) against a local fixture site in a real browser (opt-in with ``WIR_LIVE_BROWSER``)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentfit.clone.browser import find_browser
from agentfit.clone.mirror import MirrorIndex

CLONE = Path(__file__).resolve().parents[1] / "src" / "agentfit" / "clone"
SITE = Path(__file__).parent / "fixtures" / "mirror" / "site"
NODE = shutil.which("node")
needs_browser = pytest.mark.skipif(
    not os.environ.get("WIR_LIVE_BROWSER") or find_browser() is None or NODE is None,
    reason="set WIR_LIVE_BROWSER to the folder with playwright-core to record a page in a real browser",
)
CLIP = bytes(range(256)) * 16  # a stub for a video: 4 KB that no decoder would accept


class _Site(BaseHTTPRequestHandler):
    root: Path
    redirects: dict[str, str] = {}

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in self.redirects:
            self.send_response(302)
            self.send_header("Location", self.redirects[path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        target = self.root / ("index.html" if path == "/" else path.lstrip("/"))
        if not target.is_file():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = target.read_bytes()
        kind = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
                ".woff2": "font/woff2", ".mp4": "video/mp4"}.get(target.suffix, "application/octet-stream")
        wanted = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if wanted:
            start = int(wanted.group(1))
            end = min(int(wanted.group(2) or len(body) - 1), len(body) - 1)
            part = body[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        else:
            part = body
            self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(part)))
        self.end_headers()
        self.wfile.write(part)

    def log_message(self, *args: object) -> None:
        pass


class _Forbidden(_Site):
    def do_GET(self) -> None:
        body = b"nope"
        self.send_response(403, "Forbidden")
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(root: Path, handler: type[_Site] = _Site, redirects: dict[str, str] | None = None) -> ThreadingHTTPServer:
    klass = type("Handler", (handler,), {"root": root, "redirects": redirects or {}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), klass)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture()
def sites(tmp_path):
    """The fixture site on one port and its ``other/`` folder as a second host on another; the stub video is written here."""
    root = tmp_path / "site"
    shutil.copytree(SITE, root)
    (root / "clip.mp4").write_bytes(CLIP)
    other = _serve(root / "other")
    page = root / "index.html"
    page.write_text(page.read_text(encoding="utf-8").replace("{{OTHER}}", f"127.0.0.1:{other.server_address[1]}"), encoding="utf-8")
    main = _serve(root, redirects={"/old": "/index.html"})
    yield main, other
    main.shutdown()
    other.shutdown()


def _run(url: str, out: Path, limits: dict[str, int] | None = None) -> subprocess.CompletedProcess[str]:
    out.mkdir(parents=True, exist_ok=True)
    config = out / "config.json"
    config.write_text(json.dumps({
        "url": url, "out": str(out), "executable": find_browser()["executable"],
        "limits": limits or {"total_mb": 150, "file_mb": 40, "responses": 3000, "seconds": 120},
    }), encoding="utf-8")
    assert NODE is not None
    return subprocess.run([NODE, str(CLONE / "mirror.mjs"), str(config)], capture_output=True, text=True, timeout=240,
                          env={**os.environ, "WIR_BROWSER_DIR": os.environ["WIR_LIVE_BROWSER"]})


def _index(out: Path) -> MirrorIndex:
    return MirrorIndex.model_validate_json((out / "index.json").read_text(encoding="utf-8"))


@needs_browser
def test_the_recorder_records_the_document_the_script_loaded_after_scroll_and_the_other_host(tmp_path, sites):
    main, other = sites
    done = _run(f"http://127.0.0.1:{main.server_address[1]}/index.html", tmp_path / "out")
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    out = tmp_path / "out"
    index = _index(out)
    by_path = {(e.host, e.path): e for e in index.entries}
    own, far = f"127.0.0.1:{main.server_address[1]}", f"127.0.0.1:{other.server_address[1]}"
    assert index.host == own and index.driver == "scroll" and index.viewport == (1440, 900)
    assert (own, "/late.js") in by_path  # fetched only after a scroll event
    assert (own, "/font.woff2") in by_path
    assert (far, "/other/other.js") in by_path  # the second host
    assert index.document is not None and b"Mirror fixture" in (out / "files" / index.document).read_bytes()
    clip = by_path[(own, "/clip.mp4")]  # the page asked for a range: the whole file is recorded, with a plain request
    assert clip.status == 200 and clip.bytes == len(CLIP) and clip.file is not None
    assert (out / "files" / clip.file).read_bytes() == CLIP
    assert [e.order for e in index.entries] == sorted(e.order for e in index.entries)
    assert (out / "thumb.jpg").is_file()
    steps = [json.loads(line).get("step") for line in done.stdout.splitlines() if '"progress"' in line]
    assert steps == ["visit", "scroll", "record", "write"]


@needs_browser
def test_a_wheel_driven_page_uses_the_wheel_driver(tmp_path, sites):
    main, _ = sites
    done = _run(f"http://127.0.0.1:{main.server_address[1]}/wheel.html", tmp_path / "out")
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    index = _index(tmp_path / "out")
    assert index.driver == "wheel"
    assert any(e.path == "/wheel-data.json" and e.file for e in index.entries)  # fetched only on a wheel event
    steps = [json.loads(line).get("step") for line in done.stdout.splitlines() if '"progress"' in line]
    assert steps == ["visit", "wheel", "record", "write"]


@needs_browser
def test_a_page_that_redirects_lists_the_requested_address_as_not_recorded(tmp_path, sites):
    main, _ = sites
    requested = f"http://127.0.0.1:{main.server_address[1]}/old"
    done = _run(requested, tmp_path / "out")
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    index = _index(tmp_path / "out")
    assert requested in index.not_recorded
    assert index.final_url.endswith("/index.html") and index.document is not None


@needs_browser
def test_a_page_that_answers_403_ends_with_a_fatal_naming_the_status(tmp_path):
    server = _serve(SITE, _Forbidden)
    try:
        done = _run(f"http://127.0.0.1:{server.server_address[1]}/", tmp_path / "out")
    finally:
        server.shutdown()
    assert done.returncode != 0
    fatal = [json.loads(x) for x in done.stdout.splitlines() if '"fatal"' in x]
    assert fatal and "403" in fatal[-1]["error"] and "Forbidden" in fatal[-1]["error"]
    assert not (tmp_path / "out" / "index.json").exists()


@needs_browser
def test_a_file_over_the_limit_is_a_limit_entry_not_a_crash(tmp_path, sites):
    main, _ = sites
    limits = {"total_mb": 150, "file_mb": 0, "responses": 3000, "seconds": 120}
    done = _run(f"http://127.0.0.1:{main.server_address[1]}/index.html", tmp_path / "out", limits)
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    index = _index(tmp_path / "out")
    hit = next(x for x in index.limits_hit if x.code == "too_large")
    assert hit.count and hit.count >= 1
    assert all(e.file is None for e in index.entries if e.bytes > 0)  # nothing is stored, every such response is listed
    assert index.document is None


@needs_browser
def test_a_page_that_changes_its_location_after_load_keeps_its_document_and_the_address_it_was_answered_at(tmp_path, sites):
    main, _ = sites
    done = _run(f"http://127.0.0.1:{main.server_address[1]}/spa.html", tmp_path / "out")
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    index = _index(tmp_path / "out")
    assert index.final_url == f"http://127.0.0.1:{main.server_address[1]}/spa.html"  # no #intro, no /spa/route
    assert index.document is not None and b"SPA fixture" in (tmp_path / "out" / "files" / index.document).read_bytes()


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_an_address_that_is_not_http_ends_with_a_fatal_naming_the_scheme_before_any_browser(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"url": "file:///C:/secret.html", "out": str(tmp_path / "out"), "executable": "none"}), encoding="utf-8")
    done = subprocess.run([NODE, str(CLONE / "mirror.mjs"), str(config)], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "WIR_BROWSER_DIR": str(tmp_path)})
    assert done.returncode != 0
    fatal = [json.loads(x) for x in done.stdout.splitlines() if '"fatal"' in x]
    assert fatal and "file" in fatal[-1]["error"] and "http" in fatal[-1]["error"]
    assert not (tmp_path / "out" / "index.json").exists()


# -- the probe (mirror_probe.mjs): live page and replay, numbers only ------------------------------------------------------------------

def _probe(url: str, out: Path, driver: str, *, watch: bool = False, size: tuple[int, int] = (1440, 900)) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    config = out / "probe.config.json"
    config.write_text(json.dumps({
        "url": url, "out": str(out / "probe.json"), "executable": find_browser()["executable"], "viewport": list(size), "steps": 20,
        "driver": driver, "shots": str(out / "shots"), "watch_hosts": watch,
    }), encoding="utf-8")
    assert NODE is not None
    done = subprocess.run([NODE, str(CLONE / "mirror_probe.mjs"), str(config)], capture_output=True, text=True, timeout=240,
                          env={**os.environ, "WIR_BROWSER_DIR": os.environ["WIR_LIVE_BROWSER"]})
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    return json.loads((out / "probe.json").read_text(encoding="utf-8"))


def _replay(tmp_path: Path, url: str):
    from agentfit.clone.mirror_server import MirrorServer

    done = _run(url, tmp_path / "rec")
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    from agentfit.clone import mirror

    mirror._rewrite_recorded(tmp_path / "rec")
    server = MirrorServer(tmp_path / "rec")
    return server, server.start()


@needs_browser
def test_the_probe_finds_the_wheel_moved_strip_moving_in_live_and_replay(tmp_path, sites):
    from agentfit.clone.mirror import motion_compare, pixel_share

    main, _ = sites
    url = f"http://127.0.0.1:{main.server_address[1]}/wheel.html"
    server, replay_url = _replay(tmp_path, url)
    try:
        live = _probe(url, tmp_path / "live", "wheel")
        replay = _probe(replay_url + "wheel.html", tmp_path / "replay", "wheel", watch=True)
    finally:
        server.stop()
    assert live["driver"] == "wheel" and live["smooth_scroll"] == "none" and live["height"] <= 900 and live["words"] == 4
    assert len(live["steps"]) == 20 and live["shots"] == 5 and replay["foreign_hosts"] == []
    motion = motion_compare(live, replay)
    assert motion is not None and motion["moving_live"] >= 1 and motion["moving_replay"] >= 1
    assert motion["matching"] is not None and motion["matching"] >= 0.5
    strip_live = [s["items"] for s in live["steps"]]
    assert any(item["tx"] < 0 for items in strip_live for item in items)  # the strip really translated
    for at in (0, 5, 10, 15, 20):
        a, b = (tmp_path / "live" / "shots" / f"shot-{at}.png"), (tmp_path / "replay" / "shots" / f"shot-{at}.png")
        assert pixel_share(a.read_bytes(), b.read_bytes()) > 0.9
    assert all(word not in json.dumps(live) for word in ("three", "four", "127.0.0.1"))  # numbers, not page text or addresses


@needs_browser
def test_the_probe_scrolls_a_vertical_page_and_reports_the_scroll_driver(tmp_path, sites):
    main, _ = sites
    url = f"http://127.0.0.1:{main.server_address[1]}/index.html"
    result = _probe(url, tmp_path / "live", "scroll")
    assert result["driver"] == "scroll" and result["smooth_scroll"] == "native" and result["height"] > 900 and result["sections"] == 3
    assert result["steps"][0]["scroll_y"] > 0 and result["steps"][-1]["scroll_y"] > result["steps"][0]["scroll_y"]
    assert result["shots"] == 5 and "foreign_hosts" not in result
    assert {p.name for p in (tmp_path / "live" / "shots").iterdir()} == {f"shot-{n}.png" for n in (0, 25, 50, 75, 100)}
