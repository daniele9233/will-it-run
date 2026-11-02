"""Dynamic mirror: models, limits, the URL rewrite and the file lookup. No browser, no network."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentfit.clone import mirror, pipeline
from agentfit.clone.mirror import LIMITS, MirrorIndex, MirrorReport, local_path, rewrite_text, safe_file
from agentfit.workflows import checks
from agentfit.workflows.checks import CheckContext
from agentfit.workflows.validator import validate
from test_clone_replica import FakeEngine


def test_the_index_leaves_an_unmeasured_field_none() -> None:
    index = MirrorIndex(url="https://a.com/", final_url="https://a.com/", host="a.com", captured_at="t", viewport=(1440, 900),
                        document=None, entries=[], limits_hit=[], not_recorded=[])
    assert index.driver is None and index.document is None and index.schema_version == 1
    report = MirrorReport()
    assert all(v is None for k, v in report.model_dump().items() if k != "schema_version")


def test_rewrite_turns_an_absolute_url_of_a_recorded_host_into_a_local_path() -> None:
    text, changed = rewrite_text('<script src="https://cdn.x.com/a.js?v=2"></script>', "x.com", {"x.com", "cdn.x.com"})
    assert changed and text == '<script src="/_m/cdn.x.com/a.js?v=2"></script>'
    assert local_path("cdn.x.com", "/a.js?v=2", "x.com") == "/_m/cdn.x.com/a.js?v=2"


def test_rewrite_handles_protocol_relative_and_own_host_urls() -> None:
    text, changed = rewrite_text('a("//cdn.x.com/b.css") b("https://x.com/c.png") c("http://x.com/d")', "x.com", {"x.com", "cdn.x.com"})
    assert changed and text == 'a("/_m/cdn.x.com/b.css") b("/c.png") c("/d")'
    assert local_path("x.com", "/c.png", "x.com") == "/c.png"


def test_rewrite_leaves_a_host_that_was_not_recorded_and_code_outside_urls_alone() -> None:
    src = 'fetch("https://other.com/api"); var a = 1 // cdn.x.com/not-a-url\nvar host = "cdn.x.com.evil.io";'
    text, changed = rewrite_text(src, "x.com", {"x.com", "cdn.x.com"})
    assert not changed and text == src


def test_safe_file_refuses_dotdot_encoded_slashes_and_long_names(tmp_path: Path) -> None:
    (tmp_path / "files").mkdir()
    name = "ab" * 8
    (tmp_path / "files" / name).write_bytes(b"x")
    (tmp_path / "secret").write_text("s", encoding="utf-8")
    assert safe_file(tmp_path, name) == tmp_path / "files" / name
    assert safe_file(tmp_path, "../secret") is None
    assert safe_file(tmp_path, "..%2fsecret") is None
    assert safe_file(tmp_path, "ab/../../secret") is None
    assert safe_file(tmp_path, "a" * 65) is None
    assert safe_file(tmp_path, "cd" * 8) is None  # well formed, not recorded


def test_limits_are_the_spec_values() -> None:
    assert LIMITS == {"total_mb": 150, "file_mb": 40, "responses": 3000, "seconds": 120}


# -- the record step as a run ----------------------------------------------------------------------------------------------------

URL = "https://www.example.test/"


@pytest.fixture
def engine(tmp_path, monkeypatch):
    install = FakeEngine(tmp_path / "engine")
    monkeypatch.setattr(pipeline, "install_source", lambda: install)
    monkeypatch.setattr(pipeline, "find_browser", lambda: {"name": "Chrome", "executable": "chrome.exe"})
    return install


def record_ctx(workspace: Path, url: str = URL) -> CheckContext:
    return CheckContext(task="t", outputs={}, ledger=[], history=[], definition=mirror.definition(url=url),
                        workspace=workspace, emit=lambda kind, payload: None, cancelled=lambda: False, complete=None)


def _entry(url: str, host: str, path: str, ctype: str | None, body: bytes, order: int, folder: Path) -> dict[str, Any]:
    name = hashlib.sha256(body).hexdigest()
    (folder / "files").mkdir(parents=True, exist_ok=True)
    (folder / "files" / name).write_bytes(body)
    return {"url": url, "host": host, "path": path, "status": 200, "content_type": ctype, "file": name, "bytes": len(body), "order": order}


def write_mirror(config: dict[str, Any], *, document: bool = True, limits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = Path(config["out"])
    html = b'<script src="https://cdn.example.test/a.js"></script><img src="//cdn.example.test/p.png">'
    js = b'fetch("https://www.example.test/api")'
    png = b"\x89PNG https://cdn.example.test/ inside a picture"
    entries = [
        _entry(URL, "www.example.test", "/", "text/html; charset=utf-8", html, 0, out),
        _entry("https://cdn.example.test/a.js", "cdn.example.test", "/a.js", "application/javascript", js, 1, out),
        _entry("https://cdn.example.test/p.png", "cdn.example.test", "/p.png", "image/png", png, 2, out),
    ]
    index = {"schema_version": 1, "url": URL, "final_url": URL, "host": "www.example.test", "captured_at": "t", "viewport": [1440, 900],
             "document": entries[0]["file"] if document else None, "entries": entries, "limits_hit": limits or [], "not_recorded": [], "driver": "scroll"}
    (out / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return {"html": html, "png": png, "entries": entries}


def test_the_record_step_rewrites_recorded_text_and_keeps_binary_files_untouched(tmp_path, engine, monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
        seen.update(write_mirror(config))
        seen["config"] = config
        return True, ""

    monkeypatch.setattr(mirror, "_record_script", fake)
    result = mirror._clone_mirror_record(mirror.definition(url=URL).checks[0], record_ctx(tmp_path))
    assert result["passed"] is True and result["verdict"] == "RECORDED"
    assert seen["config"]["limits"] == LIMITS
    folder = mirror.mirror_dir(tmp_path)
    index = MirrorIndex.model_validate_json((folder / "index.json").read_text(encoding="utf-8"))
    html, js, png = index.entries
    assert html.rewritten and js.rewritten and not png.rewritten
    for entry in index.entries:  # every entry points at a file that exists
        assert entry.file is not None and (folder / "files" / entry.file).is_file()
    assert (folder / "files" / str(html.file)).read_bytes() == b'<script src="/_m/cdn.example.test/a.js"></script><img src="/_m/cdn.example.test/p.png">'
    assert (folder / "files" / str(js.file)).read_bytes() == b'fetch("/api")'
    assert (folder / "files" / str(png.file)).read_bytes() == seen["png"]
    assert index.document == html.file and html.file != hashlib.sha256(seen["html"]).hexdigest()
    assert not (folder / "files" / hashlib.sha256(seen["html"]).hexdigest()).exists()


def test_a_site_that_refuses_ends_with_what_it_answered(tmp_path, engine, monkeypatch) -> None:
    monkeypatch.setattr(mirror, "_record_script", lambda ctx, config: (False, "HTTP 403 Forbidden"))
    result = mirror._clone_mirror_record(mirror.definition(url=URL).checks[0], record_ctx(tmp_path))
    assert result["passed"] is False and result["verdict"] == "MIRROR_FAILED" and "403" in result["summary"]


def test_a_recording_without_the_main_document_is_a_failure(tmp_path, engine, monkeypatch) -> None:
    monkeypatch.setattr(mirror, "_record_script", lambda ctx, config: (write_mirror(config, document=False) and True, ""))
    result = mirror._clone_mirror_record(mirror.definition(url=URL).checks[0], record_ctx(tmp_path))
    assert result["passed"] is False and result["verdict"] == "MIRROR_FAILED"


def test_a_timeout_with_a_partial_recording_is_not_a_failure(tmp_path, engine, monkeypatch) -> None:
    monkeypatch.setattr(mirror, "_record_script", lambda ctx, config: (write_mirror(config, limits=[{"code": "timeout", "count": 1}]) and True, ""))
    result = mirror._clone_mirror_record(mirror.definition(url=URL).checks[0], record_ctx(tmp_path))
    assert result["passed"] is True and "timeout" in result["summary"]


@pytest.mark.parametrize("url", ["file:///C:/secret.txt", "chrome://settings", "http://192.168.1.10/", "http://169.254.169.254/latest", "http://10.0.0.5:8080/"])
def test_an_address_that_is_not_public_never_reaches_the_script(tmp_path, engine, monkeypatch, url) -> None:
    def boom(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
        raise AssertionError("the script must not run")

    monkeypatch.setattr(mirror, "_record_script", boom)
    result = mirror._clone_mirror_record(mirror.definition(url=url).checks[0], record_ctx(tmp_path, url))
    assert result["passed"] is False and result["verdict"] == "MIRROR_FAILED"


@pytest.mark.parametrize("url", ["http://localhost:8000/", "http://127.0.0.1:5173/a", "https://example.com/"])
def test_this_pc_and_public_addresses_are_allowed(monkeypatch, url) -> None:
    monkeypatch.setattr(mirror.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))])
    assert mirror.address_problem(url) is None


def test_the_mirror_definition_validates_with_no_agent() -> None:
    definition = mirror.definition(url=URL)
    assert definition.agents == [] and [c.id for c in definition.checks] == ["record", "verify"]
    assert validate(definition, require_models=True).ok


def test_the_record_check_is_registered_and_not_listed() -> None:
    spec = checks.CHECKS["clone_mirror_record"]
    assert spec.listed is False and spec.run is mirror._clone_mirror_record
