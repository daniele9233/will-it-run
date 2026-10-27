"""The parts a model is made of, what is already on disk, and how to fetch what is missing.

A diffusion model is rarely one file, and never the same three: an SDXL checkpoint carries
its text encoder and its VAE inside, a Krea 2 or Qwen-Image build needs them separate. So the
catalog describes every model as a list of **components**, each with its own file, folder and
source, and a model that shares an encoder with another model names the same component. That
is what makes shared dependencies free: the second model finds the encoder already there.

Three rules this module keeps:

- a component is "present" only when the file is on disk where ComfyUI looks for it, matched
  by name, whatever a database row says;
- a download resolves its real URL, size and checksum from the publisher's API first, so
  nothing is guessed and the size is known before a byte is written;
- a component referenced by more than one model is never removed automatically. Removing it
  is a decision the person makes, and the interface names the models that would break.
"""

from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from agentfit import storage
from agentfit.db import Database, utcnow
from agentfit.events import EventBus
from agentfit.hardware.models import DriveInfo
from agentfit.i18n import tr
from agentfit.imagegen import catalog
from agentfit.imagegen.engine import ComfyEngine
from agentfit.logging_setup import get_logger

log = get_logger("imagegen.components")

CHUNK = 1024 * 1024
PROGRESS_EVERY = 0.4
REQUEST_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0


class ComponentError(Exception):
    """A component cannot be downloaded, and the message says why."""


class DiskFull(ComponentError):
    pass


@dataclass(frozen=True)
class ComponentDef:
    id: str
    name: str
    role: str
    folder: str
    file: str
    source: dict[str, Any]
    families: tuple[str, ...] = ()
    vram_gb: float | None = None
    licence: str = ""
    notes: str = ""
    url: str = ""
    size_bytes: int | None = None
    #: For a LoRA: the loader format, the trigger word, the strengths it was trained for.
    lora: dict[str, Any] = field(default_factory=dict)

    @property
    def downloadable(self) -> bool:
        return str(self.source.get("kind") or "") in ("huggingface", "civitai", "url", "git")

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "folder": self.folder,
            "file": self.file,
            "families": list(self.families),
            "vram_gb": self.vram_gb,
            "licence": self.licence,
            "notes": self.notes,
            "url": self.url or str(self.source.get("url") or ""),
            "size_bytes": self.size_bytes,
            "downloadable": self.downloadable,
            "source": self.source,
            "lora": dict(self.lora),
        }


@dataclass(frozen=True)
class Resolved:
    """What the publisher's API said about a file, before a byte is written."""

    url: str
    filename: str
    size_bytes: int | None
    sha256: str | None
    headers: dict[str, str] = field(default_factory=dict)


# -- the catalog of components ----------------------------------------------

_DATA = Path(__file__).with_name("data") / "components.json"
_DEFS: dict[str, ComponentDef] | None = None


