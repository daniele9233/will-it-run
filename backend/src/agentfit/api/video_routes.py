"""Authenticated endpoints for the embedded video studio."""
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agentfit.app import AppServices
from agentfit.i18n import tr
from agentfit.imagegen.video import VideoRequest, VideoStudio
from agentfit.imagegen.video_collections import VideoCollectionStore
from agentfit.imagegen.video_prompts import VideoPromptInput, VideoPromptStore
from agentfit.imagegen.video_upscale import UpscaleRequest, VideoUpscaler


class VideoSaveBody(BaseModel):
    path: str = Field(min_length=1, max_length=32767)


def _save_mp4(source: Path, path: str) -> dict[str, Any]:
    """Copy an MP4 to the destination chosen in the desktop Save dialog."""
    target = Path(path)
    if not target.is_absolute() or not target.parent.is_dir():
        raise HTTPException(400, tr("That place does not exist: {path}", path=path))
    if not target.suffix:
        target = target.with_suffix(".mp4")
    if target.suffix.lower() != ".mp4":
        raise HTTPException(400, tr("Choose an MP4 filename."))
    try:
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return {"path": str(target), "bytes": target.stat().st_size}
    except OSError as exc:
        raise HTTPException(400, tr("The file could not be saved: {error}", error=str(exc))) from exc


