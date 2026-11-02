"""A mirror as a clone entry: how it is listed, run again, read, deleted, and the three routes that start it, give its report and open its recording.

No page is opened: the run ends at once here (the engine is absent), or the files it would write are written by the test.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.clone import mirror, studio
from agentfit.clone.mirror import MirrorIndex, MirrorReport
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.workflows.runner import RunError
from test_clone_picture import make_studio

HASH = "a" * 32


def write_report(study, entry, thumb: bool = False) -> MirrorReport:
    report = MirrorReport(live_at="2026-10-29T10:00:00Z", driver="scroll", seconds=3.5)
    folder = mirror.mirror_dir(study.workspace(entry))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(report.model_dump_json(), encoding="utf-8")
    if thumb:
        (folder / "thumb.jpg").write_bytes(b"\xff\xd8\xff")
    return report


def write_recording(study, entry) -> None:
    folder = mirror.mirror_dir(study.workspace(entry))
    (folder / "files").mkdir(parents=True, exist_ok=True)
    (folder / "files" / HASH).write_bytes(b"<html>hi</html>")
    index = MirrorIndex(
        url="https://example.com/", final_url="https://example.com/", host="example.com", captured_at="2026-10-29T10:00:00Z", viewport=(1440, 900),
        document=HASH, limits_hit=[], not_recorded=[],
        entries=[{"url": "https://example.com/", "host": "example.com", "path": "/", "status": 200, "content_type": "text/html", "file": HASH,
                  "bytes": 15, "order": 0}],
    )
    (folder / "index.json").write_text(index.model_dump_json(), encoding="utf-8")


def test_a_mirror_entry_is_listed_with_its_mode_and_thumbnail(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_mirror("example.com/page")
    manager.wait(record.id, 30)
    assert entry["mode"] == "mirror" and entry["host"] == "example.com" and entry["source"] == {"kind": "none"}
    assert entry["workspace"] and Path(entry["workspace"]) == study.workspace(entry)
    assert db.kv_get("run_definition", record.id)["workflow"]["id"] == mirror.WORKFLOW
    assert study.list()[0]["mode"] == "mirror" and study.list()[0]["thumbnail"] is None
    write_report(study, entry, thumb=True)
    assert study.list()[0]["thumbnail"] == "mirror/thumb.jpg"


def test_a_mirror_runs_again_from_its_entry_and_keeps_its_mode(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)
    write_report(study, entry, thumb=True)
    write_recording(study, entry)
    again, second = study.start("", model_key="", sees=False, study_id=entry["id"])
    manager.wait(second.id, 30)
    assert again["mode"] == "mirror" and again["url"] == entry["url"] and second.id != record.id
    assert study.mirror(entry["id"]) is None  # the old answer went once the new run started, which could not write its own
    folder = mirror.mirror_dir(study.workspace(entry))
    assert not (folder / "report.json").exists() and not (folder / "thumb.jpg").exists()
    # The recording is not deleted by the start: the record step clears the folder itself, before it writes.
    assert (folder / "index.json").is_file() and (folder / "files" / HASH).is_file()
    with pytest.raises(studio.CloneError, match="not the address"):
        study.start_mirror("nothing")


def test_a_mirror_entry_can_be_read_and_deleted_like_a_clone(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)
    write_report(study, entry, thumb=True)
    got = study.get(entry["id"])
    assert got["mode"] == "mirror" and got["project"] is False and got["shots"] == ["mirror/thumb.jpg"]
    assert len(got["runs"]) == 1
    study.delete(entry["id"])
    assert study.list() == [] and not study.home(entry["id"]).exists()


def test_the_report_is_none_until_written_and_then_valid(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)
    assert study.mirror(entry["id"]) is None
    written = write_report(study, entry)
    assert study.mirror(entry["id"]) == written
    (mirror.mirror_dir(study.workspace(entry)) / "report.json").write_text("{not json", encoding="utf-8")
    assert study.mirror(entry["id"]) is None
    with pytest.raises(studio.CloneError):
        study.mirror("nope")


def test_a_run_that_cannot_start_leaves_the_previous_recording(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)
    written = write_report(study, entry, thumb=True)
    write_recording(study, entry)

    def refuse(*args, **kwargs):
        raise RunError("no")

    monkeypatch.setattr(study.runs, "start", refuse)
    with pytest.raises(RunError):
        study.start_mirror("", study_id=entry["id"])
    folder = mirror.mirror_dir(study.workspace(entry))
    assert study.mirror(entry["id"]) == written
    assert (folder / "thumb.jpg").is_file() and (folder / "index.json").is_file() and (folder / "files").is_dir()


def client_of(study, monkeypatch) -> TestClient:
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    svc = AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())
    svc._clone = study
    return TestClient(create_app(svc))


def test_the_mirror_route_refuses_without_the_engine_with_409(db, tmp_path, monkeypatch):
    study, _ = make_studio(db, tmp_path)
    client = client_of(study, monkeypatch)
    reply = client.post("/api/clone/mirror", json={"url": "https://example.com/"})
    assert reply.status_code == 409 and "browser engine" in reply.json()["detail"]
    assert study.list() == []  # refused before anything was created
    assert client.post("/api/clone/mirror", json={"url": "  "}).status_code == 400
    assert client.post("/api/clone/mirror", json={"url": "  "}, headers={"x-willitrun-lang": "it"}).json()["detail"] == "Scrivi l'indirizzo di un sito."


def test_the_mirror_route_answers_400_for_an_address_the_studio_refuses(db, tmp_path, monkeypatch):
    study, _ = make_studio(db, tmp_path)
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    svc = AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())
    svc._clone = study
    svc._browser_install = study.install
    monkeypatch.setattr(study.install, "installed_version", lambda: "1.0")
    monkeypatch.setattr(study.install, "node", lambda: {"ok": True, "detail": ""})
    monkeypatch.setattr("agentfit.api.clone_routes.find_browser", lambda: {"executable": "chrome"})
    client = TestClient(create_app(svc))
    reply = client.post("/api/clone/mirror", json={"url": "nothing"})
    assert reply.status_code == 400 and "not the address" in reply.json()["detail"]
    assert study.list() == []


def test_the_report_route_404s_before_the_run_wrote_it(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    client = client_of(study, monkeypatch)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)
    waiting = client.get(f"/api/clone/{entry['id']}/mirror", headers={"x-willitrun-lang": "en"})
    assert waiting.status_code == 404 and "not finished" in waiting.json()["detail"]
    written = write_report(study, entry)
    reply = client.get(f"/api/clone/{entry['id']}/mirror")
    assert reply.status_code == 200 and reply.json() == written.model_dump(mode="json")
    missing = client.get("/api/clone/unknown/mirror", headers={"x-willitrun-lang": "it"})
    assert missing.status_code == 404 and missing.json()["detail"] == "Questa copia viva non esiste."


def test_open_serves_the_recording_and_answers_with_an_address_the_app_may_open(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    client = client_of(study, monkeypatch)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)
    assert client.post(f"/api/clone/{entry['id']}/mirror/open").status_code == 409  # nothing recorded yet
    write_recording(study, entry)
    try:
        reply = client.post(f"/api/clone/{entry['id']}/mirror/open")
        assert reply.status_code == 200
        url = reply.json()["url"]
        assert url.startswith("http://127.0.0.1:") and url.endswith("/") and url.count("/") == 3  # a path, as the app's link opener needs
        assert client.post(f"/api/clone/{entry['id']}/mirror/open").json()["url"] == url  # one server per study
        assert urllib.request.urlopen(url, timeout=5).read() == b"<html>hi</html>"
    finally:
        study.stop_mirrors()
    assert client.post("/api/clone/unknown/mirror/open").status_code == 404


def engine_client(study, monkeypatch) -> TestClient:
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    svc = AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())
    svc._clone = study
    svc._browser_install = study.install
    monkeypatch.setattr(study.install, "installed_version", lambda: "1.0")
    monkeypatch.setattr(study.install, "node", lambda: {"ok": True, "detail": ""})
    monkeypatch.setattr("agentfit.api.clone_routes.find_browser", lambda: {"executable": "chrome"})
    return TestClient(create_app(svc))


def test_the_run_again_route_keeps_the_entry_id_and_mode(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    client = engine_client(study, monkeypatch)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)
    reply = client.post(f"/api/clone/{entry['id']}/mirror/run")
    assert reply.status_code == 200 and reply.json()["study"] == entry["id"] and reply.json()["run"] != record.id
    manager.wait(reply.json()["run"], 30)
    assert [e["id"] for e in study.list()] == [entry["id"]] and study.list()[0]["mode"] == "mirror"
    assert client.post("/api/clone/unknown/mirror/run").status_code == 404


def test_the_run_again_route_404s_for_an_entry_that_is_not_a_mirror(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    client = engine_client(study, monkeypatch)
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    reply = client.post(f"/api/clone/{entry['id']}/mirror/run", headers={"x-willitrun-lang": "it"})
    assert reply.status_code == 404 and reply.json()["detail"] == "Questa copia viva non esiste."


def test_the_run_again_route_refuses_without_the_engine_with_409(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    client = client_of(study, monkeypatch)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)
    reply = client.post(f"/api/clone/{entry['id']}/mirror/run")
    assert reply.status_code == 409 and "browser engine" in reply.json()["detail"]
    assert len(study._runs(entry["id"])) == 1


def test_the_run_again_route_answers_400_while_the_previous_run_is_working(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    client = engine_client(study, monkeypatch)
    entry, record = study.start_mirror("https://example.com/")
    manager.wait(record.id, 30)

    real = study._runs
    monkeypatch.setattr(study, "_runs", lambda study_id: [r.model_copy(update={"status": "running"}) for r in real(study_id)])
    reply = client.post(f"/api/clone/{entry['id']}/mirror/run")
    assert reply.status_code == 400 and "still working" in reply.json()["detail"]
