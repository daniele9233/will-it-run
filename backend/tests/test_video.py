"""Video workflow and lifecycle, without ComfyUI, GPU, network or user data."""
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from agentfit.events import EventBus
from agentfit.imagegen import ltx25, minimax_h3
from agentfit.imagegen import video_catalog as catalog
from agentfit.imagegen.comfy import ComfyUnavailable
from agentfit.imagegen.video import VideoRequest, VideoStudio, workflow


class FakeVideoClient:
    def __init__(self):
        self.graph = None
        self.save_id = None
        self.fail = False
        self.wait = False
        self.running = "another-app"
        self.interrupted = False
        self.deleted = []
        self.unreachable = 0
        self.cause: Exception = httpx.ReadTimeout("timed out")

    def available(self):
        return True

    def models(self, folder):
        return sorted({name for p in catalog.PROFILES for _, name, f in p.requirements() if f == folder})

    def all_models(self, folders):
        return {f: self.models(f) for f in folders}

    def object_info(self):
        classes = set()
        for selected in catalog.PROFILES:
            graph = workflow(VideoRequest(model_key=selected.key, images=["a.png", "b.png", "c.png"], prompt="check",
                                          turbo=selected.turbo_steps is not None),
                             {role: name for role, name, _ in selected.requirements()}, "check")
            classes |= {node["class_type"] for node in graph.values()}
        return {name: {} for name in classes}

    def missing_nodes(self, classes):
        return [name for name in classes if name not in self.object_info()]

    def queue(self, graph):
        self.graph = graph
        self.save_id = next(key for key, node in graph.items() if node["class_type"] == "SaveVideo")
        return "our-prompt"

    def history(self, prompt_id):
        if self.unreachable:
            self.unreachable -= 1
            raise ComfyUnavailable("ComfyUI could not be reached") from self.cause
        if self.wait:
            return None
        if self.fail:
            return {"status": {"status_str": "error"}}
        return {"outputs": {self.save_id: {"images": [{"filename": "test.mp4", "subfolder": "video"}]}}}

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


def ready_status() -> dict:
    return {"job": {"status": "idle", "percent": 100, "received": 0, "total": 0, "file": "", "error": None},
            "environment": {"ready": True, "error": None, "vram_gb": 16.0},
            "weights_ready": True, "source_ready": True, "models_dir": ""}


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr("agentfit.imagegen.video_finish.tools", lambda: {"ffmpeg": "fake", "ffprobe": "fake"})
    result = VideoStudio(SimpleNamespace(client=FakeVideoClient(), bus=EventBus()),
                         directory=tmp_path / "videos", models_dir=tmp_path / "models")
    for setup in result.setups.values():
        monkeypatch.setattr(setup, "status", ready_status)
    return result


def finish(studio):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = next(iter(studio.jobs.values()))
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.02)
    pytest.fail("Video worker did not finish")


def files(key: str) -> dict[str, str]:
    return {role: name for role, name, _ in catalog.profile(key).requirements()}


def assert_connected(graph: dict) -> None:
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


def test_minimax_puts_first_and_last_on_the_fl2va_node_and_pins_the_middle_with_guides():
    r = VideoRequest(model_key=minimax_h3.KEY, images=["a.png", "b.png", "c.png", "d.png"],
                     keyframe_times=[0, 1.5, 6, 10], prompt="The camera dives through the clouds.",
                     duration_seconds=10, seed=5)
    graph = workflow(r, files(minimax_h3.KEY), "test")
    assert (r.frames, r.width, r.height, r.steps) == (243, 1344, 768, 20)
    i2v = graph["6"]["inputs"]
    assert graph[i2v["first_frame"][0]]["inputs"]["image"] == "a.png"
    assert graph[i2v["last_frame"][0]]["inputs"]["image"] == "d.png"
    guides = [graph[key]["inputs"] for key in ("200", "201")]
    assert [g["frame_idx"] for g in guides] == [36, 145]
    assert [graph[g["image"][0]]["inputs"]["image"] for g in guides] == ["b.png", "c.png"]
    assert guides[0]["positive"] == ["6", 0] and guides[1]["positive"] == ["200", 0]
    assert graph["12"]["inputs"]["conditioning"] == ["201", 0]
    assert graph["11"]["inputs"]["latent_image"] == ["6", 1]
    assert graph["9"]["inputs"]["sampler_name"] == "res_multistep"
    assert "5" not in graph  # no Turbo LoRA unless asked
    assert graph["13"]["inputs"]["samples"] == graph["14"]["inputs"]["samples"] == ["11", 0]
    assert graph["16"]["inputs"]["format"] == "mp4"
    assert_connected(graph)


