"""Local image-to-3D jobs, isolated from the core and ComfyUI in WSL2."""
from __future__ import annotations

import base64
import binascii
import copy
import json
import os
import re
import struct
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentfit import paths
from agentfit.i18n import tr

MAX_IMAGE = 20 * 1024 * 1024
MODEL_REVISION = "af44b45f2e35a493886929c6d786e563ec68364d"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class TrellisRequest(BaseModel):
    image: str = Field(pattern=r"^[a-f0-9]{32}$")
    seed: int = Field(default=1, ge=0, le=2147483647)
    steps: int = Field(default=24, ge=1, le=50)
    texture_size: Literal[2048, 4096] = 4096
    resolution: Literal[1024, 1536] = 1024


class WebExportRequest(BaseModel):
    triangles: int = Field(default=28000, ge=25000, le=30000)
    texture_size: Literal[1024, 2048] = 2048


def validate_glb(data: bytes) -> None:
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(tr("TRELLIS.2 did not produce a valid GLB model."))
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2 or length != len(data):
        raise ValueError(tr("TRELLIS.2 did not produce a valid GLB model."))
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A or 20 + chunk_length > len(data):
        raise ValueError(tr("TRELLIS.2 did not produce a valid GLB model."))
    document = json.loads(data[20:20 + chunk_length])
    if not document.get("meshes"):
        raise ValueError(tr("TRELLIS.2 did not produce a valid GLB model."))


