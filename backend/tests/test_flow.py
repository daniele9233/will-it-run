"""Flow keeps authentication in the browser and verifies imported local sequences."""
import json
import time
import zipfile
from pathlib import Path

import pytest

from agentfit.imagegen import flow


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(flow, "tools", lambda: {"ffmpeg": "fake-ffmpeg", "ffprobe": "fake-ffprobe"})
    monkeypatch.setattr(flow, "_probe", lambda *args: {"width": 1280, "height": 720, "seconds": 8.0})
    return flow.FlowStudio(tmp_path / "flow")


def prepared(studio, tmp_path):
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(b"first reference")
    last.write_bytes(b"last reference")
    return studio.prepare(flow.FlowPrepare(name="../A shot", first=str(first), last=str(last), prompt=" exact\nprompt "))


def wait(studio, session_id):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        record = studio.get(session_id)
        if record["status"] != "processing":
            return record
        time.sleep(.01)
    pytest.fail("extraction never finished")


def test_preparation_keeps_originals_and_exact_prompt(studio, tmp_path):
    record = prepared(studio, tmp_path)
    assert record["status"] == "prepared"
    assert Path(record["first"]).read_bytes() == (tmp_path / "first.png").read_bytes()
    assert (Path(record["folder"]) / "prompt.txt").read_text(encoding="utf-8") == " exact\nprompt "
    assert Path(record["folder"]).parent == studio.directory
    assert studio.status()["sessions"] == [record]
    with pytest.raises(KeyError):
        studio.get("../outside")
    with pytest.raises(KeyError):
        studio.sequence(record["id"])


@pytest.mark.parametrize("path", ["relative.png", "C:/missing.png", "photo.exe"])
def test_invalid_reference_paths_never_publish_a_session(studio, tmp_path, path):
    with pytest.raises(ValueError):
        studio.prepare(flow.FlowPrepare(name="shot", first=path, last=str(tmp_path / "missing.png"), prompt="move"))
    assert not studio.status()["sessions"]


def test_import_publishes_verified_manifest_and_150_frames(studio, tmp_path, monkeypatch):
    record = prepared(studio, tmp_path)
    video = tmp_path / "downloaded.mp4"
    video.write_bytes(b"local-video")

    def extract(args):
        assert args[0] == "fake-ffmpeg" and "-nostdin" in args
        pattern = args[-1]
        for i in range(1, 151):
            Path(pattern.replace("%03d", f"{i:03d}")).write_bytes(b"decoded-frame")
        return ""

    monkeypatch.setattr(flow, "_run", extract)
    studio.import_video(record["id"], str(video))
    result = wait(studio, record["id"])
    assert result["status"] == "ready" and result["frames"] == 150
    with zipfile.ZipFile(studio.sequence(record["id"])) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(archive.namelist()) == 151
        assert manifest["frames"][0] == "frame-001.webp" and manifest["frames"][-1] == "frame-150.webp"
        assert manifest["provider"] == "Google Flow (user-imported)"
        assert manifest["source_sha256"] == result["source_sha256"]
    assert video.read_bytes() == b"local-video"


def test_incomplete_extraction_is_failed_and_has_no_download(studio, tmp_path, monkeypatch):
    record = prepared(studio, tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"clip")
    monkeypatch.setattr(flow, "_run", lambda *args: "")
    studio.import_video(record["id"], str(video))
    result = wait(studio, record["id"])
    assert result["status"] == "failed" and result["error"]
    with pytest.raises(KeyError):
        studio.sequence(record["id"])
    assert not list(Path(record["folder"]).glob("frames-*"))


def test_restart_reports_interrupted_processing(studio, tmp_path):
    record = prepared(studio, tmp_path)
    record["status"] = "processing"
    studio._write(Path(record["folder"]), record)
    assert flow.FlowStudio(studio.directory).get(record["id"])["status"] == "failed"