def test_minimax_turbo_loads_the_fl2v_lora_and_uses_eight_steps():
    r = VideoRequest(model_key=minimax_h3.KEY, images=["a.png", "b.png"], prompt="move", turbo=True)
    graph = workflow(r, files(minimax_h3.KEY), "test")
    assert r.steps == 8
    assert graph["5"]["inputs"]["lora_name"] == "minimax_h3_fl2v_turbo_8step_v1.1_768p_comfyui_bf16.safetensors"
    assert graph["10"]["inputs"]["model"] == graph["12"]["inputs"]["model"] == ["5", 0]
    with pytest.raises(ValidationError):
        VideoRequest(model_key=ltx25.KEY, image="a.png", prompt="move", turbo=True)


def test_minimax_prompt_follows_the_official_fl2va_layout_and_keeps_a_finished_prompt():
    graph = workflow(VideoRequest(model_key=minimax_h3.KEY, images=["a.png", "b.png"], prompt="The car comes apart.\nSound: soft clicks",
                                  duration_seconds=6), files(minimax_h3.KEY), "test")
    prompt = graph["6"]["inputs"]["prompt"]
    assert prompt.startswith("How the reference pictures align with the target video")
    assert "Picture 2 (from Shot 1) aligns with the 6.58-second mark" in prompt
    assert "integrated_multimodal_description: [Shot 1] The car comes apart." in prompt
    assert "overall_soundscape: soft clicks" in prompt and prompt.endswith("non_diegetic_music: N/A")
    single = workflow(VideoRequest(model_key=minimax_h3.KEY, image="a.png", prompt="move"), files(minimax_h3.KEY), "test")
    assert single["6"]["inputs"]["prompt"].startswith("For the target video, at 0.00 seconds")
    assert "last_frame" not in single["6"]["inputs"]
    written = "integrated_multimodal_description: [Shot 1] mine\n\noverall_soundscape: N/A\n\nnon_diegetic_music: N/A"
    kept = workflow(VideoRequest(model_key=minimax_h3.KEY, image="a.png", prompt=written), files(minimax_h3.KEY), "test")
    assert kept["6"]["inputs"]["prompt"] == written


def test_ltx_pins_every_keyframe_in_both_stages_and_crops_guides_before_upsampling_and_decoding():
    r = VideoRequest(model_key=ltx25.KEY, images=["a.png", "b.png", "c.png"], prompt="Fly through.",
                     duration_seconds=10, seed=123)
    graph = workflow(r, files(ltx25.KEY), "test")
    assert (r.frames, r.width, r.height) == (241, 1280, 704)
    assert graph["8"]["inputs"]["width"] == 640 and graph["8"]["inputs"]["height"] == 352
    stage_one = [graph[str(240 + i)]["inputs"] for i in range(3)]
    stage_two = [graph[str(340 + i)]["inputs"] for i in range(3)]
    for guides in (stage_one, stage_two):
        assert [(g["frame_idx"], g["strength"]) for g in guides] == [(0, 1.0), (120, 0.6), (-1, 0.9)]
    assert graph["17"]["inputs"]["latent"] == ["16", 0]
    assert graph["31"]["inputs"]["samples"] == ["17", 2]
    assert stage_two[0]["latent"] == ["31", 0] and stage_two[0]["positive"] == ["17", 0]
    assert graph["38"]["inputs"]["latent"] == ["37", 0]
    assert graph["18"]["inputs"]["samples"] == ["38", 2]
    assert graph["19"]["inputs"]["samples"] == ["37", 1]
    assert graph["5"]["inputs"]["text"].startswith("Use the provided start image as the first frame")
    assert graph["21"]["inputs"]["format"] == "mp4"
    assert_connected(graph)


@pytest.mark.parametrize(("selected", "seconds", "frames"), [
    (minimax_h3.KEY, 5, 124), (minimax_h3.KEY, 15, 362), (ltx25.KEY, 5, 121), (ltx25.KEY, 15, 361),
])
def test_keyframe_models_use_their_own_frame_grid_and_reject_other_recipes(selected, seconds, frames):
    r = VideoRequest(model_key=selected, image="a.png", prompt="move", duration_seconds=seconds)
    assert (r.frames, r.fps) == (frames, 24)
    with pytest.raises(ValidationError):
        VideoRequest(model_key=selected, image="a.png", prompt="move", steps=7)
    with pytest.raises(ValidationError):
        VideoRequest(model_key=selected, image="a.png", prompt="move", negative="bad")
    with pytest.raises(ValidationError):
        VideoRequest(model_key=selected, images=[f"r{index}.png" for index in range(10)], prompt="move")


