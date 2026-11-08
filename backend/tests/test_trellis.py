"""3D pipeline and authenticated API, using fakes without WSL, network or GPU."""
import base64
import json
import struct
import subprocess

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentfit.api.trellis_routes import install_routes
from agentfit.imagegen.trellis import TrellisRequest, TrellisStudio, WebExportRequest, validate_glb


def glb():
    document = json.dumps({"asset": {"version": "2.0"}, "meshes": [{"primitives": []}]}).encode()
    document += b" " * (-len(document) % 4)
    return b"glTF" + struct.pack("<IIII", 2, len(document) + 20, len(document), 0x4E4F534A) + document


@pytest.fixture
def studio(tmp_path, monkeypatch):
    value = TrellisStudio(tmp_path)
    monkeypatch.setattr(value, "status", lambda: {"ready": True, "distributions": ["TestLinux"], "job": value.job()})
    (tmp_path / "runtime.json").write_text(json.dumps({"distribution": "TestLinux"}), encoding="utf-8")
    return value


@pytest.mark.parametrize("payload", [b"", b"fake-glb", glb()[:-1], b"glTF" + bytes(30)])
def test_rejects_invalid_output(payload):
    with pytest.raises((ValueError, struct.error)):
        validate_glb(payload)


@pytest.mark.parametrize("fields", [{"image": "../secret"}, {"seed": -1}, {"steps": 0}, {"steps": 51}, {"texture_size": 513}, {"resolution": 768}, {"resolution": 512}])
def test_request_limits(fields):
    with pytest.raises(ValidationError):
        TrellisRequest.model_validate({"image": "a" * 32, **fields})


def test_generation_saves_only_verified_model_and_recipe(studio, monkeypatch):
    image = studio.upload(base64.b64encode(b"\x89PNG\r\n\x1a\nreference").decode())
    request = TrellisRequest(image=image, seed=42, resolution=1536, texture_size=4096)
    identifier = studio._begin("generate")["id"]

    def stream(command, bootstrap, log_path):
        body = json.loads(bootstrap.decode().split("\n")[-3])
        assert body["seed"] == 42
        assert body["resolution"] == 1536
        assert body["texture_size"] == request.texture_size
        assert base64.b64decode(body["image"]).startswith(b"\x89PNG")
        assert command[:4] == ["wsl", "-d", "TestLinux", "--exec"]
        log_path.write_bytes(b"Loading model\n")
        return 0, base64.b64encode(glb()), {"resolution": 1536}

    monkeypatch.setattr(studio, "_stream", stream)
    studio._generate(identifier, request)
    assert studio.job()["status"] == "completed"
    assert studio.file(identifier).read_bytes() == glb()
    assert studio.gallery()[0]["request"]["seed"] == 42
    assert studio.gallery()[0]["request"]["resolution"] == 1536
    assert studio.gallery()[0]["model"] == "microsoft/TRELLIS.2-4B"
    assert b"WIR_RESULT" not in studio.file(identifier).with_suffix(".log").read_bytes()
    assert TrellisStudio(studio.directory).gallery() == studio.gallery()


def test_failed_runtime_never_creates_a_gallery_model(studio, monkeypatch):
    image = studio.upload(base64.b64encode(b"\x89PNG\r\n\x1a\nreference").decode())
    identifier = studio._begin("generate")["id"]
    def stream(command, bootstrap, log_path):
        log_path.write_text("CUDA out of memory", encoding="utf-8")
        return 1, None, {}
    monkeypatch.setattr(studio, "_stream", stream)
    studio._generate(identifier, TrellisRequest(image=image))
    assert studio.job()["status"] == "failed"
    assert "1024³" in studio.job()["error"]
    assert studio.gallery() == []


def test_single_job_and_missing_inputs(studio):
    with pytest.raises(ValueError, match="reference image"):
        studio.generate(TrellisRequest(image="a" * 32))
    studio._begin("install")
    with pytest.raises(ValueError, match="already running"):
        studio._begin("generate")
    with pytest.raises(ValueError):
        studio.install("--evil")
    with pytest.raises(ValueError):
        studio.upload("invalid")
    with pytest.raises(KeyError):
        studio.file("../runtime")


@pytest.mark.parametrize("exit_code", [0, 1])
def test_runtime_readiness_comes_from_disk_probe(tmp_path, monkeypatch, exit_code):
    studio = TrellisStudio(tmp_path)
    (tmp_path / "runtime.json").write_text(json.dumps({"distribution": "TestLinux"}), encoding="utf-8")

    def run(command, **kwargs):
        if command[1:] == ["--list", "--quiet"]:
            return subprocess.CompletedProcess(command, 0, "TestLinux\r\n".encode("utf-16-le"), b"")
        assert command[-3:] == ["--exec", "bash", "-s"]
        assert b"willitrun-trellis2/weights-ready" in kwargs["input"]
        assert b"willitrun-trellis/" not in kwargs["input"]
        return subprocess.CompletedProcess(command, exit_code, b"", b"")

    monkeypatch.setattr(studio, "_run", run)
    assert studio.status()["ready"] is (exit_code == 0)


