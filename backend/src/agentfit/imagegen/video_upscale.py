"""Existing-video RelayVSR jobs, sharing the video task slot and external ComfyUI."""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import psutil
from pydantic import BaseModel, Field, model_validator

from agentfit import paths
from agentfit.i18n import tr
from agentfit.imagegen import relayvsr, video_finish
from agentfit.imagegen.comfy import status_message
from agentfit.imagegen.video_progress import ProgressStream, VideoProgress
from agentfit.imagegen.video_setup import asset_path, verified_asset


class UpscaleRequest(BaseModel):
    source_id: str = Field(default="", pattern=r"^([a-f0-9]{32})?$")
    path: str = Field(default="", max_length=32767)
    factor: Literal[2, 4] = 2
    target: Literal["native", "1080p", "4k"] = "native"
    detail: float = Field(default=1.0, ge=0, le=1)
    start_seconds: float = Field(default=0, ge=0, le=86400)
    duration_seconds: float = Field(default=0, ge=0, le=86400)

    @model_validator(mode="after")
    def source(self) -> UpscaleRequest:
        if bool(self.source_id) == bool(self.path.strip()):
            raise ValueError(tr("Choose one gallery video or one local video file."))
        return self


def probe(path: Path, scratch: Path, cancelled: Any = lambda: False) -> dict[str, Any]:
    executable = video_finish.tools()["ffprobe"]
    if not executable:
        raise ValueError(tr("Local FFmpeg and FFprobe are required."))
    data = json.loads(video_finish._run([executable, "-v", "error", "-protocol_whitelist", "file,pipe",
                                        "-count_frames", "-show_streams", "-of", "json", str(path)], cancelled, scratch))
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    try:
        fps = Fraction(stream["avg_frame_rate"])
        frames, width, height = int(stream["nb_read_frames"]), int(stream["width"]), int(stream["height"])
        if not 1 <= fps <= 120 or min(frames, width, height) < 1:
            raise ValueError
    except (TypeError, KeyError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(tr("The video dimensions, frame count or frame rate could not be measured.")) from exc
    def ratio(value: Any) -> Fraction | None:
        try:
            result = Fraction(str(value).replace(":", "/"))
            return result if result > 0 else None
        except (ValueError, ZeroDivisionError):
            return None
    sar, nominal_fps = ratio(stream.get("sample_aspect_ratio")), ratio(stream.get("r_frame_rate"))
    return {"width": width, "height": height, "frames": frames, "fps": str(fps), "seconds": float(frames / fps),
            "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
            "sample_aspect_ratio": str(sar) if sar else None,
            "display_aspect_ratio": str(Fraction(width, height) * sar) if sar else None,
            "nominal_fps": str(nominal_fps) if nominal_fps else None,
            "irregular_timing": nominal_fps != fps if nominal_fps else None}


def dimensions(source: dict[str, Any], request: UpscaleRequest) -> tuple[int, int]:
    if request.target == "native":
        width, height = source["width"] * request.factor, source["height"] * request.factor
    else:
        width, height = (1920, 1080) if request.target == "1080p" else (3840, 2160)
        aspect = Fraction(source["display_aspect_ratio"]) if source.get("display_aspect_ratio") else Fraction(source["width"], source["height"])
        if aspect < 1:
            width, height = height, width
    if width * height > 3840 * 2160 or max(width, height) > 4096:
        raise ValueError(tr("Native upscaling exceeds 4K. Choose the 1080p or 4K output preset."))
    return width, height


def preparation_geometry(source: dict[str, Any], request: UpscaleRequest, width: int, height: int) -> tuple[str, str]:
    """Keep coded pixels for native scale; rectify source display geometry for square-pixel presets."""
    if request.target == "native":
        return f"scale={width}:{height}:flags=lanczos,setsar=1", source.get("sample_aspect_ratio") or "1"
    aspect = Fraction(source["display_aspect_ratio"]) if source.get("display_aspect_ratio") else Fraction(source["width"], source["height"])
    if aspect >= Fraction(width, height):
        fitted_w, fitted_h = width, max(2, int(Fraction(width) / aspect) // 2 * 2)
    else:
        fitted_w, fitted_h = max(2, int(height * aspect) // 2 * 2), height
    return (f"scale={fitted_w}:{fitted_h}:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"), "1"


def frame_windows(count: int, whole: bool) -> list[tuple[int, int, int, int]]:
    """Read context on both sides; publish each input frame exactly once, in order."""
    if whole:
        return [(0, count, 0, count)]
    result = []
    for start in range(0, count, 15):
        first, end = max(0, start - 45), min(count, start + 16)
        result.append((first, end, start - first, min(15, count - start)))
    return result


class VideoUpscaler:
    def __init__(self, studio: Any) -> None:
        self.studio = studio
        self.directory = studio.directory / "upscaled"
        self.setup = relayvsr.RelayVSRSetup(getattr(studio.images, "engine", None), studio.models_dir)

    def status(self) -> dict[str, Any]:
        client = self.studio.images.client
        available = client.available()
        listed = client.all_models(["diffusion_models", "vae"]) if available else {}
        objects = client.object_info() if available else {}
        state = self.setup.status()
        rows = []
        for factor in (2, 4):
            files = {}
            for asset in relayvsr.requirements(factor):
                name = next((n for n in listed.get(asset["folder"]) or [] if n.replace("\\", "/").split("/")[-1] == asset["filename"]), None)
                if name and verified_asset(self.setup.root, asset):
                    files[asset["role"]] = name
            missing = sorted(relayvsr.NODE_TYPES - objects.keys()) if available else []
            rows.append({"factor": factor, "name": f"RelayVSR {factor}x" + (" · LightX2V" if factor == 2 else ""),
                         "ready": available and len(files) == 2 and not missing and state["environment"]["ready"],
                         "files": files, "missing_nodes": missing})
        with self.studio.lock:
            jobs = [dict(j) for j in reversed(list(self.studio.jobs.values())) if j.get("kind") == "upscale"]
        return {"available": available, "models": rows, "setup": state, "jobs": jobs,
                "tools_ready": all(video_finish.tools().values()), "disk": self.disk()}

    def disk(self) -> dict[str, Any]:
        root = self.studio.models_dir
        if not root.is_dir():
            return {"free": None, "needed": sum(a["size"] for a in relayvsr.assets()), "safe": None}
        usage = shutil.disk_usage(root)
        needed = 0
        for asset in relayvsr.assets():
            if not verified_asset(self.setup.root, asset):
                partial = asset_path(self.setup.root, asset).with_name(asset["filename"] + ".partial")
                received = partial.stat().st_size if partial.is_file() and not partial.is_symlink() else 0
                needed += asset["size"] - received if received <= asset["size"] else asset["size"]
        reserve = min(30 * 1024**3, max(10 * 1024**3, int(usage.total * .15)))
        return {"free": usage.free, "needed": needed, "reserve": reserve, "safe": usage.free >= needed + reserve}

    def source_path(self, request: UpscaleRequest) -> Path:
        source = self.studio.file(request.source_id) if request.source_id else Path(request.path)
        if not source.is_absolute() or not source.is_file() or source.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
            raise ValueError(tr("Choose an existing local MP4, MOV, MKV, WebM or AVI video."))
        return source

    def generate(self, request: UpscaleRequest) -> dict[str, Any]:
        state = self.status()
        model = next(m for m in state["models"] if m["factor"] == request.factor)
        if not model["ready"] or not state["tools_ready"]:
            raise ValueError(tr("RelayVSR is not ready. Check its models, native nodes and local FFmpeg."))
        source = self.source_path(request)
        with self.studio.lock:
            if any(j["status"] in ("queued", "running") for j in self.studio.jobs.values()):
                raise ValueError(tr("A video is already being generated."))
            job = {"id": uuid.uuid4().hex, "kind": "upscale", "status": "queued", "stage": "queued",
                   "percent": 0, "step": None, "total": None, "elapsed": 0, "cancel": False, "error": None,
                   "created_at": time.time(), "request": request.model_dump()}
            self.studio.jobs[job["id"]] = job
            result = dict(job)
        threading.Thread(target=self._run, args=(job, request, source, model["files"]), daemon=True).start()
        return result

    def _run(self, job: dict[str, Any], request: UpscaleRequest, source: Path, files: dict[str, str]) -> None:
        started, prompt_id = time.monotonic(), None
        client = self.studio.images.client
        stream = None
        def cancelled() -> bool:
            with self.studio.lock:
                job["elapsed"] = round(time.monotonic() - started, 1)
                return bool(job["cancel"])
        def check() -> None:
            if cancelled():
                raise video_finish.VideoCancelled
            if time.monotonic() - started > 7200:
                raise ValueError(tr("Video generation timed out after two hours."))
        def update(**values: Any) -> None:
            with self.studio.lock:
                job.update(values)
        try:
            folder = paths.ensure(self.directory)
            install = self.studio.images.engine.install()
            source_stamp = (source.stat().st_size, source.stat().st_mtime_ns)
            with tempfile.TemporaryDirectory(prefix="relayvsr-", dir=folder) as scratch_raw, tempfile.TemporaryDirectory(prefix="willitrun-relay-", dir=paths.ensure(install.root / "input")) as inputs_raw:
                scratch, inputs = Path(scratch_raw), Path(inputs_raw)
                update(status="running", stage="inspecting", percent=1)
                info = probe(source, scratch, cancelled)
                fps = Fraction(info["fps"])
                first = round(request.start_seconds * fps)
                count = info["frames"] - first if request.duration_seconds == 0 else min(info["frames"] - first, round(request.duration_seconds * fps))
                if count < 2:
                    raise ValueError(tr("Choose a video interval containing at least two frames."))
                width, height = dimensions(info, request)
                low_w, low_h = width // request.factor, height // request.factor
                geometry, output_sar = preparation_geometry(info, request, low_w, low_h)
                per_frame = (width * height + low_w * low_h) * 3 * 4
                memory = psutil.virtual_memory().available
                budget = min(12 * 1024**3, int(memory * .7))
                whole = count * per_frame + 2 * 1024**3 <= budget
                windows = frame_windows(count, whole)
                largest_input = max(end - start for start, end, _, _ in windows)
                largest_output = max(end - start - skip for start, end, skip, _ in windows)
                needed_memory = (largest_input * low_w * low_h + largest_output * width * height) * 3 * 4 + 2 * 1024**3
                if needed_memory > budget:
                    raise ValueError(tr("Not enough available RAM for this output. Choose 1080p or close other applications."))
                disk = self.disk()
                if disk["free"] is None or disk["free"] < disk["reserve"] + 256 * 1024**2:
                    raise ValueError(tr("Not enough disk space for video processing and the safety reserve."))
                ffmpeg = str(video_finish.tools()["ffmpeg"])
                segments, gpu_seconds = [], 0.0
                for index, (start, end, skip, keep) in enumerate(windows):
                    check()
                    update(stage="preparing", percent=2 + int(88 * index / len(windows)), step=index, total=len(windows))
                    prepared = inputs / f"part-{index}.mp4"
                    filters = f"trim=start_frame={first + start}:end_frame={first + end},setpts=N/({fps}*TB),{geometry}"
                    video_finish._run([ffmpeg, "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
                                       "-map", "0:v:0", "-an", "-vf", filters, "-r", str(fps), "-frames:v", str(end - start),
                                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "0", "-pix_fmt", "yuv444p", str(prepared)], cancelled, scratch)
                    graph = relayvsr.workflow(request.factor, files, f"{inputs.name}/{prepared.name}", f"{job['id']}-{index}",
                                             detail=request.detail, decode_start=skip, decode_length=end - start - skip)
                    progress = VideoProgress(graph)
                    stream = ProgressStream(client)
                    tick = time.monotonic()
                    prompt_id = client.queue(graph)
                    progress.prompt_id = prompt_id
                    update(stage="upscaling")
                    while True:
                        check()
                        for event in stream.poll():
                            observed = progress.event(event)
                            if observed:
                                update(percent=2 + int(88 * (index + observed["percent"] / 100) / len(windows)))
                                if observed["stage"] == "upscaling" and observed["step"] is not None:
                                    done = sum(w[3] for w in windows[:index]) + min(keep, observed["step"])
                                    update(step=done, total=count, unit="frames")
                        entry = client.history(prompt_id)
                        if entry:
                            break
                        time.sleep(.25)
                    gpu_seconds += time.monotonic() - tick
                    if error := status_message(entry):
                        raise ValueError(error)
                    output = entry.get("outputs", {}).get("15", {})
                    video = next((v for v in output.get("images", []) + output.get("videos", []) if str(v.get("filename", "")).endswith(".mp4")), None)
                    if not video:
                        raise ValueError(tr("The engine finished without producing an MP4 video."))
                    stream.close()
                    stream, prompt_id = None, None
                    native = scratch / "native.mp4"
                    native.write_bytes(client.view(video["filename"], video.get("subfolder", ""), video.get("type", "output")))
                    part_info = probe(native, scratch, cancelled)
                    if (part_info["width"], part_info["height"], part_info["frames"]) != (width, height, end - start - skip):
                        raise ValueError(tr("RelayVSR returned an unexpected resolution or frame count."))
                    segment = scratch / f"segment-{index}.mp4"
                    video_finish._run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(native), "-an", "-vf",
                                       f"trim=start_frame=0:end_frame={keep},setpts=N/({fps}*TB),setsar={output_sar}",
                                       "-r", str(fps), "-frames:v", str(keep), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(segment)], cancelled, scratch)
                    segments.append(segment)
                    prepared.unlink(missing_ok=True)
                    generated = install.root / "output" / video.get("subfolder", "") / video["filename"]
                    owned = install.root / "output" / "WillItRun" / "upscale"
                    if generated.resolve().is_relative_to(owned.resolve()) and generated.name.startswith(job["id"]):
                        generated.unlink(missing_ok=True)
                check()
                update(stage="encoding", percent=92, step=None, total=None)
                concat = scratch / "segments.txt"
                concat.write_text("\n".join(f"file '{s.name}'" for s in segments), encoding="utf-8")
                final = scratch / "result.mp4"
                audio = ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"] if info["audio"] else ["-an"]
                video_finish._run([ffmpeg, "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1", "-i", str(concat),
                                   "-ss", f"{float(first / fps):.10f}", "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:v:0", *audio,
                                   "-vf", f"setpts=N/({fps}*TB),setsar={output_sar}", "-r", str(fps), "-frames:v", str(count),
                                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                                   "-t", f"{float(count / fps):.10f}", "-movflags", "+faststart", str(final)], cancelled, scratch)
                update(stage="verifying", percent=97)
                measured = probe(final, scratch, cancelled)
                if ((measured["width"], measured["height"], measured["frames"], Fraction(measured["fps"])) != (width, height, count, fps)
                        or measured["audio"] != info["audio"]
                        or (measured["sample_aspect_ratio"] is not None and Fraction(measured["sample_aspect_ratio"]) != Fraction(output_sar))):
                    raise ValueError(tr("The upscaled video failed its resolution, frame rate, frame count or audio checks."))
                if source_stamp != (source.stat().st_size, source.stat().st_mtime_ns):
                    raise ValueError(tr("The source video changed during upscaling. The result was not published."))
                check()
                elapsed_seconds = time.monotonic() - started
                elapsed = round(elapsed_seconds, 2)
                metadata = {"id": job["id"], "kind": "upscale", "model": f"RelayVSR {request.factor}x",
                            "created_at": job["created_at"], "request": request.model_dump(), "source": {"name": source.name, **info},
                            "prepared": {"width": low_w, "height": low_h, "frames": count, "start_frame": first,
                                         "chunks": len(windows), "context_frames": 0 if whole else 45,
                                         "decoded_context_frames": 0, "output_sample_aspect_ratio": output_sar},
                            "output": measured, "elapsed": elapsed,
                            "processing_fps": round(count / elapsed_seconds, 2) if elapsed_seconds > 0 else None,
                            "engine_seconds": round(gpu_seconds, 2), "weights_revision": relayvsr.assets()[0]["revision"],
                            "reasons": ["Native model scale" if request.target == "native" else "Explicit output preset with aspect-preserving input preparation",
                                        "Whole interval fits the available RAM budget" if whole else "Coarse temporal context is trimmed before Flash decoding to bound output RAM",
                                        ("Native pixel aspect ratio retained" if info["sample_aspect_ratio"] else "Unspecified source pixel aspect ratio; native coded geometry used")
                                        if request.target == "native" else "Square-pixel output preserves the source display aspect ratio",
                                        "Irregular timestamps normalized to the measured average frame rate" if info["irregular_timing"] else "Measured average frame rate retained"]}
                recipe = scratch / "result.json"
                recipe.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                with self.studio.lock:
                    check()
                    target = folder / f"{job['id']}.mp4"
                    final.replace(target)
                    try:
                        recipe.replace(target.with_suffix(".json"))
                    except OSError:
                        target.unlink(missing_ok=True)
                        raise
                    job.update(status="done", stage="done", percent=100, elapsed=elapsed)
        except video_finish.VideoCancelled:
            update(status="cancelled")
        except Exception as exc:
            update(status="failed", error=str(exc))
        finally:
            if prompt_id:
                with contextlib.suppress(Exception):
                    self.studio._cancel_prompt(client, prompt_id)
            if stream:
                stream.close()
            update(elapsed=round(time.monotonic() - started, 2))
            self.studio.images.bus.publish("video.update", {"id": job["id"]})

    def file(self, identifier: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise KeyError(identifier)
        result = self.directory / f"{identifier}.mp4"
        if not result.is_file():
            raise KeyError(identifier)
        return result

    def gallery(self) -> list[dict[str, Any]]:
        rows = []
        for recipe in self.directory.glob("*.json"):
            try:
                row = json.loads(recipe.read_text(encoding="utf-8"))
                self.file(row["id"])
                rows.append(row)
            except (KeyError, OSError, ValueError):
                continue
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)
