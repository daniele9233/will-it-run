"""Native RelayVSR graph, resource gates and publication use fakes, never CUDA or owner data."""
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentfit.events import EventBus
from agentfit.imagegen import relayvsr, video_finish
from agentfit.imagegen.video import VideoStudio
from agentfit.imagegen.video_setup import VideoModelSetup, asset_path
from agentfit.imagegen.video_upscale import (
    UpscaleRequest,
    VideoUpscaler,
    dimensions,
    frame_windows,
    preparation_geometry,
    probe,
)


def test_four_pinned_files_are_the_requested_models_and_matching_decoders():
    assets = relayvsr.assets()
    assert sum(a["size"] for a in assets) == 10582994448
    assert {a["filename"] for a in assets} == {
        "relayvsr_x2_lightx2v_int8_convrot.safetensors", "relayvsr_x4_int8_convrot.safetensors",
        "relayvsr_flash_vae_x2_fp32.safetensors", "relayvsr_flash_vae_x4_fp32.safetensors"}
    assert all(len(a["sha256"]) == 64 and len(a["revision"]) == 40 and a["revision"] in a["url"] for a in assets)


@pytest.mark.parametrize("factor", [2, 4])
def test_graph_pairs_each_scale_with_its_flash_and_fixed_one_step_recipe(factor):
    files = {a["role"]: a["filename"] for a in relayvsr.requirements(factor)}
    graph = relayvsr.workflow(factor, files, "owned/input.mp4", "job", detail=.8)
    assert graph["1"]["inputs"]["unet_name"] == files[f"x{factor}"]
    assert graph["4"]["inputs"]["vae_name"] == files[f"vae_x{factor}"]
    assert graph["11"]["inputs"]["steps"] == graph["11"]["inputs"]["cfg"] == 1
    assert graph["11"]["inputs"]["positive"] == graph["11"]["inputs"]["negative"] == ["8", 0]
    assert graph["13"]["inputs"]["images"] == graph["8"]["inputs"]["images"] == ["3", 0]
    assert graph["13"]["inputs"]["detail"] == .8
    assert graph["14"]["inputs"]["fps"] == ["3", 2]
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


@pytest.mark.parametrize("count", [2, 15, 16, 31, 150, 1200])
def test_bounded_context_windows_keep_every_frame_exactly_once(count):
    windows = frame_windows(count, False)
    actual = [frame for first, _, skip, keep in windows for frame in range(first + skip, first + skip + keep)]
    assert actual == list(range(count))
    assert all(first % 15 == 0 and end - first <= 61 for first, end, _, _ in windows)
    assert frame_windows(count, True) == [(0, count, 0, count)]


def test_native_scale_and_explicit_presets_are_not_silently_substituted():
    assert dimensions({"width": 960, "height": 540}, UpscaleRequest(path="video.mp4", factor=4)) == (3840, 2160)
    assert dimensions({"width": 768, "height": 1344}, UpscaleRequest(path="video.mp4", target="4k")) == (2160, 3840)
    with pytest.raises(ValueError, match="4K"):
        dimensions({"width": 1920, "height": 1080}, UpscaleRequest(path="video.mp4", factor=4))


def test_anamorphic_sd_is_not_squeezed_into_square_pixel_presets():
    source = {"width": 720, "height": 576, "sample_aspect_ratio": "64/45", "display_aspect_ratio": "16/9"}
    filters, sar = preparation_geometry(source, UpscaleRequest(path="test.mp4", target="1080p"), 960, 540)
    assert "scale=960:540" in filters and sar == "1"
    native, sar = preparation_geometry(source, UpscaleRequest(path="test.mp4"), 720, 576)
    assert "scale=720:576" in native and sar == "64/45"


def test_chunk_context_guides_the_coarse_model_but_is_not_decoded_again():
    files = {a["role"]: a["filename"] for a in relayvsr.requirements(2)}
    graph = relayvsr.workflow(2, files, "input.mp4", "job", decode_start=45, decode_length=16)
    assert graph["8"]["inputs"]["images"] == ["3", 0]
    assert graph["10"]["inputs"]["trim_amount"] == 3
    assert graph["9"]["inputs"]["batch_index"] == 45 and graph["9"]["inputs"]["length"] == 16
    assert graph["13"]["inputs"]["images"] == ["9", 0] and graph["13"]["inputs"]["samples"] == ["10", 0]