def install_routes(app: FastAPI, svc: AppServices, guard: list[Any]) -> None:
    studio = VideoStudio(svc.images)
    prompts = VideoPromptStore(studio.directory / "prompts")
    collections = VideoCollectionStore(studio.directory / "collections")
    upscaler = VideoUpscaler(studio)

    @app.get("/api/videos/upscale/status", dependencies=guard)
    def upscale_status() -> dict[str, Any]:
        return upscaler.status()

    @app.post("/api/videos/upscale/setup", dependencies=guard)
    def upscale_setup() -> dict[str, Any]:
        return upscaler.setup.start()

    @app.post("/api/videos/upscale/setup/cancel", dependencies=guard)
    def upscale_setup_cancel() -> dict[str, Any]:
        return upscaler.setup.cancel()

    @app.post("/api/videos/upscale", dependencies=guard)
    def upscale_generate(body: UpscaleRequest) -> dict[str, Any]:
        try:
            return upscaler.generate(body)
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/videos/upscale/gallery", dependencies=guard)
    def upscale_gallery() -> dict[str, Any]:
        return {"videos": upscaler.gallery()}

    @app.get("/api/videos/upscale/gallery/{video_id}/file", dependencies=guard)
    def upscale_file(video_id: str) -> FileResponse:
        try:
            return FileResponse(upscaler.file(video_id), media_type="video/mp4", filename=f"RelayVSR-{video_id}.mp4")
        except KeyError as exc:
            raise HTTPException(404, tr("Upscaled video not found.")) from exc

    @app.delete("/api/videos/upscale/gallery/{video_id}", dependencies=guard)
    def upscale_delete(video_id: str) -> dict[str, bool]:
        try:
            path = upscaler.file(video_id)
            path.with_suffix(".json").unlink(missing_ok=True)
            path.unlink()
            return {"deleted": True}
        except KeyError as exc:
            raise HTTPException(404, tr("Upscaled video not found.")) from exc

    @app.post("/api/videos/upscale/gallery/{video_id}/save", dependencies=guard)
    def upscale_save(video_id: str, body: VideoSaveBody) -> dict[str, Any]:
        try:
            source = upscaler.file(video_id)
            target = Path(body.path)
            if not target.is_absolute() or not target.parent.is_dir():
                raise ValueError(tr("That place does not exist: {path}", path=body.path))
            if not target.suffix:
                target = target.with_suffix(".mp4")
            if target.suffix.lower() != ".mp4":
                raise ValueError(tr("Choose an MP4 filename."))
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            return {"path": str(target), "bytes": target.stat().st_size}
        except KeyError as exc:
            raise HTTPException(404, tr("Upscaled video not found.")) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/videos/models/{model_key}/setup", dependencies=guard)
    def video_model_setup(model_key: str) -> dict[str, Any]:
        try:
            return studio.setup(model_key).start()
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/videos/models/{model_key}/setup/cancel", dependencies=guard)
    def video_model_setup_cancel(model_key: str) -> dict[str, Any]:
        try:
            return studio.setup(model_key).cancel()
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/videos/prompts", dependencies=guard)
    def videos_prompts() -> dict[str, Any]:
        return {"prompts": prompts.list()}

    @app.post("/api/videos/prompts", dependencies=guard)
    def videos_prompt_save(body: VideoPromptInput) -> dict[str, Any]:
        try:
            return prompts.save(body)
        except OSError as exc:
            raise HTTPException(400, tr("Could not save video prompts: {error}", error=str(exc))) from exc

    @app.delete("/api/videos/prompts/{prompt_id}", dependencies=guard)
    def videos_prompt_delete(prompt_id: str) -> dict[str, bool]:
        try:
            prompts.delete(prompt_id)
            return {"deleted": True}
        except KeyError as exc:
            raise HTTPException(404, tr("Saved video prompt not found.")) from exc
        except OSError as exc:
            raise HTTPException(400, tr("Could not delete video prompts: {error}", error=str(exc))) from exc

    @app.get("/api/videos/status", dependencies=guard)
    def videos_status() -> dict[str, Any]:
        return studio.status()

    @app.post("/api/videos/generate", dependencies=guard)
    def videos_generate(body: VideoRequest) -> dict[str, Any]:
        try:
            return studio.generate(body)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/videos/jobs/{job_id}/cancel", dependencies=guard)
    def videos_cancel(job_id: str) -> dict[str, Any]:
        try:
            return studio.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(404, "Unknown video job") from exc

    @app.get("/api/videos/gallery", dependencies=guard)
    def videos_gallery() -> dict[str, Any]:
        return {"videos": studio.gallery()}

    @app.get("/api/videos/gallery/{video_id}/file", dependencies=guard)
    def videos_file(video_id: str) -> FileResponse:
        try:
            return FileResponse(studio.file(video_id), media_type="video/mp4", filename=f"{video_id}.mp4")
        except KeyError as exc:
            raise HTTPException(404, "Unknown video") from exc

    @app.delete("/api/videos/gallery/{video_id}", dependencies=guard)
    def videos_delete(video_id: str) -> dict[str, bool]:
        try:
            path = studio.file(video_id)
            path.with_suffix(".json").unlink(missing_ok=True)
            path.unlink()
            return {"deleted": True}
        except KeyError as exc:
            raise HTTPException(404, "Unknown video") from exc

    @app.post("/api/videos/gallery/{video_id}/save", dependencies=guard)
    def videos_save(video_id: str, body: VideoSaveBody) -> dict[str, Any]:
        """Copy the MP4 to the destination chosen in the desktop Save dialog."""
        try:
            source = studio.file(video_id)
        except KeyError as exc:
            raise HTTPException(404, "Unknown video") from exc
        return _save_mp4(source, body.path)

    # -- prompt collections: finished videos with every prompt that made them ----------------
    @app.get("/api/videos/collections", dependencies=guard)
    def videos_collections() -> dict[str, Any]:
        return {"collections": collections.list()}

    @app.get("/api/videos/collections/{collection_id}/entries/{entry_id}/file", dependencies=guard)
    def videos_collection_file(collection_id: str, entry_id: str) -> FileResponse:
        try:
            return FileResponse(collections.video(collection_id, entry_id), media_type="video/mp4", filename=f"{entry_id}.mp4")
        except KeyError as exc:
            raise HTTPException(404, tr("This video of the collection is not on disk.")) from exc

    @app.post("/api/videos/collections/{collection_id}/entries/{entry_id}/save", dependencies=guard)
    def videos_collection_save(collection_id: str, entry_id: str, body: VideoSaveBody) -> dict[str, Any]:
        try:
            source = collections.video(collection_id, entry_id)
        except KeyError as exc:
            raise HTTPException(404, tr("This video of the collection is not on disk.")) from exc
        return _save_mp4(source, body.path)

    @app.get("/api/videos/collections/{collection_id}/entries/{entry_id}/keyframes/{index}/file", dependencies=guard)
    def videos_collection_keyframe(collection_id: str, entry_id: str, index: int) -> FileResponse:
        try:
            return FileResponse(collections.keyframe(collection_id, entry_id, index))
        except KeyError as exc:
            raise HTTPException(404, tr("This keyframe picture is not on disk.")) from exc

    @app.post("/api/videos/collections/{collection_id}/entries/{entry_id}/use", dependencies=guard)
    def videos_collection_use(collection_id: str, entry_id: str) -> dict[str, Any]:
        """Hand the entry's keyframe pictures to the engine, so the generator can run the same recipe again."""
        try:
            entry = collections.entry(collection_id, entry_id)
            paths = [collections.keyframe(collection_id, entry_id, i) for i in range(len(entry.keyframes))]
        except KeyError as exc:
            raise HTTPException(404, tr("This keyframe picture is not on disk.")) from exc
        names = [svc.images.client.upload(path.read_bytes(), f"collection-{collection_id}-{path.name}") for path in paths]
        return {"images": names}
