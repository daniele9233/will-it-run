"""Refinement persistence, queue exclusivity and asset integrity without GPU/WSL/network."""
import base64
import hashlib
import json
import struct

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentfit.api.trellis_routes import install_routes
from agentfit.imagegen.hi3d import Hi3DRequest, Hi3DStudio
from agentfit.imagegen.trellis import TrellisStudio


def glb():
    document = json.dumps({"asset": {"version": "2.0"}, "meshes": [{"primitives": []}]}).encode()
    document += b" " * (-len(document) % 4)
    return b"glTF" + struct.pack("<IIII", 2, len(document) + 20, len(document), 0x4E4F534A) + document


@pytest.fixture
def refiner(tmp_path, monkeypatch):
    studio = TrellisStudio(tmp_path)
    (tmp_path / "runtime.json").write_text(json.dumps({"distribution": "TestLinux"}), encoding="utf-8")
    source_id, image = "a" * 32, "b" * 32
    (tmp_path / f"{source_id}.glb").write_bytes(glb())
    (tmp_path / f"{source_id}.json").write_text(json.dumps({"id": source_id, "request": {"image": image}}), encoding="utf-8")
    (tmp_path / f"{image}.png").write_bytes(b"\x89PNG\r\n\x1a\nreference")
    value = Hi3DStudio(studio)
    monkeypatch.setattr(value, "status", lambda **kwargs: {"ready": True})
    return value


@pytest.mark.parametrize("fields", [{"resolution": 768}, {"steps": 0}, {"steps": 51}, {"seed": -1}, {"triangles": 9999}, {"triangles": 200001}, {"image": "../runtime"}])
def test_invalid_refinement_options(fields):
    with pytest.raises(ValidationError):
        Hi3DRequest(**fields)


def test_refinement_preserves_source_and_persists_actual_recipe(refiner, monkeypatch):
    studio = refiner.studio
    identifier = studio._begin("hi3d_refine")["id"]
    def stream(command, bootstrap, path):
        assert command[:4] == ["wsl", "-d", "TestLinux", "--exec"]
        body = json.loads(bootstrap.decode().splitlines()[-2])
        assert body["resolution"] == 1024 and body["triangles"] == 60000
        assert base64.b64decode(body["image"]).startswith(b"\x89PNG")
        path.write_text("progress only", encoding="utf-8")
        return 0, base64.b64encode(glb()), {"resolution": 1024, "triangles": 100, "coarse_triangles": 60}
    monkeypatch.setattr(studio, "_stream", stream)
    refiner._refine(identifier, "a" * 32, "b" * 32, Hi3DRequest())
    assert studio.file("a" * 32).read_bytes() == glb()
    recipe = json.loads((studio.directory / f"{identifier}.json").read_text(encoding="utf-8"))
    assert recipe["source_id"] == "a" * 32 and recipe["geometry_only"]
    assert recipe["runtime"]["coarse_triangles"] == 60
    assert recipe["request"]["image"] == "b" * 32
    assert recipe["source_sha256"] == hashlib.sha256(glb()).hexdigest()
    assert any(row["id"] == identifier for row in TrellisStudio(studio.directory).gallery())
    studio._finish()
    studio.forget(identifier)
    studio.restore(identifier)
    assert studio.file(identifier).read_bytes() == glb()
    assert studio.file("a" * 32).read_bytes() == glb()


@pytest.mark.parametrize("output,runtime", [(b"invalid", {"resolution": 1024, "triangles": 10}), (glb(), {"resolution": 512, "triangles": 10})])
def test_bad_outputs_never_enter_gallery(refiner, monkeypatch, output, runtime):
    identifier = "c" * 32
    monkeypatch.setattr(refiner.studio, "_stream", lambda *a: (0, base64.b64encode(output), runtime))
    with pytest.raises(ValueError):
        refiner._refine(identifier, "a" * 32, "b" * 32, Hi3DRequest())
    assert not (refiner.studio.directory / f"{identifier}.glb").exists()
    assert refiner.studio.file("a" * 32).read_bytes() == glb()


