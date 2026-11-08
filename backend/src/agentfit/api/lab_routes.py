"""Additional local 3D engines; uses the existing protected API and queue."""
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agentfit.i18n import tr
from agentfit.imagegen.lab import LabRequest, ThreeDLab


class LabAction(BaseModel):
    engine: Literal["pixal3d", "idol", "triposplat", "supra-tex", "effigy", "opencartridge", "scenario"]
    action: Literal["install", "launch", "stop"]


class LabImport(BaseModel):
    engine: Literal["pixal3d", "idol", "triposplat", "supra-tex", "effigy", "opencartridge", "scenario"]
    data: str = Field(max_length=140_000_000)


def install_routes(app: FastAPI, guard: list[Any], lab: ThreeDLab) -> None:
    @app.get("/api/3d/lab/status", dependencies=guard)
    def lab_status() -> dict[str, Any]:
        return lab.status()

    @app.post("/api/3d/lab/action", dependencies=guard)
    def lab_action(body: LabAction) -> dict[str, Any]:
        try:
            return lab.action(body.engine, body.action)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/3d/lab/generate", dependencies=guard)
    def lab_generate(body: LabRequest) -> dict[str, Any]:
        try:
            return lab.generate(body)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/3d/lab/splats", dependencies=guard)
    def lab_splats() -> dict[str, Any]:
        return {"items": lab.splats()}

    @app.get("/api/3d/lab/splats/{identifier}/file", dependencies=guard)
    def lab_splat_file(identifier: str) -> FileResponse:
        try:
            return FileResponse(lab.splat_file(identifier), media_type="application/octet-stream", filename=f"{identifier}.ply")
        except KeyError as exc:
            raise HTTPException(404, tr("Gaussian splat not found.")) from exc

    @app.post("/api/3d/lab/import", dependencies=guard)
    def lab_import(body: LabImport) -> dict[str, str]:
        try:
            return lab.import_glb(body.data, body.engine)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