def definitions() -> dict[str, ComponentDef]:
    global _DEFS
    if _DEFS is None:
        try:
            raw = json.loads(_DATA.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ComponentError(f"The component catalog could not be read: {exc}") from exc
        out: dict[str, ComponentDef] = {}
        for item in raw.get("components") or []:
            definition = ComponentDef(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                role=str(item["role"]),
                folder=str(item["folder"]),
                file=str(item.get("file") or ""),
                source=dict(item.get("source") or {"kind": "manual"}),
                families=tuple(item.get("families") or ()),
                vram_gb=item.get("vram_gb"),
                licence=str(item.get("licence") or ""),
                notes=str(item.get("notes") or ""),
                url=str(item.get("url") or ""),
                size_bytes=item.get("size_bytes"),
                lora=dict(item.get("lora") or {}),
            )
            out[definition.id] = definition
        _DEFS = out
    return _DEFS


def definition(component_id: str) -> ComponentDef | None:
    if component_id.startswith("local:"):
        filename = component_id.split(":", 1)[1]
        return ComponentDef(
            id=component_id,
            name=Path(filename).stem,
            role="checkpoint",
            folder="checkpoints",
            file=filename,
            source={"kind": "local"},
        )
    return definitions().get(component_id)


def custom_nodes() -> list[ComponentDef]:
    return [item for item in definitions().values() if item.role == "custom_node"]


# -- resolving a real URL, size and checksum --------------------------------


class Resolver:
    """Turns a component's source into a URL, a file name, a size and a checksum."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @property
    def images(self) -> Any:
        return self.settings.images

    def _headers(self, kind: str) -> dict[str, str]:
        token = str(getattr(self.images, "hf_token" if kind == "huggingface" else "civitai_token", "") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def resolve(self, component: ComponentDef) -> Resolved:
        kind = str(component.source.get("kind") or "manual")
        if kind == "huggingface":
            return self._huggingface(component)
        if kind == "civitai":
            return self._civitai(component)
        if kind == "url":
            return Resolved(
                url=str(component.source["url"]),
                filename=component.file or Path(str(component.source["url"])).name,
                size_bytes=component.size_bytes,
                sha256=None,
                headers={},
            )
        if kind == "git":
            raise ComponentError("A custom node is installed with git, not downloaded as a file.")
        raise ComponentError(
            tr("This component has no download source. Open {url} and put the file in {folder}.")
            .format(url=component.url or tr("its page"), folder=f"models/{component.folder}")
        )

    def _huggingface(self, component: ComponentDef) -> Resolved:
        source = component.source
        repo = str(source["repo"])
        revision = str(source.get("revision") or "main")
        wanted = str(source.get("filename") or component.file)
        headers = self._headers("huggingface")
        try:
            response = httpx.get(
                f"https://huggingface.co/api/models/{repo}/tree/{revision}",
                params={"recursive": "true"},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise ComponentError(tr("Hugging Face is not reachable: {error}").format(error=exc)) from exc
        if response.status_code in (401, 403):
            raise ComponentError(
                tr("{repo} needs a Hugging Face token. Add it in Settings > Images.").format(repo=repo)
            )
        if response.status_code == 404:
            raise ComponentError(tr("Hugging Face does not know the repository {repo}.").format(repo=repo))
        if response.status_code >= 400:
            raise ComponentError(tr("Hugging Face answered {code}.").format(code=response.status_code))
        try:
            entries = response.json()
        except ValueError as exc:
            raise ComponentError(tr("Hugging Face answered with something that is not JSON.")) from exc
        files = [item for item in entries if isinstance(item, dict) and item.get("type") == "file"]
        match = next((item for item in files if str(item.get("path", "")).endswith(wanted)), None)
        if match is None and not wanted:
            candidates = [item for item in files if str(item.get("path", "")).endswith(".safetensors")]
            match = max(candidates, key=lambda item: item.get("size") or 0, default=None)
        if match is None:
            raise ComponentError(
                tr("{file} is not in {repo}.").format(file=wanted or tr("the expected file"), repo=repo)
            )
        path = str(match["path"])
        lfs = match.get("lfs") if isinstance(match.get("lfs"), dict) else {}
        return Resolved(
            url=f"https://huggingface.co/{repo}/resolve/{revision}/{path}",
            filename=Path(path).name,
            size_bytes=int(match.get("size") or 0) or None,
            sha256=(str(lfs.get("oid")) if lfs.get("oid") else None),
            headers=headers,
        )

    def _civitai(self, component: ComponentDef) -> Resolved:
        source = component.source
        headers = self._headers("civitai")
        version_id = source.get("version_id")
        model_id = source.get("model_id")
        payload: dict[str, Any] | None = None
        try:
            if version_id:
                response = httpx.get(
                    f"https://civitai.com/api/v1/model-versions/{version_id}",
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=True,
                )
                if response.status_code >= 400:
                    raise ComponentError(
                        tr("Civitai answered {code} for version {id}.").format(code=response.status_code, id=version_id)
                    )
                payload = response.json()
            elif model_id:
                response = httpx.get(
                    f"https://civitai.com/api/v1/models/{model_id}",
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=True,
                )
                if response.status_code >= 400:
                    raise ComponentError(
                        tr("Civitai answered {code} for model {id}.").format(code=response.status_code, id=model_id)
                    )
                model = response.json()
                versions = model.get("modelVersions") or []
                if not versions:
                    raise ComponentError(tr("Civitai lists no versions for model {id}.").format(id=model_id))
                payload = versions[0]
            else:
                raise ComponentError(tr("This Civitai component names neither a model nor a version."))
        except httpx.HTTPError as exc:
            raise ComponentError(tr("Civitai is not reachable: {error}").format(error=exc)) from exc
        except ValueError as exc:
            raise ComponentError(tr("Civitai answered with something that is not JSON.")) from exc
        files = payload.get("files") or []
        wanted_id = source.get("file_id")
        if wanted_id:
            primary = next((item for item in files if str(item.get("id")) == str(wanted_id)), None)
            if primary is None:
                raise ComponentError(
                    tr("Civitai version {id} no longer lists file {file}.").format(id=version_id or model_id, file=wanted_id)
                )
        else:
            primary = next((item for item in files if item.get("primary")), files[0] if files else None)
        if not primary:
            raise ComponentError(tr("Civitai lists no file for this version."))
        name = str(primary.get("name") or component.file or "model.safetensors")
        size_kb = primary.get("sizeKB")
        hashes = primary.get("hashes") or {}
        download = str(primary.get("downloadUrl") or "")
        if not download:
            raise ComponentError(tr("Civitai did not give a download address for this file."))
        if self._headers("civitai") and "token=" not in download:
            download = f"{download}{'&' if '?' in download else '?'}token={self.images.civitai_token}"
        return Resolved(
            url=download,
            filename=sanitize_filename(name),
            size_bytes=int(float(size_kb) * 1024) if size_kb else None,
            sha256=(str(hashes.get("SHA256")).lower() if hashes.get("SHA256") else None),
            headers=self._headers("civitai"),
        )


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._\-() \[\]]+", "_", name).strip(" ._")
    return cleaned or "model.safetensors"


# -- installed state --------------------------------------------------------


@dataclass
class Download:
    component_id: str
    status: str = "queued"  # queued | downloading | verifying | done | failed | cancelled
    received: int = 0
    total: int | None = None
    speed: float = 0.0
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def percent(self) -> float | None:
        if not self.total:
            return None
        return min(100.0, self.received * 100.0 / self.total)

    def to_json(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "status": self.status,
            "received": self.received,
            "total": self.total,
            "percent": self.percent,
            "speed": self.speed,
            "error": self.error,
        }


class ComponentStore:
    """Every component, whether it is on disk, and the downloads in flight."""

    def __init__(
        self,
        db: Database,
        settings: Any,
        bus: EventBus,
        engine: ComfyEngine,
        drives: Callable[[], list[DriveInfo]] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.bus = bus
        self.engine = engine
        self._drives = drives or (lambda: [])
        self._resolver = Resolver(settings)
        self._lock = threading.RLock()
        self._downloads: dict[str, Download] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel: set[str] = set()
        #: folder -> (read at, names). ComfyUI's own listing includes the folders of
        #: extra_model_paths.yaml, which is where this PC keeps the big shared files.
        self._listings: dict[str, tuple[float, list[str]]] = {}
        #: (checked at, is the engine answering), so 17 components do not mean 17 probes.
        self._reachable: tuple[float, bool] | None = None

    # -- where things live --------------------------------------------------
    @property
    def models_dir(self) -> Path | None:
        install = self.engine.install()
        return install.models_dir if install else None

    def listing(self, folder: str, *, max_age: float = 6.0) -> list[str]:
        """The files ComfyUI sees in one of its folders, shared paths included.

        When the engine is stopped the answer comes from disk and from the last listing
        ComfyUI gave (kept in the database): a model in a shared folder must not disappear
        from the interface just because ComfyUI is not running this minute.
        """
        now = time.time()
        cached = self._listings.get(folder)
        if cached and now - cached[0] < max_age:
            return cached[1]
        factory = getattr(self.engine, "client", None)
        names: list[str] = []
        authoritative = False
        if callable(factory) and self.engine_reachable():
            try:
                names = factory().models(folder, timeout=5.0)
                authoritative = True
                self.db.kv_set("imagegen_listing", folder, {"at": utcnow(), "names": names})
            except Exception:
                # A folder the engine does not know, or a call that timed out: the disk and
                # the last listing are still worth reading.
                authoritative = False
        if not authoritative:
            names = list(dict.fromkeys([*self._files_in(folder), *self._cached_listing(folder)]))
        self._listings[folder] = (now, names)
        return names

    def _cached_listing(self, folder: str) -> list[str]:
        payload = self.db.kv_get("imagegen_listing", folder) or {}
        names = payload.get("names") if isinstance(payload, dict) else None
        return [str(name) for name in names] if isinstance(names, list) else []

    def engine_reachable(self, *, max_age: float = 10.0) -> bool:
        """Is ComfyUI answering? Asked once every few seconds, never once per file."""
        now = time.time()
        if self._reachable is not None and now - self._reachable[0] < max_age:
            return self._reachable[1]
        factory = getattr(self.engine, "client", None)
        up = False
        if callable(factory):
            try:
                up = bool(factory().available())
            except Exception:
                up = False
        self._reachable = (now, up)
        return up

    def invalidate(self) -> None:
        """Forget what the engine said: it was just started, stopped, or moved."""
        self._reachable = None
        self._listings.clear()

    def register(
        self,
        component: ComponentDef,
        *,
        path: Path,
        size_bytes: int,
        sha256: str | None,
        note: str = "",
    ) -> None:
        """Record a file that is already in place — imported, not downloaded."""
        self._remember(
            component.id,
            role=component.role,
            file_name=path.name,
            folder=component.folder,
            path=str(path),
            size_bytes=size_bytes,
            sha256=sha256,
            status="present",
            source=component.source,
            licence=component.licence,
            installed_at=utcnow(),
            error=None,
        )
        log.info("image component registered", extra={"component": component.id, "path": str(path), "note": note})

    def listed(self, component: ComponentDef, filename: str | None = None) -> bool:
        """Whether ComfyUI would offer this file, even from a folder this app does not know."""
        name = filename or component.file
        if not name or component.role == "custom_node":
            return False
        return name in self.listing(component.folder)

    def target_path(self, component: ComponentDef, filename: str | None = None) -> Path | None:
        models = self.models_dir
        if models is None:
            return None
        return models / component.folder / (filename or component.file)

    def _files_in(self, folder: str) -> list[str]:
        models = self.models_dir
        if models is None:
            return []
        base = models / folder
        if not base.is_dir():
            return []
        try:
            return [item.name for item in base.iterdir() if item.is_file()]
        except OSError:
            return []

    def on_disk(self, component: ComponentDef, filename: str | None = None) -> Path | None:
        """Where the file actually is, if it is, whatever the database says."""
        models = self.models_dir
        if models is None:
            return None
        if component.role == "custom_node":
            folder = models.parent / "custom_nodes" / component.folder
            return folder if folder.is_dir() else None
        expected = filename or component.file
        if expected:
            path = models / component.folder / expected
            return path if path.is_file() else None
        state = self.state(component.id)
        if state and state.get("file_name"):
            path = models / component.folder / str(state["file_name"])
            return path if path.is_file() else None
        return None

    # -- database -----------------------------------------------------------
    def state(self, component_id: str) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM image_component WHERE id = ?", (component_id,))

    def _remember(self, component_id: str, **fields: Any) -> None:
        existing = self.state(component_id)
        if existing is None:
            self.db.execute(
                "INSERT INTO image_component(id, role, file_name, folder, path, size_bytes, sha256, status, "
                "source_json, licence, installed_at, verified_at, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    component_id,
                    fields.get("role", ""),
                    fields.get("file_name", ""),
                    fields.get("folder", ""),
                    fields.get("path", ""),
                    int(fields.get("size_bytes") or 0),
                    fields.get("sha256"),
                    fields.get("status", "missing"),
                    json.dumps(fields.get("source") or {}),
                    fields.get("licence", ""),
                    fields.get("installed_at"),
                    utcnow(),
                    fields.get("error"),
                ),
            )
            return
        allowed = {
            "role", "file_name", "folder", "path", "size_bytes", "sha256", "status",
            "source_json", "licence", "installed_at", "error",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if "source" in fields:
            updates["source_json"] = json.dumps(fields["source"])
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        self.db.execute(
            f"UPDATE image_component SET {assignments}, verified_at = ? WHERE id = ?",
            (*updates.values(), utcnow(), component_id),
        )

    # -- the view the interface reads --------------------------------------
    def references(self, component_id: str) -> list[str]:
        return [profile.name for profile in catalog.profiles() if component_id in profile.components.values()]

    def row(self, component: ComponentDef) -> dict[str, Any]:
        state = self.state(component.id) or {}
        path = self.on_disk(component, state.get("file_name") or None)
        download = self._downloads.get(component.id)
        listed = path is not None or self.listed(component, state.get("file_name") or None)
        if download is not None and download.status in ("queued", "downloading", "verifying"):
            status = download.status
        elif listed:
            status = "present"
        elif state.get("status") == "failed" or (state.get("error") and path is None):
            status = "failed"
        else:
            status = "missing"
        size = int(state.get("size_bytes") or 0) or None
        if size is None and path is not None:
            try:
                size = path.stat().st_size
            except OSError:
                size = None
        payload = component.to_json()
        payload.update(
            {
                "status": status,
                "path": str(path) if path else None,
                "installed_size": path.stat().st_size if path else None,
                "size_bytes": size or component.size_bytes,
                "sha256": state.get("sha256"),
                "error": (download.error if download and download.error else state.get("error")),
                "download": download.to_json() if download else None,
                "used_by": self.references(component.id),
                "target": str(self.target_path(component, state.get("file_name") or None) or ""),
                "lora": dict(component.lora),
                # A Civitai file that cannot be downloaded until the person gives their token.
                "needs_token": bool(
                    component.source.get("kind") == "civitai"
                    and component.source.get("needs_token")
                    and not self._civitai_token()
                ),
                # Present, but in a folder this app did not know about: it can be used and
                # not deleted from here.
                "external": bool(listed and path is None),
            }
        )
        return payload

    def _civitai_token(self) -> str:
        return str(getattr(self.settings.images, "civitai_token", "") or "").strip()

    def rows(self) -> list[dict[str, Any]]:
        return [self.row(component) for component in definitions().values()]

    def component_state(self, component_id: str) -> dict[str, Any]:
        component = definition(component_id)
        if component is None:
            return {"id": component_id, "status": "missing", "error": tr("Unknown component.")}
        return self.row(component)

    # -- planning -----------------------------------------------------------
    def plan(self, model_key: str) -> dict[str, Any]:
        profile = catalog.by_key(model_key)
        if profile is None:
            raise ComponentError(tr("There is no model called {key}.").format(key=model_key))
        needed: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        total = 0
        unknown = False
        for component_id in profile.component_ids():
            component = definition(component_id)
            if component is None:
                continue
            row = self.row(component)
            needed.append(row)
            if row["status"] != "present":
                missing.append(row)
                if row["size_bytes"]:
                    total += int(row["size_bytes"])
                else:
                    unknown = True
        plan: dict[str, Any] = {
            "model": model_key,
            "components": needed,
            "missing": missing,
            "download_bytes": total if not unknown else None,
            "disk": None,
            "problems": [],
        }
        models = self.models_dir
        if models is None:
            plan["problems"].append(
                tr("ComfyUI was not found on this PC, so there is nowhere to put the files.")
            )
            return plan
        if missing and total:
            disk = storage.plan_download(total, str(models), self.settings.storage, self._drives())
            plan["disk"] = disk.model_dump(mode="json")
            if not disk.safe:
                plan["problems"].append(disk.verdict)
        elif unknown and missing:
            plan["problems"].append(
                tr("The size of what is missing is known only once the download starts.")
            )
        return plan

    def missing_for(self, model_key: str) -> list[str]:
        return [item["id"] for item in self.plan(model_key)["missing"] if item.get("downloadable")]

    # -- downloading --------------------------------------------------------
    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="image-downloads", daemon=True)
                self._worker.start()

    def start_download(self, component_ids: list[str]) -> list[dict[str, Any]]:
        started: list[dict[str, Any]] = []
        for component_id in component_ids:
            component = definition(component_id)
            if component is None:
                raise ComponentError(tr("Unknown component: {id}.").format(id=component_id))
            if not component.downloadable:
                raise ComponentError(
                    tr("{name} has no download source. Open its page and put the file in models/{folder}.")
                    .format(name=component.name, folder=component.folder)
                )
            with self._lock:
                existing = self._downloads.get(component_id)
                if existing and existing.status in ("queued", "downloading", "verifying"):
                    started.append(existing.to_json())
                    continue
                if self.on_disk(component) is not None:
                    started.append({**Download(component_id, status="done").to_json()})
                    continue
                download = Download(component_id)
                self._downloads[component_id] = download
                self._cancel.discard(component_id)
            self._remember(component_id, role=component.role, folder=component.folder, source=component.source,
                           licence=component.licence, status="downloading", error=None)
            self._ensure_worker()
            self._queue.put(component_id)
            started.append(download.to_json())
            self.bus.publish("image.component", {"id": component_id, "status": "queued"})
        return started

    def downloads(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.to_json() for item in self._downloads.values()]

    def cancel_download(self, component_id: str) -> dict[str, Any]:
        with self._lock:
            self._cancel.add(component_id)
            download = self._downloads.get(component_id)
            if download and download.status == "queued":
                download.status = "cancelled"
                download.finished_at = time.time()
                return download.to_json()
        return {"component_id": component_id, "status": "cancelling"}

    def remove(self, component_id: str) -> dict[str, Any]:
        component = definition(component_id)
        if component is None:
            raise ComponentError(tr("Unknown component: {id}.").format(id=component_id))
        used_by = self.references(component_id)
        if len(used_by) > 1:
            raise ComponentError(
                tr("{name} is used by {count} models ({models}). Removing it would break them.")
                .format(name=component.name, count=len(used_by), models=", ".join(used_by))
            )
        path = self.on_disk(component, (self.state(component_id) or {}).get("file_name") or None)
        if path is None and self.listed(component, (self.state(component_id) or {}).get("file_name") or None):
            raise ComponentError(
                tr(
                    "{name} is in a folder ComfyUI knows but this app does not, so it is not deleted from here."
                ).format(name=component.name)
            )
        if path is not None:
            if path.is_dir():
                import shutil

                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        self.db.execute("DELETE FROM image_component WHERE id = ?", (component_id,))
        return {"removed": component_id, "was_present": path is not None}

    # -- the worker ---------------------------------------------------------
    def _work(self) -> None:
        while True:
            component_id = self._queue.get()
            try:
                self._download_one(component_id)
            except Exception as exc:
                log.warning("image component download failed", extra={"component": component_id, "error": str(exc)})
                self._finish(component_id, "failed", error=str(exc))
            finally:
                self._queue.task_done()

    def _finish(self, component_id: str, status: str, *, error: str = "") -> None:
        with self._lock:
            download = self._downloads.get(component_id)
            if download is not None:
                download.status = status
                download.error = error
                download.finished_at = time.time()
        stored = {"done": "present", "failed": "failed"}.get(status, "missing")
        self._remember(component_id, status=stored, error=error or None)
        self.bus.publish(
            "image.component", {"id": component_id, "status": status, "error": error}, keep=True
        )

    def _download_one(self, component_id: str) -> None:
        component = definition(component_id)
        if component is None:
            raise ComponentError(tr("Unknown component: {id}.").format(id=component_id))
        if self._cancelled(component_id):
            return self._finish(component_id, "cancelled")
        models = self.models_dir
        if models is None:
            raise ComponentError(tr("ComfyUI was not found on this PC, so there is nowhere to put the file."))
        if component.source.get("kind") == "git":
            return self._install_custom_node(component_id, component)
        if component.source.get("kind") == "civitai" and component.source.get("needs_token") and not self._civitai_token():
            raise ComponentError(
                tr("Civitai gives this file only to a signed-in account. Add your Civitai token in Settings > Images, then download again.")
            )
        resolved = self._resolver.resolve(component)
        target_dir = models / component.folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / resolved.filename
        if target.exists() and target.stat().st_size > 0:
            self._finish(component_id, "done")
            return
        if resolved.size_bytes:
            disk = storage.plan_download(resolved.size_bytes, str(models), self.settings.storage, self._drives())
            if not disk.safe:
                raise DiskFull(disk.verdict)
        with self._lock:
            download = self._downloads.get(component_id)
            if download:
                download.status = "downloading"
                download.total = resolved.size_bytes
        self.bus.publish("image.component", {"id": component_id, "status": "downloading"})
        part = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        received = 0
        started = time.time()
        last = 0.0
        try:
            with httpx.stream(
                "GET", resolved.url, headers=resolved.headers, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True
            ) as response:
                if response.status_code in (401, 403):
                    raise ComponentError(
                        tr("The download needs a token. Add it in Settings > Images.")
                    )
                if response.status_code >= 400:
                    raise ComponentError(tr("The download failed with HTTP {code}.").format(code=response.status_code))
                total = resolved.size_bytes or _int_header(response.headers.get("content-length"))
                with self._lock:
                    download = self._downloads.get(component_id)
                    if download:
                        download.total = total
                        download.status = "downloading"
                with part.open("wb") as handle:
                    for chunk in response.iter_bytes(CHUNK):
                        if self._cancelled(component_id):
                            raise _Cancelled
                        handle.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        now = time.time()
                        if now - last >= PROGRESS_EVERY:
                            last = now
                            self._progress(component_id, received, total, received / max(0.001, now - started))
            with self._lock:
                download = self._downloads.get(component_id)
                if download:
                    download.status = "verifying"
                    download.received = received
            self.bus.publish("image.component", {"id": component_id, "status": "verifying"})
            checksum = digest.hexdigest()
            expected = (resolved.sha256 or "").lower()
            if expected and checksum != expected:
                part.unlink(missing_ok=True)
                raise ComponentError(
                    tr("The file does not match its checksum: expected {expected}, got {actual}.")
                    .format(expected=expected[:12], actual=checksum[:12])
                )
            part.replace(target)
            self._remember(
                component_id,
                role=component.role,
                file_name=resolved.filename,
                folder=component.folder,
                path=str(target),
                size_bytes=target.stat().st_size,
                sha256=checksum,
                status="present",
                source=component.source,
                licence=component.licence,
                installed_at=utcnow(),
                error=None,
            )
            self._finish(component_id, "done")
        except _Cancelled:
            part.unlink(missing_ok=True)
            self._finish(component_id, "cancelled")
        except (httpx.HTTPError, OSError) as exc:
            part.unlink(missing_ok=True)
            raise ComponentError(tr("The download stopped: {error}").format(error=exc)) from exc

    def _install_custom_node(self, component_id: str, component: ComponentDef) -> None:
        install = self.engine.install()
        if install is None:
            raise ComponentError(tr("ComfyUI was not found on this PC."))
        repo = str(component.source.get("repo") or "")
        if not repo:
            raise ComponentError(tr("This custom node has no repository address."))
        target = install.root / "custom_nodes" / component.folder
        if target.is_dir():
            self._finish(component_id, "done")
            return
        import subprocess

        creation = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", repo, str(target)],
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=creation,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ComponentError(tr("git could not install the node: {error}").format(error=exc)) from exc
        if result.returncode != 0:
            raise ComponentError(
                tr("git could not install the node: {error}").format(error=(result.stderr or "").strip()[:300])
            )
        self._remember(
            component_id,
            role=component.role,
            folder=component.folder,
            path=str(target),
            status="present",
            source=component.source,
            installed_at=utcnow(),
        )
        self._finish(component_id, "done")

    def _cancelled(self, component_id: str) -> bool:
        with self._lock:
            return component_id in self._cancel

    def _progress(self, component_id: str, received: int, total: int | None, speed: float) -> None:
        with self._lock:
            download = self._downloads.get(component_id)
            if download is None:
                return
            download.received = received
            download.total = total
            download.speed = speed
            payload = download.to_json()
        self.bus.publish("image.component.progress", payload, keep=False)


class _Cancelled(Exception):
    pass


def _int_header(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
