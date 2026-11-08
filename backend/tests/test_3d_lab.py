"""Additional engines share references/queue and never replace existing gallery models."""
import base64
import json
import struct

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from agentfit.api.lab_routes import install_routes
from agentfit.imagegen.lab import ENGINES, LabRequest, ThreeDLab
from agentfit.imagegen.trellis import TrellisStudio


def glb(extra=None):
    doc = json.dumps({"asset": {"version": "2.0"}, "meshes": [{"primitives": []}], **(extra or {})}).encode()
    doc += b" " * (-len(doc) % 4)
    return b"glTF" + struct.pack("<IIII", 2, len(doc) + 20, len(doc), 0x4E4F534A) + doc


@pytest.fixture
def lab(tmp_path):
    return ThreeDLab(TrellisStudio(tmp_path))


def test_catalog_has_all_requested_choices():
    assert set(ENGINES) == {"pixal3d", "idol", "triposplat", "supra-tex", "effigy", "opencartridge", "scenario"}


def test_import_preserves_existing_gallery_and_records_unknown_recipe(lab):
    old = lab.studio.directory / ("a" * 32 + ".glb")
    old.write_bytes(glb())
    old.with_suffix(".json").write_text(json.dumps({"id": "a" * 32, "request": {}}))
    result = lab.import_glb(base64.b64encode(glb()).decode(), "effigy")
    assert old.read_bytes() == glb()
    assert len(lab.studio.gallery()) == 2
    recipe = json.loads(lab.studio.file(result["id"]).with_suffix(".json").read_text())
    assert recipe["model"] == "Effigy"
    assert recipe["request"]["steps"] is None
    assert recipe["elapsed"] is None


def test_invalid_import_leaves_no_success(lab):
    with pytest.raises(ValueError):
        lab.import_glb(base64.b64encode(b"wrong").decode(), "effigy")
    assert lab.studio.gallery() == []


def test_external_glb_textures_cannot_reach_network(lab):
    with pytest.raises(ValueError, match="self-contained"):
        lab.import_glb(base64.b64encode(glb({"images": [{"uri": "https://example.org/tracker.png"}]})).decode(), "effigy")
    assert lab.studio.gallery() == []


def test_missing_reference_refused_before_worker(lab):
    with pytest.raises(ValueError, match="reference image"):
        lab.generate(LabRequest(engine="pixal3d", image="b" * 32))


def test_native_generation_records_only_applied_parameters(lab, monkeypatch):
    image = "b" * 32
    (lab.studio.directory / f"{image}.png").write_bytes(b"reference")
    monkeypatch.setattr(lab, "status", lambda: {"engines": [{"id": "pixal3d", "ready": True}]})
    monkeypatch.setattr(lab, "_worker", lambda _: (["fake"], b""))
    monkeypatch.setattr(lab.studio, "_stream", lambda *a: (0, base64.b64encode(glb()), {"source_commit": "verified"}))
    def task(kind, work):
        job = lab.studio._begin(kind)
        work(job["id"], lambda _: None)
        lab.studio._finish()
        return job
    monkeypatch.setattr(lab.studio, "start_task", task)
    job = lab.generate(LabRequest(engine="pixal3d", image=image, seed=123, resolution=1536))
    recipe = json.loads(lab.studio.file(job["id"]).with_suffix(".json").read_text())
    assert recipe["request"]["resolution"] == 1536
    assert recipe["request"]["seed"] is None
    assert recipe["request"]["steps"] is None
    assert recipe["runtime"]["source_commit"] == "verified"


def test_wsl_failure_is_visible_and_never_claims_ready(lab, monkeypatch):
    def blocked(_):
        raise OSError("WSL access denied")
    monkeypatch.setattr(lab, "_worker", blocked)
    state = lab.status()
    assert "access denied" in state["error"]
    assert not any(engine["ready"] for engine in state["engines"])


def test_splat_path_traversal_refused(lab):
    with pytest.raises(KeyError):
        lab.splat_file("../../secret")


def test_lab_routes_are_protected(lab):
    app = FastAPI()
    def denied():
        raise HTTPException(401, "denied")
    install_routes(app, [Depends(denied)], lab)
    client = TestClient(app)
    assert client.get("/api/3d/lab/status").status_code == 401
    assert client.post("/api/3d/lab/action", json={"engine": "scenario", "action": "install"}).status_code == 401
