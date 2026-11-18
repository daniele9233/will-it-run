"""Finishing contracts without external programs, network, GPU or owner data."""
import json
from types import SimpleNamespace

import pytest

from agentfit.imagegen import video_catalog as catalog
from agentfit.imagegen import video_finish as finish
from agentfit.imagegen.video import VideoRequest


@pytest.mark.parametrize("selected", catalog.PROFILES, ids=lambda p: p.key)
@pytest.mark.parametrize("seconds", [6, 15])
def test_duration_generates_the_whole_interval_at_each_models_native_rate(selected, seconds):
    r = VideoRequest(model_key=selected.key, images=["start.png", "end.png"], prompt="move", duration_seconds=seconds, frames=33)
    assert r.frames == selected.frames_for(seconds) >= seconds * selected.fps
    assert r.fps == selected.fps == 24
    assert (r.frames - selected.frame_offset) % selected.frame_stride == 0


@pytest.mark.parametrize("seconds", [4, 16])
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


def test_current_profiles_keep_their_own_native_runtime_and_grid():
    h3 = VideoRequest(model_key="minimax-h3-fl2va", image="start.png", prompt="move", duration_seconds=8)
    assert (h3.frames, h3.fps, h3.steps) == (192, 24, 20)
    assert (h3.width, h3.height) == (1344, 768)
    ltx = VideoRequest(model_key="ltx25-keyframes", image="start.png", prompt="move", duration_seconds=8)
    assert (ltx.frames, ltx.fps) == (193, 24)
    assert (ltx.width, ltx.height) == (1280, 704)
    with pytest.raises(ValueError, match="keyframe pictures only"):
        VideoRequest(model_key="ltx25-keyframes", image="start.png", end_image="end.png", prompt="move")
    with pytest.raises(ValueError):
        VideoRequest(model_key="minimax-h3-fl2va", image="start.png", prompt="move", motion_frames=33)


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
