"""Verified, resumable video model setup and graph progress, using HTTP fakes, never GPU or owner data."""
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from agentfit.imagegen import ltx25, minimax_h3, video_setup
from agentfit.imagegen.video import VideoRequest, workflow
from agentfit.imagegen.video_catalog import profile
from agentfit.imagegen.video_progress import VideoProgress
from agentfit.imagegen.video_setup import KeyframeModelSetup, SetupCancelled, asset_path, verified_asset


def test_keyframe_sampling_and_audio_decoding_are_visible_in_video_progress():
    r = VideoRequest(model_key=minimax_h3.KEY, images=["a.png", "b.png"], prompt="move")
    graph = workflow(r, {role: name for role, name, _ in profile(minimax_h3.KEY).requirements()}, "test")
    progress = VideoProgress(graph)
    progress.prompt_id = "ours"
    def event(kind, **data):
        return progress.event({"type": kind, "data": {"prompt_id": "ours", **data}})
    assert event("progress", node="11", value=10, max=20)["percent"] == 45
    assert event("executing", node="14")["stage"] == "decoding"
    assert event("executing", node="16")["percent"] < 100


def test_ltx_two_stage_progress_counts_both_samplers():
    r = VideoRequest(model_key=ltx25.KEY, image="a.png", prompt="move")
    graph = workflow(r, {role: name for role, name, _ in profile(ltx25.KEY).requirements()}, "test")
    progress = VideoProgress(graph)
    progress.prompt_id = "ours"
    assert progress.samplers == ["15", "36"]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data = b"verified model bytes"
    asset = {"filename": "model.safetensors", "folder": "diffusion_models", "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "revision": "a" * 40,
             "url": "https://huggingface.co/publisher/model/resolve/" + "a" * 40 + "/model.safetensors"}
    return KeyframeModelSetup(None, tmp_path / "video" / "minimaxh3", [asset], recommended_vram_gb=24), asset, data


def test_pinned_download_verifies_before_atomic_publication_and_detects_later_changes(setup):
    setup, asset, data = setup
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=data))) as client:
        setup._download(asset, client)
    target = asset_path(setup.root, asset)
    assert verified_asset(setup.root, asset)
    assert target.read_bytes() == data
    target.write_bytes(b"tampered")
    assert not verified_asset(setup.root, asset)


def test_bad_hash_cannot_replace_an_existing_model_or_publish_a_verified_marker(setup):
    setup, asset, data = setup
    target = asset_path(setup.root, asset)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing file")
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * len(data)))) as client,
        pytest.raises(ValueError, match="SHA-256"),
    ):
        setup._download(asset, client)
    assert target.read_bytes() == b"existing file"
    assert not verified_asset(setup.root, asset)
    assert not target.with_name(target.name + ".partial").exists()


@pytest.mark.parametrize("honors_range", [True, False])
def test_interrupted_download_resumes_and_handles_servers_that_ignore_range(setup, honors_range):
    setup, asset, data = setup
    target = asset_path(setup.root, asset)
    target.parent.mkdir(parents=True)
    target.with_name(target.name + ".partial").write_bytes(data[:5])
    def respond(request):
        assert request.headers["range"] == "bytes=5-"
        return httpx.Response(206, content=data[5:], headers={"Content-Range": f"bytes 5-{len(data)-1}/{len(data)}"}) if honors_range else httpx.Response(200, content=data)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        setup._download(asset, client)
    assert target.read_bytes() == data
    assert verified_asset(setup.root, asset)


def test_cancel_before_download_leaves_existing_files_untouched(setup):
    setup, asset, _ = setup
    setup.cancel()
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("must not reach network"))) as client,
        pytest.raises(SetupCancelled),
    ):
        setup._download(asset, client)
    assert not setup.root.exists()


@pytest.mark.parametrize("values", [{"filename": "../../outside"}, {"folder": "../outside"}])
def test_model_paths_cannot_escape_setup_folder(setup, values):
    setup, asset, _ = setup
    with pytest.raises(ValueError):
        asset_path(setup.root, asset | values)


def test_low_disk_blocks_setup_before_any_download(setup, monkeypatch):
    setup, _, _ = setup
    monkeypatch.setattr(setup, "environment", lambda: {"ready": True})
    monkeypatch.setattr(video_setup.shutil, "disk_usage", lambda _: SimpleNamespace(total=100 * 1024**3, free=1))
    monkeypatch.setattr(setup, "_download", lambda *a: pytest.fail("disk check must come first"))
    setup._run()
    assert setup.job["status"] == "failed"
    assert "reserve" in setup.job["error"]


def test_no_gpu_probe_in_the_core_and_an_unavailable_environment_is_not_ready(setup):
    setup, _, _ = setup
    state = setup.status()
    assert not state["environment"]["ready"]
    assert not state["weights_ready"]
    # Both keyframe models run on native ComfyUI nodes: there is no custom-node source to pin.
    assert state["source_ready"]


@pytest.mark.parametrize(("module", "roles", "total"), [
    (minimax_h3, {"high", "encoder", "vae", "audio_vae", "turbo"}, 44426778471),
    (ltx25, {"high", "encoder", "vae", "audio_vae", "upscaler"}, 39709874648),
])
def test_keyframe_manifests_have_every_role_and_immutable_verified_sources(module, roles, total):
    # No fixture here: inspect the actual shipped manifest without contacting its sources.
    assets = module.assets()
    assert {a["role"] for a in assets} == roles
    assert sum(a["size"] for a in assets) == total
    assert all(len(a["sha256"]) == 64 and len(a["revision"]) == 40 and a["revision"] in a["url"] for a in assets)
    assert "ref2va" not in json.dumps(assets) and "MSR" not in json.dumps(assets)
