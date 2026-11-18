"""Local keyframe video studio (MiniMax H3 FL2VA, LTX-2.5), sharing the Images engine and keeping MP4s in app data."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import uuid
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, model_validator

from agentfit import paths
from agentfit.i18n import tr
from agentfit.imagegen import ltx25, minimax_h3, video_finish
from agentfit.imagegen import video_catalog as catalog
from agentfit.imagegen.comfy import ComfyUnavailable, status_message
from agentfit.imagegen.keyframes import default_times, frame_indices
from agentfit.imagegen.video_progress import ProgressStream, VideoProgress
from agentfit.imagegen.video_setup import KeyframeModelSetup, asset_path

# How long a running ComfyUI may leave status requests unanswered before the job fails.
ENGINE_SILENCE_LIMIT = 900.0


def _engine_busy(exc: ComfyUnavailable) -> bool:
    """A read timeout means the engine is alive but slow; a refused or failed connection means it is gone."""
    cause = exc.__cause__
    return isinstance(cause, httpx.TimeoutException) and not isinstance(cause, httpx.ConnectTimeout)


class VideoRequest(BaseModel):
    model_key: str = catalog.DEFAULT
    image: str = Field(default="", max_length=500)
    images: list[str] = Field(default_factory=list, max_length=9)
    # Seconds on the output timeline, one per picture; empty spreads them from 0 to the end.
    keyframe_times: list[float] = Field(default_factory=list, max_length=9)
    end_image: str = Field(default="", max_length=500)
    prompt: str = Field(min_length=1, max_length=10000)
    negative: str = Field(default="", max_length=10000)
    width: int = Field(default=1344, ge=256, le=1408)
    height: int = Field(default=768, ge=256, le=1408)
    frames: int = Field(default=124, ge=5, le=362)
    duration_seconds: int | None = Field(default=None, ge=5, le=15)
    motion_frames: int | None = Field(default=None, ge=5, le=361)
    fps: int = Field(default=24, ge=8, le=30)
    steps: int = Field(default=20, ge=4, le=60)
    cfg: float = Field(default=1.0, ge=1, le=10)
    seed: int = Field(default=-1, ge=-1, le=2147483647)
    turbo: bool = False

    def seconds(self) -> float:
        return float(self.duration_seconds) if self.duration_seconds is not None else self.frames / self.fps

    def times(self) -> list[float]:
        return list(self.keyframe_times) or default_times(len(self.images), self.seconds())

    @model_validator(mode="after")
    def valid(self) -> VideoRequest:
        selected = catalog.profile(self.model_key)
        if self.images:
            if len(self.images) > selected.max_images:
                raise ValueError(tr("This video model accepts at most {count} keyframe pictures.").format(count=selected.max_images))
            if self.image and self.image != self.images[0]:
                raise ValueError(tr("The first keyframe picture was given twice with different files."))
            self.image = self.images[0]
        elif self.image:
            self.images = [self.image]
        if not self.image or not self.prompt.strip():
            raise ValueError(tr("Choose at least one keyframe picture and describe the movement."))
        if self.end_image or self.negative.strip() or self.motion_frames is not None:
            raise ValueError(tr("Keyframe video models take ordered keyframe pictures only, without a separate ending picture, negative prompt or slow motion."))
        if self.turbo and selected.turbo_steps is None:
            raise ValueError(tr("This video model has no Turbo recipe."))
        steps = selected.turbo_steps if self.turbo and selected.turbo_steps else selected.steps
        for name, value in (("fps", selected.fps), ("steps", steps), ("cfg", selected.cfg)):
            if name not in self.model_fields_set:
                setattr(self, name, value)
        if (self.steps, self.cfg, self.fps) != (steps, selected.cfg, selected.fps):
            raise ValueError(tr("This video model uses {steps} steps, CFG {cfg} and {fps} FPS.").format(
                steps=steps, cfg=selected.cfg, fps=selected.fps))
        for name in ("width", "height"):
            if name not in self.model_fields_set:
                setattr(self, name, getattr(selected, name))
        if self.duration_seconds is not None:
            if self.duration_seconds < selected.min_seconds:
                raise ValueError(tr("This video model needs at least {seconds} seconds.").format(seconds=selected.min_seconds))
            self.frames = selected.frames_for(self.duration_seconds)
        elif "frames" not in self.model_fields_set:
            self.frames = selected.frames_for(selected.min_seconds)
        if (self.frames - selected.frame_offset) % selected.frame_stride:
            raise ValueError(tr("Video frames must follow {stride}n+{offset} for this model.").format(
                stride=selected.frame_stride, offset=selected.frame_offset))
        if self.width % selected.size_multiple or self.height % selected.size_multiple:
            raise ValueError(tr("This video model needs dimensions divisible by {multiple}.").format(multiple=selected.size_multiple))
        if self.keyframe_times:
            if len(self.keyframe_times) != len(self.images):
                raise ValueError(tr("Give one time for each keyframe picture."))
            if any(t < 0 or t > self.seconds() for t in self.keyframe_times):
                raise ValueError(tr("Keyframe times must be between 0 and {seconds} seconds.").format(seconds=f"{self.seconds():g}"))
        indices = frame_indices(self.times(), self.seconds(), self.frames)
        if any(b <= a for a, b in pairwise(indices)):
            raise ValueError(tr("Keyframe times must increase and land on different frames."))
        for image in self.images:
            if ".." in image or image.startswith(("/", "\\")) or ":" in image:
                raise ValueError(tr("Choose at least one keyframe picture and describe the movement."))
        return self


def workflow(r: VideoRequest, files: dict[str, str], job_id: str) -> dict[str, Any]:
    """The family's own keyframe graph."""
    if catalog.profile(r.model_key).family == "minimax-h3":
        return minimax_h3.workflow(r, files, job_id)
    return ltx25.workflow(r, files, job_id)