@pytest.mark.parametrize("times", [[0, 11], [0, 4, 4], [5, 1], [0]])
def test_keyframe_times_must_be_one_per_picture_inside_the_clip_and_increasing(times):
    count = 2 if len(times) == 1 else len(times)
    with pytest.raises(ValidationError):
        VideoRequest(model_key=ltx25.KEY, images=[f"k{i}.png" for i in range(count)], keyframe_times=times,
                     prompt="move", duration_seconds=10)


def test_default_keyframe_times_spread_from_the_first_to_the_last_frame():
    r = VideoRequest(model_key=minimax_h3.KEY, images=[f"k{i}.png" for i in range(6)], prompt="move", duration_seconds=10)
    assert r.times() == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]


def test_generation_persists_mp4_and_reproducible_recipe(studio):
    result = studio.generate(VideoRequest(model_key=ltx25.KEY, image="uploaded.png", prompt="A clothed portrait, slow movement"))
    assert finish(studio)["status"] == "done"
    rows = studio.gallery()
    assert rows[0]["id"] == result["id"]
    assert rows[0]["request"]["seed"] >= 0
    assert rows[0]["request"]["images"] == ["uploaded.png"]
    assert studio.file(result["id"]).read_bytes() == b"fake-mp4"
    reopened = VideoStudio(studio.images, directory=studio.directory)
    assert reopened.gallery() == rows
    with pytest.raises(KeyError):
        studio.file("../secret")


def test_engine_error_is_reported_without_gallery_result(studio):
    studio.images.client.fail = True
    studio.generate(VideoRequest(model_key=ltx25.KEY, image="uploaded.png", prompt="movement"))
    assert finish(studio)["status"] == "failed"
    assert not studio.gallery()


def test_a_busy_engine_that_answers_late_does_not_fail_the_job(studio, monkeypatch):
    monkeypatch.setattr("agentfit.imagegen.video.time.sleep", lambda _: None)
    studio.images.client.unreachable = 3
    studio.generate(VideoRequest(model_key=ltx25.KEY, image="uploaded.png", prompt="movement"))
    assert finish(studio)["status"] == "done"
    assert studio.gallery()


def test_an_engine_that_refuses_connections_fails_the_job(studio):
    studio.images.client.unreachable = 1
    studio.images.client.cause = httpx.ConnectError("refused")
    studio.generate(VideoRequest(model_key=ltx25.KEY, image="uploaded.png", prompt="movement"))
    job = finish(studio)
    assert job["status"] == "failed"
    assert "could not be reached" in job["error"]


def test_an_engine_silent_beyond_the_limit_fails_the_job(studio, monkeypatch):
    monkeypatch.setattr("agentfit.imagegen.video.ENGINE_SILENCE_LIMIT", 0.0)
    studio.images.client.unreachable = 1
    studio.generate(VideoRequest(model_key=ltx25.KEY, image="uploaded.png", prompt="movement"))
    assert finish(studio)["status"] == "failed"


def test_fullhd_metadata_is_published_only_after_finishing(studio, monkeypatch):
    def finishing(source, destination, **options):
        assert source.read_bytes() == b"fake-mp4"
        assert options["seconds"] == 15 and options["source_frames"] == 361
        assert options["audio_fps"] == 24
        destination.write_bytes(b"finished-mp4")
        return {"width": 1920, "height": 1080, "fps": 30, "frames": 450, "seconds": 15}

    monkeypatch.setattr("agentfit.imagegen.video_finish.finish", finishing)
    studio.generate(VideoRequest(model_key=ltx25.KEY, image="start.png", prompt="move", duration_seconds=15))
    assert finish(studio)["status"] == "done"
    row = studio.gallery()[0]
    assert row["output"]["frames"] == 450
    assert row["request"]["frames"] == 361
    assert studio.file(row["id"]).read_bytes() == b"finished-mp4"


