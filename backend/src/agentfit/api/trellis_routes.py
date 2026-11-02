"""Token-protected local TRELLIS studio."""
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agentfit.i18n import tr
from agentfit.imagegen.trellis import TrellisRequest, TrellisStudio, WebExportRequest
from agentfit.three_d_coder import ThreeDCodeStudio


class TrellisUpload(BaseModel):
    data: str = Field(max_length=28_000_000)


class TrellisInstall(BaseModel):
    distribution: str = Field(min_length=1, max_length=200)


class TrellisSave(BaseModel):
    path: str = Field(min_length=1, max_length=32767)
    variant: str = Field(default="original", pattern="^(original|web)$")


class CodeRequest(BaseModel):
    model_key: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=12000)


class CodeRepair(BaseModel):
    error: str = Field(default="", max_length=2000)


def install_routes(app: FastAPI, guard: list[Any], studio: TrellisStudio | None = None,
                   coder: ThreeDCodeStudio | None = None) -> None:
    studio = studio or TrellisStudio()

    @app.get("/api/3d/coder/models", dependencies=guard)
    def coder_models() -> dict[str, Any]:
        return {"models": coder.models() if coder else []}

    @app.get("/api/3d/coder/scenes", dependencies=guard)
    def coder_scenes() -> dict[str, Any]:
        return {"scenes": coder.gallery() if coder else []}

    @app.post("/api/3d/coder/generate", dependencies=guard)
    def coder_generate(body: CodeRequest) -> dict[str, Any]:
        if coder is None:
            raise HTTPException(503)
        try:
            return coder.generate(body.model_key, body.prompt)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/3d/coder/{scene_id}/preview", dependencies=guard)
    def coder_preview(scene_id: str) -> dict[str, str]:
        if coder is None:
            raise HTTPException(503)
        try:
            return {"url": coder.preview(scene_id)}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/3d/coder/{scene_id}", dependencies=guard)
    def coder_delete(scene_id: str) -> dict[str, bool]:
        if coder is None:
            raise HTTPException(503)
        try:
            coder.forget(scene_id)
            return {"deleted": True}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/3d/coder/{scene_id}/repair", dependencies=guard)
    def coder_repair(scene_id: str, body: CodeRepair | None = None) -> dict[str, Any]:
        if coder is None:
            raise HTTPException(503)
        try:
            return coder.repair(scene_id, body.error if body else "")
        except (KeyError, OSError) as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/3d/coder/{scene_id}/save", dependencies=guard)
    def coder_save(scene_id: str, body: TrellisSave) -> dict[str, Any]:
        if coder is None:
            raise HTTPException(503)
        target = Path(body.path)
        if not target.is_absolute() or not target.parent.is_dir() or target.suffix.lower() != ".html":
            raise HTTPException(400, tr("Choose an HTML filename in an existing folder."))
        try:
            target.write_text(coder.standalone(scene_id), encoding="utf-8")
            return {"path": str(target), "bytes": target.stat().st_size}
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/3d/status", dependencies=guard)
    def trellis_status() -> dict[str, Any]:
        return studio.status()

    @app.post("/api/3d/install", dependencies=guard)
    def trellis_install(body: TrellisInstall) -> dict[str, Any]:
        try:
            return studio.install(body.distribution)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/3d/upload", dependencies=guard)
    def trellis_upload(body: TrellisUpload) -> dict[str, str]:
        try:
            return {"image": studio.upload(body.data)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/3d/generate", dependencies=guard)
    def trellis_generate(body: TrellisRequest) -> dict[str, Any]:
        try:
            return studio.generate(body)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/3d/gallery", dependencies=guard)
    def trellis_gallery() -> dict[str, Any]:
        return {"models": studio.gallery()}

    @app.get("/api/3d/gallery/{model_id}/file", dependencies=guard)
    def trellis_file(model_id: str, variant: str = "original") -> FileResponse:
        try:
            return FileResponse(studio.file(model_id, variant), media_type="model/gltf-binary", filename=f"{model_id}.glb")
        except KeyError as exc:
            raise HTTPException(404, tr("3D model not found.")) from exc

    @app.post("/api/3d/gallery/{model_id}/save", dependencies=guard)
    def trellis_save(model_id: str, body: TrellisSave) -> dict[str, Any]:
        try:
            source = studio.file(model_id, body.variant)
        except KeyError as exc:
            raise HTTPException(404, tr("3D model not found.")) from exc
        target = Path(body.path)
        if not target.is_absolute() or not target.parent.is_dir() or target.suffix.lower() != ".glb":
            raise HTTPException(400, tr("Choose a GLB filename in an existing folder."))
        try:
            if target.resolve() != source.resolve():
                shutil.copyfile(source, target)
            return {"path": str(target), "bytes": target.stat().st_size}
        except OSError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/3d/gallery/{model_id}", dependencies=guard)
    def trellis_delete(model_id: str) -> dict[str, bool]:
        try:
            studio.forget(model_id)
            return {"deleted": True}
        except KeyError as exc:
            raise HTTPException(404, tr("3D model not found.")) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/3d/gallery/{model_id}/restore", dependencies=guard)
    def trellis_restore(model_id: str) -> dict[str, bool]:
        try:
            studio.restore(model_id)
            return {"restored": True}
        except KeyError as exc:
            raise HTTPException(404, tr("3D model not found.")) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/3d/gallery/{model_id}/web", dependencies=guard)
    def trellis_web(model_id: str, body: WebExportRequest) -> dict[str, Any]:
        try:
            return studio.export_web(model_id, body)
        except KeyError as exc:
            raise HTTPException(404, tr("3D model not found.")) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
