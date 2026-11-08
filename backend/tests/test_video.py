"""Video workflow and lifecycle, without ComfyUI, GPU, network or user data."""
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentfit.events import EventBus
from agentfit.imagegen import video_catalog as catalog
from agentfit.imagegen.video import ENCODER, HIGH, LOW, VAE, VideoRequest, VideoStudio, workflow


class FakeVideoClient:
    def __init__(self):
        self.graph = None
        self.fail = False
        self.wait = False
        self.running = "another-app"
        self.interrupted = False
        self.deleted = []

    def available(self):
        return True

    def models(self, folder):
        return sorted({name for p in catalog.PROFILES for _, name, f in p.requirements() if f == folder})

    def all_models(self, folders):
        return {f: self.models(f) for f in folders}

    def object_info(self):
        return {name: {} for name in ["UnetLoaderGGUF", "CLIPLoader", "VAELoader", "CLIPTextEncode", "LoadImage",
                              "WanImageToVideo", "WanFirstLastFrameToVideo",
                              "ModelSamplingSD3", "VAEDecode", "ResizeAndPadImage", "KSampler", "KSamplerAdvanced",
                              "CLIPVisionLoader", "CLIPVisionEncode", "CreateVideo", "SaveVideo"]}

    def missing_nodes(self, classes):
        return [name for name in classes if name not in self.object_info()]

    def queue(self, graph):
        self.graph = graph
        return "our-prompt"

    def history(self, prompt_id):
        if self.wait:
            return None
        if self.fail:
            return {"status": {"status_str": "error"}}
        return {"outputs": {"15": {"images": [{"filename": "test.mp4", "subfolder": "video"}]}}}

    def view(self, *args):
        return b"fake-mp4"

    def _request(self, method, path, **kwargs):
        if method == "POST":
            self.deleted.extend(kwargs["json_body"]["delete"])
        return {"queue_running": [[0, self.running]]}

    def _json(self, value):
        return value

    def interrupt(self):
        self.interrupted = True


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr("agentfit.imagegen.video_finish.tools", lambda: {"ffmpeg": "fake", "ffprobe": "fake"})
    return VideoStudio(SimpleNamespace(client=FakeVideoClient(), bus=EventBus()), directory=tmp_path / "videos", models_dir=tmp_path / "models")


def finish(studio):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = next(iter(studio.jobs.values()))
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.02)
    pytest.fail("Video worker did not finish")


def test_graph_uses_exact_models_and_continuous_noise_schedule():
    request = VideoRequest(image="uploaded.png", prompt="A clothed adult portrait, slight head movement", seed=123)
    graph = workflow(request, {"high": HIGH, "low": LOW, "encoder": ENCODER, "vae": VAE}, "test")
    assert graph["1"]["inputs"]["unet_name"] == HIGH
    assert graph["2"]["inputs"]["unet_name"] == LOW
    high, low = graph["11"]["inputs"], graph["12"]["inputs"]
    assert high["end_at_step"] == low["start_at_step"] == 10
    assert high["return_with_leftover_noise"] == "enable"
    assert low["add_noise"] == "disable"
    assert low["latent_image"] == ["11", 0]
    assert graph["8"]["inputs"]["length"] == 33


@pytest.mark.parametrize("override", [{"width": 513}, {"frames": 32}, {"frames": 365}, {"image": "../secret"}, {"prompt": " "}])
def test_request_rejects_invalid_inputs(override):
    with pytest.raises(ValidationError):
        VideoRequest(**({"image": "test.png", "prompt": "movement"} | override))


def test_generation_persists_mp4_and_reproducible_recipe(studio):
    result = studio.generate(VideoRequest(image="uploaded.png", prompt="A clothed portrait, slow movement"))
    assert finish(studio)["status"] == "done"
    rows = studio.gallery()
    assert rows[0]["id"] == result["id"]
    assert rows[0]["request"]["seed"] >= 0
    assert studio.file(result["id"]).read_bytes() == b"fake-mp4"
    reopened = VideoStudio(studio.images, directory=studio.directory)
    assert reopened.gallery() == rows
    with pytest.raises(KeyError):
        studio.file("../secret")


def test_engine_error_is_reported_without_gallery_result(studio):
    studio.images.client.fail = True
    studio.generate(VideoRequest(image="uploaded.png", prompt="movement"))
    assert finish(studio)["status"] == "failed"
    assert not studio.gallery()


def test_fullhd_metadata_is_published_only_after_finishing(studio, monkeypatch):
    def finishing(source, destination, **options):
        assert source.read_bytes() == b"fake-mp4"
        assert options["seconds"] == 15 and options["source_frames"] == 241
        destination.write_bytes(b"finished-mp4")
        return {"width": 1920, "height": 1080, "fps": 30, "frames": 450, "seconds": 15}

    monkeypatch.setattr("agentfit.imagegen.video_finish.finish", finishing)
    studio.generate(VideoRequest(image="start.png", prompt="move", duration_seconds=15))
    assert finish(studio)["status"] == "done"
    row = studio.gallery()[0]
    assert row["output"]["frames"] == 450
    assert row["request"]["frames"] == 241
    assert studio.file(row["id"]).read_bytes() == b"finished-mp4"


