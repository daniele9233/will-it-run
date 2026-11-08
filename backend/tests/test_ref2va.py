"""Real graph contracts and verified/resumable setup using HTTP fakes, never GPU or owner data."""
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from agentfit.imagegen import ref2va, video_setup
from agentfit.imagegen.video import VideoRequest, workflow
from agentfit.imagegen.video_catalog import profile
from agentfit.imagegen.video_progress import VideoProgress
from agentfit.imagegen.video_setup import Ref2VASetup, SetupCancelled, asset_path, verified_asset


def request(**values):
    return VideoRequest(**({"model_key": ref2va.KEY, "image": "portrait.png", "prompt": "A clothed adult, slight movement"} | values))


@pytest.mark.parametrize("seconds,frames", [(5, 124), (6, 158), (15, 362)])
def test_reference_video_has_its_own_real_frame_grid_and_recipe(seconds, frames):
    result = request(duration_seconds=seconds)
    assert (result.frames, result.fps, result.steps, result.cfg) == (frames, 24, 4, 1)
    assert (result.width, result.height) == (1344, 768)
    assert result.frames % 17 == 5


@pytest.mark.parametrize("values", [{"end_image": "end.png"}, {"negative": "bad"}, {"motion_frames": 33},
    {"steps": 8}, {"cfg": 3}, {"fps": 16}, {"frames": 129}, {"width": 833}, {"vsa_sparsity": 1.0}])
def test_unsupported_reference_controls_are_rejected_instead_of_silently_ignored(values):
    with pytest.raises(ValueError):
        request(**values)


def test_reference_graph_transplants_gate_after_turbo_and_preserves_both_av_outputs():
    r = request(seed=42, vsa_sparsity=.7)
    files = {role: name for role, name, _ in profile(ref2va.KEY).requirements()}
    graph = workflow(r, files, "test")
    assert graph["1"]["class_type"] == "UNETLoader"
    assert graph["30"]["inputs"]["model"] == ["1", 0]
    assert graph["31"]["inputs"] == {"model": ["30", 0], "gate_file": "fasth3_vsa_gate.safetensors", "sparsity": .7}
    assert graph["32"]["inputs"]["model"] == ["31", 0]
    assert graph["10"]["inputs"]["steps"] == 4
    assert graph["6"]["inputs"]["ref_images.ref_image_0"] == ["7", 0]
    assert graph["11"]["inputs"]["latent_image"] == ["6", 1]
    assert graph["14"]["inputs"]["audio"] == ["24", 0]
    assert graph["13"]["inputs"]["samples"] == graph["24"]["inputs"]["samples"] == ["11", 0]
    assert graph["15"]["inputs"]["format"] == "mp4"
    assert not any("end_image" in n["inputs"] or n["class_type"] == "UnetLoaderGGUF" for n in graph.values())
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


def test_reference_sampling_and_audio_decoding_are_visible_in_video_progress():
    graph = workflow(request(), {role: name for role, name, _ in profile(ref2va.KEY).requirements()}, "test")
    progress = VideoProgress(graph)
    progress.prompt_id = "ours"
    def event(kind, **data):
        return progress.event({"type": kind, "data": {"prompt_id": "ours", **data}})
    assert event("progress", node="11", value=2, max=4)["percent"] == 45
    assert event("executing", node="24")["stage"] == "decoding"
    assert event("executing", node="15")["percent"] < 100


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data = b"verified model bytes"
    asset = {"filename": "model.safetensors", "folder": "diffusion_models", "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "revision": "a" * 40,
             "url": "https://huggingface.co/publisher/model/resolve/" + "a" * 40 + "/model.safetensors"}
    monkeypatch.setattr(ref2va, "assets", lambda: [asset])
    return Ref2VASetup(None, tmp_path), asset, data


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


def test_low_disk_blocks_setup_before_git_or_download(setup, monkeypatch):
    setup, _, _ = setup
    monkeypatch.setattr(setup, "environment", lambda: {"ready": True})
    setup.engine = SimpleNamespace(install=lambda: SimpleNamespace(root=setup.root))
    monkeypatch.setattr(video_setup.shutil, "disk_usage", lambda _: SimpleNamespace(total=100 * 1024**3, free=1))
    monkeypatch.setattr(video_setup, "source_checkout", lambda *a: pytest.fail("disk check must come first"))
    setup._run()
    assert setup.job["status"] == "failed"
    assert "reserve" in setup.job["error"]


def test_no_gpu_probe_in_the_core_and_an_unavailable_environment_is_not_ready(setup):
    setup, _, _ = setup
    state = setup.status()
    assert not state["environment"]["ready"]
    assert not state["weights_ready"] and not state["source_ready"]


def test_reference_manifest_has_all_six_roles_and_immutable_verified_sources():
    # No fixture here: inspect the actual shipped manifest without contacting its sources.
    assets = ref2va.assets()
    assert {a["role"] for a in assets} == {"high", "encoder", "vae", "audio_vae", "turbo", "gate"}
    assert sum(a["size"] for a in assets) == 48280301087
    assert all(len(a["sha256"]) == 64 and len(a["revision"]) == 40 and a["revision"] in a["url"] for a in assets)
    assert "MiniMax_H3_repackaged" not in json.dumps(assets)