def test_inaccessible_wsl_is_an_unknown_setup_not_a_distribution(tmp_path, monkeypatch):
    studio = TrellisStudio(tmp_path)
    monkeypatch.setattr(studio, "_run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b"Access denied", b""))
    status = studio.status()
    assert not status["ready"]
    assert status["distributions"] == []
    assert "Could not check" in status["error"]


def test_routes_enforce_guard_and_save(studio, tmp_path):
    def guard():
        raise HTTPException(401)
    app = FastAPI()
    install_routes(app, [Depends(guard)], studio)
    client = TestClient(app)
    assert client.get("/api/3d/status").status_code == 401
    assert client.post("/api/3d/install", json={"distribution": "TestLinux"}).status_code == 401
    app.dependency_overrides[guard] = lambda: None
    identifier = "b" * 32
    (tmp_path / f"{identifier}.glb").write_bytes(glb())
    assert client.get(f"/api/3d/gallery/{identifier}/file").content == glb()
    assert client.post(f"/api/3d/gallery/{identifier}/save", json={"path": str(tmp_path / "saved.glb")}).status_code == 200
    assert (tmp_path / "saved.glb").read_bytes() == glb()
    assert client.post(f"/api/3d/gallery/{identifier}/save", json={"path": "relative.glb"}).status_code == 400
    assert client.post("/api/3d/generate", json={"image": "../private"}).status_code == 422


def test_weight_preparation_checks_hashes_before_readiness(tmp_path, monkeypatch):
    import hashlib
    import io

    from agentfit.imagegen import trellis_worker

    monkeypatch.setattr(trellis_worker, "ROOT", tmp_path)
    data = b"verified model weights"
    asset = {"repo": "microsoft/test", "revision": "a" * 40, "file": "model.safetensors",
             "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(trellis_worker.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
    trellis_worker.prepare([asset])
    assert trellis_worker.asset_path(asset).read_bytes() == data
    trellis_worker.asset_path(asset).write_bytes(b"x" * len(data))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        trellis_worker.prepare([asset])
    assert not trellis_worker.asset_path(asset).exists()


def test_install_does_not_claim_ready_without_weights(tmp_path, monkeypatch):
    studio = TrellisStudio(tmp_path)
    (tmp_path / "runtime.json").write_text(json.dumps({"distribution": "TestLinux"}), encoding="utf-8")
    studio._begin("install")
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs["input"])
        return subprocess.CompletedProcess(command, 0 if len(calls) == 1 else 1)

    monkeypatch.setattr(subprocess, "run", run)
    studio._install("TestLinux")
    assert len(calls) == 2
    assert b'"prepare": true' in calls[1]
    assert studio.job()["status"] == "failed"
    assert "setup failed" in studio.job()["error"]


def test_delete_restore_keeps_both_exports_and_reference(studio):
    identifier = "a" * 32
    original = studio.directory / f"{identifier}.glb"
    original.write_bytes(glb())
    (studio.directory / f"{identifier}.json").write_text(json.dumps({"id": identifier}))
    web = studio.directory / f"{identifier}.web.glb"
    web.write_bytes(b"separate web copy")
    reference = studio.directory / ("c" * 32 + ".png")
    reference.write_bytes(b"shared reference")
    studio.forget(identifier)
    assert studio.gallery() == []
    assert not original.exists() and reference.exists()
    studio.restore(identifier)
    assert original.read_bytes() == glb()
    assert web.read_bytes() == b"separate web copy"
    assert len(studio.gallery()) == 1
    with pytest.raises(KeyError):
        studio.file(identifier, "../secret")


def test_progress_only_completes_after_success(studio):
    studio._begin("generate")
    studio._progress({"percent": 61, "phase": "materials", "step": 2, "total": 24})
    studio._progress({"percent": 40, "phase": "geometry_decode"})
    assert studio.job()["percent"] == 61
    assert "step" not in studio.job()
    studio._finish("not enough memory")
    assert studio.job()["status"] == "failed"
    assert studio.job()["percent"] == 61
    studio._begin("generate")
    studio._progress({"percent": 1000})
    assert studio.job()["percent"] == 99
    studio._finish()
    assert studio.job()["percent"] == 100


def test_web_failure_preserves_original(studio, monkeypatch):
    identifier = "d" * 32
    original = studio.directory / f"{identifier}.glb"
    original.write_bytes(glb())
    studio._begin("web_export")
    monkeypatch.setattr(studio, "_stream", lambda *a: (0, base64.b64encode(glb()), {}))
    studio._export_web(identifier, WebExportRequest())
    assert studio.job()["status"] == "failed"
    assert "Normal Map" in studio.job()["error"]
    assert original.read_bytes() == glb()
    assert not (studio.directory / f"{identifier}.web.glb").exists()


def test_web_budget_limits():
    for fields in ({"triangles": 1000000}, {"triangles": 24999}, {"texture_size": 4096}):
        with pytest.raises(ValidationError):
            WebExportRequest(**fields)
