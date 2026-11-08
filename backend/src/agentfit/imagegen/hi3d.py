"""Optional local Hi3D refinement, sharing the 3D queue and gallery."""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentfit.i18n import tr
from agentfit.imagegen.trellis import TrellisStudio, validate_glb

SOURCE_COMMIT = "13657ad09afaa92e350b461a1cd66399e44a59c4"
MODEL_REVISION = "d502e938cbedc6e87aba8884614c5ca6d177a4b6"


class Hi3DRequest(BaseModel):
    image: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    resolution: Literal[512, 1024, 1536] = 1024
    steps: int = Field(default=25, ge=1, le=50)
    seed: int = Field(default=1, ge=0, le=2147483647)
    triangles: int = Field(default=60000, ge=10000, le=200000)
    cpu_offload: bool = True


class Hi3DStudio:
    def __init__(self, studio: TrellisStudio):
        self.studio = studio
        self._probe: tuple[float, dict[str, Any]] | None = None

    @staticmethod
    def assets() -> list[dict[str, Any]]:
        return json.loads(Path(__file__).with_name("data").joinpath("hi3d_assets.json").read_text(encoding="utf-8"))

    def _worker(self, body: dict[str, Any]) -> tuple[list[str], bytes]:
        script = Path(__file__).with_name("hi3d_worker.py").read_text(encoding="utf-8-sig")
        body = {**body, "assets": self.assets(), "source_commit": SOURCE_COMMIT}
        command = ["wsl", "-d", self.studio._config()["distribution"], "--exec", "bash", "-s"]
        bootstrap = (
            'export LD_LIBRARY_PATH="$HOME/.local/share/willitrun-trellis2/env/lib:${LD_LIBRARY_PATH:-}"\n'
            'exec timeout 7100 "$HOME/.local/share/willitrun-hi3d/env/bin/python" -c '
            f"'import base64; exec(base64.b64decode(\"{base64.b64encode(script.encode()).decode()}\"))' <<'WIR_JOB'\n"
            + json.dumps(body) + "\nWIR_JOB\n"
        )
        return command, bootstrap.encode()

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        if not refresh and self._probe and time.monotonic() - self._probe[0] < 15:
            return dict(self._probe[1])
        payload = {"ready": False, "model": "Hi3D v1.0", "source": "https://github.com/hi3d-official/Hi3Dv1.0", "error": None}
        if self.studio._config().get("distribution"):
            try:
                command, bootstrap = self._worker({"probe": True})
                result = self.studio._run(command, input=bootstrap, timeout=25)
                if result.returncode == 0:
                    payload["ready"] = json.loads(result.stdout.decode("utf-8").strip())["ready"] is True
                else:
                    payload["error"] = tr("Could not check local Hi3D setup.")
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                payload["error"] = tr("Could not check local Hi3D setup.")
        self._probe = (time.monotonic(), payload)
        return dict(payload)

    def _backing_free(self) -> int | None:
        """The actual Windows disk backing the selected WSL distro, not virtual ext4 capacity."""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    with winreg.OpenKey(root, winreg.EnumKey(root, index)) as key:
                        if winreg.QueryValueEx(key, "DistributionName")[0] == self.studio._config()["distribution"]:
                            return shutil.disk_usage(winreg.QueryValueEx(key, "BasePath")[0]).free
        except (ImportError, OSError, KeyError):
            pass
        return None

    def install(self) -> dict[str, Any]:
        if not self.studio.status()["ready"]:
            raise ValueError(tr("Install the local TRELLIS.2 runtime first."))
        def work(identifier: str, progress: Any) -> None:
            self._probe = None
            progress({"percent": 1, "phase": "hi3d_setup"})
            script = Path(__file__).with_name("hi3d_setup.sh").read_bytes().replace(b"\r\n", b"\n")
            log = self.studio.directory / f"{identifier}.log"
            try:
                with log.open("wb") as output:
                    result = subprocess.run(["wsl", "-d", self.studio._config()["distribution"], "--exec", "bash", "-s"],
                                            input=script, stdout=output, stderr=subprocess.STDOUT, timeout=7200,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    raise ValueError(tr("Hi3D setup failed. See the local setup log."))
                command, bootstrap = self._worker({"prepare": True, "host_free_bytes": self._backing_free()})
                code, _, _ = self.studio._stream(command, bootstrap, self.studio.directory / f"{identifier}.weights.log")
                if code or not self.status(refresh=True)["ready"]:
                    raise ValueError(tr("Hi3D setup failed. See the local setup log."))
            finally:
                self._probe = None
        return self.studio.start_task("hi3d_install", work)

    def _reference(self, source_id: str, image: str | None) -> str:
        self.studio.file(source_id)
        recipe = json.loads((self.studio.directory / f"{source_id}.json").read_text(encoding="utf-8"))
        reference = image or recipe.get("request", {}).get("image")
        if not isinstance(reference, str):
            raise ValueError(tr("Choose a reference photo for Hi3D."))
        import re
        if not re.fullmatch(r"[a-f0-9]{32}", reference) or not (self.studio.directory / f"{reference}.png").is_file():
            raise ValueError(tr("Choose a reference photo for Hi3D."))
        return reference

    def refine(self, source_id: str, request: Hi3DRequest) -> dict[str, Any]:
        reference = self._reference(source_id, request.image)
        if not self.status()["ready"]:
            raise ValueError(tr("Install Hi3D locally first."))
        return self.studio.start_task("hi3d_refine", lambda identifier, progress: self._refine(identifier, source_id, reference, request))

    def _refine(self, identifier: str, source_id: str, reference: str, request: Hi3DRequest) -> None:
        started = time.monotonic()
        source = self.studio.file(source_id).read_bytes()
        image = (self.studio.directory / f"{reference}.png").read_bytes()
        body = {**request.model_dump(), "image": base64.b64encode(image).decode(), "glb": base64.b64encode(source).decode()}
        command, bootstrap = self._worker(body)
        code, output, runtime = self.studio._stream(command, bootstrap, self.studio.directory / f"{identifier}.log")
        if code or output is None:
            log = (self.studio.directory / f"{identifier}.log").read_text(encoding="utf-8", errors="replace")
            if "out of memory" in log.lower():
                raise ValueError(tr("Hi3D ran out of memory. Try a lower resolution or enable CPU offloading. The source model is unchanged."))
            raise ValueError(tr("Hi3D refinement failed. The source model is unchanged. See the local job log."))
        data = base64.b64decode(output, validate=True)
        validate_glb(data)
        if runtime.get("resolution") != request.resolution or not runtime.get("triangles"):
            raise ValueError(tr("Hi3D returned invalid geometry or a different resolution."))
        if hashlib.sha256(self.studio.file(source_id).read_bytes()).hexdigest() != hashlib.sha256(source).hexdigest():
            raise ValueError(tr("The source model changed during refinement."))
        recipe = {"id": identifier, "model": "hi3d-official/Hi3Dv1.0", "model_revision": MODEL_REVISION,
                  "source_id": source_id, "source_sha256": hashlib.sha256(source).hexdigest(),
                  "reference_sha256": hashlib.sha256(image).hexdigest(),
                  "request": {**request.model_dump(), "image": reference}, "runtime": runtime,
                  "geometry_only": runtime.get("geometry_only", True), "appearance": runtime.get("appearance", "geometry_only"),
                  "bytes": len(data), "elapsed": round(time.monotonic() - started, 1)}
        target = self.studio.directory / f"{identifier}.glb"
        try:
            target.write_bytes(data)
            target.with_suffix(".json").write_text(json.dumps(recipe), encoding="utf-8")
        except OSError:
            target.unlink(missing_ok=True)
            raise
