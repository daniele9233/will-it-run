"""Disk safety and the download manager (direct and LM Studio backends)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentfit import storage
from agentfit.config import Settings, StorageSettings
from agentfit.downloads import (
    DownloadError,
    DownloadManager,
    DownloadRequest,
    UnsafeDownload,
    normalize_folder,
)
from agentfit.events import EventBus
from agentfit.hardware.models import DriveInfo
from agentfit.lmstudio.models import DownloadJob, InstalledModel, LMStudioInstall

GIB = 1024**3
PAYLOAD = bytes(range(256)) * 4096  # ~1 MB of deterministic data
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def drive(letter: str, total_gb: float, free_gb: float) -> DriveInfo:
    return DriveInfo(letter=letter, filesystem="NTFS", total_bytes=int(total_gb * GIB), free_bytes=int(free_gb * GIB))


# -- disk safety --------------------------------------------------------------


def test_safe_download_plan_matches_documented_shape():
    plan = storage.plan_download(int(16.8 * GIB), "D:\\Models", StorageSettings(), [drive("D:", 931, 412)], free_gb=412)
    assert plan.safe and plan.drive == "D:"
    assert plan.download_gb == 16.8 and plan.temp_gb == pytest.approx(1.68)
    assert plan.after_install_free_gb == pytest.approx(395.2)
    assert plan.verdict.startswith("SAFE TO INSTALL")


def test_unsafe_download_is_refused_with_options():
    # Required safely: 31 GB (+ temp). Available safely: 22 GB.
    settings = StorageSettings(reserve_percent=0, min_free_gb=10)
    drives = [drive("C:", 500, 32), drive("E:", 2000, 900)]
    plan = storage.plan_download(31 * GIB, "C:\\Models", settings, drives, free_gb=32, smaller_options=["Q4_K_M (18.0 GB)"])
    assert not plan.safe and plan.safe_available_gb == 22
    assert plan.verdict.startswith("NOT SAFE")
    joined = " | ".join(plan.options)
    assert "Use a smaller quantization: Q4_K_M" in joined
    assert "Choose a smaller model" in joined and "Delete unused models" in joined
    assert "Save to drive E: instead" in joined


def test_reserve_is_bounded():
    settings = StorageSettings()
    assert storage.reserve_gb(100, settings) == 15.0  # 15 percent
    assert storage.reserve_gb(20, settings) == 10.0  # floor
    assert storage.reserve_gb(4000, settings) == storage.MAX_RESERVE_GB  # ceiling


def test_nearly_full_system_drive_blocks_everything():
    plan = storage.plan_download(4 * GIB, "C:\\Models", StorageSettings(), [drive("C:", 292, 11)], free_gb=11)
    assert not plan.safe and plan.safe_available_gb == 0


def test_storage_recommendation_prefers_lmstudio_folder_with_room():
    locations = storage.storage_locations([drive("C:", 292, 11), drive("D:", 639, 116)], StorageSettings(), "D:\\Models")
    recommended = next(loc for loc in locations if loc.recommended)
    assert recommended.drive == "D:" and recommended.path == "D:\\Models" and recommended.is_lmstudio_dir


def test_storage_recommendation_moves_when_lmstudio_drive_is_full():
    locations = storage.storage_locations([drive("C:", 292, 11), drive("D:", 639, 400)], StorageSettings(), "C:\\Users\\me\\.lmstudio\\models")
    recommended = next(loc for loc in locations if loc.recommended)
    assert recommended.drive == "D:" and "nearly full" in recommended.reason


# -- download manager ---------------------------------------------------------


class StubClient:
    def __init__(self, statuses: list[DownloadJob] | None = None, error: Exception | None = None) -> None:
        self.statuses = statuses or []
        self.error = error
        self.requests: list[tuple[str, str | None]] = []

    def download(self, model: str, quantization: str | None = None) -> DownloadJob:
        self.requests.append((model, quantization))
        if self.error:
            raise self.error
        return DownloadJob(job_id="job_1", status="downloading", total_size_bytes=len(PAYLOAD))

    def download_status(self, job_id: str) -> DownloadJob:
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]


class StubRuntime:
    def __init__(self, models_dir: str, *, available: bool = False, client: Any = None, installed: list[InstalledModel] | None = None) -> None:
        self.models_dir, self.available, self.client, self.installed = models_dir, available, client, installed or []

    def status(self, deep: bool = False) -> LMStudioInstall:
        return LMStudioInstall(installed=True, models_dir=self.models_dir)

    def is_available(self) -> bool:
        return self.available

    def list_installed(self) -> list[InstalledModel]:
        return self.installed


def hub(behaviour: dict[str, Any] | None = None) -> httpx.Client:
    state = behaviour if behaviour is not None else {}
    state.setdefault("requests", [])

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request.headers.get("range"))
        if state.get("status"):
            return httpx.Response(state["status"])
        data = state.get("payload", PAYLOAD)
        header = request.headers.get("range")
        if header and not state.get("ignore_range"):
            start = int(header.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=data[start:])
        return httpx.Response(200, content=data)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def make_manager(db, tmp_path: Path, *, free_gb: float = 400, runtime: StubRuntime | None = None, http: httpx.Client | None = None, monkeypatch=None) -> DownloadManager:
    settings = Settings()
    letter = storage.drive_of(str(tmp_path))
    if monkeypatch is not None:
        monkeypatch.setattr(storage, "free_gb_at", lambda path: free_gb)
    return DownloadManager(
        db, settings, runtime or StubRuntime(str(tmp_path)),  # type: ignore[arg-type]
        lambda: [drive(letter, 931, free_gb)], bus=EventBus(), http=http or hub(), autostart=False,
    )


def request(**kwargs: Any) -> DownloadRequest:
    base = {"repo": "pub/Model-GGUF", "filename": "Model-Q4_K_M.gguf", "quant": "Q4_K_M", "size_bytes": len(PAYLOAD), "sha256": SHA, "revision": "rev1", "backend": "direct"}
    return DownloadRequest(**(base | kwargs))


def test_direct_download_writes_into_lmstudio_layout_and_verifies_hash(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    record = manager.enqueue(request())
    assert record.status == "queued" and record.backend == "direct"
    done = manager.run(record.id)
    target = tmp_path / "pub" / "Model-GGUF" / "Model-Q4_K_M.gguf"
    assert done.status == "completed" and done.progress == 1.0
    assert target.read_bytes() == PAYLOAD
    assert not target.with_name(target.name + ".part").exists()


def test_disk_full_never_starts_the_download(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, free_gb=27, monkeypatch=monkeypatch)
    with pytest.raises(UnsafeDownload) as excinfo:
        manager.enqueue(request(size_bytes=42 * GIB), smaller_options=["Q3_K_M (20.0 GB)"])
    plan = excinfo.value.plan
    assert not plan.safe and plan.required_gb > plan.safe_available_gb
    assert any("smaller quantization" in o for o in plan.options)
    assert manager.list() == []  # nothing was queued
    assert not (tmp_path / "pub").exists()  # nothing was written


def test_unknown_size_cannot_be_checked_so_it_is_refused(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    with pytest.raises(DownloadError, match="size is unknown"):
        manager.enqueue(request(size_bytes=None))


def test_queued_downloads_count_against_free_space(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, free_gb=60, monkeypatch=monkeypatch)
    manager.enqueue(request(filename="first.gguf", size_bytes=20 * GIB))
    with pytest.raises(UnsafeDownload):
        manager.enqueue(request(filename="second.gguf", size_bytes=20 * GIB))


def test_hash_mismatch_fails_and_deletes_the_file(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    record = manager.enqueue(request(sha256="00" * 32))
    done = manager.run(record.id)
    assert done.status == "failed" and "SHA-256 mismatch" in (done.error or "")
    assert not list((tmp_path / "pub" / "Model-GGUF").glob("*"))


def test_http_error_fails_with_information_and_can_be_retried(db, tmp_path, monkeypatch):
    state: dict[str, Any] = {"status": 503}
    manager = make_manager(db, tmp_path, http=hub(state), monkeypatch=monkeypatch)
    record = manager.enqueue(request())
    failed = manager.run(record.id)
    assert failed.status == "failed" and "HTTP 503" in (failed.error or "")
    state["status"] = None
    retried = manager.retry(record.id)
    assert retried.status == "queued" and retried.error is None
    assert manager.run(record.id).status == "completed"


def test_resume_continues_from_the_partial_file(db, tmp_path, monkeypatch):
    state: dict[str, Any] = {}
    manager = make_manager(db, tmp_path, http=hub(state), monkeypatch=monkeypatch)
    record = manager.enqueue(request())
    part = tmp_path / "pub" / "Model-GGUF" / "Model-Q4_K_M.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:300_000])
    done = manager.run(record.id)
    assert state["requests"] == ["bytes=300000-"]
    assert done.status == "completed"
    assert (tmp_path / "pub" / "Model-GGUF" / "Model-Q4_K_M.gguf").read_bytes() == PAYLOAD


def test_server_ignoring_range_restarts_cleanly(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, http=hub({"ignore_range": True}), monkeypatch=monkeypatch)
    record = manager.enqueue(request())
    part = tmp_path / "pub" / "Model-GGUF" / "Model-Q4_K_M.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"stale-bytes")
    assert manager.run(record.id).status == "completed"
    assert (tmp_path / "pub" / "Model-GGUF" / "Model-Q4_K_M.gguf").read_bytes() == PAYLOAD


def test_truncated_download_is_detected(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, http=hub({"payload": PAYLOAD[:1000]}), monkeypatch=monkeypatch)
    record = manager.enqueue(request(sha256=None))
    done = manager.run(record.id)
    assert done.status == "failed" and "Incomplete download" in (done.error or "")


def test_cancel_and_pause_of_queued_direct_download(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    first = manager.enqueue(request(filename="a.gguf"))
    second = manager.enqueue(request(filename="b.gguf"))
    assert manager.pause(first.id).status == "paused"
    assert manager.resume(first.id).status == "queued"
    assert manager.cancel(second.id).status == "cancelled"
    manager.remove(second.id)
    assert [d.id for d in manager.list()] == [first.id]


def test_cancel_during_transfer_removes_the_partial_file(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    record = manager.enqueue(request())
    manager._stop[record.id] = "cancelled"
    done = manager.run(record.id)
    assert done.status == "cancelled"
    assert not list((tmp_path / "pub" / "Model-GGUF").glob("*.part"))


def test_interrupted_downloads_become_resumable_after_restart(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    record = manager.enqueue(request())
    db.execute("UPDATE download SET status = 'downloading' WHERE id = ?", (record.id,))
    restarted = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    recovered = restarted.get(record.id)
    assert recovered.status == "paused" and "Interrupted" in (recovered.error or "")


def test_lmstudio_backend_polls_job_until_complete(db, tmp_path, monkeypatch):
    monkeypatch.setattr("agentfit.downloads.time.sleep", lambda s: None)
    client = StubClient([
        DownloadJob(job_id="job_1", status="downloading", total_size_bytes=1000, downloaded_bytes=400, bytes_per_second=31e6),
        DownloadJob(job_id="job_1", status="completed", total_size_bytes=1000, downloaded_bytes=1000),
    ])
    runtime = StubRuntime(str(tmp_path), available=True, client=client)
    manager = make_manager(db, tmp_path, runtime=runtime, monkeypatch=monkeypatch)
    record = manager.enqueue(request(backend="lmstudio"))
    assert record.backend == "lmstudio" and not record.can_pause
    done = manager.run(record.id)
    assert client.requests == [("https://huggingface.co/pub/Model-GGUF", "Q4_K_M")]
    assert done.status == "completed" and done.external_job_id == "job_1" and done.downloaded_bytes == 1000


def test_lmstudio_download_error_is_reported(db, tmp_path, monkeypatch):
    from agentfit.lmstudio.client import LMStudioError

    runtime = StubRuntime(str(tmp_path), available=True, client=StubClient(error=LMStudioError("Could not resolve model")))
    manager = make_manager(db, tmp_path, runtime=runtime, monkeypatch=monkeypatch)
    record = manager.enqueue(request(backend="lmstudio"))
    done = manager.run(record.id)
    assert done.status == "failed" and "Could not resolve model" in (done.error or "")


def test_lmstudio_job_failure_is_reported(db, tmp_path, monkeypatch):
    runtime = StubRuntime(str(tmp_path), available=True, client=StubClient([DownloadJob(job_id="job_1", status="failed")]))
    manager = make_manager(db, tmp_path, runtime=runtime, monkeypatch=monkeypatch)
    done = manager.run(manager.enqueue(request(backend="lmstudio")).id)
    assert done.status == "failed"


def test_lmstudio_backend_says_pause_is_unsupported(db, tmp_path, monkeypatch):
    runtime = StubRuntime(str(tmp_path), available=True, client=StubClient())
    manager = make_manager(db, tmp_path, runtime=runtime, monkeypatch=monkeypatch)
    record = manager.enqueue(request(backend="lmstudio"))
    with pytest.raises(DownloadError, match="cannot be paused"):
        manager.pause(record.id)


def test_auto_backend_falls_back_to_direct_when_lmstudio_is_offline(db, tmp_path, monkeypatch):
    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    assert manager.resolve_backend("auto") == "direct"
    with pytest.raises(DownloadError):
        manager.resolve_backend("ftp")


def test_auto_backend_prefers_the_stoppable_downloader_even_when_lmstudio_is_online(db, tmp_path, monkeypatch):
    runtime = StubRuntime(str(tmp_path), available=True, client=StubClient())
    manager = make_manager(db, tmp_path, runtime=runtime, monkeypatch=monkeypatch)
    record = manager.enqueue(request(backend="auto"))
    assert record.backend == "direct" and record.can_pause and record.can_cancel
    # An explicit choice is still honored, except that a custom folder needs Will It Run's downloader.
    assert manager.resolve_backend("lmstudio") == "lmstudio"
    assert manager.resolve_backend("lmstudio", custom_folder=True) == "direct"


def test_custom_folder_is_used_and_the_file_is_linked_into_lmstudio(db, tmp_path, monkeypatch):
    lm_dir = tmp_path / "lmstudio-models"
    lm_dir.mkdir()
    custom = tmp_path / "elsewhere" / "Models"
    body = b"x" * 5000
    served = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    runtime = StubRuntime(str(lm_dir))
    manager = make_manager(db, tmp_path, runtime=runtime, monkeypatch=monkeypatch, http=httpx.Client(transport=served))
    req = request(size_bytes=len(body), sha256=None, base_dir=str(custom))
    assert manager.plan(req).target_dir == str(custom / "pub" / "Model-GGUF")
    assert manager.outside_lmstudio(str(custom / "pub" / "Model-GGUF"))
    done = manager.run(manager.enqueue(req).id)
    saved = custom / "pub" / "Model-GGUF" / req.filename
    link = lm_dir / "pub" / "Model-GGUF" / req.filename
    assert done.status == "completed" and saved.read_bytes() == body
    # Same drive: a hard link, so LM Studio lists the model without a second copy.
    assert link.exists() and link.read_bytes() == body
    assert "linked into LM Studio" in (done.note or "")


def test_cross_drive_link_falls_back_to_a_junction_when_symlinks_are_refused(db, tmp_path, monkeypatch):
    lm_dir = tmp_path / "lmstudio-models"
    lm_dir.mkdir()
    custom = tmp_path / "other-drive" / "Models"
    body = b"z" * 4000
    served = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    manager = make_manager(db, tmp_path, runtime=StubRuntime(str(lm_dir)), monkeypatch=monkeypatch, http=httpx.Client(transport=served))

    def refuse(*args, **kwargs):
        raise OSError(1314, "A required privilege is not held by the client")

    # Pretend the two folders are on different drives and the user may not create symbolic links.
    monkeypatch.setattr("agentfit.downloads.os.link", refuse)
    monkeypatch.setattr("agentfit.downloads.os.symlink", refuse)
    done = manager.run(manager.enqueue(request(size_bytes=len(body), sha256=None, base_dir=str(custom))).id)
    listed = lm_dir / "pub" / "Model-GGUF" / "Model-Q4_K_M.gguf"
    assert done.status == "completed" and "linked into LM Studio" in (done.note or "")
    # LM Studio reads the file through the junction; the data lives only in the chosen folder.
    assert listed.read_bytes() == body
    assert (custom / "pub" / "Model-GGUF" / "Model-Q4_K_M.gguf").read_bytes() == body


def test_a_link_is_never_forced_over_existing_models(db, tmp_path, monkeypatch):
    lm_dir = tmp_path / "lmstudio-models"
    existing = lm_dir / "pub" / "Model-GGUF"
    existing.mkdir(parents=True)
    (existing / "Other-Q8_0.gguf").write_bytes(b"keep me")
    body = b"w" * 2000
    served = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    manager = make_manager(db, tmp_path, runtime=StubRuntime(str(lm_dir)), monkeypatch=monkeypatch, http=httpx.Client(transport=served))

    def refuse(*args, **kwargs):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr("agentfit.downloads.os.link", refuse)
    monkeypatch.setattr("agentfit.downloads.os.symlink", refuse)
    done = manager.run(manager.enqueue(request(size_bytes=len(body), sha256=None, base_dir=str(tmp_path / "elsewhere"))).id)
    assert done.status == "completed" and "did not allow linking" in (done.note or "")
    assert (existing / "Other-Q8_0.gguf").read_bytes() == b"keep me"
    assert not (existing / "Model-Q4_K_M.gguf").exists()


def test_default_folder_needs_no_link(db, tmp_path, monkeypatch):
    body = b"y" * 3000
    served = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch, http=httpx.Client(transport=served))
    done = manager.run(manager.enqueue(request(size_bytes=len(body), sha256=None)).id)
    assert done.status == "completed" and done.note is None


@pytest.mark.parametrize("folder", ["", "models", "relative\\path", "Q:\\nowhere"])
def test_custom_folder_must_be_a_real_absolute_path(folder):
    with pytest.raises(DownloadError):
        normalize_folder(folder)


def test_verify_installed_checks_lmstudio_listing(db, tmp_path, monkeypatch):
    installed = [InstalledModel(model_key="model", display_name="Model", repo="pub/Model-GGUF", filename="Model-Q4_K_M.gguf", quant="Q4_K_M")]
    manager = make_manager(db, tmp_path, runtime=StubRuntime(str(tmp_path), installed=installed), monkeypatch=monkeypatch)
    record = manager.enqueue(request())
    assert manager.verify_installed(record.id)
    other = manager.enqueue(request(repo="pub/Other-GGUF", filename="Other.gguf"))
    assert not manager.verify_installed(other.id)


def test_companion_files_are_queued_with_the_model(db, tmp_path, monkeypatch):
    from agentfit.catalog.models import ModelFile

    manager = make_manager(db, tmp_path, monkeypatch=monkeypatch)
    manager.enqueue(request(companions=[ModelFile(repo="pub/Model-GGUF", filename="mmproj-Model-f16.gguf", size_bytes=1000)]))
    assert {d.filename for d in manager.list()} == {"Model-Q4_K_M.gguf", "mmproj-Model-f16.gguf"}
