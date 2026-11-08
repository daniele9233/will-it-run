"""Additional 3D engines and companion studios, preserving the existing TRELLIS gallery."""
from __future__ import annotations

import base64
import json
import re
import struct
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentfit import media_runtime
from agentfit.i18n import tr
from agentfit.imagegen.trellis import TrellisStudio, validate_glb

ENGINES = {
    "pixal3d": {"name": "Pixal3D", "repository": "TencentARC/Pixal3D", "kind": "generation", "format": "glb", "license": "MIT + dependencies"},
    "idol": {"name": "Idol", "repository": "speridlabs/idol-sdk", "kind": "generation", "format": "ply", "license": "Apache-2.0 + dependencies"},
    "triposplat": {"name": "TripoSplat", "repository": "VAST-AI-Research/TripoSplat", "kind": "generation", "format": "ply", "license": "MIT + dependencies"},
    "supra-tex": {"name": "SUPRA-Tex", "repository": "Supra-3D/SUPRA-Tex", "kind": "generation", "format": "glb", "license": "Material adapter: non-commercial research"},
    "effigy": {"name": "Effigy", "repository": "SifuInTheShell/Effigy", "kind": "studio", "format": "glb", "license": "MIT + dependencies"},
    "opencartridge": {"name": "OpenCartridge", "repository": "camenduru/OpenCartridge", "kind": "studio", "format": "glb", "license": "MIT; cartridge licenses vary"},
    "scenario": {"name": "Scenario expert tools", "repository": "scenario-labs/skills", "kind": "skills", "format": "glb", "license": "MIT; DCC applications required"},
}


def validate_embedded_glb(data: bytes) -> None:
    try:
        validate_glb(data)
        length = struct.unpack_from("<I", data, 12)[0]
        document = json.loads(data[20:20 + length])
        for resource in [*document.get("buffers", []), *document.get("images", [])]:
            uri = resource.get("uri")
            if uri is not None and (not isinstance(uri, str) or not uri.startswith("data:")):
                raise ValueError
    except (ValueError, TypeError, AttributeError, struct.error) as exc:
        raise ValueError(tr("Import a self-contained GLB with embedded textures and buffers.")) from exc


class LabRequest(BaseModel):
    engine: Literal["pixal3d", "idol", "triposplat", "supra-tex"]
    image: str = Field(pattern=r"^[a-f0-9]{32}$")
    seed: int = Field(default=42, ge=0, le=2147483647)
    resolution: Literal[1024, 1536] = 1024
    steps: int = Field(default=50, ge=1, le=50)
    num_gaussians: Literal[32768, 65536, 131072, 262144] = 262144


