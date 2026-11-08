"""Local Wan 2.2 I2V studio, sharing the Images engine and keeping MP4s in app data."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from agentfit import paths
from agentfit.i18n import tr
from agentfit.imagegen import video_catalog as catalog
from agentfit.imagegen import video_finish
from agentfit.imagegen.comfy import status_message
from agentfit.imagegen.video_catalog import ENCODER, HIGH, LOW, VAE  # noqa: F401
from agentfit.imagegen.video_progress import ProgressStream, VideoProgress


class VideoRequest(BaseModel):
    model_key: str = catalog.DEFAULT
    end_image: str = Field(default="", max_length=500)
    image: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=10000)
    negative: str = Field(default="", max_length=10000)
    width: int = Field(default=832, ge=256, le=1280)
    height: int = Field(default=480, ge=256, le=1280)
    frames: int = Field(default=33, ge=5, le=361)
    duration_seconds: int | None = Field(default=None, ge=6, le=15)
    motion_frames: int | None = Field(default=None, ge=5, le=361)
    fps: int = Field(default=16, ge=8, le=30)
    steps: int = Field(default=20, ge=4, le=60)
    cfg: float = Field(default=3.5, ge=1, le=10)
    seed: int = Field(default=-1, ge=-1, le=2147483647)

    @model_validator(mode="after")
    def valid(self) -> VideoRequest:
        selected = catalog.profile(self.model_key)
        for name in ("fps", "steps", "cfg"):
            if name not in self.model_fields_set:
                setattr(self, name, getattr(selected, name))
        if self.duration_seconds is not None:
            self.fps = selected.fps
            self.frames = self.motion_frames or self.duration_seconds * selected.fps + 1
            if selected.required_end:
                if "width" not in self.model_fields_set and "height" not in self.model_fields_set:
                    self.width, self.height = 1280, 720
                if self.width * self.height < 1280 * 720:
                    raise ValueError(tr("This 720p video model needs at least 1280 x 720 pixels. Choose a higher generation resolution."))
        if self.width % 16 or self.height % 16 or (self.frames - 1) % 4:
            raise ValueError(tr("Video sizes must be multiples of 16; frames must be 4n+1."))
        if self.width % selected.size_multiple or self.height % selected.size_multiple:
            raise ValueError(tr("This video model needs dimensions divisible by {multiple}.").format(multiple=selected.size_multiple))
        if selected.required_end and not self.end_image:
            raise ValueError(tr("This video model needs both a starting and an ending picture."))
        if not self.prompt.strip():
            raise ValueError(tr("Choose an uploaded picture and describe the movement."))
        for image in (self.image, self.end_image):
            if ".." in image or image.startswith(("/", "\\")) or ":" in image:
                raise ValueError(tr("Choose an uploaded picture and describe the movement."))
        return self


def workflow(r: VideoRequest, files: dict[str, str], job_id: str) -> dict[str, Any]:
    """Bind the official family-specific first/last-frame recipes to local GGUF loaders."""
    selected = catalog.profile(r.model_key)

    def node(kind: str, **inputs: Any) -> dict[str, Any]:
        return {"class_type": kind, "inputs": inputs}

    conditioning = "WanImageToVideo" if selected.family == "wan22-i2v" and not r.end_image else (
        "WanFunInpaintToVideo" if selected.family.startswith("wan22-fun") else "WanFirstLastFrameToVideo"
    )
    shift = 5.0 if conditioning == "WanImageToVideo" else 8.0
    graph = {
        "1": node("UnetLoaderGGUF", unet_name=files["high"]),
        "3": node("CLIPLoader", clip_name=files["encoder"], type="wan", device="default"),
        "4": node("VAELoader", vae_name=files["vae"]),
        "5": node("CLIPTextEncode", clip=["3", 0], text=r.prompt),
        "6": node("CLIPTextEncode", clip=["3", 0], text=r.negative),
        "7": node("LoadImage", image=r.image),
        "20": node("ResizeAndPadImage", image=["7", 0], target_width=r.width, target_height=r.height,
                   padding_color="black", interpolation="lanczos"),
        "8": node(conditioning, positive=["5", 0], negative=["6", 0], vae=["4", 0],
                  start_image=["20", 0], width=r.width, height=r.height, length=r.frames, batch_size=1),
        "9": node("ModelSamplingSD3", model=["1", 0], shift=shift),
        "13": node("VAEDecode", samples=["12" if selected.low else "11", 0], vae=["4", 0]),
        "14": node("CreateVideo", images=["13", 0], fps=r.fps),
        "15": node("SaveVideo", video=["14", 0], filename_prefix=f"WillItRun/video/{job_id}", format="mp4"),
    }
    if r.end_image:
        graph["16"] = node("LoadImage", image=r.end_image)
        graph["21"] = node("ResizeAndPadImage", image=["16", 0], target_width=r.width, target_height=r.height,
                           padding_color="black", interpolation="lanczos")
        graph["8"]["inputs"]["end_image"] = ["21", 0]
    if selected.vision:
        graph["17"] = node("CLIPVisionLoader", clip_name=files["vision"])
        graph["18"] = node("CLIPVisionEncode", clip_vision=["17", 0], image=["7", 0], crop="none")
        graph["19"] = node("CLIPVisionEncode", clip_vision=["17", 0], image=["16", 0], crop="none")
        graph["8"]["inputs"].update(clip_vision_start_image=["18", 0], clip_vision_end_image=["19", 0])
    common = dict(steps=r.steps, cfg=r.cfg, sampler_name=selected.sampler, scheduler="simple",
                  positive=["8", 0], negative=["8", 1])
    if selected.low:
        split = r.steps // 2
        graph["2"] = node("UnetLoaderGGUF", unet_name=files["low"])
        graph["10"] = node("ModelSamplingSD3", model=["2", 0], shift=shift)
        graph["11"] = node("KSamplerAdvanced", **common, noise_seed=r.seed, model=["9", 0], latent_image=["8", 2],
                           add_noise="enable", start_at_step=0, end_at_step=split, return_with_leftover_noise="enable")
        graph["12"] = node("KSamplerAdvanced", **common, noise_seed=r.seed, model=["10", 0], latent_image=["11", 0],
                           add_noise="disable", start_at_step=split, end_at_step=r.steps, return_with_leftover_noise="disable")
    else:
        graph["11"] = node("KSampler", **common, seed=r.seed, model=["9", 0], latent_image=["8", 2], denoise=1.0)
    return graph


class VideoStudio:
    def __init__(self, images: Any, *, directory: Path | None = None, models_dir: Path | None = None) -> None:
        self.images = images
        self.directory = directory or paths.local_dir() / "videos"
        self.models_dir = models_dir or Path(r"D:\Models")
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}

    def status(self) -> dict[str, Any]:
        client = self.images.client
        available = client.available()
        folders = sorted({folder for p in catalog.PROFILES for _, _, folder in p.requirements()})
        listed = client.all_models(folders) if available else {}
        objects = client.object_info() if available else {}
        models = []
        expected_sizes = {a["filename"]: a["size"] for a in catalog.assets()}
        for selected in catalog.PROFILES:
            files = {}
            rows = []
            for role, name, folder in selected.requirements():
                names = listed.get(folder) or []
                found = next((n for n in names if n.replace("\\", "/").split("/")[-1] == name), None)
                source = catalog.local_path(self.models_dir, name, folder)
                present = source.is_file()
                valid_size = not present or name not in expected_sizes or source.stat().st_size == expected_sizes[name]
                if found and valid_size:
                    files[role] = found
                rows.append({"role": role, "name": name, "visible": found is not None and valid_size,
                             "source_present": present, "size_valid": valid_size})
            dummy = VideoRequest(model_key=selected.key, image="check.png", end_image="end.png", prompt="check")
            graph = workflow(dummy, {role: name for role, name, _ in selected.requirements()}, "check")
            classes = {n["class_type"] for n in graph.values()}
            # The original single-picture mode has a different conditioning node.
            if selected.family == "wan22-i2v":
                classes.add("WanImageToVideo")
            missing = sorted(classes - objects.keys()) if available else []
            models.append({"key": selected.key, "name": selected.name,
                           "ready": available and len(files) == len(selected.requirements()) and not missing,
                           "components": rows, "files": files, "missing_nodes": missing,
                           "required_end": selected.required_end, "size_multiple": selected.size_multiple,
                           "defaults": {"fps": selected.fps, "steps": selected.steps, "cfg": selected.cfg,
                                        "width": 1280 if selected.required_end else 832,
                                        "height": 720 if selected.required_end else 480}})
        with self.lock:
            jobs = [dict(j) for j in reversed(list(self.jobs.values()))]
        default = models[0]
        finishing = video_finish.tools()
        return {**default, "available": available, "models": models, "jobs": jobs,
                "finishing": {"ready": all(finishing.values()), "tools": finishing,
                              "width": 1920, "height": 1080, "fps": 30, "min_seconds": 6, "max_seconds": 15}}

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
        progress = VideoProgress(graph)
        stream = ProgressStream(client)

        def cancelled() -> bool:
            with self.lock:
                job["elapsed"] = round(time.monotonic() - started, 1)
                return bool(job["cancel"])

        def stage(value: str) -> None:
            with self.lock:
                job.update(stage=value, percent={"finishing": 90, "verifying": 97}[value], step=None, total=None)

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
                entry = client.history(prompt_id)
                if entry:
                    break
                time.sleep(1)
            if error := status_message(entry):
                raise ValueError(error)
            outputs = entry.get("outputs", {}).get("15", {})
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
                                                source_frames=request.frames, cancelled=cancelled, stage=stage)
                metadata = {"id": job["id"], "request": request.model_dump(), "created_at": job["created_at"],
                            "elapsed": round(time.monotonic() - started, 1), "model": job["model_name"],
                            "output": final,
                            "timing": {"native_motion_seconds": (request.frames - 1) / request.fps,
                                       "export_seconds": request.duration_seconds,
                                       "mode": "slow-motion" if request.motion_frames else "continuous"}}
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
