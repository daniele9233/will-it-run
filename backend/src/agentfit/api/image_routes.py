"""HTTP routes for the image studio: the engine, the models, the jobs and the gallery.

Every route is behind the same token guard as the rest of the API. A generation returns
at once with a job; the picture arrives in ``/api/images/gallery`` and on the event bus.
"""

from __future__ import annotations

import base64
import binascii
import os
import subprocess
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentfit.app import AppServices
from agentfit.imagegen import GenerationRequest, StudioError, importer
from agentfit.imagegen import templates as template_mod
from agentfit.imagegen.comfy import ComfyError, ComfyUnavailable
from agentfit.imagegen.components import ComponentError, DiskFull
from agentfit.imagegen.engine import EngineError

MAX_UPLOAD_BYTES = 40 * 1024 * 1024


class LoraBody(BaseModel):
    name: str
    weight: float = 0.8
    weight_clip: float | None = None


class CheckBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_key: str
    template: str = ""
    width: int = 1024
    height: int = 1024
    image: str = ""


class GenerateBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_key: str
    template: str = ""
    prompt: str = ""
    negative: str = ""
    width: int = 1024
    height: int = 1024
    steps: int | None = Field(default=None, ge=1, le=200)
    cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    sampler: str | None = None
    scheduler: str | None = None
    seed: int = -1
    denoise: float | None = Field(default=None, ge=0.0, le=1.0)
    loras: list[LoraBody] = Field(default_factory=list)
    image: str = ""
    images: list[str] = Field(default_factory=list)
    resolution: int | None = Field(default=None, ge=512, le=2048)
    hires_scale: float | None = Field(default=None, ge=1.0, le=2.0)
    hires_denoise: float | None = Field(default=None, ge=0.0, le=1.0)
    batch: int = Field(default=1, ge=1, le=4)

    def to_request(self) -> GenerationRequest:
        return GenerationRequest(
            model_key=self.model_key,
            template_key=self.template,
            prompt=self.prompt,
            negative=self.negative,
            width=self.width,
            height=self.height,
            steps=self.steps,
            cfg=self.cfg,
            sampler=self.sampler,
            scheduler=self.scheduler,
            seed=self.seed,
            denoise=self.denoise,
            loras=[lora.model_dump() for lora in self.loras],
            image=self.image,
            images=list(self.images),
            resolution=self.resolution,
            hires_scale=self.hires_scale,
            hires_denoise=self.hires_denoise,
            batch=self.batch,
        )


class UploadBody(BaseModel):
    filename: str = "upload.png"
    #: The file, base64. A data URL prefix ("data:image/png;base64,") is accepted.
    data: str


class DownloadBody(BaseModel):
    components: list[str] = Field(default_factory=list)


class ImportScanBody(BaseModel):
    directory: str
    recursive: bool = False


class ImportApplyBody(BaseModel):
    directory: str
    components: list[str] = Field(default_factory=list)
    mode: Literal["copy", "move", "existing"] = "copy"
    recursive: bool = False
    overwrite: bool = False


class LoraConvertBody(BaseModel):
    component_id: str
    base_component_id: str = ""