def test_cancel_during_finishing_does_not_publish_a_partial_video(studio, monkeypatch):
    from agentfit.imagegen.video_finish import VideoCancelled

    def finishing(source, destination, **options):
        options["stage"]("finishing")
        while not options["cancelled"]():
            time.sleep(0.01)
        raise VideoCancelled

    monkeypatch.setattr("agentfit.imagegen.video_finish.finish", finishing)
    result = studio.generate(VideoRequest(model_key=ltx25.KEY, image="start.png", prompt="move", duration_seconds=6))
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
    result = studio.generate(VideoRequest(model_key=ltx25.KEY, image="uploaded.png", prompt="movement"))
    with pytest.raises(ValueError, match="already"):
        studio.generate(VideoRequest(model_key=ltx25.KEY, image="uploaded.png", prompt="movement"))
    studio.cancel(result["id"])
    assert finish(studio)["status"] == "cancelled"
    assert studio.images.client.deleted == ["our-prompt"]
    assert not studio.images.client.interrupted


def test_missing_ltx_weights_never_fall_back(studio, monkeypatch):
    monkeypatch.setattr(studio.setups["ltx25"], "status", lambda: {**ready_status(), "weights_ready": False})
    assert not next(m for m in studio.status()["models"] if m["key"] == ltx25.KEY)["ready"]
    with pytest.raises(ValueError, match="not ready"):
        studio.generate(VideoRequest(model_key=ltx25.KEY, image="uploaded.png", prompt="movement"))


@pytest.mark.parametrize("values", [
    {"model_key": "invented"}, {"end_image": "../private.png"},
    {"end_image": "C:\\private.png"}, {"model_key": "wan21-flf-q4"},
    {"model_key": "wan22-i2v-q6"},
])
def test_model_and_ending_picture_validation(values):
    with pytest.raises((ValueError, ValidationError)):
        VideoRequest(**({"image": "start.png", "prompt": "movement"} | values))


def test_selected_profile_persists_every_reference_and_the_model(studio):
    studio.generate(VideoRequest(model_key=ltx25.KEY, images=["a.png", "b.png"], prompt="movement"))
    assert finish(studio)["status"] == "done"
    row = studio.gallery()[0]
    assert "LTX-2.5" in row["model"]
    assert row["request"]["model_key"] == ltx25.KEY
    assert row["request"]["images"] == ["a.png", "b.png"]
    assert row["request"]["fps"] == 24
    assert row["runtime"]["keyframe_times"][0] == 0.0
    assert row["runtime"]["keyframe_frames"] == [0, 120]


def test_incomplete_download_is_not_ready_even_when_listed(studio):
    p = catalog.profile(minimax_h3.KEY)
    path = studio.models_dir / "video" / "minimaxh3" / "diffusion_models" / "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"partial")
    row = next(m for m in studio.status()["models"] if m["key"] == p.key)
    assert not row["ready"]
    assert row["components"][0]["source_present"]
    assert not row["components"][0]["size_valid"]


def test_download_manifest_has_pinned_verified_sources():
    import re

    assets = catalog.assets()
    assert len(assets) == 10
    assert {a["filename"] for a in assets} >= {
        "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
        "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
        "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "minimax_h3_fl2v_turbo_8step_v1.1_768p_comfyui_bf16.safetensors"}
    # The removed profiles stay removed: no Ref2VA, MSR LoRA, VSA gate or Wan VACE weights.
    assert not any(word in a["filename"] for a in assets for word in ("ref2va", "MSR", "vsa_gate", "wan"))
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
        assert [m["key"] for m in state["models"]] == [minimax_h3.KEY, ltx25.KEY]
        assert state["models"][0]["max_images"] == 9
        assert client.post("/api/videos/models/invented/setup").status_code == 404
        assert client.post("/api/videos/ref2va/setup").status_code == 404
        assert client.get("/api/videos/flow").status_code == 404
        assert client.post("/api/videos/generate", json={"model_key": ltx25.KEY, "image": "a.png", "prompt": "move", "steps": 4}).status_code == 422
        response = client.post("/api/videos/generate", json={"model_key": ltx25.KEY, "image": "a.png", "prompt": "move"})
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


def test_prompt_library_round_trip_after_restart(studio):
    from agentfit.imagegen.video_prompts import VideoPromptInput, VideoPromptStore

    store = VideoPromptStore(studio.directory / "prompts")
    saved = store.save(VideoPromptInput(name="Night", prompt="A clothed adult walks.", negative=""))
    assert VideoPromptStore(studio.directory / "prompts").list() == [saved]
    assert json.dumps(saved)  # serialisable recipe metadata
