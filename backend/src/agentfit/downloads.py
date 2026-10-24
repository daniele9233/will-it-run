"""Download manager: disk-safety gate, queue, progress, retry, resume and cancel.

Two backends:

``direct``    Will It Run streams the exact file from Hugging Face into the models folder
              (``<models>/<publisher>/<repo>/<file>``), with HTTP range resume, pause,
              stop and SHA-256 verification. The folder is LM Studio's by default; the
              user may pick another one per download, and the finished file is then
              linked into LM Studio's folder so LM Studio still lists it.
``lmstudio``  LM Studio's official REST download endpoint. Progress comes from its job
              status endpoint. The documented API has no pause or cancel call, so those
              actions are reported as unsupported for this backend, and it always saves
              to LM Studio's own folder.

``auto`` picks ``direct`` whenever a models folder is known, so a download can always be
paused or stopped; ``lmstudio`` is the fallback. No download starts unless
``storage.plan_download`` says it is safe.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agentfit import storage
from agentfit.branding import USER_AGENT
from agentfit.catalog.models import ModelFile
from agentfit.config import Settings
from agentfit.db import Database, utcnow
from agentfit.events import EventBus
from agentfit.events import bus as default_bus
from agentfit.hardware.models import DriveInfo
from agentfit.i18n import tr
from agentfit.lmstudio.client import LMStudioError
from agentfit.lmstudio.runtime import LMStudioRuntime
from agentfit.logging_setup import get_logger, log_event

log = get_logger("downloads")

ACTIVE = {"queued", "downloading", "verifying"}
CHUNK = 1024 * 1024


class UnsafeDownload(Exception):
    """Raised instead of starting a download that would breach the disk reserve."""

    def __init__(self, plan: storage.DiskPlan) -> None:
        super().__init__(plan.verdict)
        self.plan = plan


class DownloadError(Exception):
    pass


class DownloadRequest(BaseModel):
    repo: str
    filename: str
    quant: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    revision: str | None = None
    backend: str | None = None
    target_dir: str | None = None
    # Models folder chosen for this download; the file goes to <base_dir>/<publisher>/<repo>.
    base_dir: str | None = None
    # Extra files that must accompany the model (for example a vision projector).
    companions: list[ModelFile] = Field(default_factory=list)


class DownloadRecord(BaseModel):
    id: str
    repo: str
    filename: str
    quant: str | None = None
    revision: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    downloaded_bytes: int = 0
    backend: str
    target_dir: str
    status: str
    error: str | None = None
    external_job_id: str | None = None
    created_at: str
    updated_at: str
    bytes_per_second: float | None = None
    note: str | None = None
    can_pause: bool = False
    can_cancel: bool = False

    @property
    def progress(self) -> float:
        if not self.size_bytes:
            return 0.0
        return min(1.0, self.downloaded_bytes / self.size_bytes)


class DownloadManager:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        runtime: LMStudioRuntime,
        drives_provider: Callable[[], list[DriveInfo]],
        *,
        bus: EventBus | None = None,
        http: httpx.Client | None = None,
        autostart: bool = True,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self._drives = drives_provider
        self.bus = bus or default_bus
        self._http = http or httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0)
        )
        self._lock = threading.RLock()
        self._stop: dict[str, str] = {}
        self._speeds: dict[str, float] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._autostart = autostart
        self._recover()

    # -- planning -----------------------------------------------------------
    def default_base_dir(self) -> str | None:
        """Where models go unless the user picks another folder for a download."""
        return self.settings.storage.custom_model_dir or self.runtime.status().models_dir or None

    def target_dir_for(self, repo: str, base_dir: str | None = None) -> str:
        base = normalize_folder(base_dir) if base_dir else self.default_base_dir()
        if not base:
            raise DownloadError(tr("LM Studio's models folder is unknown. Choose a folder for this download."))
        return str(Path(base) / Path(*repo.split("/")))

    def default_target_dir(self, repo: str) -> str:
        return self.target_dir_for(repo)

    def resolve_backend(self, requested: str | None = None, *, custom_folder: bool = False) -> str:
        choice = (requested or self.settings.downloads.backend or "auto").lower()
        if choice not in ("auto", "lmstudio", "direct"):
            raise DownloadError(tr("Unknown download method: {choice}", choice=choice))
        if custom_folder:
            # LM Studio's downloader only saves to its own folder.
            return "direct"
        if choice == "auto":
            if self.default_base_dir():
                return "direct"
            return "lmstudio" if self.runtime.is_available() else "direct"
        return choice

    def outside_lmstudio(self, target_dir: str) -> bool:
        """True when LM Studio will not find a file saved in ``target_dir`` by itself."""
        models_dir = self.runtime.status().models_dir
        return bool(models_dir) and not _is_inside(target_dir, models_dir)

    def plan(self, request: DownloadRequest, smaller_options: list[str] | None = None) -> storage.DiskPlan:
        if not request.size_bytes:
            raise DownloadError(
                tr("The file size is unknown, so disk safety cannot be verified. Refresh the model's file list first.")
            )
        total = request.size_bytes + sum(c.size_bytes or 0 for c in request.companions)
        target = request.target_dir or self.target_dir_for(request.repo, request.base_dir)
        # Space already promised to queued and running downloads on the same drive is not free.
        pending = sum(
            max(0, (r.size_bytes or 0) - r.downloaded_bytes)
            for r in self.list()
            if r.status in ACTIVE and storage.drive_of(r.target_dir) == storage.drive_of(target)
        )
        free = storage.free_gb_at(target)
        adjusted = max(0.0, free - pending / 1024**3) if free is not None else None
        return storage.plan_download(
            total, target, self.settings.storage, self._drives(), free_gb=adjusted, smaller_options=smaller_options
        )

    # -- lifecycle ----------------------------------------------------------
    def enqueue(self, request: DownloadRequest, smaller_options: list[str] | None = None) -> DownloadRecord:
        """Queue a download after the disk-safety check. Raises ``UnsafeDownload`` if unsafe."""
        plan = self.plan(request, smaller_options)
        if not plan.safe:
            log_event(log, "download refused: unsafe", repo=request.repo, file=request.filename, verdict=plan.verdict)
            raise UnsafeDownload(plan)
        backend = self.resolve_backend(request.backend, custom_folder=bool(request.base_dir or request.target_dir))
        record_id = uuid.uuid4().hex[:12]
        now = utcnow()
        self.db.execute(
            "INSERT INTO download(id, repo, filename, quant, revision, sha256, size_bytes, downloaded_bytes, "
            "backend, target_dir, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'queued', ?, ?)",
            (
                record_id, request.repo, request.filename, request.quant, request.revision, request.sha256,
                request.size_bytes, backend, plan.target_dir, now, now,
            ),
        )
        log_event(log, "download queued", id=record_id, repo=request.repo, file=request.filename, backend=backend)
        if backend == "direct":
            for companion in request.companions:
                self.db.execute(
                    "INSERT INTO download(id, repo, filename, quant, revision, sha256, size_bytes, downloaded_bytes, "
                    "backend, target_dir, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'direct', ?, 'queued', ?, ?)",
                    (
                        uuid.uuid4().hex[:12], companion.repo, companion.filename, companion.quant, companion.revision,
                        companion.sha256, companion.size_bytes, plan.target_dir, now, now,
                    ),
                )
        self._emit(record_id)
        self._pump()
        return self.get(record_id)

    def _row_to_record(self, row: dict[str, Any]) -> DownloadRecord:
        direct = row["backend"] == "direct"
        return DownloadRecord(
            **row,
            bytes_per_second=self._speeds.get(row["id"]),
            can_pause=direct and row["status"] in ("downloading", "queued"),
            can_cancel=(direct and row["status"] in ACTIVE | {"paused", "failed"}) or row["status"] == "queued",
        )

    def list(self) -> list[DownloadRecord]:
        rows = self.db.query("SELECT * FROM download ORDER BY created_at DESC")
        return [self._row_to_record(r) for r in rows]

    def get(self, record_id: str) -> DownloadRecord:
        row = self.db.query_one("SELECT * FROM download WHERE id = ?", (record_id,))
        if not row:
            raise DownloadError(f"Download {record_id} not found")
        return self._row_to_record(row)

    def _update(self, record_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE download SET {assignments} WHERE id = ?", (*fields.values(), record_id))

    def _emit(self, record_id: str) -> None:
        try:
            record = self.get(record_id)
        except DownloadError:
            return
        self.bus.publish("download.update", record.model_dump() | {"progress": record.progress})

    def pause(self, record_id: str) -> DownloadRecord:
        record = self.get(record_id)
        if record.backend != "direct":
            raise DownloadError(
                tr("LM Studio's own downloader cannot be paused. Stop it from LM Studio, or use Will It Run's downloader next time.")
            )
        if record.status == "queued":
            self._update(record_id, status="paused")
        elif record.status == "downloading":
            self._stop[record_id] = "paused"
        self._emit(record_id)
        return self.get(record_id)

    def resume(self, record_id: str) -> DownloadRecord:
        record = self.get(record_id)
        if record.status not in ("paused", "failed"):
            return record
        self._update(record_id, status="queued", error=None)
        self._emit(record_id)
        self._pump()
        return self.get(record_id)

    retry = resume

    def cancel(self, record_id: str) -> DownloadRecord:
        record = self.get(record_id)
        if record.status == "downloading" and record.backend != "direct":
            raise DownloadError(
                tr("LM Studio's own downloader cannot be stopped from here. Stop it from LM Studio, or use Will It Run's downloader next time.")
            )
        if record.status == "downloading":
            self._stop[record_id] = "cancelled"
        else:
            self._discard_partial(record)
            self._update(record_id, status="cancelled")
        self._emit(record_id)
        return self.get(record_id)

    def remove(self, record_id: str) -> None:
        record = self.get(record_id)
        if record.status in ("downloading", "verifying"):
            raise DownloadError(tr("Stop the download before removing it."))
        self.db.execute("DELETE FROM download WHERE id = ?", (record_id,))
        self.bus.publish("download.removed", {"id": record_id})

    def _discard_partial(self, record: DownloadRecord) -> None:
        if record.backend == "direct":
            part = Path(record.target_dir) / (record.filename + ".part")
            part.unlink(missing_ok=True)

    def _recover(self) -> None:
        """After a restart nothing is running: interrupted direct downloads become resumable."""
        for row in self.db.query("SELECT id, backend, external_job_id FROM download WHERE status IN ('downloading', 'verifying')"):
            if row["backend"] == "direct" or not row["external_job_id"]:
                self._update(row["id"], status="paused", error=tr("Interrupted when Will It Run closed; resume to continue."))
            else:
                self._update(row["id"], status="queued")

    # -- workers ------------------------------------------------------------
    def _pump(self) -> None:
        if not self._autostart:
            return
        with self._lock:
            running = sum(1 for t in self._workers.values() if t.is_alive())
            capacity = max(1, self.settings.downloads.max_concurrent) - running
            if capacity <= 0:
                return
            queued = self.db.query("SELECT id FROM download WHERE status = 'queued' ORDER BY created_at ASC")
            for row in queued[:capacity]:
                if row["id"] in self._workers and self._workers[row["id"]].is_alive():
                    continue
                thread = threading.Thread(target=self._run_safely, args=(row["id"],), daemon=True, name=f"dl-{row['id']}")
                self._workers[row["id"]] = thread
                self._update(row["id"], status="downloading")
                thread.start()

    def _run_safely(self, record_id: str) -> None:
        try:
            self.run(record_id)
        finally:
            self._speeds.pop(record_id, None)
            self._stop.pop(record_id, None)
            self._pump()

    def run(self, record_id: str) -> DownloadRecord:
        """Execute one download to completion. Public so tests can drive it synchronously."""
        record = self.get(record_id)
        self._update(record_id, status="downloading", error=None)
        self._emit(record_id)
        try:
            if record.backend == "lmstudio":
                self._run_lmstudio(record)
            else:
                self._run_direct(record)
        except _Stopped as stop:
            if stop.state == "cancelled":
                self._discard_partial(record)
            self._update(record_id, status=stop.state)
        except (DownloadError, LMStudioError, httpx.HTTPError, OSError) as exc:
            self._update(record_id, status="failed", error=str(exc)[:500])
            log.error("download %s failed: %s", record_id, exc)
        self._emit(record_id)
        final = self.get(record_id)
        if final.status in ("completed", "failed"):
            self.bus.publish("notify", {
                "title": tr("Model download complete") if final.status == "completed" else tr("Model download failed"),
                "body": f"{final.repo} ({final.quant or final.filename})" + (f": {final.error}" if final.error else ""),
            })
        return final

    def _run_lmstudio(self, record: DownloadRecord) -> None:
        job_id = record.external_job_id
        if not job_id:
            job = self.runtime.client.download(f"https://huggingface.co/{record.repo}", record.quant)
            if job.status == "already_downloaded":
                self._update(record.id, status="completed", downloaded_bytes=record.size_bytes or 0)
                return
            if not job.job_id:
                raise DownloadError(tr("LM Studio did not return a download job (status: {status}).", status=job.status))
            job_id = job.job_id
            self._update(record.id, external_job_id=job_id, size_bytes=job.total_size_bytes or record.size_bytes)
        while True:
            job = self.runtime.client.download_status(job_id)
            self._speeds[record.id] = job.bytes_per_second or 0.0
            self._update(
                record.id,
                downloaded_bytes=job.downloaded_bytes or 0,
                size_bytes=job.total_size_bytes or record.size_bytes,
            )
            self._emit(record.id)
            if job.status == "completed":
                self._update(record.id, status="completed", downloaded_bytes=job.total_size_bytes or record.size_bytes or 0)
                return
            if job.status == "failed":
                raise DownloadError(tr("LM Studio reported that the download failed."))
            if job.status == "paused":
                raise _Stopped("paused")
            time.sleep(1.0)

    def _run_direct(self, record: DownloadRecord) -> None:
        target_dir = Path(record.target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        final_path = target_dir / record.filename
        part_path = target_dir / (record.filename + ".part")
        part_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists() and (not record.size_bytes or final_path.stat().st_size == record.size_bytes):
            self._update(record.id, status="completed", downloaded_bytes=final_path.stat().st_size)
            return

        offset = part_path.stat().st_size if part_path.exists() else 0
        if record.size_bytes and offset > record.size_bytes:
            part_path.unlink()
            offset = 0
        url = ModelFile(repo=record.repo, filename=record.filename, revision=record.revision).url
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with self._http.stream("GET", url, headers=headers) as response:
            if response.status_code == 416 and record.size_bytes and offset == record.size_bytes:
                pass  # Already fully downloaded; fall through to verification.
            elif response.status_code == 200 and offset:
                offset = 0  # Server ignored the range request: restart from zero.
                part_path.unlink(missing_ok=True)
            elif response.status_code not in (200, 206):
                raise DownloadError(
                    tr("Hugging Face returned HTTP {code} for {file}", code=response.status_code, file=record.filename)
                )
            if response.status_code in (200, 206):
                total = record.size_bytes or (offset + int(response.headers.get("Content-Length") or 0)) or None
                if total and total != record.size_bytes:
                    self._update(record.id, size_bytes=total)
                done = offset
                window_start, window_bytes, last_emit = time.monotonic(), 0, 0.0
                with part_path.open("ab" if offset else "wb") as fh:
                    for chunk in response.iter_bytes(CHUNK):
                        stop = self._stop.get(record.id)
                        if stop:
                            fh.flush()
                            self._update(record.id, downloaded_bytes=done)
                            raise _Stopped(stop)
                        fh.write(chunk)
                        done += len(chunk)
                        window_bytes += len(chunk)
                        now = time.monotonic()
                        if now - window_start >= 1.0:
                            self._speeds[record.id] = window_bytes / (now - window_start)
                            window_start, window_bytes = now, 0
                        if now - last_emit >= 0.5:
                            self._update(record.id, downloaded_bytes=done)
                            self._emit(record.id)
                            last_emit = now
                self._update(record.id, downloaded_bytes=done)

        size = part_path.stat().st_size
        if record.size_bytes and size != record.size_bytes:
            raise DownloadError(
                tr(
                    "Incomplete download: got {got} of {total} bytes. Retry to resume from where it stopped.",
                    got=size, total=record.size_bytes,
                )
            )
        if record.sha256 and self.settings.downloads.verify_hash:
            self._update(record.id, status="verifying")
            self._emit(record.id)
            digest = hashlib.sha256()
            with part_path.open("rb") as fh:
                while block := fh.read(CHUNK * 4):
                    digest.update(block)
            if digest.hexdigest().lower() != record.sha256.lower():
                part_path.unlink(missing_ok=True)
                self._update(record.id, downloaded_bytes=0)
                raise DownloadError(
                    tr("SHA-256 mismatch: the downloaded file is corrupt and was deleted. Retry the download.")
                )
        part_path.replace(final_path)
        note = self._link_into_lmstudio(record, final_path)
        self._update(record.id, status="completed", downloaded_bytes=size, note=note)
        log_event(log, "download completed", id=record.id, path=str(final_path), bytes=size)

    def _link_into_lmstudio(self, record: DownloadRecord, final_path: Path) -> str | None:
        """Make LM Studio list a file that was saved outside its models folder.

        LM Studio only scans its own folder, so the file gets a link there: a hard link
        on the same drive, a symbolic link across drives. Windows only lets privileged
        users create symbolic links, so the fallback is a directory junction for the
        repository folder, which needs no privilege. The data stays where the user put
        it. Returns a remark for the download list, or None when nothing was needed.
        """
        models_dir = self.runtime.status().models_dir
        if not models_dir or _is_inside(str(final_path), models_dir):
            return None
        link = Path(models_dir) / Path(*record.repo.split("/")) / record.filename
        try:
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.exists() or link.is_symlink():
                return tr("Saved to {folder}. LM Studio already has a file with this name.", folder=str(final_path.parent))
            if storage.drive_of(str(link)) == storage.drive_of(str(final_path)):
                os.link(final_path, link)
            else:
                os.symlink(final_path, link)
        except OSError as exc:
            if _junction(link.parent, final_path.parent):
                log_event(log, "download linked", id=record.id, junction=str(link.parent), target=str(final_path.parent))
                return tr("Saved to {folder} and linked into LM Studio's folder.", folder=str(final_path.parent))
            log.warning("could not link %s into %s: %s", final_path, link.parent, exc)
            return tr(
                "Saved to {folder}, but Windows did not allow linking it into LM Studio's folder ({folder_lm}). "
                "LM Studio will not list it until you move it there, or turn on Developer Mode in Windows and download again.",
                folder=str(final_path.parent), folder_lm=models_dir,
            )
        log_event(log, "download linked", id=record.id, link=str(link), target=str(final_path))
        return tr("Saved to {folder} and linked into LM Studio's folder.", folder=str(final_path.parent))

    def verify_installed(self, record_id: str) -> bool:
        """Confirm LM Studio now lists the downloaded model."""
        record = self.get(record_id)
        try:
            installed = self.runtime.list_installed()
        except Exception:
            return False
        for model in installed:
            if (model.repo or "").lower() == record.repo.lower() and (
                model.filename == record.filename or (record.quant and model.quant == record.quant.upper())
            ):
                return True
        return False


def normalize_folder(folder: str) -> str:
    """Validate a user-chosen models folder: an absolute path on a real drive."""
    text = folder.strip().strip('"')
    path = Path(text)
    if not text or not path.is_absolute() or not path.drive:
        raise DownloadError(tr("Choose a full folder path, for example D:\\AI\\Models."))
    if not Path(path.anchor).exists():
        raise DownloadError(tr("Drive {drive} does not exist on this computer.", drive=path.drive))
    if path.exists() and not path.is_dir():
        raise DownloadError(tr("{path} is a file, not a folder.", path=text))
    return str(path)


def _junction(link_dir: Path, target_dir: Path) -> bool:
    """Make ``link_dir`` a directory junction to ``target_dir``. Only replaces an empty folder."""
    try:
        import _winapi

        if link_dir.is_dir() and not link_dir.is_symlink():
            if any(link_dir.iterdir()):
                return False
            link_dir.rmdir()
        elif link_dir.exists():
            return False
        _winapi.CreateJunction(str(target_dir), str(link_dir))
    except (ImportError, AttributeError, OSError):
        return False
    return True


def _is_inside(path: str, folder: str) -> bool:
    base = os.path.abspath(folder).lower()
    try:
        return os.path.commonpath([os.path.abspath(path).lower(), base]) == base
    except ValueError:
        return False


class _Stopped(Exception):
    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state