def test_cancel_during_finishing_does_not_publish_a_partial_video(studio, monkeypatch):
    from agentfit.imagegen.video_finish import VideoCancelled

    def finishing(source, destination, **options):
        options["stage"]("finishing")
        while not options["cancelled"]():
            time.sleep(0.01)
        raise VideoCancelled

    monkeypatch.setattr("agentfit.imagegen.video_finish.finish", finishing)
    result = studio.generate(VideoRequest(image="start.png", prompt="move", duration_seconds=6))
    deadline = time.monotonic() + 3
    while studio.jobs[result["id"]]["stage"] != "finishing" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert studio.jobs[result["id"]]["stage"] == "finishing"
    studio.cancel(result["id"])
    assert finish(studio)["status"] == "cancelled"
    assert not list(studio.directory.iterdir())
    assert not studio.images.client.interrupted


def test_cancel_pending_video_never_interrupts_another_apps_job(studio):
    studio.images.client.wait = True
    result = studio.generate(VideoRequest(image="uploaded.png", prompt="movement"))
    with pytest.raises(ValueError, match="already"):
        studio.generate(VideoRequest(image="uploaded.png", prompt="movement"))
    studio.cancel(result["id"])
    assert finish(studio)["status"] == "cancelled"
    assert studio.images.client.deleted == ["our-prompt"]
    assert not studio.images.client.interrupted


def test_missing_q6_never_falls_back_to_q4(studio):
    original = studio.images.client.models
    studio.images.client.models = lambda folder: [HIGH] if folder == "unet_gguf" else original(folder)
    assert not studio.status()["ready"]
    with pytest.raises(ValueError, match="not ready"):
        studio.generate(VideoRequest(image="uploaded.png", prompt="movement"))


@pytest.mark.parametrize("selected", [p for p in catalog.PROFILES if p.input_mode != "reference"], ids=lambda p: p.key)
def test_every_profile_binds_both_pictures_with_no_dangling_links(selected):
    request = VideoRequest(model_key=selected.key, image="start.png", end_image="end.png", prompt="movement")
    graph = workflow(request, {role: name for role, name, _ in selected.requirements()}, "test")
    assert graph["16"]["inputs"]["image"] == "end.png"
    assert graph["8"]["inputs"]["start_image"] == ["20", 0]
    assert graph["8"]["inputs"]["end_image"] == ["21", 0]
    assert graph["20"]["inputs"]["image"] == ["7", 0]
    assert graph["21"]["inputs"]["image"] == ["16", 0]
    assert graph["20"]["inputs"]["padding_color"] == "black"
    assert graph["13"]["class_type"] == "VAEDecode"
    assert request.cfg == selected.cfg and request.fps == selected.fps
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph
    if selected.low:
        assert graph["2"]["inputs"]["unet_name"] == selected.low
        assert graph["12"]["inputs"]["add_noise"] == "disable"
    else:
        assert "2" not in graph and "12" not in graph
        assert graph["11"]["class_type"] == "KSampler"
    if selected.vision:
        assert graph["19"]["inputs"]["image"] == ["16", 0]
        assert graph["8"]["inputs"]["clip_vision_end_image"] == ["19", 0]


@pytest.mark.parametrize("values", [
    {"model_key": "invented"}, {"end_image": "../private.png"},
    {"end_image": "C:\\private.png"}, {"model_key": "wan21-flf-q4"},
    {"model_key": "wan22-fun-5b-q6"}, {"model_key": "wan22-fun-a14b-q4"},
])
def test_model_and_ending_picture_validation(values):
    with pytest.raises((ValueError, ValidationError)):
        VideoRequest(**({"image": "start.png", "prompt": "movement"} | values))


def test_selected_profile_persists_both_inputs_and_model(studio):
    studio.generate(VideoRequest(model_key="wan21-flf-q4", image="start.png", end_image="end.png", prompt="movement"))
    assert finish(studio)["status"] == "done"
    row = studio.gallery()[0]
    assert "FLF2V" in row["model"]
    assert row["request"]["model_key"] == "wan21-flf-q4"
    assert row["request"]["end_image"] == "end.png"
    assert row["request"]["fps"] == 16


def test_incomplete_download_is_not_ready_even_when_listed(studio):
    p = catalog.profile("wan21-flf-q4")
    path = studio.models_dir / p.high
    path.parent.mkdir(parents=True)
    path.write_bytes(b"partial")
    row = next(m for m in studio.status()["models"] if m["key"] == p.key)
    assert not row["ready"]
    assert row["components"][0]["source_present"]
    assert not row["components"][0]["size_valid"]


def test_download_manifest_has_pinned_verified_sources():
    import re

    assets = catalog.assets()
    assert len(assets) == 8
    assert not any("Fun" in a["filename"] or "Wan2.2_VAE" in a["filename"] for a in assets)
    for item in assets:
        assert item["url"].startswith("https://huggingface.co/")
        assert re.fullmatch("[a-f0-9]{64}", item["sha256"])
        assert re.fullmatch("[a-f0-9]{40}", item["revision"])
        assert item["revision"] in item["url"] and item["size"] > 0


