"""Isolated Blender web export runner; executed in WSL, never in the core."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path.home() / ".local/share/willitrun-trellis2"


def progress(percent, phase):
    print("WIR_PROGRESS:" + json.dumps({"percent": percent, "phase": phase}), flush=True)


def prepare(asset):
    os.environ["LD_LIBRARY_PATH"] = str(ROOT / "env/lib") + ":" + os.environ.get("LD_LIBRARY_PATH", "")
    directory = ROOT / "web"
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / f"blender-{asset['version']}-linux-x64/blender"
    if not binary.is_file():
        if shutil.disk_usage(directory).free < asset["bytes"] * 5 + 10 * 1024**3:
            raise RuntimeError("Not enough space for the web exporter.")
        archive = directory / "blender.tar.xz"
        progress(2, "web_setup")
        with urllib.request.urlopen(asset["url"], timeout=120) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        digest = hashlib.sha256()
        with archive.open("rb") as source:
            for data in iter(lambda: source.read(8 * 1024**2), b""):
                digest.update(data)
        if archive.stat().st_size != asset["bytes"] or digest.hexdigest() != asset["sha256"]:
            archive.unlink()
            raise RuntimeError("Blender download checksum mismatch.")
        progress(8, "web_setup")
        with tarfile.open(archive) as source:
            source.extractall(directory, filter="data")
        archive.unlink()
    libraries = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, check=False)
    if "libSM.so.6 => not found" in libraries.stdout or "libICE.so.6 => not found" in libraries.stdout:
        subprocess.run([str(ROOT / "bin/micromamba"), "install", "-y", "-p", str(ROOT / "env"),
                        "-c", "conda-forge", "xorg-libsm", "xorg-libice"], check=True,
                       env={**os.environ, "MAMBA_ROOT_PREFIX": str(ROOT / "mamba")})
    subprocess.run([str(binary), "--background", "--version"], check=True)
    return binary


def main():
    body = json.load(sys.stdin)
    binary = prepare(body["runtime"])
    if body.get("prepare"):
        return
    job_root = ROOT / "web-jobs"
    job_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="export-", dir=job_root) as work:
        work = Path(work)
        (work / "source.glb").write_bytes(base64.b64decode(body["glb"], validate=True))
        (work / "export.py").write_text(body["blender_script"], encoding="utf-8")
        (work / "job.json").write_text(json.dumps({"directory": str(work), "triangles": body["triangles"],
                                                   "texture_size": body["texture_size"]}), encoding="utf-8")
        environment = {**os.environ, "DO_NOT_TRACK": "1"}
        process = subprocess.Popen([str(binary), "--background", "--factory-startup", "--python-exit-code", "1",
                                    "--python", str(work / "export.py"), "--", str(work / "job.json")],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
        for line in process.stdout:
            print(line, end="", flush=True)
        if process.wait():
            raise RuntimeError("Blender web export failed. See the generation log.")
        metadata = json.loads((work / "report.json").read_text())
        print("WIR_METADATA:" + json.dumps(metadata), flush=True)
        print("WIR_RESULT:" + base64.b64encode((work / "web.glb").read_bytes()).decode(), flush=True)


if __name__ == "__main__":
    main()
