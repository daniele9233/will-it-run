"""A study of a site as a clone entry: how it is listed, run again, read, deleted, and the two routes that start it and give its DNA.

No page is opened: the run of a study ends at once here (the engine is absent), or the file it would write is written by the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.clone import dna, studio
from agentfit.clone.dna import SiteDNA, analyze
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.workflows.runner import RunError
from test_clone_picture import make_studio

RAW = Path(__file__).parent / "fixtures" / "dna" / "raw_example.json"


def write_dna(study, entry, thumb: bool = False) -> SiteDNA:
    found = analyze(json.loads(RAW.read_text(encoding="utf-8-sig")))
    folder = dna.dna_dir(study.workspace(entry), entry["host"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dna.json").write_text(found.model_dump_json(), encoding="utf-8")
    if thumb:
        (folder / "thumb.jpg").write_bytes(b"\xff\xd8\xff")
    return found


def test_a_study_entry_is_listed_with_its_mode_and_thumbnail(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_study("example.com/page")
    manager.wait(record.id, 30)
    assert entry["mode"] == "study" and entry["host"] == "example.com" and entry["source"] == {"kind": "none"}
    assert entry["workspace"] and Path(entry["workspace"]) == study.workspace(entry)
    assert db.kv_get("run_definition", record.id)["workflow"]["id"] == dna.WORKFLOW
    assert study.list()[0]["mode"] == "study" and study.list()[0]["thumbnail"] is None
    write_dna(study, entry, thumb=True)
    assert study.list()[0]["thumbnail"] == "docs/research/example.com/dna/thumb.jpg"


def test_a_study_runs_again_from_its_entry_and_keeps_its_mode(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    write_dna(study, entry)
    again, second = study.start("", model_key="", sees=False, study_id=entry["id"])
    manager.wait(second.id, 30)
    assert again["mode"] == "study" and again["url"] == entry["url"] and second.id != record.id
    assert study.dna(entry["id"]) is None  # the old answer went once the new run started, which could not write its own
    with pytest.raises(studio.CloneError, match="not the address"):
        study.start_study("nothing")


def test_a_stale_thumbnail_and_raw_probe_do_not_survive_a_restart(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    write_dna(study, entry, thumb=True)
    folder = dna.dna_dir(study.workspace(entry), entry["host"])
    (folder / "dna-raw.json").write_text("{}", encoding="utf-8")
    (folder / "style_guide.md").write_text("kept", encoding="utf-8")
    _, second = study.start_study("", study_id=entry["id"])
    manager.wait(second.id, 30)
    assert [p.name for p in folder.glob("*") if p.name in ("dna.json", "thumb.jpg", "dna-raw.json")] == []
    assert study.list()[0]["thumbnail"] is None


def test_a_run_that_cannot_start_leaves_the_previous_dna(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    written = write_dna(study, entry, thumb=True)

    def refuse(*args, **kwargs):
        raise RunError("no")

    monkeypatch.setattr(study.runs, "start", refuse)
    with pytest.raises(RunError):
        study.start_study("", study_id=entry["id"])
    assert study.dna(entry["id"]) == written
    assert (dna.dna_dir(study.workspace(entry), entry["host"]) / "thumb.jpg").is_file()


def test_a_study_entry_can_be_read_and_deleted_like_a_clone(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    write_dna(study, entry, thumb=True)
    got = study.get(entry["id"])
    assert got["mode"] == "study" and got["project"] is False and got["shots"] == ["docs/research/example.com/dna/thumb.jpg"]
    assert len(got["runs"]) == 1
    study.delete(entry["id"])
    assert study.list() == [] and not study.home(entry["id"]).exists()


def test_the_dna_is_none_until_the_run_wrote_it_and_then_valid(db, tmp_path):
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    assert study.dna(entry["id"]) is None
    written = write_dna(study, entry)
    assert study.dna(entry["id"]) == written
    (dna.dna_dir(study.workspace(entry), entry["host"]) / "dna.json").write_text("{not json", encoding="utf-8")
    assert study.dna(entry["id"]) is None
    with pytest.raises(studio.CloneError):
        study.dna("nope")


def client_of(study, monkeypatch) -> TestClient:
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    svc = AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())
    svc._clone = study
    return TestClient(create_app(svc))


def test_the_study_route_refuses_without_the_engine_like_the_clone_routes(db, tmp_path, monkeypatch):
    study, _ = make_studio(db, tmp_path)
    client = client_of(study, monkeypatch)
    reply = client.post("/api/clone/study", json={"url": "https://example.com/"})
    assert reply.status_code == 409 and "browser engine" in reply.json()["detail"]
    assert study.list() == []  # refused before anything was created
    assert client.post("/api/clone/study", json={"url": "  "}).status_code == 400
    assert client.post("/api/clone/study", json={"url": "  "}, headers={"x-willitrun-lang": "it"}).json()["detail"] == "Scrivi l'indirizzo di un sito."


def test_the_dna_route_returns_the_dna(db, tmp_path, monkeypatch):
    study, manager = make_studio(db, tmp_path)
    client = client_of(study, monkeypatch)
    entry, record = study.start_study("https://example.com/")
    manager.wait(record.id, 30)
    waiting = client.get(f"/api/clone/{entry['id']}/dna", headers={"x-willitrun-lang": "en"})
    assert waiting.status_code == 404 and "not finished" in waiting.json()["detail"]
    written = write_dna(study, entry)
    reply = client.get(f"/api/clone/{entry['id']}/dna")
    assert reply.status_code == 200 and reply.json() == written.model_dump(mode="json")
    missing = client.get("/api/clone/unknown/dna", headers={"x-willitrun-lang": "it"})
    assert missing.status_code == 404 and missing.json()["detail"] == "Questo studio non esiste."
