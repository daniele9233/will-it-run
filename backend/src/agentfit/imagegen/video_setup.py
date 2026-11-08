"""Explicit, resumable Ref2VA setup in the existing ComfyUI, with pinned and verified files."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx

from agentfit import paths
from agentfit.i18n import tr
from agentfit.imagegen import ref2va
from agentfit.media_runtime import FLAGS, digest_file, safe_member, source_checkout


class SetupCancelled(Exception):
    pass


def asset_path(root: Path, asset: dict[str, Any]) -> Path:
    target = root / safe_member(asset["folder"]) / safe_member(asset["filename"])
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("The video model path escapes its destination.")
    return target


def verified_asset(root: Path, asset: dict[str, Any]) -> bool:
    try:
        target = asset_path(root, asset)
        info = json.loads(target.with_name(target.name + ".verified.json").read_text(encoding="utf-8"))
        return (target.is_file() and target.stat().st_size == asset["size"]
                and info["sha256"] == asset["sha256"] and info["revision"] == asset["revision"]
                and info["mtime_ns"] == target.stat().st_mtime_ns)
    except (OSError, ValueError, KeyError, TypeError):
        return False


class VideoModelSetup:
    """Shared verified download lifecycle; each video runtime supplies its own prerequisites."""
    def __init__(self, engine: Any, directory: Path, assets: list[dict[str, Any]]) -> None:
        self.engine = engine
        self.root = directory
        self.assets = assets
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.job: dict[str, Any] = {"status": "idle", "percent": 0, "received": 0,
                                    "total": sum(a["size"] for a in assets), "file": "", "error": None}
        self._environment_at = 0.0
        self._environment: dict[str, Any] = {}
        self._received: dict[str, int] = {}

    def environment(self) -> dict[str, Any]:
        raise NotImplementedError

    def prepare(self) -> None:
        """Optional runtime-specific source setup, after the disk-space gate."""

    def status(self) -> dict[str, Any]:
        with self.lock:
            job = dict(self.job)
        environment = self.environment()
        return {"job": job, "environment": environment,
                "weights_ready": all(verified_asset(self.root, a) for a in self.assets),
                "models_dir": str(self.root)}

    def start(self) -> dict[str, Any]:
        with self.lock:
            if self.job["status"] == "running":
                return dict(self.job)
            self.cancelled.clear()
            self._received.clear()
            self.job.update(status="running", percent=0, received=0, file="", error=None)
            threading.Thread(target=self._run, name="video-model-setup", daemon=True).start()
            return dict(self.job)

    def cancel(self) -> dict[str, Any]:
        self.cancelled.set()
        with self.lock:
            return dict(self.job)

    def _check_cancel(self) -> None:
        if self.cancelled.is_set():
            raise SetupCancelled

    def _progress(self, received: int, filename: str) -> None:
        with self.lock:
            self._received[filename] = received
            total = sum(self._received.values())
            self.job.update(file=filename, received=total,
                            percent=min(99, int(99 * total / self.job["total"])))

    def _download(self, asset: dict[str, Any], client: httpx.Client) -> None:
        self._check_cancel()
        target = asset_path(self.root, asset)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == asset["size"] and digest_file(target) == asset["sha256"]:
            self._stamp(target, asset)
            self._progress(asset["size"], asset["filename"])
            return
        partial = target.with_name(target.name + ".partial")
        if partial.is_symlink():
            raise ValueError("The temporary video download is a symbolic link.")
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > asset["size"]:
            partial.unlink()
            offset = 0
        self._progress(offset, asset["filename"])
        if offset != asset["size"]:
            with client.stream("GET", asset["url"], headers={"Range": f"bytes={offset}-"} if offset else {}) as response:
                response.raise_for_status()
                if response.status_code == 206:
                    if not response.headers.get("content-range", "").startswith(f"bytes {offset}-"):
                        raise ValueError("The resumed video download has an invalid range.")
                elif response.status_code == 200:
                    offset = 0
                else:
                    raise ValueError("Unexpected video download response.")
                with partial.open("ab" if offset else "wb") as output:
                    for chunk in response.iter_bytes(1024**2):
                        self._check_cancel()
                        offset += len(chunk)
                        if offset > asset["size"]:
                            raise ValueError("The video download exceeds its published size.")
                        output.write(chunk)
                        self._progress(offset, asset["filename"])
        self._check_cancel()
        if partial.stat().st_size != asset["size"] or digest_file(partial) != asset["sha256"]:
            partial.unlink(missing_ok=True)
            raise ValueError("Video model size or SHA-256 verification failed.")
        partial.replace(target)
        self._stamp(target, asset)
        self._progress(asset["size"], asset["filename"])

    @staticmethod
    def _stamp(target: Path, asset: dict[str, Any]) -> None:
        marker = target.with_name(target.name + ".verified.json")
        temporary = marker.with_name(marker.name + ".tmp")
        temporary.write_text(json.dumps({"sha256": asset["sha256"], "revision": asset["revision"],
                                         "mtime_ns": target.stat().st_mtime_ns}), encoding="utf-8")
        temporary.replace(marker)

    def _run(self) -> None:
        try:
            self._check_cancel()
            environment = self.environment()
            if not environment["ready"]:
                raise ValueError(environment["error"] or tr("The local video runtime prerequisites are missing."))
            self.root.mkdir(parents=True, exist_ok=True)
            assets = self.assets
            pending = 0
            for asset in assets:
                target = asset_path(self.root, asset)
                if verified_asset(self.root, asset):
                    continue
                if target.is_file() and target.stat().st_size == asset["size"] and digest_file(target) == asset["sha256"]:
                    self._stamp(target, asset)
                    continue
                partial = target.with_name(target.name + ".partial")
                if partial.is_symlink():
                    raise ValueError("The temporary video download is a symbolic link.")
                existing = partial.stat().st_size if partial.is_file() else 0
                pending += asset["size"] - existing if existing <= asset["size"] else asset["size"]
            # Downloads stream into their final directory and rename in place: no second full copy.
            disk = shutil.disk_usage(self.root)
            reserve = min(30 * 1024**3, max(10 * 1024**3, int(disk.total * .15)))
            if disk.free < pending + reserve:
                raise ValueError(tr("Not enough disk space for the video models and the safety reserve."))
            self.prepare()
            with (
                httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": "WillItRun/Ref2VA-setup"}) as client,
                ThreadPoolExecutor(max_workers=3, thread_name_prefix="ref2va-download") as workers,
            ):
                tasks = [workers.submit(self._download, asset, client) for asset in assets]
                try:
                    for task in as_completed(tasks):
                        task.result()
                except Exception:
                    self.cancelled.set()
                    raise
            with self.lock:
                self.job.update(status="done", percent=100, received=sum(a["size"] for a in assets), file="")
        except SetupCancelled:
            with self.lock:
                self.job.update(status="cancelled", error=None)
        except Exception as exc:
            with self.lock:
                self.job.update(status="failed", error=str(exc))
        finally:
            self._environment_at = 0.0


class Ref2VASetup(VideoModelSetup):
    def __init__(self, engine: Any, models_dir: Path) -> None:
        super().__init__(engine, models_dir / "video" / "ref2va", ref2va.assets())

    def prepare(self) -> None:
        install = self.engine.install()
        source_checkout(ref2va.REPOSITORY, install.root / "custom_nodes" / "ComfyUI-Ref2VA-VSA",
                        self.root, paths.logs_dir() / "ref2va-setup.log")

    def status(self) -> dict[str, Any]:
        result = super().status()
        return {**result, "source_ready": result["environment"].get("source_commit") == ref2va.COMMIT,
                "source": f"https://github.com/{ref2va.REPOSITORY}", "source_commit": ref2va.COMMIT,
                "recommended_vram_gb": 24}

    def environment(self) -> dict[str, Any]:
        if time.monotonic() - self._environment_at < 60:
            return dict(self._environment)
        result: dict[str, Any] = {"ready": False, "error": None, "vram_gb": None, "source_commit": None}
        install = self.engine.install() if self.engine is not None else None
        if install is None or install.python is None:
            result["error"] = tr("ComfyUI was not found on this PC.")
        else:
            query = (
                "import json,torch,comfy_kitchen as ck; "
                "cuda=ck.list_backends().get('cuda',{}); "
                "print(json.dumps({'ready':bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported() "
                "and cuda.get('available') and 'sol_attn' in cuda.get('capabilities',[])), "
                "'vram_gb':torch.cuda.get_device_properties(0).total_memory/1024**3 if torch.cuda.is_available() else None}))"
            )
            try:
                probe = subprocess.run([str(install.python), "-c", query], cwd=install.root,
                                       capture_output=True, timeout=60, creationflags=FLAGS)
                if probe.returncode:
                    raise ValueError(probe.stderr.decode("utf-8", errors="replace")[-1000:])
                result.update(json.loads(probe.stdout.decode("utf-8").splitlines()[-1]))
                if not result["ready"]:
                    result["error"] = tr("Ref2VA-VSA needs CUDA BF16 and the comfy-kitchen sol_attn CUDA backend.")
                source = install.root / "custom_nodes" / "ComfyUI-Ref2VA-VSA"
                if (source / ".git").is_dir():
                    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source,
                                              capture_output=True, timeout=15, creationflags=FLAGS)
                    if revision.returncode == 0:
                        result["source_commit"] = revision.stdout.decode().strip()
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                result.update(ready=False, error=str(exc))
        self._environment, self._environment_at = result, time.monotonic()
        return dict(result)
