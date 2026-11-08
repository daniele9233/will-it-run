"""Isolated local media runtimes; never install ML dependencies in the core environment."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def local_media_tools() -> dict[str, str | None]:
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        found = shutil.which(name)
        # Explorer-launched apps may inherit an older PATH than a terminal.
        installed = Path.home() / "AppData/Local/Programs/FFmpeg/bin" / f"{name}.exe"
        result[name] = found or (str(installed) if installed.is_file() else None)
    return result


class ModelDiskSpaceError(ValueError):
    def __init__(self, target: Path, free: int, required: int, reserve: int):
        super().__init__("Not enough disk space for the model, temporary download and safety reserve.")
        self.target = target
        self.free = free
        self.required = required
        self.reserve = reserve


def private_env(root: Path, *, offline: bool = False) -> dict[str, str]:
    env = {**os.environ, "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
           "GRADIO_ANALYTICS_ENABLED": "False", "HF_HOME": str(root / "cache"),
           "UV_CACHE_DIR": str(root / "uv-cache"), "PIP_CACHE_DIR": str(root / "pip-cache"),
           "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    env.update(UV_PYTHON_INSTALL_DIR=str(root / "python"), GIT_TERMINAL_PROMPT="0", PIP_DISABLE_PIP_VERSION_CHECK="1")
    if offline:
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return env


def run_logged(command: list[str], root: Path, log: Path, *, cwd: Path | None = None,
               offline: bool = False, timeout: int = 7200) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as stream:
        result = subprocess.run(command, cwd=cwd, env=private_env(root, offline=offline),
                                stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, creationflags=FLAGS)
    if result.returncode:
        raise ValueError(f"Local media command failed (exit {result.returncode}). See {log.name}.")


def install_local_package(python: Path, source: Path, root: Path, log: Path, *, headless_distribution: str = "") -> None:
    """Keep upstream pins, excluding only Gradio for an explicitly embedded audio runtime."""
    pip = [str(python), "-m", "pip", "install"]
    constraints = source / "constraints" / "recommended.txt"
    options = ["-c", str(constraints)] if constraints.is_file() else []
    if not headless_distribution:
        run_logged([*pip, str(source), *options], root, log)
        return
    # Install the unchanged source first so its declared requirements can be read
    # by the target interpreter, including Python-version and extra markers.
    run_logged([*pip, "--no-deps", str(source), *options], root, log)
    query = "import importlib.metadata as m,json,sys; print(json.dumps(m.requires(sys.argv[1])))"
    result = subprocess.run([str(python), "-c", query, headless_distribution], capture_output=True,
                            env=private_env(root), timeout=30, creationflags=FLAGS)
    with log.open("ab") as stream:
        stream.write(result.stderr)
    if result.returncode:
        raise ValueError("The installed audio package requirements could not be read.")
    requirements = json.loads(result.stdout)
    if not isinstance(requirements, list) or not requirements or any(not isinstance(r, str) or not re.match(r"^[A-Za-z0-9]", r) for r in requirements):
        raise ValueError("The installed audio package requirements are invalid.")
    excluded = [r for r in requirements if re.match(r"(?i)^gradio(?=\s|[<>=!~;\[@]|$)", r)]
    runtime_requirements = [r for r in requirements if r not in excluded]
    if not excluded or not runtime_requirements:
        raise ValueError("The embedded audio dependency profile no longer matches the pinned source.")
    run_logged([*pip, *runtime_requirements, *options], root, log)
    profile = {"distribution": headless_distribution, "excluded_demo_dependencies": excluded,
               "runtime_requirements": runtime_requirements}
    (root / "dependency-profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")


def isolated_python(root: Path, log: Path) -> Path:
    python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    if not uv:
        candidate = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
        if candidate.is_file():
            uv = str(candidate)
    if not python.is_file():
        if not uv:
            raise ValueError("uv is required to prepare isolated Python 3.12. Existing runtimes are unchanged.")
        root.mkdir(parents=True, exist_ok=True)
        run_logged([uv, "venv", "--python", "3.12", "--seed", str(root / "env")], root, log)
    else:
        # uv can create python.exe before a failed seed download. Repair that environment in place.
        pip = subprocess.run([str(python), "-c", "import pip"], capture_output=True,
                             timeout=30, creationflags=FLAGS, env=private_env(root))
        if pip.returncode:
            if not uv:
                raise ValueError("uv is required to resume this incomplete Python environment.")
            run_logged([uv, "pip", "install", "--python", str(python), "pip"], root, log)
    return python


def source_checkout(repository: str, target: Path, root: Path, log: Path) -> str:
    """Resolve once, then keep that exact commit; never pull over an existing checkout."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid source repository.")
    git = shutil.which("git")
    if not git:
        raise ValueError("Git is required for this local runtime.")
    pins = json.loads(Path(__file__).with_name("media_sources.json").read_text(encoding="utf-8"))
    revision = pins[repository]
    if not (target / ".git").is_dir():
        if target.exists() and any(target.iterdir()):
            raise ValueError("The source directory is not empty; existing files were left unchanged.")
        run_logged([git, "init", str(target)], root, log)
    result = subprocess.run([git, "rev-parse", "HEAD"], cwd=target, capture_output=True,
                            timeout=15, creationflags=FLAGS, env=private_env(root))
    if result.returncode:
        run_logged([git, "-C", str(target), "fetch", "--depth", "1", f"https://github.com/{repository}.git", revision], root, log)
        run_logged([git, "-C", str(target), "checkout", "--detach", "FETCH_HEAD"], root, log)
        result = subprocess.run([git, "rev-parse", "HEAD"], cwd=target, capture_output=True,
                                timeout=15, creationflags=FLAGS, env=private_env(root))
    commit = result.stdout.decode().strip()
    if result.returncode or commit != revision:
        raise ValueError("The source commit could not be verified.")
    return commit