class VideoStudio:
    def __init__(self, images: Any, *, directory: Path | None = None, models_dir: Path | None = None) -> None:
        self.images = images
        self.directory = directory or paths.local_dir() / "videos"
        self.models_dir = models_dir or Path(r"D:\Models")
        engine = getattr(images, "engine", None)
        self.setups = {
            p.family: KeyframeModelSetup(engine, self.models_dir / "video" / catalog.FOLDERS[p.family],
                                         [a for a in catalog.assets() if (a["role"], a["filename"], a["folder"]) in p.requirements()],
                                         recommended_vram_gb=p.recommended_vram_gb)
            for p in catalog.PROFILES}
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}

    def setup(self, key: str) -> KeyframeModelSetup:
        return self.setups[catalog.profile(key).family]

    def status(self) -> dict[str, Any]:
        client = self.images.client
        available = client.available()
        folders = sorted({folder for p in catalog.PROFILES for _, _, folder in p.requirements()})
        listed = client.all_models(folders) if available else {}
        objects = client.object_info() if available else {}
        models = []
        for selected in catalog.PROFILES:
            setup = self.setups[selected.family]
            state = setup.status()
            assets = {a["filename"]: a for a in setup.assets}
            files = {}
            rows = []
            for role, name, folder in selected.requirements():
                names = listed.get(folder) or []
                found = next((n for n in names if n.replace("\\", "/").split("/")[-1] == name), None)
                source = asset_path(setup.root, assets[name])
                present = source.is_file()
                valid_size = not present or source.stat().st_size == assets[name]["size"]
                if found and valid_size:
                    files[role] = found
                rows.append({"role": role, "name": name, "visible": found is not None and valid_size,
                             "source_present": present, "size_valid": valid_size})
            dummy = VideoRequest(model_key=selected.key, images=["first.png", "middle.png", "last.png"], prompt="check",
                                 turbo=selected.turbo_steps is not None)
            graph = workflow(dummy, {role: name for role, name, _ in selected.requirements()}, "check")
            classes = {n["class_type"] for n in graph.values()}
            missing = sorted(classes - objects.keys()) if available else []
            runtime_ready = state["weights_ready"] and state["source_ready"] and state["environment"]["ready"]
            models.append({"key": selected.key, "name": selected.name,
                           "ready": available and len(files) == len(selected.requirements()) and not missing
                           and runtime_ready,
                           "components": rows, "files": files, "missing_nodes": missing, "setup": state,
                           "input_mode": selected.input_mode, "max_images": selected.max_images,
                           "size_multiple": selected.size_multiple, "frame_stride": selected.frame_stride,
                           "frame_offset": selected.frame_offset, "min_seconds": selected.min_seconds,
                           "turbo_steps": selected.turbo_steps,
                           "defaults": {"fps": selected.fps, "steps": selected.steps, "cfg": selected.cfg,
                                        "width": selected.width, "height": selected.height}})
        with self.lock:
            jobs = [dict(j) for j in reversed(list(self.jobs.values()))]
        finishing = video_finish.tools()
        return {"available": available, "models": models, "jobs": jobs,
                "finishing": {"ready": all(finishing.values()), "tools": finishing,
                              "width": 1920, "height": 1080, "fps": 30, "min_seconds": 5, "max_seconds": 15}}

    def generate(self, request: VideoRequest) -> dict[str, Any]:
        status = self.status()
        selected = next(m for m in status["models"] if m["key"] == request.model_key)
        if not selected["ready"]:
            raise ValueError(tr("The video engine is not ready. Check the components and start the engine."))
        if request.duration_seconds is not None and not status["finishing"]["ready"]:
            raise ValueError(tr("Full HD video needs local FFmpeg and FFprobe."))
        request = request.model_copy(update={"seed": uuid.uuid4().int & 0x7fffffff if request.seed < 0 else request.seed})
        with self.lock:
            if any(j["status"] in ("queued", "running") for j in self.jobs.values()):
                raise ValueError(tr("A video is already being generated."))
            job = {"id": uuid.uuid4().hex, "status": "queued", "request": request.model_dump(),
                   "created_at": time.time(), "elapsed": 0, "error": None, "cancel": False,
                   "model_key": request.model_key, "model_name": selected["name"], "stage": "queued",
                   "percent": 0, "step": None, "total": None}
            self.jobs[job["id"]] = job
            if len(self.jobs) > 40:
                del self.jobs[next(iter(self.jobs))]
            result = dict(job)
        threading.Thread(target=self._run, args=(job, request, selected["files"]), daemon=True).start()
        return result

    def _cancel_prompt(self, client: Any, prompt_id: str) -> None:
        # Do not interrupt an image (or a job queued from another app).
        client._request("POST", "/queue", json_body={"delete": [prompt_id]})
        queue = client._json(client._request("GET", "/queue"))
        if any(item[1] == prompt_id for item in queue.get("queue_running", [])):
            client.interrupt()

    def _run(self, job: dict[str, Any], request: VideoRequest, files: dict[str, str]) -> None:
        client = self.images.client
        started = time.monotonic()
        prompt_id = None
        graph = workflow(request, files, job["id"])
        save_id = next((key for key, node in graph.items() if node["class_type"] == "SaveVideo"), "16")
        progress = VideoProgress(graph)
        stream = ProgressStream(client)

        def cancelled() -> bool:
            with self.lock:
                job["elapsed"] = round(time.monotonic() - started, 1)
                return bool(job["cancel"])

        def stage(value: str) -> None:
            with self.lock:
                job.update(stage=value, percent={"finishing": 90, "verifying": 97}[value], step=None, total=None)

        silent_since: float | None = None
        try:
            prompt_id = client.queue(graph)
            progress.prompt_id = prompt_id
            with self.lock:
                job.update(status="running", stage="generation", percent=None)
            while True:
                with self.lock:
                    is_cancelled = job["cancel"]
                    job["elapsed"] = round(time.monotonic() - started, 1)
                if is_cancelled:
                    self._cancel_prompt(client, prompt_id)
                    with self.lock:
                        job["status"] = "cancelled"
                    return
                if time.monotonic() - started > 7200:
                    self._cancel_prompt(client, prompt_id)
                    raise ValueError(tr("Video generation timed out after two hours."))
                for event in stream.poll():
                    update = progress.event(event)
                    if update:
                        with self.lock:
                            job.update(update)
                try:
                    entry = client.history(prompt_id)
                    silent_since = None
                except ComfyUnavailable as exc:
                    if not _engine_busy(exc):
                        raise
                    # Under memory pressure ComfyUI keeps working but answers HTTP late.
                    silent_since = silent_since or time.monotonic()
                    if time.monotonic() - silent_since >= ENGINE_SILENCE_LIMIT:
                        raise
                    entry = None
                if entry:
                    break
                time.sleep(1)
            if error := status_message(entry):
                raise ValueError(error)
            outputs = entry.get("outputs", {}).get(save_id, {})
            videos = outputs.get("images", []) + outputs.get("videos", [])
            video = next((v for v in videos if str(v.get("filename", "")).lower().endswith(".mp4")), None)
            if not video:
                raise ValueError(tr("The engine finished without producing an MP4 video."))
            stream.close()
            with self.lock:
                job.update(stage="saving", percent=89, step=None, total=None)
            data = client.view(video["filename"], video.get("subfolder", ""), video.get("type", "output"))
            folder = paths.ensure(self.directory)
            with tempfile.TemporaryDirectory(prefix=f"{job['id']}-", dir=folder) as scratch:
                source = Path(scratch) / "native.mp4"
                source.write_bytes(data)
                output = source
                final = None
                if request.duration_seconds is not None:
                    output = Path(scratch) / "final.mp4"
                    final = video_finish.finish(source, output, seconds=request.duration_seconds,
                                                source_frames=request.frames, cancelled=cancelled, stage=stage,
                                                audio_fps=request.fps)
                runtime = {"keyframe_times": request.times(),
                           "keyframe_frames": frame_indices(request.times(), request.seconds(), request.frames)}
                metadata = {"id": job["id"], "request": request.model_dump(), "created_at": job["created_at"],
                            "elapsed": round(time.monotonic() - started, 1), "model": job["model_name"],
                            "output": final,
                            "runtime": runtime,
                            "timing": {"native_motion_seconds": request.frames / request.fps,
                                       "export_seconds": request.duration_seconds,
                                       "mode": "continuous"}}
                pending = Path(scratch) / "recipe.json"
                pending.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
                with self.lock:
                    if cancelled():
                        raise video_finish.VideoCancelled
                    target = folder / f"{job['id']}.mp4"
                    output.replace(target)
                    try:
                        pending.replace(folder / f"{job['id']}.json")
                    except OSError:
                        target.unlink(missing_ok=True)
                        raise
                    job.update(status="done", stage="done", percent=100, step=None, total=None, elapsed=metadata["elapsed"])
        except video_finish.VideoCancelled:
            with self.lock:
                job["status"] = "cancelled"
        except Exception as exc:
            with self.lock:
                job.update(status="failed", error=str(exc))
        finally:
            stream.close()
            with self.lock:
                job["elapsed"] = round(time.monotonic() - started, 1)
            self.images.bus.publish("video.update", {"id": job["id"]})

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] in ("queued", "running"):
                job["cancel"] = True
            return dict(job)

    def file(self, video_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", video_id):
            raise KeyError(video_id)
        path = self.directory / f"{video_id}.mp4"
        if not path.is_file():
            raise KeyError(video_id)
        return path

    def gallery(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.directory.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                self.file(row["id"])
                rows.append(row)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)
