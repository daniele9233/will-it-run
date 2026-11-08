"""Finishing contracts without external programs, network, GPU or owner data."""
import json
from types import SimpleNamespace

import pytest

from agentfit.imagegen import video_catalog as catalog
from agentfit.imagegen import video_finish as finish
from agentfit.imagegen.video import VideoRequest, workflow


@pytest.mark.parametrize("selected", [p for p in catalog.PROFILES if p.input_mode != "reference"], ids=lambda p: p.key)
@pytest.mark.parametrize("seconds", [6, 15])
def test_duration_generates_the_whole_interval_at_each_models_native_rate(selected, seconds):
    r = VideoRequest(model_key=selected.key, image="start.png", end_image="end.png", prompt="move",
                     duration_seconds=seconds, frames=33, fps=30,
                     width=1280 if selected.required_end else 832, height=720 if selected.required_end else 480)
    assert r.frames == seconds * selected.fps + 1
    assert r.fps == selected.fps
    assert (r.frames - 1) % 4 == 0


@pytest.mark.parametrize("seconds", [5, 16])
def test_hero_duration_rejects_outside_supported_range(seconds):
    with pytest.raises(ValueError):
        VideoRequest(image="a.png", prompt="move", duration_seconds=seconds)


def simulate(monkeypatch, *, frames=180, repeat=False, width=1920):
    commands = []

    def run(args, cancelled, directory):
        commands.append(args)
        if "-show_entries" in args:
            return json.dumps({"streams": [{"width": width, "height": 1080, "avg_frame_rate": "30/1",
                                            "nb_read_frames": str(frames), "duration": "6.000000", "codec_name": "h264"}]})
        if "framemd5" in args:
            return "# comment\n" + "\n".join(f"0, 0, 0, 1, 123, {0 if repeat else i}" for i in range(frames))
        return ""

    monkeypatch.setattr(finish, "tools", lambda: {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
    monkeypatch.setattr(finish, "_run", run)
    return commands


def test_finishing_interpolates_without_export_duplication_and_checks_decoded_frames(tmp_path, monkeypatch):
    commands = simulate(monkeypatch)
    stages = []
    output = finish.finish(tmp_path / "native.mp4", tmp_path / "final.mp4", seconds=6,
                           source_frames=97, cancelled=lambda: False, stage=stages.append)
    assert stages == ["finishing", "verifying"]
    assert output["frames"] == output["distinct_frames"] == 180
    assert output["adjacent_repeats"] == 0
    filters = commands[0][commands[0].index("-vf") + 1]
    assert "mi_mode=mci" in filters and "scd=none" in filters
    assert "setpts=N*179/(30*96*TB)" in filters
    assert "scale=1920:1080" in filters and "pad=1920:1080" in filters
    assert "force_original_aspect_ratio=decrease" in filters and "crop=" not in filters
    assert "-r" not in commands[0] and "-t" not in commands[0]
    assert commands[0][commands[0].index("-frames:v") + 1] == "180"


@pytest.mark.parametrize("values", [{"frames": 179}, {"width": 1280}, {"repeat": True}])
def test_incomplete_wrong_size_or_duplicated_output_is_rejected(tmp_path, monkeypatch, values):
    simulate(monkeypatch, **values)
    with pytest.raises(ValueError):
        finish.finish(tmp_path / "native.mp4", tmp_path / "final.mp4", seconds=6,
                      source_frames=97, cancelled=lambda: False, stage=lambda _: None)


def test_cancellation_kills_only_the_finishing_process(tmp_path, monkeypatch):
    calls = []
    process = SimpleNamespace(poll=lambda: None, kill=lambda: calls.append("kill"), wait=lambda: calls.append("wait"))
    monkeypatch.setattr(finish.subprocess, "Popen", lambda *a, **kw: process)
    with pytest.raises(finish.VideoCancelled):
        finish._run(["fake"], lambda: True, tmp_path)
    assert calls == ["kill", "wait"]


def test_fullhd_flf_cannot_silently_use_a_tiny_latent():
    with pytest.raises(ValueError, match="720p"):
        VideoRequest(model_key="wan21-flf-q4", image="start.png", end_image="end.png",
                     prompt="move", duration_seconds=8, width=512, height=288)
    request = VideoRequest(model_key="wan21-flf-q4", image="start.png", end_image="end.png",
                           prompt="move", duration_seconds=8)
    assert (request.width, request.height) == (1280, 720)
    portrait = request.model_dump() | {"width": 720, "height": 1280}
    assert VideoRequest(**portrait).height == 1280


def test_slow_motion_uses_the_same_graph_as_the_validated_native_sequence():
    values = dict(model_key="wan21-flf-q4", image="start.png", end_image="end.png",
                  prompt="parts separate", width=1280, height=720, seed=42)
    native = VideoRequest(**values, frames=33)
    slow = VideoRequest(**values, duration_seconds=8, motion_frames=33)
    files = {role: name for role, name, _ in catalog.profile(native.model_key).requirements()}
    assert slow.frames == native.frames == 33
    assert slow.fps == native.fps == 16
    assert workflow(native, files, "comparison") == workflow(slow, files, "comparison")
    with pytest.raises(ValueError):
        VideoRequest(**values, duration_seconds=8, motion_frames=34)


@pytest.mark.parametrize("missing_audio", [False, True])
def test_reference_audio_is_retimed_and_verified_instead_of_discarded(tmp_path, monkeypatch, missing_audio):
    commands = []
    def run(args, cancelled, directory):
        commands.append(args)
        if "a:0" in args:
            return json.dumps({"streams": [] if missing_audio else [{"codec_name": "aac", "duration": "5.000000"}]})
        if "-show_entries" in args:
            return json.dumps({"streams": [{"width": 1920, "height": 1080, "avg_frame_rate": "30/1",
                                            "nb_read_frames": "150", "duration": "5.000000", "codec_name": "h264"}]})
        if "framemd5" in args:
            return "\n".join(f"0,0,0,1,123,{i}" for i in range(150))
        return ""
    monkeypatch.setattr(finish, "tools", lambda: {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
    monkeypatch.setattr(finish, "_run", run)
    if missing_audio:
        with pytest.raises(ValueError, match="audio"):
            finish.finish(tmp_path / "native.mp4", tmp_path / "final.mp4", seconds=5, source_frames=124,
                          audio_fps=24, cancelled=lambda: False, stage=lambda _: None)
    else:
        result = finish.finish(tmp_path / "native.mp4", tmp_path / "final.mp4", seconds=5, source_frames=124,
                               audio_fps=24, cancelled=lambda: False, stage=lambda _: None)
        assert result["audio"] and result["frames"] == 150
    command = commands[0]
    assert "0:a:0" in command and "-an" not in command
    assert "atempo=1.0333333333" in command[command.index("-af") + 1]