def safe_member(name: str) -> Path:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe model file path.")
    return Path(*path.parts)


def digest_file(path: Path, algorithm: str = "sha256", *, git_blob: bool = False) -> str:
    digest = hashlib.new(algorithm)
    if git_blob:
        digest.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_snapshot(repository: str, target: Path, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch published sizes/digests, reserve space, download at an immutable revision and verify bytes."""
    import httpx

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid model repository.")
    target.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=120)
    try:
        response = client.get(f"https://huggingface.co/api/models/{repository}", params={"blobs": "true"})
        response.raise_for_status()
        info = response.json()
        revision = info["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("The model revision is invalid.")
        files = []
        for item in info["siblings"]:
            name = item["rfilename"]
            if name.startswith(".") or name.lower().endswith((".md", ".png", ".jpg", ".mp4", ".wav")):
                continue
            safe_member(name)
            lfs = item.get("lfs") or {}
            size = item.get("size", lfs.get("size"))
            digest = lfs.get("sha256") or item.get("blobId")
            if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", digest):
                raise ValueError(f"Published size/checksum missing for {name}.")
            files.append({"path": name, "bytes": size, "digest": digest})
        if not files or not any(f["path"].endswith((".safetensors", ".bin", ".pt", ".pth")) for f in files):
            raise ValueError("The model manifest contains no weights.")
        pending = []
        for item in files:
            destination = target / safe_member(item["path"])
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError("The model path escapes its destination directory.")
            if (destination.is_file() and destination.stat().st_size == item["bytes"]
                    and digest_file(destination, "sha1" if len(item["digest"]) == 40 else "sha256", git_blob=len(item["digest"]) == 40) == item["digest"]):
                continue
            pending.append(item)
        if pending:
            disk = shutil.disk_usage(target)
            reserve = min(30 * 1024**3, max(10 * 1024**3, int(disk.total * .15)))
            required = sum(item["bytes"] for item in pending) + max(item["bytes"] for item in pending) + reserve
            if disk.free < required:
                raise ModelDiskSpaceError(target, disk.free, required, reserve)
        for item in pending:
            destination = target / safe_member(item["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(destination.name + ".partial")
            if partial.is_symlink():
                raise ValueError("The temporary download path is a symbolic link.")
            try:
                with client.stream("GET", f"https://huggingface.co/{repository}/resolve/{revision}/{item['path']}") as download:
                    download.raise_for_status()
                    written = 0
                    with partial.open("wb") as stream:
                        for chunk in download.iter_bytes(1024**2):
                            written += len(chunk)
                            if written > item["bytes"]:
                                raise ValueError("The downloaded file exceeds its published size.")
                            stream.write(chunk)
                if partial.stat().st_size != item["bytes"] or digest_file(partial, "sha1" if len(item["digest"]) == 40 else "sha256", git_blob=len(item["digest"]) == 40) != item["digest"]:
                    raise ValueError("Model checksum verification failed.")
                partial.replace(destination)
            finally:
                partial.unlink(missing_ok=True)
        manifest = {"repository": repository, "revision": revision, "files": files}
        (target / "verified.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest
    finally:
        if own_client:
            client.close()


def snapshot_present(target: Path) -> bool:
    try:
        manifest = json.loads((target / "verified.json").read_text(encoding="utf-8"))
        return bool(manifest["files"]) and all(
            (target / safe_member(item["path"])).is_file()
            and (target / safe_member(item["path"])).stat().st_size == item["bytes"] for item in manifest["files"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