@pytest.mark.parametrize("values", [{}, {"source_id": "a" * 32, "path": "video.mp4"}, {"path": "video.mp4", "factor": 3},
    {"source_id": "../escape"}, {"path": "video.mp4", "detail": 2}, {"path": "video.mp4", "start_seconds": -1}])
def test_invalid_or_ambiguous_requests_are_refused(values):
    with pytest.raises(ValueError):
        UpscaleRequest(**values)


@pytest.mark.parametrize("rate,count", [("30000/1001", "75"), ("0/0", "75"), ("30/1", "N/A")])
def test_probe_preserves_measured_rational_rates_and_rejects_unknowns(tmp_path, monkeypatch, rate, count):
    monkeypatch.setattr(video_finish, "tools", lambda: {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
    data = {"streams": [{"codec_type": "video", "width": 960, "height": 540, "avg_frame_rate": rate, "nb_read_frames": count,
                         "sample_aspect_ratio": "1:1", "r_frame_rate": rate}]}
    monkeypatch.setattr(video_finish, "_run", lambda *a: json.dumps(data))
    if rate == "0/0" or count == "N/A":
        with pytest.raises(ValueError):
            probe(tmp_path / "input.mp4", tmp_path)
    else:
        assert probe(tmp_path / "input.mp4", tmp_path)["fps"] == "30000/1001"


def test_probe_records_anamorphic_geometry_and_irregular_average_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(video_finish, "tools", lambda: {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
    data = {"streams": [{"codec_type": "video", "width": 720, "height": 576, "nb_read_frames": "312",
                          "avg_frame_rate": "11700/487", "r_frame_rate": "150/1", "sample_aspect_ratio": "64:45"}]}
    monkeypatch.setattr(video_finish, "_run", lambda *a: json.dumps(data))
    info = probe(tmp_path / "test.mp4", tmp_path)
    assert info["sample_aspect_ratio"] == "64/45" and info["display_aspect_ratio"] == "16/9"
    assert info["irregular_timing"] and info["fps"] == "11700/487"


class Client:
    def __init__(self, assets):
        self.assets, self.graph, self.wait = assets, None, False
        self.deleted, self.interrupted = [], False

    def available(self):
        return True

    def all_models(self, folders):
        return {f: [a["filename"] for a in self.assets if a["folder"] == f] for f in folders}

    def object_info(self):
        return {n: {} for n in relayvsr.NODE_TYPES}

    def queue(self, graph):
        self.graph = graph
        return "ours"

    def history(self, identifier):
        return None if self.wait else {"outputs": {"15": {"videos": [{"filename": "native.mp4", "subfolder": "owned"}]}}}

    def view(self, *args):
        return b"upscaled-native"

    def _request(self, method, route, **kwargs):
        if method == "POST":
            self.deleted.extend(kwargs["json_body"]["delete"])
        return {"queue_running": [[0, "another-app"]]}

    def _json(self, value):
        return value

    def interrupt(self):
        self.interrupted = True


@pytest.fixture
def upscaler(tmp_path, monkeypatch):
    assets = [{"role": role, "folder": "vae" if role.startswith("vae") else "diffusion_models", "filename": f"{role}.safetensors",
               "size": 1, "sha256": hashlib.sha256(b"x").hexdigest(), "revision": "a" * 40} for role in ("x2", "x4", "vae_x2", "vae_x4")]
    monkeypatch.setattr(relayvsr, "assets", lambda: assets)
    monkeypatch.setattr(relayvsr.RelayVSRSetup, "environment", lambda _: {"ready": True, "error": None, "vram_gb": 16, "native_nodes": True})
    engine = SimpleNamespace(install=lambda: SimpleNamespace(root=tmp_path / "comfy", python=None))
    studio = VideoStudio(SimpleNamespace(client=Client(assets), bus=EventBus(), engine=engine), directory=tmp_path / "videos", models_dir=tmp_path / "models")
    studio.directory.mkdir()
    (studio.directory / ("a" * 32 + ".mp4")).write_bytes(b"original-video")
    value = VideoUpscaler(studio)
    for asset in assets:
        target = asset_path(value.setup.root, asset)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
        VideoModelSetup._stamp(target, asset)
    monkeypatch.setattr(value, "disk", lambda: {"free": 100 * 1024**3, "reserve": 30 * 1024**3, "needed": 0, "safe": True})
    monkeypatch.setattr(video_finish, "tools", lambda: {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
    monkeypatch.setattr("agentfit.imagegen.video_upscale.psutil.virtual_memory", lambda: SimpleNamespace(available=16 * 1024**3))
    commands = []
    def run(args, cancelled, directory):
        commands.append(args)
        if "-show_streams" in args:
            source = Path(args[-1]).name.startswith("a" * 32)
            return json.dumps({"streams": [{"codec_type": "video", "width": 960 if source else 1920, "height": 540 if source else 1080,
                                             "avg_frame_rate": "30/1", "r_frame_rate": "30/1", "sample_aspect_ratio": "1:1", "nb_read_frames": "20"}] +
                              ([{"codec_type": "audio"}] if source or Path(args[-1]).name == "result.mp4" else [])})
        Path(args[-1]).write_bytes(b"verified-video")
        return ""
    monkeypatch.setattr(video_finish, "_run", run)
    return value, commands


def wait(upscaler):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = next(iter(upscaler.studio.jobs.values()))
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(.02)
    pytest.fail("upscale worker did not finish")


def test_result_is_verified_and_published_as_a_new_copy_with_original_audio(upscaler):
    service, commands = upscaler
    job = service.generate(UpscaleRequest(source_id="a" * 32))
    finished = wait(service)
    assert finished["status"] == "done", finished["error"]
    result = service.gallery()[0]
    assert result["id"] == job["id"] and result["output"]["audio"]
    assert result["output"]["frames"] == 20 and result["output"]["width"] == 1920
    assert result["prepared"]["chunks"] == 1
    assert result["processing_fps"] > 0
    assert service.studio.file("a" * 32).read_bytes() == b"original-video"
    assert any("1:a:0" in c for c in commands)
    with pytest.raises(KeyError):
        service.file("../outside")


def test_resource_failure_never_queues_gpu_work_or_publishes_a_result(upscaler, monkeypatch):
    service, _ = upscaler
    monkeypatch.setattr("agentfit.imagegen.video_upscale.psutil.virtual_memory", lambda: SimpleNamespace(available=1))
    service.generate(UpscaleRequest(source_id="a" * 32))
    assert wait(service)["status"] == "failed"
    assert not service.gallery() and service.studio.images.client.graph is None


def test_cancel_shares_the_video_slot_and_does_not_interrupt_someone_else(upscaler):
    service, _ = upscaler
    client = service.studio.images.client
    client.wait = True
    job = service.generate(UpscaleRequest(source_id="a" * 32))
    deadline = time.monotonic() + 3
    while client.graph is None and time.monotonic() < deadline:
        time.sleep(.02)
    with pytest.raises(ValueError, match="already"):
        service.generate(UpscaleRequest(source_id="a" * 32))
    service.studio.cancel(job["id"])
    assert wait(service)["status"] == "cancelled"
    assert client.deleted == ["ours"] and not client.interrupted
    assert not service.gallery()


def test_upscale_routes_require_auth_and_native_save_keeps_the_source(upscaler, monkeypatch, tmp_path):
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from agentfit.api import video_routes
    service, _ = upscaler
    monkeypatch.setattr(video_routes, "VideoStudio", lambda _: service.studio)
    monkeypatch.setattr(video_routes, "VideoUpscaler", lambda _: service)
    def guard():
        raise HTTPException(401)
    app = FastAPI()
    video_routes.install_routes(app, SimpleNamespace(images=service.studio.images), [Depends(guard)])
    client = TestClient(app)
    assert client.get("/api/videos/upscale/status").status_code == 401
    assert client.post("/api/videos/upscale/setup").status_code == 401
    app.dependency_overrides[guard] = lambda: None
    assert client.post("/api/videos/upscale", json={"source_id": "a" * 32, "factor": 3}).status_code == 422
    result = client.post("/api/videos/upscale", json={"source_id": "a" * 32})
    assert result.status_code == 200 and wait(service)["status"] == "done"
    identifier = result.json()["id"]
    destination = tmp_path / "saved.mp4"
    assert client.post(f"/api/videos/upscale/gallery/{identifier}/save", json={"path": str(destination)}).status_code == 200
    assert destination.read_bytes() == service.file(identifier).read_bytes()
    assert client.post(f"/api/videos/upscale/gallery/{identifier}/save", json={"path": "relative.mp4"}).status_code == 400
    assert client.delete(f"/api/videos/upscale/gallery/{identifier}").status_code == 200
    assert service.studio.file("a" * 32).read_bytes() == b"original-video"
