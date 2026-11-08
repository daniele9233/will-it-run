"""Browser-assisted Google Flow handoff and local, verified frame extraction.

No Google API, account tokens, cookies or purchases. Generation stays in Flow.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentfit.i18n import tr
from agentfit.imagegen.video_finish import tools

FLOW_URL = "https://flow.google.com/"


class FlowPrepare(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    first: str = Field(min_length=1, max_length=32767)
    last: str = Field(min_length=1, max_length=32767)
    prompt: str = Field(min_length=1, max_length=10000)


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, timeout=300,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise ValueError(tr("The media file could not be processed.") + " " + result.stderr.decode("utf-8", errors="replace")[-800:])
    return result.stdout.decode("utf-8")


def _probe(path: Path, executable: str) -> dict[str, Any]:
    data = json.loads(_run([executable, "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json", str(path)]))
    streams = data.get("streams", [])
    if not streams or not streams[0].get("width") or not streams[0].get("height"):
        raise ValueError(tr("The media file has no readable video stream."))
    return {"width": streams[0]["width"], "height": streams[0]["height"],
            "seconds": float(data.get("format", {}).get("duration", 0) or 0)}


class FlowStudio:
    def __init__(self, directory: Path):
        self.directory = directory
        self._lock = threading.RLock()
        self._active: set[str] = set()

    def _folder(self, session_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise KeyError(session_id)
        folder = self.directory / session_id
        if not (folder / "session.json").is_file():
            raise KeyError(session_id)
        return folder

    def _write(self, folder: Path, record: dict[str, Any]) -> None:
        with self._lock:
            temporary = folder / "session.tmp"
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(folder / "session.json")

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            record = json.loads((self._folder(session_id) / "session.json").read_text(encoding="utf-8"))
            active = session_id in self._active
        if record["status"] == "processing" and not active:
            record = record | {"status": "failed", "error": tr("Frame extraction was interrupted. Import the video again.")}
        return record

    def status(self) -> dict[str, Any]:
        records = []
        if self.directory.is_dir():
            for file in sorted(self.directory.glob("*/session.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    records.append(self.get(file.parent.name))
                except (KeyError, ValueError, OSError):
                    continue
        return {"url": FLOW_URL, "tools_ready": all(tools().values()), "sessions": records}

    def prepare(self, body: FlowPrepare) -> dict[str, Any]:
        if not body.name.strip() or not body.prompt.strip():
            raise ValueError(tr("Enter a name and a video prompt."))
        probe = tools()["ffprobe"]
        if not probe:
            raise ValueError(tr("Local FFmpeg and FFprobe are required."))
        sources = [Path(body.first), Path(body.last)]
        for source in sources:
            if (not source.is_absolute() or not source.is_file() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
                    or not 0 < source.stat().st_size <= 30 * 1024 * 1024):
                raise ValueError(tr("Choose PNG, JPEG or WebP reference images under 30 MB."))
            info = _probe(source, probe)
            if max(info["width"], info["height"]) > 8192 or min(info["width"], info["height"]) < 64:
                raise ValueError(tr("Reference images must be between 64 and 8192 pixels."))
        session_id = uuid.uuid4().hex
        folder = self.directory / session_id
        folder.mkdir(parents=True)
        try:
            references = []
            for name, source in zip(("first", "last"), sources, strict=True):
                destination = folder / (name + source.suffix.lower())
                shutil.copyfile(source, destination)
                references.append(str(destination))
            (folder / "prompt.txt").write_text(body.prompt, encoding="utf-8")
            record = {"id": session_id, "name": body.name.strip(), "status": "prepared", "prompt": body.prompt,
                      "first": references[0], "last": references[1], "folder": str(folder), "url": FLOW_URL, "error": None}
            self._write(folder, record)
        except Exception:
            # Only this newly-created, UUID-owned staging folder can be removed.
            shutil.rmtree(folder)
            raise
        return record

    def import_video(self, session_id: str, path: str) -> dict[str, Any]:
        folder = self._folder(session_id)
        source = Path(path)
        if not source.is_absolute() or not source.is_file() or source.suffix.lower() != ".mp4" or not 0 < source.stat().st_size <= 500 * 1024 * 1024:
            raise ValueError(tr("Choose an MP4 video under 500 MB."))
        executables = tools()
        if not all(executables.values()):
            raise ValueError(tr("Local FFmpeg and FFprobe are required."))
        with self._lock:
            if session_id in self._active:
                raise ValueError(tr("This video is already being processed."))
            self._active.add(session_id)
        try:
            record = json.loads((folder / "session.json").read_text(encoding="utf-8"))
            record.update(status="processing", error=None)
            self._write(folder, record)
            threading.Thread(target=self._extract, args=(folder, record, source, executables), daemon=True).start()
        except Exception:
            with self._lock:
                self._active.discard(session_id)
            raise
        return record

    def _extract(self, folder: Path, record: dict[str, Any], source: Path, executables: dict[str, Any]) -> None:
        staging = folder / ("frames-" + uuid.uuid4().hex)
        temporary_zip = folder / "sequence.tmp"
        try:
            staging.mkdir()
            info = _probe(source, executables["ffprobe"])
            if not 0.5 <= info["seconds"] <= 60:
                raise ValueError(tr("Choose a video between 0.5 and 60 seconds."))
            local = folder / "video.mp4"
            if source.resolve() != local.resolve():
                shutil.copyfile(source, local)
            with local.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            _run([executables["ffmpeg"], "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe", "-i", str(local), "-an", "-vf",
                  f"fps={150 / info['seconds']:.10f},scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,tpad=stop_mode=clone:stop_duration=0.1",
                  "-frames:v", "150", "-c:v", "libwebp", "-quality", "82", str(staging / "frame-%03d.webp")])
            frames = sorted(staging.glob("frame-*.webp"))
            if len(frames) != 150 or any(not p.stat().st_size for p in frames):
                raise ValueError(tr("Frame extraction did not produce 150 valid frames."))
            dimensions = _probe(frames[0], executables["ffprobe"])
            manifest = {"version": 1, "provider": "Google Flow (user-imported)", "source_sha256": digest,
                        "frames": [p.name for p in frames], "count": len(frames), "width": dimensions["width"],
                        "height": dimensions["height"], "source": info}
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_STORED) as archive:
                for item in [*frames, staging / "manifest.json"]:
                    archive.write(item, item.name)
            # Publish the whole sequence only after all frames have been verified.
            temporary_zip.replace(folder / "sequence.zip")
            record.update(status="ready", frames=150, output=info, sequence=str(folder / "sequence.zip"),
                          frame_folder=str(staging), source_sha256=digest)
            self._write(folder, record)
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            temporary_zip.unlink(missing_ok=True)
            record.update(status="failed", error=str(exc))
            self._write(folder, record)
        finally:
            with self._lock:
                self._active.discard(record["id"])

    def sequence(self, session_id: str) -> Path:
        record = self.get(session_id)
        path = self._folder(session_id) / "sequence.zip"
        if record["status"] != "ready" or not path.is_file():
            raise KeyError(session_id)
        return path