def test_video_api_keeps_guards_validation_playback_and_deletion(studio, monkeypatch, tmp_path):
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient

    from agentfit.api import video_routes

    def guard(authorization: str = Header(default="")):
        if authorization != "Bearer test":
            raise HTTPException(401)

    monkeypatch.setattr(video_routes, "VideoStudio", lambda images: studio)
    app = FastAPI()
    video_routes.install_routes(app, SimpleNamespace(images=studio.images), [Depends(guard)])
    with TestClient(app) as client:
        assert client.get("/api/videos/status").status_code == 401
        assert client.post("/api/videos/gallery/unknown/save", json={"path": str(tmp_path / "export.mp4")}).status_code == 401
        client.headers["Authorization"] = "Bearer test"
        state = client.get("/api/videos/status").json()
        assert [m["key"] for m in state["models"]] == ["wan22-i2v-q6", "wan21-flf-q4", "minimax-h3-ref2va-vsa"]
        assert client.get("/api/videos/flow").status_code == 404
        assert client.post("/api/videos/generate", json={"model_key": "wan21-flf-q4", "image": "a.png", "prompt": "move"}).status_code == 422
        response = client.post("/api/videos/generate", json={"model_key": "wan21-flf-q4", "image": "a.png", "end_image": "b.png", "prompt": "move"})
        assert response.status_code == 200
        assert finish(studio)["status"] == "done"
        video_id = response.json()["id"]
        result = client.get(f"/api/videos/gallery/{video_id}/file")
        assert result.headers["content-type"] == "video/mp4" and result.content == b"fake-mp4"
        destination = tmp_path / "My exported video"
        saved = client.post(f"/api/videos/gallery/{video_id}/save", json={"path": str(destination)})
        assert saved.status_code == 200
        assert saved.json() == {"path": str(destination.with_suffix(".mp4")), "bytes": 8}
        assert destination.with_suffix(".mp4").read_bytes() == b"fake-mp4"
        assert studio.file(video_id).read_bytes() == b"fake-mp4"
        assert client.post(f"/api/videos/gallery/{video_id}/save", json={"path": "relative.mp4"}).status_code == 400
        assert client.post(f"/api/videos/gallery/{video_id}/save", json={"path": str(tmp_path / "bad.exe")}).status_code == 400
        assert client.post(f"/api/videos/gallery/{video_id}/save", json={"path": str(tmp_path / "absent" / "hero.mp4")}).status_code == 400
        assert client.post("/api/videos/gallery/unknown/save", json={"path": str(destination)}).status_code == 404
        with monkeypatch.context() as patch:
            patch.setattr(video_routes.shutil, "copyfile", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
            assert client.post(f"/api/videos/gallery/{video_id}/save", json={"path": str(tmp_path / "error.mp4")}).status_code == 400
        assert client.delete(f"/api/videos/gallery/{video_id}").status_code == 200
        assert client.get(f"/api/videos/gallery/{video_id}/file").status_code == 404


def test_video_prompt_library_keeps_pairs_after_gallery_deletion_and_requires_auth(studio, monkeypatch):
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient

    from agentfit.api import video_routes

    def guard(authorization: str = Header(default="")):
        if authorization != "Bearer test":
            raise HTTPException(401)

    monkeypatch.setattr(video_routes, "VideoStudio", lambda images: studio)
    app = FastAPI()
    video_routes.install_routes(app, SimpleNamespace(images=studio.images), [Depends(guard)])
    body = {"name": "My GPU effect", "prompt": "Separate the GPU.\nCamera locked.", "negative": "no morphing\nno flicker"}
    with TestClient(app) as client:
        assert client.get("/api/videos/prompts").status_code == 401
        assert client.post("/api/videos/prompts", json=body).status_code == 401
        assert client.delete("/api/videos/prompts/unknown").status_code == 401
        client.headers["Authorization"] = "Bearer test"
        assert client.get("/api/videos/prompts").json() == {"prompts": []}
        assert client.post("/api/videos/prompts", json=body | {"prompt": " "}).status_code == 422
        response = client.post("/api/videos/prompts", json=body)
        assert response.status_code == 200
        saved = response.json()
        assert all(saved[key] == value for key, value in body.items())
        video_id = "a" * 32
        studio.directory.mkdir(exist_ok=True)
        (studio.directory / f"{video_id}.mp4").write_bytes(b"fake-video")
        assert client.delete(f"/api/videos/gallery/{video_id}").status_code == 200
        assert client.get("/api/videos/prompts").json()["prompts"] == [saved]
        assert studio.images.client.graph is None
        assert client.delete(f"/api/videos/prompts/{saved['id']}").status_code == 200
        assert client.get("/api/videos/prompts").json() == {"prompts": []}
        assert client.delete(f"/api/videos/prompts/{saved['id']}").status_code == 404
