"""Published-hash verification and disk/path safety use HTTP fakes only."""
import hashlib
import json
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from agentfit.media_runtime import checked_snapshot, safe_member, snapshot_present


@pytest.fixture(autouse=True)
def available_disk_space(monkeypatch):
    """Downloader tests must not depend on how much space the owner's real drive has."""
    monkeypatch.setattr("agentfit.media_runtime.shutil.disk_usage",
                        lambda _: SimpleNamespace(total=100 * 1024**3, free=80 * 1024**3))


def test_interrupted_seed_can_resume_without_recreating_environment(tmp_path, monkeypatch):
    import os

    import agentfit.media_runtime as runtime
    python = tmp_path / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"existing interpreter")
    commands = []
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    monkeypatch.setattr(runtime, "run_logged", lambda command, *a, **k: commands.append(command))
    assert runtime.isolated_python(tmp_path, tmp_path / "setup.log") == python
    assert commands == [["uv", "pip", "install", "--python", str(python), "pip"]]
    assert python.read_bytes() == b"existing interpreter"


def test_embedded_audio_omits_only_gradio_and_keeps_upstream_constraints(tmp_path, monkeypatch):
    import agentfit.media_runtime as runtime

    source = tmp_path / "source"
    constraints = source / "constraints" / "recommended.txt"
    constraints.parent.mkdir(parents=True)
    constraints.write_text("gradio==6.17.0\ntorch==2.8.0\n", encoding="utf-8")
    requirements = ["torch>=2.8.0", "gradio>=6.17,<7", "gradio-client>=1", 'optional; extra == "full"']
    commands = []
    monkeypatch.setattr(runtime, "run_logged", lambda command, *a, **k: commands.append(command))
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(requirements).encode(), b""))
    python = tmp_path / "python"
    runtime.install_local_package(python, source, tmp_path, tmp_path / "setup.log", headless_distribution="dots.tts")
    assert commands[0] == [str(python), "-m", "pip", "install", "--no-deps", str(source), "-c", str(constraints)]
    assert commands[1] == [str(python), "-m", "pip", "install", *[r for r in requirements if not r.startswith("gradio>")], "-c", str(constraints)]
    profile = json.loads((tmp_path / "dependency-profile.json").read_text(encoding="utf-8"))
    assert profile["excluded_demo_dependencies"] == ["gradio>=6.17,<7"]
    assert constraints.read_text(encoding="utf-8") == "gradio==6.17.0\ntorch==2.8.0\n"


def test_embedded_audio_stops_if_upstream_dependency_profile_changes(tmp_path, monkeypatch):
    import agentfit.media_runtime as runtime

    commands = []
    monkeypatch.setattr(runtime, "run_logged", lambda command, *a, **k: commands.append(command))
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b'["torch>=2.8.0"]', b""))
    with pytest.raises(ValueError, match="no longer matches"):
        runtime.install_local_package(tmp_path / "python", tmp_path / "source", tmp_path, tmp_path / "setup.log", headless_distribution="dots.tts")
    assert len(commands) == 1
    assert not (tmp_path / "dependency-profile.json").exists()


def test_normal_local_package_keeps_standard_dependency_resolution(tmp_path, monkeypatch):
    import agentfit.media_runtime as runtime

    commands = []
    monkeypatch.setattr(runtime, "run_logged", lambda command, *a, **k: commands.append(command))
    python = tmp_path / "python"
    source = tmp_path / "source"
    runtime.install_local_package(python, source, tmp_path, tmp_path / "setup.log")
    assert commands == [[str(python), "-m", "pip", "install", str(source)]]


@pytest.mark.parametrize("name", ["../secret", "/etc/passwd", "C:/private", "a\\b", "", "folder/../../x"])
def test_unsafe_asset_paths(name):
    with pytest.raises(ValueError):
        safe_member(name)


def transport(payload, *, corrupt=False, unknown=False):
    def serve(request):
        if request.url.path.startswith("/api/"):
            item = {"rfilename": "model.safetensors", "size": len(payload), "lfs": {"sha256": hashlib.sha256(payload).hexdigest()}}
            if unknown:
                item.pop("size")
            return httpx.Response(200, json={"sha": "a" * 40, "siblings": [item]})
        assert "/resolve/" + "a" * 40 + "/" in request.url.path
        return httpx.Response(200, content=b"broken" if corrupt else payload)
    return httpx.MockTransport(serve)


def test_verified_snapshot_and_removed_weights(tmp_path):
    with httpx.Client(transport=transport(b"weights")) as client:
        result = checked_snapshot("publisher/model", tmp_path, client=client)
    assert result["revision"] == "a" * 40
    assert snapshot_present(tmp_path)
    (tmp_path / "model.safetensors").unlink()
    assert not snapshot_present(tmp_path)


def test_bad_hash_is_not_accepted(tmp_path):
    with httpx.Client(transport=transport(b"weight", corrupt=True)) as client, pytest.raises(ValueError, match="checksum"):
        checked_snapshot("publisher/model", tmp_path, client=client)
    assert not (tmp_path / "verified.json").exists()
    assert not (tmp_path / "model.safetensors.partial").exists()


def test_unknown_size_refused_before_download(tmp_path):
    with httpx.Client(transport=transport(b"weight", unknown=True)) as client, pytest.raises(ValueError, match="size/checksum"):
        checked_snapshot("publisher/model", tmp_path, client=client)


def test_manifest_path_cannot_escape(tmp_path):
    (tmp_path / "verified.json").write_text(json.dumps({"files": [{"path": "../outside", "bytes": 0}]}))
    assert not snapshot_present(tmp_path)


def test_disk_reserve_still_prevents_download(tmp_path, monkeypatch):
    monkeypatch.setattr("agentfit.media_runtime.shutil.disk_usage", lambda _: SimpleNamespace(total=100 * 1024**3, free=1))
    with httpx.Client(transport=transport(b"weights")) as client, pytest.raises(ValueError, match="disk space"):
        checked_snapshot("publisher/model", tmp_path, client=client)
    assert not (tmp_path / "model.safetensors").exists()


def test_verified_files_can_be_reused_without_reserving_their_space_twice(tmp_path, monkeypatch):
    with httpx.Client(transport=transport(b"weights")) as client:
        checked_snapshot("publisher/model", tmp_path, client=client)
        monkeypatch.setattr("agentfit.media_runtime.shutil.disk_usage", lambda _: SimpleNamespace(total=100 * 1024**3, free=1))
        checked_snapshot("publisher/model", tmp_path, client=client)
    assert snapshot_present(tmp_path)