class ThreeDLab:
    def __init__(self, studio: TrellisStudio):
        self.studio = studio
        self.directory = studio.directory / "lab"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: tuple[float, dict[str, Any]] | None = None

    def _worker(self, body: dict[str, Any]) -> tuple[list[str], bytes]:
        distro = self.studio._config().get("distribution")
        if not distro:
            raise ValueError(tr("Select a WSL distribution in the existing 3D setup first."))
        source = Path(__file__).with_name("lab_worker.py").read_text(encoding="utf-8-sig")
        encoded = base64.b64encode(source.encode()).decode()
        bootstrap = ('export LD_LIBRARY_PATH="$HOME/.local/share/willitrun-trellis2/env/lib:${LD_LIBRARY_PATH:-}"\n'
                     'exec timeout 7100 "$HOME/.local/share/willitrun-trellis2/env/bin/python" -c '
                     f"'import base64; exec(base64.b64decode(\"{encoded}\"))' <<'WIR_JOB'\n"
                     + json.dumps({**body, "engines": ENGINES,
                                   "source_pins": json.loads(Path(media_runtime.__file__).with_name("media_sources.json").read_text(encoding="utf-8")),
                                   "asset_helper": base64.b64encode(Path(media_runtime.__file__).read_bytes()).decode()}) + "\nWIR_JOB\n")
        return ["wsl", "-d", distro, "--exec", "bash", "-s"], bootstrap.encode()

    def status(self) -> dict[str, Any]:
        if self._cache and time.monotonic() - self._cache[0] < 15:
            return self._cache[1]
        installed: dict[str, Any] = {}
        error = None
        try:
            command, bootstrap = self._worker({"action": "probe"})
            result = self.studio._run(command, input=bootstrap, timeout=25)
            if result.returncode:
                raise ValueError(tr("The additional 3D runtimes could not be reached through WSL."))
            installed = json.loads(result.stdout.decode("utf-8"))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
        payload = {"engines": [{"id": key, **value, **installed.get(key, {}),
                                "ready": bool(installed.get(key, {}).get("ready", False))} for key, value in ENGINES.items()], "error": error}
        self._cache = (time.monotonic(), payload)
        return payload

    def action(self, engine: str, action: Literal["install", "launch", "stop"]) -> dict[str, Any]:
        if engine not in ENGINES:
            raise ValueError(tr("Unknown 3D engine."))
        if action in ("launch", "stop") and ENGINES[engine]["kind"] != "studio":
            raise ValueError(tr("This option is not a companion studio."))
        command, bootstrap = self._worker({"action": action, "engine": engine})
        def work(identifier: str, progress: Any) -> None:
            self._cache = None
            progress({"percent": 5, "phase": "lab_setup" if action == "install" else "lab_launch"})
            try:
                code, _, metadata = self.studio._stream(command, bootstrap, self.directory / f"{identifier}.log")
                if code:
                    raise ValueError(tr("The 3D tool could not complete this action. See its local job log."))
                if metadata:
                    (self.directory / f"{engine}.runtime.json").write_text(json.dumps(metadata), encoding="utf-8")
            finally:
                self._cache = None
        return self.studio.start_task(f"lab_{action}", work)

    def generate(self, request: LabRequest) -> dict[str, Any]:
        if not (self.studio.directory / f"{request.image}.png").is_file():
            raise ValueError(tr("Upload a reference image first."))
        current = next(item for item in self.status()["engines"] if item["id"] == request.engine)
        if not current["ready"]:
            raise ValueError(tr("Install the selected 3D engine first."))
        def work(identifier: str, progress: Any) -> None:
            progress({"percent": 5, "phase": "lab_generating"})
            image = (self.studio.directory / f"{request.image}.png").read_bytes()
            body = {**request.model_dump(), "action": "generate", "image_data": base64.b64encode(image).decode()}
            command, bootstrap = self._worker(body)
            code, output, metadata = self.studio._stream(command, bootstrap, self.directory / f"{identifier}.log")
            if code or output is None:
                raise ValueError(tr("The 3D engine failed. See its local job log. Existing models are unchanged."))
            data = base64.b64decode(output, validate=True)
            suffix = ENGINES[request.engine]["format"]
            if suffix == "glb":
                validate_embedded_glb(data)
            elif not data.startswith(b"ply\n") and not data.startswith(b"ply\r\n"):
                raise ValueError(tr("The engine returned an invalid Gaussian splat."))
            target = self.studio.directory if suffix == "glb" else self.directory
            applied = {"image": request.image, "engine": request.engine, "resolution": None, "steps": None, "seed": None}
            if request.engine == "pixal3d":
                applied["resolution"] = request.resolution
            elif suffix == "ply":
                applied.update(steps=request.steps, seed=request.seed, num_gaussians=request.num_gaussians)
            recipe = {"id": identifier, "model": ENGINES[request.engine]["name"], "engine": request.engine,
                      "request": applied, "bytes": len(data), "elapsed": time.time() - self.studio.job()["started_at"],
                      "runtime": metadata, "format": suffix, "created_at": time.time()}
            output_path = target / f"{identifier}.{suffix}"
            try:
                output_path.write_bytes(data)
                output_path.with_suffix(".json").write_text(json.dumps(recipe), encoding="utf-8")
            except OSError:
                output_path.unlink(missing_ok=True)
                raise
        return self.studio.start_task("lab_generate", work)

    def splats(self) -> list[dict[str, Any]]:
        result = []
        for path in self.directory.glob("*.ply"):
            try:
                result.append(json.loads(path.with_suffix(".json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda item: item.get("created_at", 0), reverse=True)

    def splat_file(self, identifier: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise KeyError(identifier)
        path = self.directory / f"{identifier}.ply"
        if not path.is_file() or not path.with_suffix(".json").is_file():
            raise KeyError(identifier)
        return path

    def import_glb(self, encoded: str, engine: str) -> dict[str, str]:
        if engine not in ENGINES:
            raise ValueError(tr("Unknown 3D engine."))
        if self.studio.job() and self.studio.job()["status"] == "running":
            raise ValueError(tr("A 3D task is already running."))
        if len(encoded) > 140_000_000:
            raise ValueError(tr("Choose a GLB smaller than 100 MB."))
        data = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
        if len(data) > 100 * 1024**2:
            raise ValueError(tr("Choose a GLB smaller than 100 MB."))
        validate_embedded_glb(data)
        import uuid
        identifier = uuid.uuid4().hex
        path = self.studio.directory / f"{identifier}.glb"
        recipe = {"id": identifier, "model": ENGINES[engine]["name"], "engine": engine, "bytes": len(data),
                  "request": {"image": None, "steps": None, "resolution": None}, "elapsed": None,
                  "runtime": {"imported": True, "model_revision": None}, "created_at": time.time()}
        try:
            path.write_bytes(data)
            path.with_suffix(".json").write_text(json.dumps(recipe), encoding="utf-8")
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return {"id": identifier}