def install_routes(app: FastAPI, svc: AppServices, guard: list[Any]) -> None:
    studio = svc.images

    @app.exception_handler(StudioError)
    async def _studio_error(_: Request, exc: StudioError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "kind": "image"})

    @app.exception_handler(ComfyUnavailable)
    async def _comfy_offline(_: Request, exc: ComfyUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "kind": "comfy_offline"})

    @app.exception_handler(ComfyError)
    async def _comfy_error(_: Request, exc: ComfyError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc), "kind": "comfy"})

    @app.exception_handler(importer.ImportError_)
    async def _import_error(_: Request, exc: importer.ImportError_) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "kind": "import"})

    @app.exception_handler(EngineError)
    async def _engine_error(_: Request, exc: EngineError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "kind": "engine"})

    @app.exception_handler(DiskFull)
    async def _disk_full(_: Request, exc: DiskFull) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "kind": "disk"})

    @app.exception_handler(ComponentError)
    async def _component_error(_: Request, exc: ComponentError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "kind": "component"})

    # -- the engine ---------------------------------------------------------

    # -- importing what is already on disk ---------------------------------
    @app.post("/api/images/import/scan", dependencies=guard)
    def images_import_scan(body: ImportScanBody) -> dict[str, Any]:
        return importer.scan(Path(body.directory), studio.components, recursive=body.recursive)

    @app.post("/api/images/import/apply", dependencies=guard)
    def images_import_apply(body: ImportApplyBody) -> dict[str, Any]:
        result = importer.apply(
            Path(body.directory),
            body.components,
            mode=body.mode,
            store=studio.components,
            overwrite=body.overwrite,
        )
        return result

    @app.post("/api/images/import/convert-lora", dependencies=guard)
    def images_import_convert_lora(body: LoraConvertBody) -> dict[str, Any]:
        """Rewrite a LoRA's tensor names so ComfyUI's loader can read them, then prove the pairing."""
        from agentfit.imagegen.components import definition

        lora = definition(body.component_id)
        if lora is None:
            raise HTTPException(status_code=404, detail="Unknown LoRA component.")
        header = importer.read_header(Path(body.component_id) and _source_of(studio, body.component_id))
        report = importer.lora_format(header)
        if report["namespace"] != "transformer" and report["comfy_ready"]:
            return {"converted": False, "reason": "The file is already in ComfyUI's namespace.", "format": report}
        if report["namespace"] != "transformer":
            return {
                "converted": False,
                "reason": report.get("declared_conversion") or "No namespace-only conversion is known for this file.",
                "format": report,
            }
        source = _source_of(studio, body.component_id)
        target = _target_lora(studio, source, suffix="-comfy")
        result = importer.convert_lora(source, target, prefix_from="transformer.", prefix_to="diffusion_model.")
        base_component = body.base_component_id or ""
        verification = None
        if base_component:
            base_path = _source_of(studio, base_component)
            verification = importer.verify_against_base(target, base_path)
        studio.components.register(
            lora,
            path=target,
            size_bytes=target.stat().st_size,
            sha256=None,
            note="converted from " + source.name,
        )
        studio.components.invalidate()
        return {"converted": True, **result, "verification": verification, "format": report}

    @app.get("/api/images/engine", dependencies=guard)
    def images_engine(refresh: bool = False) -> dict[str, Any]:
        return studio.engine.status(refresh=refresh)

    @app.post("/api/images/engine/start", dependencies=guard)
    def images_engine_start() -> dict[str, Any]:
        result = studio.engine.start()
        studio.components.invalidate()
        return result

    @app.post("/api/images/engine/stop", dependencies=guard)
    def images_engine_stop() -> dict[str, Any]:
        result = studio.engine.stop()
        studio.components.invalidate()
        return result

    # -- components and downloads ------------------------------------------
    @app.get("/api/images/components", dependencies=guard)
    def images_components() -> dict[str, Any]:
        return {"components": studio.components.rows()}

    @app.post("/api/images/components/download", dependencies=guard)
    def images_components_download(body: DownloadBody) -> dict[str, Any]:
        return {"started": studio.components.start_download(body.components)}

    @app.delete("/api/images/components/{component_id}", dependencies=guard)
    def images_component_remove(component_id: str) -> dict[str, Any]:
        return studio.components.remove(component_id)

    @app.get("/api/images/downloads", dependencies=guard)
    def images_downloads() -> dict[str, Any]:
        return {"downloads": studio.components.downloads()}

    @app.post("/api/images/downloads/{component_id}/cancel", dependencies=guard)
    def images_download_cancel(component_id: str) -> dict[str, Any]:
        return studio.components.cancel_download(component_id)

    @app.get("/api/images/models/{model_key}/plan", dependencies=guard)
    def images_model_plan(model_key: str) -> dict[str, Any]:
        return studio.plan(model_key)

    @app.post("/api/images/models/{model_key}/download", dependencies=guard)
    def images_model_download(model_key: str) -> dict[str, Any]:
        plan = studio.components.plan(model_key)
        wanted = [item["id"] for item in plan["missing"] if item.get("downloadable")]
        skipped = [item for item in plan["missing"] if not item.get("downloadable")]
        started = studio.components.start_download(wanted) if wanted else []
        return {"started": started, "skipped": skipped, "plan": plan}

    @app.get("/api/images/status", dependencies=guard)
    def images_status() -> dict[str, Any]:
        status = studio.status()
        status["gallery"] = len(studio.gallery(limit=500))
        return status

    @app.get("/api/images/models", dependencies=guard)
    def images_models() -> dict[str, Any]:
        return {"models": studio.models()}

    @app.get("/api/images/templates", dependencies=guard)
    def images_templates() -> dict[str, Any]:
        return {"templates": studio.template_list()}

    @app.get("/api/images/loras", dependencies=guard)
    def images_loras() -> dict[str, Any]:
        try:
            names = studio.client.models("loras")
        except ComfyError:
            names = []
        return {"loras": names}

    @app.post("/api/images/check", dependencies=guard)
    def images_check(body: CheckBody) -> dict[str, Any]:
        request = GenerationRequest(
            model_key=body.model_key, template_key=body.template, width=body.width, height=body.height, image=body.image
        )
        return studio.check(body.model_key, body.template, request)

    @app.post("/api/images/generate", dependencies=guard)
    def images_generate(body: GenerateBody) -> dict[str, Any]:
        return {"job": studio.generate(body.to_request())}

    @app.get("/api/images/jobs", dependencies=guard)
    def images_jobs() -> dict[str, Any]:
        return {"jobs": studio.jobs()}

    @app.get("/api/images/jobs/{job_id}", dependencies=guard)
    def images_job(job_id: str) -> dict[str, Any]:
        return {"job": studio.job(job_id)}

    @app.post("/api/images/jobs/{job_id}/cancel", dependencies=guard)
    def images_cancel(job_id: str) -> dict[str, Any]:
        return {"job": studio.cancel(job_id)}

    @app.get("/api/images/gallery", dependencies=guard)
    def images_gallery(limit: int = Query(default=60, ge=1, le=500)) -> dict[str, Any]:
        return {"images": studio.gallery(limit=limit)}

    @app.get("/api/images/gallery/{image_id}/file", dependencies=guard)
    def images_file(image_id: str) -> FileResponse:
        path = studio.image_path(image_id)
        return FileResponse(path, media_type="image/png", filename=path.name)

    @app.delete("/api/images/gallery/{image_id}", dependencies=guard)
    def images_forget(image_id: str) -> dict[str, Any]:
        return {"deleted": studio.forget(image_id)}

    @app.post("/api/images/upload", dependencies=guard)
    def images_upload(body: UploadBody) -> dict[str, Any]:
        raw = body.data.split(",", 1)[1] if body.data.startswith("data:") and "," in body.data else body.data
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail="The picture could not be read: it is not base64.") from exc
        if not data:
            raise HTTPException(status_code=400, detail="The picture is empty.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="The picture is larger than 40 MB.")
        name = Path(body.filename or "upload.png").name
        return {"name": studio.client.upload(data, name)}

    @app.post("/api/images/free", dependencies=guard)
    def images_free() -> dict[str, Any]:
        return {"freed": studio.free()}

    @app.post("/api/images/open-folder", dependencies=guard)
    def images_open_folder() -> dict[str, Any]:
        folder = studio.images_dir
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            subprocess.Popen(["explorer", str(folder)])
        return {"folder": str(folder)}

    @app.get("/api/images/templates/{key}", dependencies=guard)
    def images_template(key: str) -> dict[str, Any]:
        try:
            return template_mod.by_key(key).to_json()
        except template_mod.TemplateError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def _source_of(studio: Any, component_id: str) -> Path:
    row = studio.components.component_state(component_id)
    path = row.get("path")
    if not path:
        raise HTTPException(status_code=409, detail=f"{row.get('name', component_id)} is not on disk yet.")
    return Path(str(path))


def _target_lora(studio: Any, source: Path, *, suffix: str) -> Path:
    folder = studio.components.models_dir
    if folder is None:
        raise HTTPException(status_code=409, detail="ComfyUI was not found on this PC.")
    return folder / "loras" / f"{source.stem}{suffix}.safetensors"