class TrellisStudio:
    def __init__(self, directory: Path | None = None, on_event: Callable[[dict[str, Any]], None] | None = None):
        self.directory = directory or paths.local_dir() / "3d"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._job: dict[str, Any] | None = None
        self._probe: tuple[float, dict[str, Any]] | None = None
        self._on_event = on_event

    def _config(self) -> dict[str, str]:
        try:
            return json.loads((self.directory / "runtime.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.run(command, capture_output=True, creationflags=FLAGS, **kwargs)

    def status(self) -> dict[str, Any]:
        with self._lock:
            cached = self._probe
        if cached and time.monotonic() - cached[0] < 15:
            return {**cached[1], "job": self.job()}
        distro = self._config().get("distribution", "")
        distributions: list[str] = []
        ready = False
        error = None
        try:
            result = self._run(["wsl", "--list", "--quiet"], timeout=15)
            distributions = [s.strip() for s in result.stdout.decode("utf-16-le", errors="replace").splitlines() if s.strip()]
            if distro and distro in distributions:
                assets = json.loads(Path(__file__).with_name("data").joinpath("trellis2_assets.json").read_text(encoding="utf-8"))
                checks = ['test -f "$HOME/.local/share/willitrun-trellis2/ready"',
                          'test -f "$HOME/.local/share/willitrun-trellis2/weights-ready"',
                          'test -x "$HOME/.local/share/willitrun-trellis2/env/bin/python"']
                checks.extend(f'test -s "$HOME/.local/share/willitrun-trellis2/models/{a["repo"]}/{a["file"]}"' for a in assets)
                result = self._run(["wsl", "-d", distro, "--exec", "bash", "-s"],
                                   input=(" && ".join(checks) + "\n").encode(), timeout=20)
                ready = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
        payload = {"ready": ready, "distribution": distro, "distributions": distributions, "error": error,
                   "model": "microsoft/TRELLIS.2-4B", "minimum_vram_gb": 24, "resolution": 1536,
                   "source": "https://github.com/microsoft/TRELLIS.2"}
        with self._lock:
            self._probe = (time.monotonic(), payload)
        return {**payload, "job": self.job()}

    def job(self) -> dict[str, Any] | None:
        with self._lock:
            value = copy.deepcopy(self._job)
        if value and value["status"] == "running":
            value["elapsed"] = round(time.time() - value["started_at"], 1)
        return value

    def _begin(self, kind: str) -> dict[str, Any]:
        with self._lock:
            if self._job and self._job["status"] == "running":
                raise ValueError(tr("A 3D task is already running."))
            self._job = {"id": uuid.uuid4().hex, "kind": kind, "status": "running", "error": None,
                         "percent": 0, "phase": "starting", "started_at": time.time(), "elapsed": 0}
            return dict(self._job)

    def _progress(self, payload: dict[str, Any]) -> None:
        with self._lock:
            if not self._job or self._job["status"] != "running":
                return
            if payload.get("phase") != self._job.get("phase"):
                self._job.pop("step", None)
                self._job.pop("total", None)
            percent = payload.get("percent")
            if isinstance(percent, (float, int)):
                self._job["percent"] = max(self._job["percent"], min(99, max(0, int(percent))))
            for field in ("phase", "step", "total"):
                if field in payload:
                    self._job[field] = payload[field]

    def start_task(self, kind: str, work: Callable[[str, Callable[[dict[str, Any]], None]], None]) -> dict[str, Any]:
        """Share the 3D task slot with the independently wired local code generator."""
        job = self._begin(kind)

        def run() -> None:
            try:
                work(job["id"], self._progress)
                self._finish()
            except Exception as exc:
                self._finish(str(exc))

        threading.Thread(target=run, daemon=True).start()
        return job

    def install(self, distribution: str) -> dict[str, Any]:
        if distribution not in self.status()["distributions"]:
            raise ValueError(tr("Choose an installed WSL2 Linux distribution."))
        job = self._begin("install")
        (self.directory / "runtime.json").write_text(json.dumps({"distribution": distribution}), encoding="utf-8")
        threading.Thread(target=self._install, args=(distribution,), daemon=True).start()
        return job

    def _install(self, distribution: str) -> None:
        try:
            script = Path(__file__).with_name("trellis_setup.sh").read_bytes().replace(b"\r\n", b"\n")
            with (self.directory / "setup.log").open("wb") as log:
                result = subprocess.run(["wsl", "-d", distribution, "--exec", "bash", "-s"], input=script,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=7200, creationflags=FLAGS)
            if result.returncode:
                raise ValueError(tr("TRELLIS.2 setup failed. See the local setup log."))
            assets = json.loads(Path(__file__).with_name("data").joinpath("trellis2_assets.json").read_text(encoding="utf-8"))
            command, bootstrap = self._worker({"prepare": True, "assets": assets, "model_revision": MODEL_REVISION})
            with (self.directory / "setup.log").open("ab") as log:
                result = subprocess.run(command, input=bootstrap, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=7200, creationflags=FLAGS)
            if result.returncode:
                raise ValueError(tr("TRELLIS.2 setup failed. See the local setup log."))
            self._finish()
        except Exception as exc:
            self._finish(str(exc))
        finally:
            self._probe = None

    def _finish(self, error: str | None = None) -> None:
        with self._lock:
            if self._job:
                self._job.update(status="failed" if error else "completed", error=error,
                                 finished_at=time.time(), elapsed=round(time.time() - self._job["started_at"], 1))
                if not error:
                    self._job.update(percent=100, phase="completed")
                event = copy.deepcopy(self._job)
            else:
                event = None
        if event and self._on_event:
            self._on_event(event)

    def upload(self, encoded: str) -> str:
        try:
            if len(encoded) > (MAX_IMAGE * 4 // 3) + 100:
                raise ValueError
            data = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
            if not data or len(data) > MAX_IMAGE or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError
        except (ValueError, binascii.Error) as exc:
            raise ValueError(tr("Choose a PNG image smaller than 20 MB.")) from exc
        identifier = uuid.uuid4().hex
        (self.directory / f"{identifier}.png").write_bytes(data)
        return identifier

    def generate(self, request: TrellisRequest) -> dict[str, Any]:
        if not self.status()["ready"]:
            raise ValueError(tr("Install the local TRELLIS.2 runtime first."))
        if not (self.directory / f"{request.image}.png").is_file():
            raise ValueError(tr("Upload a reference image first."))
        job = self._begin("generate")
        threading.Thread(target=self._generate, args=(job["id"], request), daemon=True).start()
        return job

    def _worker(self, body: dict[str, Any], worker: str = "trellis_worker.py") -> tuple[list[str], bytes]:
        script = Path(__file__).with_name(worker).read_text(encoding="utf-8-sig")
        # Feed bash over stdin: Windows/WSL argument quoting must never rewrite Python source.
        command = ["wsl", "-d", self._config()["distribution"], "--exec", "bash", "-s"]
        encoded_script = base64.b64encode(script.encode()).decode()
        bootstrap = (
            'export LD_LIBRARY_PATH="$HOME/.local/share/willitrun-trellis2/env/lib:${LD_LIBRARY_PATH:-}"\n'
            'exec timeout 7100 "$HOME/.local/share/willitrun-trellis2/env/bin/python" -c '
            f"'import base64; exec(base64.b64decode(\"{encoded_script}\"))' <<'WIR_JOB'\n"
            + json.dumps(body) + "\nWIR_JOB\n"
        )
        return command, bootstrap.encode()

    def _stream(self, command: list[str], bootstrap: bytes, log_path: Path) -> tuple[int, bytes | None, dict[str, Any]]:
        """Drain logs/progress live, keeping the large GLB marker out of the log."""
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=FLAGS)

        def feed() -> None:
            try:
                process.stdin.write(bootstrap)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        timer = threading.Timer(7200, process.kill)
        timer.start()
        output = None
        metadata = {}
        try:
            with log_path.open("wb") as log:
                for line in process.stdout:
                    marker = line.find(b"WIR_")
                    if marker > 0:
                        log.write(line[:marker])
                        line = line[marker:]
                    if line.startswith(b"WIR_RESULT:"):
                        output = line[len(b"WIR_RESULT:"):].strip()
                    elif line.startswith(b"WIR_PROGRESS:"):
                        try:
                            payload = json.loads(line[len(b"WIR_PROGRESS:"):])
                            if isinstance(payload, dict):
                                self._progress(payload)
                        except (ValueError, TypeError):
                            pass
                        log.write(line)
                        log.flush()
                    elif line.startswith(b"WIR_METADATA:"):
                        metadata = json.loads(line[len(b"WIR_METADATA:"):])
                        log.write(line)
                    else:
                        log.write(line)
                        log.flush()
            return process.wait(), output, metadata
        finally:
            timer.cancel()
            writer.join(timeout=5)
            process.stdout.close()

    def _failure(self, identifier: str) -> str:
        text = (self.directory / f"{identifier}.log").read_text(encoding="utf-8", errors="replace")
        if "out of memory" in text.lower() or "cuda_error_out_of_memory" in text.lower():
            return tr("GPU memory ran out while generating this model. Try 1024³ or close other GPU applications. The reference image is still available.")
        lines = [line for line in text.splitlines() if line.strip()]
        detail = next((line for line in reversed(lines) if "Error:" in line), lines[-1] if lines else "")
        return tr("Local 3D generation failed: {error}", error=detail[-400:])

    def _generate(self, identifier: str, request: TrellisRequest) -> None:
        started = time.monotonic()
        try:
            body = {**request.model_dump(), "model_revision": MODEL_REVISION,
                    "image": base64.b64encode((self.directory / f"{request.image}.png").read_bytes()).decode()}
            command, bootstrap = self._worker(body)
            code, output, runtime = self._stream(command, bootstrap, self.directory / f"{identifier}.log")
            if code or output is None:
                raise ValueError(self._failure(identifier))
            data = base64.b64decode(output, validate=True)
            validate_glb(data)
            (self.directory / f"{identifier}.glb").write_bytes(data)
            metadata = {"id": identifier, "request": request.model_dump(), "bytes": len(data),
                        "elapsed": round(time.monotonic() - started, 1), "model": "microsoft/TRELLIS.2-4B",
                        "model_revision": MODEL_REVISION, "runtime": runtime}
            (self.directory / f"{identifier}.json").write_text(json.dumps(metadata), encoding="utf-8")
            self._finish()
        except Exception as exc:
            self._finish(str(exc))

    def file(self, identifier: str, variant: str = "original") -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise KeyError(identifier)
        if variant not in ("original", "web"):
            raise KeyError(variant)
        path = self.directory / (f"{identifier}.web.glb" if variant == "web" else f"{identifier}.glb")
        if not path.is_file():
            raise KeyError(identifier)
        return path

    def forget(self, identifier: str) -> None:
        self.file(identifier)
        with self._lock:
            if self._job and self._job["status"] == "running":
                raise ValueError(tr("A 3D task is already running."))
            trash = self.directory / ".trash" / identifier
            trash.mkdir(parents=True, exist_ok=True)
            moved = []
            try:
                for suffix in (".glb", ".json", ".log", ".web.glb", ".web.json", ".web.log"):
                    source = self.directory / f"{identifier}{suffix}"
                    if source.is_file():
                        source.rename(trash / source.name)
                        moved.append(source)
            except OSError:
                for source in reversed(moved):
                    (trash / source.name).rename(source)
                raise

    def restore(self, identifier: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise KeyError(identifier)
        trash = self.directory / ".trash" / identifier
        if not (trash / f"{identifier}.glb").is_file():
            raise KeyError(identifier)
        with self._lock:
            suffixes = (".glb", ".json", ".log", ".web.glb", ".web.json", ".web.log")
            if any((self.directory / f"{identifier}{suffix}").exists() for suffix in suffixes):
                raise ValueError(tr("This model is already in the gallery."))
            moved = []
            try:
                for suffix in suffixes:
                    source = trash / f"{identifier}{suffix}"
                    if source.is_file():
                        source.rename(self.directory / source.name)
                        moved.append(source)
            except OSError:
                for source in reversed(moved):
                    (self.directory / source.name).rename(source)
                raise
            trash.rmdir()

    def export_web(self, identifier: str, request: WebExportRequest) -> dict[str, Any]:
        self.file(identifier)
        if not self.status()["ready"]:
            raise ValueError(tr("Install the local TRELLIS.2 runtime first."))
        job = self._begin("web_export")
        with self._lock:
            self._job["source_id"] = identifier
        threading.Thread(target=self._export_web, args=(identifier, request), daemon=True).start()
        return job

    def _export_web(self, identifier: str, request: WebExportRequest) -> None:
        try:
            body = {**request.model_dump(), "runtime": json.loads(Path(__file__).with_name("data").joinpath("trellis_web_runtime.json").read_text(encoding="utf-8")),
                    "glb": base64.b64encode(self.file(identifier).read_bytes()).decode(),
                    "blender_script": Path(__file__).with_name("trellis_blender.py").read_text(encoding="utf-8-sig")}
            command, bootstrap = self._worker(body, "trellis_web_worker.py")
            code, output, metadata = self._stream(command, bootstrap, self.directory / f"{identifier}.web.log")
            if code or output is None:
                raise ValueError(tr("Web export failed. The original model is unchanged. See the local export log."))
            data = base64.b64decode(output, validate=True)
            validate_glb(data)
            length = struct.unpack_from("<I", data, 12)[0]
            document = json.loads(data[20:20 + length])
            if not any(m.get("normalTexture") for m in document.get("materials", [])):
                raise ValueError(tr("Web export did not produce a Normal Map."))
            triangles = sum(document["accessors"][p["indices"]]["count"] // 3
                            for mesh in document["meshes"] for p in mesh["primitives"] if "indices" in p)
            if triangles > request.triangles * 1.02 or triangles == 0:
                raise ValueError(tr("Web export could not meet the triangle budget."))
            metadata["triangles"] = triangles
            metadata.update(bytes=len(data), request=request.model_dump())
            self.file(identifier).with_suffix(".web.glb").write_bytes(data)
            (self.directory / f"{identifier}.web.json").write_text(json.dumps(metadata), encoding="utf-8")
            self._finish()
        except Exception as exc:
            self._finish(str(exc))

    def gallery(self) -> list[dict[str, Any]]:
        models = []
        for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if not re.fullmatch(r"[a-f0-9]{32}", path.stem) or not path.with_suffix(".glb").is_file():
                continue
            try:
                model = json.loads(path.read_text(encoding="utf-8"))
                web = self.directory / f"{path.stem}.web.json"
                if web.is_file() and (self.directory / f"{path.stem}.web.glb").is_file():
                    model["web"] = json.loads(web.read_text(encoding="utf-8"))
                models.append(model)
            except (ValueError, OSError):
                continue
        return models
