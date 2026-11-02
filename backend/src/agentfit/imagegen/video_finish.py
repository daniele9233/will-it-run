"""Local hero-video finishing: motion interpolation, Lanczos scaling and verified MP4s."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Any

from agentfit.i18n import tr

WIDTH, HEIGHT, FPS = 1920, 1080, 30


class VideoCancelled(Exception):
    """The owner cancelled generation or finishing."""


def tools() -> dict[str, str | None]:
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        found = shutil.which(name)
        # Explorer-launched apps may inherit an older PATH than a terminal.
        installed = Path.home() / "AppData/Local/Programs/FFmpeg/bin" / f"{name}.exe"
        result[name] = found or (str(installed) if installed.is_file() else None)
    return result


def _run(args: list[str], cancelled: Callable[[], bool], directory: Path) -> str:
    """Drain to disk, poll cancellation, and terminate only the process we created."""
    with tempfile.TemporaryFile(dir=directory) as output, tempfile.TemporaryFile(dir=directory) as errors:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():
                    raise VideoCancelled
                if time.monotonic() - started > 7200:
                    raise ValueError(tr("Video finishing timed out after two hours."))
                time.sleep(0.2)
            if cancelled():
                raise VideoCancelled
            if process.returncode:
                errors.seek(0)
                raise ValueError(tr("Local video finishing failed: {error}").format(
                    error=errors.read().decode("utf-8", errors="replace")[-1500:]))
            output.seek(0)
            return output.read().decode("utf-8", errors="replace")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def finish(source: Path, destination: Path, *, seconds: int, source_frames: int,
           cancelled: Callable[[], bool], stage: Callable[[str], None]) -> dict[str, Any]:
    executables = tools()
    if not all(executables.values()):
        raise ValueError(tr("Full HD video needs local FFmpeg and FFprobe."))
    count = seconds * FPS
    # Map the original endpoints to frames 0 and count-1. Two guard frames give
    # minterpolate lookahead; they are never included in the exported frames.
    filters = (
        f"tpad=stop_mode=clone:stop=2,settb=AVTB,setpts=N*{count - 1}/({FPS}*{source_frames - 1}*TB),"
        f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=none,"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"
    )
    stage("finishing")
    _run([str(executables["ffmpeg"]), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
          "-i", str(source), "-map", "0:v:0", "-an", "-vf", filters, "-frames:v", str(count),
          "-fps_mode", "passthrough", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
          "-movflags", "+faststart", str(destination)], cancelled, source.parent)
    stage("verifying")
    payload = json.loads(_run([str(executables["ffprobe"]), "-v", "error", "-select_streams", "v:0",
                              "-count_frames", "-show_entries",
                              "stream=width,height,avg_frame_rate,nb_read_frames,duration,codec_name,pix_fmt",
                              "-of", "json", str(destination)], cancelled, source.parent))
    stream = payload["streams"][0]
    if (int(stream["width"]) != WIDTH or int(stream["height"]) != HEIGHT
            or Fraction(stream["avg_frame_rate"]) != FPS or int(stream["nb_read_frames"]) != count
            or abs(float(stream["duration"]) - seconds) > 0.001):
        raise ValueError(tr("The exported video does not match the requested Full HD format."))
    hashes = _run([str(executables["ffmpeg"]), "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-i", str(destination), "-map", "0:v:0", "-an", "-f", "framemd5", "-"],
                  cancelled, source.parent)
    frames = [line.rsplit(",", 1)[-1].strip() for line in hashes.splitlines() if line and not line.startswith("#")]
    repeats = sum(a == b for a, b in pairwise(frames))
    if len(frames) != count or repeats:
        raise ValueError(tr("The video contains repeated frames. Try a prompt with more visible movement."))
    return {"width": WIDTH, "height": HEIGHT, "fps": FPS, "frames": count, "seconds": seconds,
            "distinct_frames": len(set(frames)), "adjacent_repeats": repeats,
            "interpolation": "motion-compensated", "resize": "lanczos", "codec": stream["codec_name"]}