def test_shared_task_slot_and_reference_validation(refiner):
    refiner.studio._begin("generate")
    with pytest.raises(ValueError, match="already running"):
        refiner.refine("a" * 32, Hi3DRequest())
    with pytest.raises(KeyError):
        refiner.refine("../runtime", Hi3DRequest())
    with pytest.raises(ValueError, match="reference photo"):
        refiner.refine("a" * 32, Hi3DRequest(image="f" * 32))


def test_failed_job_reports_memory_without_overwriting_source(refiner, monkeypatch):
    def stream(command, bootstrap, path):
        path.write_text("CUDA out of memory", encoding="utf-8")
        return 1, None, {}
    monkeypatch.setattr(refiner.studio, "_stream", stream)
    with pytest.raises(ValueError, match="out of memory"):
        refiner._refine("c" * 32, "a" * 32, "b" * 32, Hi3DRequest())
    assert refiner.studio.file("a" * 32).read_bytes() == glb()


def test_routes_require_authentication_and_validate_requests(refiner, monkeypatch):
    def guard():
        raise HTTPException(401)
    app = FastAPI()
    install_routes(app, [Depends(guard)], refiner.studio)
    client = TestClient(app)
    assert client.get("/api/3d/hi3d/status").status_code == 401
    assert client.post("/api/3d/hi3d/install").status_code == 401
    url = "/api/3d/gallery/" + "a" * 32 + "/refine"
    assert client.post(url, json={}).status_code == 401
    app.dependency_overrides[guard] = lambda: None
    assert client.post(url, json={"resolution": 768}).status_code == 422
    monkeypatch.setattr(Hi3DStudio, "status", lambda *a, **k: {"ready": True})
    monkeypatch.setattr(Hi3DStudio, "refine", lambda *a: {"kind": "hi3d_refine", "id": "c" * 32})
    assert client.post(url, json={"resolution": 1536}).json()["kind"] == "hi3d_refine"


def test_weight_preparation_refuses_tampered_download(tmp_path, monkeypatch):
    import io

    from agentfit.imagegen import hi3d_worker
    monkeypatch.setattr(hi3d_worker, "ROOT", tmp_path)
    data = b"expected weights"
    asset = {"repo": "publisher/model", "revision": "a" * 40, "file": "model.safetensors", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    body = {"assets": [asset], "host_free_bytes": 100 * 1024**3}
    monkeypatch.setattr(hi3d_worker.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"x" * len(data)))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        hi3d_worker.prepare(body)
    assert not (tmp_path / "verified.json").exists()
    assert not hi3d_worker.asset_path(asset).exists()
    body["host_free_bytes"] = None
    with pytest.raises(RuntimeError, match="reserve"):
        hi3d_worker.prepare(body)


def test_pinned_assets_have_sizes_and_checksums():
    assets = Hi3DStudio.assets()
    assert {a["repo"] for a in assets} == {"hi3d-official/Hi3Dv1.0", "facebook/dinov2-large", "openai/clip-vit-large-patch14"}
    assert all(a["bytes"] > 0 and len(a["sha256"]) == 64 and len(a["revision"]) == 40 for a in assets)


def test_source_colors_survive_hi3d_rescaling_without_changing_geometry(monkeypatch):
    import sys
    from types import SimpleNamespace

    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy.spatial")
    from agentfit.imagegen.hi3d_worker import transfer_source_colors

    monkeypatch.setitem(sys.modules, "trimesh", SimpleNamespace(visual=SimpleNamespace(ColorVisuals=lambda **kw: kw)))
    vertices = np.array([[0., 0, 0], [1, 0, 0], [0, 2, 0]])
    colors = np.array([[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255]], dtype=np.uint8)
    source = SimpleNamespace(vertices=vertices, visual=SimpleNamespace(kind="vertex", vertex_colors=colors))
    target = SimpleNamespace(vertices=vertices * 4 + 12)
    before = target.vertices.copy()
    assert transfer_source_colors(source, target)
    np.testing.assert_array_equal(target.visual["vertex_colors"], colors)
    np.testing.assert_array_equal(target.vertices, before)
    source.visual.kind = None
    assert not transfer_source_colors(source, target)
