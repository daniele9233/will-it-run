"""The image studio: queue a ComfyUI workflow, watch it, keep the result.

The work happens in ComfyUI, in a thread of its own, because an image takes from a few
seconds to a few minutes and the HTTP call must return at once. The studio owns three
things and nothing else:

- the request as the person made it (prompt, size, seed, LoRAs), so any picture can be
  reproduced exactly by asking for it again with the same seed;
- the record of what was generated, in SQLite, with the file on disk under the app's
  data folder — not under ComfyUI's, which the person may clear;
- the live state, published on the event bus for the interface to follow.

Nothing is invented: a model counts as installed only when ComfyUI lists every one of
its files, and a workflow is queued only when every node class it uses exists.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentfit import paths
from agentfit.db import Database, utcnow
from agentfit.events import EventBus
from agentfit.i18n import tr
from agentfit.imagegen import catalog, templates
from agentfit.imagegen import plan as plan_mod
from agentfit.imagegen.comfy import ComfyClient, ComfyError, produced_images, status_message
from agentfit.imagegen.components import ComponentStore
from agentfit.imagegen.engine import ComfyEngine

#: Jobs kept in memory for the interface. The pictures themselves are in SQLite.
KEEP_JOBS = 40

#: Role in the component catalog -> name the workflow templates use for it.
ROLE_PLACEHOLDER = {
    "diffusion_model": "unet",
    "text_encoder": "clip",
    "vae": "vae",
    "checkpoint": "checkpoint",
}


class StudioError(Exception):
    """The request cannot be served: a missing model, a bad size, an unknown template."""


@dataclass
class GenerationRequest:
    model_key: str
    template_key: str = ""
    prompt: str = ""
    negative: str = ""
    width: int = 1024
    height: int = 1024
    steps: int | None = None
    cfg: float | None = None
    sampler: str | None = None
    scheduler: str | None = None
    seed: int = -1
    denoise: float | None = None
    loras: list[dict[str, Any]] = field(default_factory=list)
    #: A picture already uploaded to ComfyUI (its name), for edit and upscale.
    image: str = ""
    images: list[str] = field(default_factory=list)
    resolution: int | None = None
    hires_scale: float | None = None
    hires_denoise: float | None = None
    #: How many pictures in one run: the same graph, one batch.
    batch: int = 1

    def normalised(self) -> GenerationRequest:
        """Clamp what a typo can break: sizes are multiples of 8, seeds are 32-bit."""
        width = max(256, min(4096, int(self.width) // 8 * 8))
        height = max(256, min(4096, int(self.height) // 8 * 8))
        seed = self.seed
        if seed is None or int(seed) < 0:
            seed = int.from_bytes(uuid.uuid4().bytes[:4], "big") & 0x7FFFFFFF
        return GenerationRequest(
            model_key=self.model_key,
            template_key=self.template_key,
            prompt=self.prompt.strip(),
            negative=self.negative.strip(),
            width=width,
            height=height,
            steps=self.steps,
            cfg=self.cfg,
            sampler=self.sampler,
            scheduler=self.scheduler,
            seed=int(seed) & 0x7FFFFFFF,
            denoise=self.denoise,
            loras=[lora for lora in self.loras if str(lora.get("name") or "").strip()],
            image=self.image,
            images=[name for name in self.images if name],
            resolution=self.resolution,
            hires_scale=self.hires_scale,
            hires_denoise=self.hires_denoise,
            batch=max(1, min(4, int(self.batch or 1))),
        )


class ImageStudio:
    def __init__(
        self,
        db: Database,
        settings: Any,
        bus: EventBus,
        *,
        client: ComfyClient | None = None,
        images_dir: Path | None = None,
        engine: ComfyEngine | None = None,
        components: ComponentStore | None = None,
        vram_budget_gb: Any = None,
        lmstudio_loaded: Any = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.bus = bus
        self._client = client
        self._images_dir = images_dir
        self._engine = engine
        self._components = components
        self._vram_budget_gb = vram_budget_gb
        self._lmstudio_loaded = lmstudio_loaded
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []

    # -- wiring -------------------------------------------------------------
    @property
    def config(self) -> Any:
        return self.settings.images

    @property
    def engine(self) -> ComfyEngine:
        if self._engine is None:
            self._engine = ComfyEngine(self.settings, self.bus)
        return self._engine

    @property
    def components(self) -> ComponentStore:
        if self._components is None:
            self._components = ComponentStore(self.db, self.settings, self.bus, self.engine)
        return self._components

    @property
    def client(self) -> ComfyClient:
        if self._client is not None:
            return self._client
        return ComfyClient(self.config.comfy_url, timeout=float(self.config.request_timeout_seconds))

    @property
    def images_dir(self) -> Path:
        if self._images_dir is not None:
            return paths.ensure(self._images_dir)
        configured = str(getattr(self.config, "gallery_dir", "") or "").strip()
        return paths.ensure(Path(configured) if configured else paths.local_dir() / "images")

    # -- what the engine can do --------------------------------------------
    def status(self) -> dict[str, Any]:
        client = self.client
        payload: dict[str, Any] = {
            "url": client.base_url,
            "available": False,
            "vram": None,
            "queue": None,
            "error": None,
            "engine": self.engine.status(),
        }
        try:
            payload["vram"] = client.vram()
            payload["queue"] = client.queue_state()
            payload["available"] = True
        except ComfyError as exc:
            payload["error"] = str(exc)
        return payload

    # -- models -------------------------------------------------------------
    def models(self) -> list[dict[str, Any]]:
        """Every model this PC could generate with, and exactly what is missing from each."""
        out = [self._model_row(profile) for profile in catalog.profiles()]
        known = {str(p.components.get("checkpoint", "")).removeprefix("local:") for p in catalog.profiles()}
        for name in sorted(self.components.listing("checkpoints")):
            if name.lower().endswith((".safetensors", ".ckpt")) and name not in known:
                out.append(self._model_row(catalog.from_checkpoint(name)))
        return out

    def _model_row(self, profile: catalog.ModelProfile) -> dict[str, Any]:
        parts = [self.components.component_state(component_id) for component_id in profile.component_ids()]
        missing = [item for item in parts if item.get("status") != "present"]
        row = profile.to_json()
        row["installed"] = not missing
        row["missing"] = missing
        row["components_detail"] = parts
        row["download_bytes"] = sum(int(item.get("size_bytes") or 0) for item in missing) or None
        row["template_kinds"] = list(profile.kinds)
        row["lora_options"] = [
            self.components.component_state(component_id) for component_id in profile.loras
        ]
        row["ready"] = row["installed"] and not profile.blocked
        return row

    def find_model(self, key: str) -> catalog.ModelProfile:
        profile = catalog.by_key(key)
        if profile is not None:
            return profile
        if key.startswith("ckpt:"):
            name = key[len("ckpt:") :]
            if name:
                return catalog.from_checkpoint(name)
        raise StudioError(f"There is no model called {key!r}.")

    def template_list(self) -> list[dict[str, Any]]:
        return [template.to_json() for template in templates.templates()]

    # -- readiness ----------------------------------------------------------
    def graph_for(self, profile: catalog.ModelProfile, template: templates.Template, request: GenerationRequest) -> dict[str, Any]:
        values: dict[str, Any] = dict(template.defaults)
        values.update(profile.sampling)
        values.update(
            {
                "prompt": _with_prefix(profile.prompt_prefix, request.prompt),
                "negative": request.negative or profile.negative or str(template.defaults.get("negative", "")),
                "width": request.width,
                "height": request.height,
                "seed": request.seed,
                "seed2": (request.seed + 1) & 0x7FFFFFFF,
                "filename_prefix": f"WillItRun/{template.key}-{time.strftime('%Y%m%d-%H%M%S')}",
                "image": request.image or None,
                "image2": request.images[0] if len(request.images) > 0 else None,
                "image3": request.images[1] if len(request.images) > 1 else None,
                "image4": request.images[2] if len(request.images) > 2 else None,
            }
        )
        for name, value in {
            "steps": request.steps,
            "cfg": request.cfg,
            "sampler": request.sampler,
            "scheduler": request.scheduler,
            "denoise": request.denoise,
            "resolution": request.resolution,
            "hires_scale": request.hires_scale,
            "hires_denoise": request.hires_denoise,
        }.items():
            if value is not None:
                values[name] = value
        for role, component_id in profile.components.items():
            state = self.components.component_state(component_id)
            values[ROLE_PLACEHOLDER.get(role, role)] = state.get("file") or None
        values.update({key: value for key, value in profile.loader.items() if value is not None})
        values.setdefault("weight_dtype", None)
        values["batch_size"] = request.batch
        graph = templates.bind(template, values)
        return templates.apply_loras(graph, template, request.loras)

    def check(self, model_key: str, template_key: str, request: GenerationRequest | None = None) -> dict[str, Any]:
        """Can this model run this template on this engine, and if not, what is missing."""
        profile = self.find_model(model_key)
        template = templates.by_key(template_key)
        request = (request or GenerationRequest(model_key=model_key)).normalised()
        problems: list[str] = []
        missing_files: list[dict[str, Any]] = []
        missing_nodes: list[str] = []
        needs_image = template.kind in ("edit", "upscale") and not request.image
        if profile.blocked:
            problems.append(self._blocked_reason(profile))
        if needs_image:
            problems.append(tr("This workflow needs a picture: upload one first."))
        try:
            client = self.client
            available = client.available()
        except ComfyError as exc:
            available = False
            problems.append(str(exc))
        if template.roles:
            parts = [self.components.component_state(cid) for cid in profile.component_ids()]
            missing_files = [item for item in parts if item.get("status") != "present"]
            if missing_files:
                problems.append(
                    tr("Missing: {names}.").format(names=", ".join(item.get("name", "") for item in missing_files))
                )
        if available and not needs_image:
            try:
                graph = self.graph_for(profile, template, request)
                missing_nodes = client.missing_nodes(template.classes(graph))
            except (templates.TemplateError, ComfyError) as exc:
                problems.append(str(exc))
        if missing_nodes:
            problems.append(tr("Nodes this ComfyUI does not have: {names}.").format(names=", ".join(missing_nodes)))
        ready = available and not missing_files and not missing_nodes and not problems
        return {
            "ready": ready,
            "engine": available,
            "missing_files": missing_files,
            "missing_nodes": missing_nodes,
            "problems": problems,
        }

    @staticmethod
    def _blocked_reason(profile: catalog.ModelProfile) -> str:
        blocked = profile.blocked or {}
        reason = str(blocked.get("reason") or tr("{name} cannot run on this engine.").format(name=profile.name))
        missing = [str(item) for item in (blocked.get("missing") or [])]
        if missing:
            reason = f"{reason} " + tr("Missing: {names}.").format(names=", ".join(missing))
        return reason

    def plan(self, model_key: str) -> dict[str, Any]:
        """Everything needed to run this model here, and what to change if it does not fit."""
        profile = self.find_model(model_key)
        base = self.components.plan(model_key)
        status = self.status()
        free = (status.get("vram") or {}).get("free_gb")
        parts = [self.components.component_state(cid) for cid in profile.component_ids()]
        node_missing = any(item.get("role") == "custom_node" and item.get("status") != "present" for item in parts)
        estimate = plan_mod.estimate(
            family=profile.family,
            vram_gb=profile.vram_gb,
            components=base["components"],
            usable_gb=self._budget(),
            free_vram_gb=free,
            engine_running=bool(status.get("available")),
            lmstudio_loaded=self._loaded(),
            node_missing=node_missing,
        )
        lighter = [
            {"key": other.key, "name": other.name, "vram_gb": other.vram_gb}
            for other in catalog.profiles()
            if other.family == profile.family
            and other.key != profile.key
            and other.vram_gb is not None
            and (profile.vram_gb is None or other.vram_gb < profile.vram_gb)
        ]
        return {**base, "estimate": estimate, "lighter": lighter}

    def _budget(self) -> float | None:
        try:
            value = self._vram_budget_gb() if callable(self._vram_budget_gb) else None
        except Exception:
            return None
        return float(value) if value else None

    def _loaded(self) -> bool:
        try:
            return bool(self._lmstudio_loaded()) if callable(self._lmstudio_loaded) else False
        except Exception:
            return False

    # -- generating ---------------------------------------------------------
    def generate(self, request: GenerationRequest) -> dict[str, Any]:
        request = request.normalised()
        profile = self.find_model(request.model_key)
        if profile.blocked:
            raise StudioError(self._blocked_reason(profile))
        if not request.template_key:
            kind = "edit" if request.image else "txt2img"
            candidates = templates.for_family(profile.family, kind)
            preferred = next((item for item in candidates if item.key == profile.template), None)
            if preferred is not None:
                request.template_key = preferred.key
            elif candidates:
                request.template_key = candidates[0].key
            else:
                raise StudioError(tr("No workflow template for {name} yet.").format(name=profile.name))
        template = templates.by_key(request.template_key)
        if template.family not in ("any", profile.family):
            raise StudioError(tr("{template} does not run {model}.").format(template=template.name, model=profile.name))
        if template.roles:
            missing = [
                self.components.component_state(cid)
                for cid in profile.component_ids()
                if self.components.component_state(cid).get("status") != "present"
            ]
            if missing:
                raise StudioError(
                    tr("This model is not complete: {names}.").format(
                        names=", ".join(item.get("name", "") for item in missing)
                    )
                )
        if template.kind in ("edit", "upscale") and not request.image:
            raise StudioError(tr("This workflow needs a picture: upload one first."))
        if template.kind == "txt2img" and not request.prompt:
            raise StudioError(tr("Write what the picture should show."))
        if template.kind == "upscale" and not request.prompt:
            request.prompt = "high detail, sharp, natural skin texture"

        job_id = uuid.uuid4().hex[:12]
        job: dict[str, Any] = {
            "id": job_id,
            "status": "queued",
            "phase": "queued",
            "model_key": profile.key,
            "model_name": profile.name,
            "template": template.key,
            "template_name": template.name,
            "prompt": request.prompt,
            "negative": request.negative,
            "width": request.width,
            "height": request.height,
            "seed": request.seed,
            "loras": request.loras,
            "batch": request.batch,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "elapsed": 0.0,
            "error": None,
            "hints": [],
            "image_id": None,
            "image_ids": [],
            "prompt_id": None,
            "cancel_requested": False,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
            for stale in self._order[:-KEEP_JOBS]:
                self._jobs.pop(stale, None)
            self._order = self._order[-KEEP_JOBS:]
        self.bus.publish("image.queued", {"job": self.public(job)})
        threading.Thread(target=self._run, args=(job, profile, template, request), name=f"image-{job_id}", daemon=True).start()
        return self.public(job)

    def _run(self, job: dict[str, Any], profile: catalog.ModelProfile, template: templates.Template, request: GenerationRequest) -> None:
        client = self.client
        try:
            graph = self.graph_for(profile, template, request)
            missing = client.missing_nodes(template.classes(graph))
            if missing:
                raise StudioError("This ComfyUI does not have: " + ", ".join(missing))
            prompt_id = client.queue(graph)
            with self._lock:
                job.update(status="running", phase="waiting", prompt_id=prompt_id, started_at=time.time())
            self.bus.publish("image.started", {"job": self.public(job)})
            entry = self._wait(job, client, prompt_id)
            reason = status_message(entry)
            if reason:
                raise StudioError(reason)
            images = produced_images(entry)
            if not images:
                raise StudioError(tr("The engine finished without producing a picture."))
            stored: list[str] = []
            for index, image in enumerate(images):
                data = client.view(image["filename"], image["subfolder"], image["type"])
                stored.append(self._store(job, profile, template, request, data, prompt_id, index))
            with self._lock:
                job.update(status="done", phase="done", image_id=stored[0], image_ids=stored, finished_at=time.time())
            self.bus.publish("image.done", {"job": self.public(job)})
        except ComfyError as exc:
            self._fail(job, str(exc))
        except StudioError as exc:
            self._fail(job, str(exc))
        except Exception as exc:
            self._fail(job, f"{type(exc).__name__}: {exc}")

    def _wait(self, job: dict[str, Any], client: ComfyClient, prompt_id: str) -> dict[str, Any]:
        deadline = time.time() + float(self.config.timeout_seconds)
        while time.time() < deadline:
            with self._lock:
                if job["cancel_requested"]:
                    client.interrupt()
                    job.update(status="cancelled", phase="cancelled", finished_at=time.time())
                    raise StudioError("Cancelled.")
                job["elapsed"] = round(time.time() - (job["started_at"] or time.time()), 1)
                if job["phase"] != "running":
                    job["phase"] = "running"
            entry = client.history(prompt_id)
            if entry:
                return entry
            self.bus.publish(
                "image.progress",
                {"job_id": prompt_id, "elapsed": job["elapsed"], "queue": self._queue_safe(client)},
                keep=False,
            )
            time.sleep(0.6)
        client.interrupt()
        raise StudioError(f"The engine did not finish within {int(self.config.timeout_seconds)} seconds.")

    @staticmethod
    def _queue_safe(client: ComfyClient) -> dict[str, Any] | None:
        try:
            return client.queue_state()
        except ComfyError:
            return None

    def _fail(self, job: dict[str, Any], message: str) -> None:
        with self._lock:
            job.update(
                status="failed",
                phase="failed",
                error=message,
                finished_at=time.time(),
                hints=plan_mod.out_of_memory_hints(message),
            )
        self.bus.publish("image.failed", {"job": self.public(job)})

    def _store(
        self,
        job: dict[str, Any],
        profile: catalog.ModelProfile,
        template: templates.Template,
        request: GenerationRequest,
        data: bytes,
        prompt_id: str,
        index: int = 0,
    ) -> str:
        image_id = job["id"] if index == 0 else f"{job['id']}-{index + 1}"
        folder = paths.ensure(self.images_dir / time.strftime("%Y-%m"))
        name = f"{image_id}.png"
        (folder / name).write_bytes(data)
        self.db.execute(
            "INSERT INTO image_generation(id, created_at, model_key, template, prompt, negative, width, height, "
            "seed, steps, cfg, sampler, scheduler, loras, file_name, bytes, elapsed_seconds, prompt_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                image_id,
                utcnow(),
                profile.key,
                template.key,
                request.prompt,
                request.negative,
                request.width,
                request.height,
                request.seed,
                request.steps,
                request.cfg,
                request.sampler,
                request.scheduler,
                _json(request.loras),
                str(folder / name),
                len(data),
                job.get("elapsed") or 0.0,
                prompt_id,
                "done",
            ),
        )
        return image_id

    # -- jobs ---------------------------------------------------------------
    @staticmethod
    def public(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if key != "cancel_requested"}

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.public(self._jobs[job_id]) for job_id in reversed(self._order) if job_id in self._jobs]

    def job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise StudioError(f"There is no job called {job_id!r}.")
            return self.public(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise StudioError(f"There is no job called {job_id!r}.")
            job["cancel_requested"] = True
        with contextlib.suppress(ComfyError):
            self.client.interrupt()
        return self.public(job)

    def free(self) -> bool:
        """Unload what the engine holds, so LM Studio can have the whole card back."""
        try:
            self.client.free_memory()
        except ComfyError:
            return False
        return True

    # -- the pictures -------------------------------------------------------
    def gallery(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, created_at, model_key, template, prompt, negative, width, height, seed, steps, cfg, "
            "sampler, scheduler, loras, bytes, elapsed_seconds FROM image_generation "
            "ORDER BY created_at DESC LIMIT ?",
            (max(1, min(500, limit)),),
        )
        for row in rows:
            row["loras"] = _loads(row.get("loras"))
        return rows

    def image_path(self, image_id: str) -> Path:
        row = self.db.query_one("SELECT file_name FROM image_generation WHERE id = ?", (image_id,))
        if row is None:
            raise StudioError("That picture is not in the gallery.")
        path = Path(str(row["file_name"]))
        if not path.exists():
            raise StudioError("The file of that picture is no longer on disk.")
        return path

    def forget(self, image_id: str) -> bool:
        """Remove a picture from the gallery. The file goes too: it is the app's own copy."""
        row = self.db.query_one("SELECT file_name FROM image_generation WHERE id = ?", (image_id,))
        if row is None:
            return False
        path = Path(str(row["file_name"]))
        path.unlink(missing_ok=True)
        self.db.execute("DELETE FROM image_generation WHERE id = ?", (image_id,))
        return True


def _with_prefix(prefix: str, prompt: str) -> str:
    """The model's own opening words, once: a prompt that already starts with them is left alone."""
    if not prefix or prompt.lstrip().lower().startswith(prefix.strip().lower()):
        return prompt
    return f"{prefix}{prompt}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Any) -> Any:
    if not value:
        return []
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []
