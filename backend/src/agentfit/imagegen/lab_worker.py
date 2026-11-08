"""WSL-only launcher for additional 3D engines. Each checkout/env is independent."""
from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path.home() / ".local/share/willitrun-3d-lab"
BASE = Path.home() / ".local/share/willitrun-trellis2/env"


def run(args, *, cwd=None, env=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True, timeout=6900)


def ready(root, key):
    try:
        receipt = json.loads((root / "installed.json").read_text())
        if receipt["engine"] != key or not (root / "source/.git").is_dir():
            return False
        if key == "scenario":
            return True
        if key == "opencartridge":
            return (root / "source/.next/BUILD_ID").is_file()
        if key == "effigy":
            return (root / "source/.venv-ml/bin/python").is_file() and (root / "source/ui/dist/index.html").is_file()
        if not (root / "env/bin/python").is_file():
            return False
        if key in ("idol", "triposplat"):
            folders = [root / "weights"] + ([root / "idol-weights"] if key == "idol" else [])
            for folder in folders:
                manifest = json.loads((folder / "verified.json").read_text())
                if not manifest["files"] or not all(
                    (folder / item["path"]).is_file() and (folder / item["path"]).stat().st_size == item["bytes"]
                    for item in manifest["files"]):
                    return False
        return True
    except (OSError, KeyError, ValueError):
        return False


def checkout(repository, source, pins):
    commit = pins[repository]
    if not (source / ".git").is_dir():
        if source.exists() and any(source.iterdir()):
            raise ValueError("Existing source files were left unchanged.")
        run(["git", "init", source])
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, timeout=15)
    if head.returncode:
        run(["git", "-C", source, "fetch", "--depth", "1", f"https://github.com/{repository}.git", commit])
        run(["git", "-C", source, "checkout", "--detach", "FETCH_HEAD"])
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() != commit:
        raise ValueError("Existing source checkout differs from the pinned revision; it was left unchanged.")
    return commit


def install(key, catalog, body):
    root = ROOT / key
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source"
    commit = checkout(catalog[key]["repository"], source, body["source_pins"])
    if key in ("pixal3d", "idol", "supra-tex", "triposplat"):
        python = root / "env/bin/python"
        if not python.is_file() and key != "supra-tex":
            run([BASE / "bin/python", "-m", "venv", "--system-site-packages", root / "env"])
        env = dict(os.environ)
        env.update(CUDA_HOME=str(BASE), MAX_JOBS="2", CXX=str(BASE / "bin/x86_64-conda-linux-gnu-c++"),
                   CC=str(BASE / "bin/x86_64-conda-linux-gnu-cc"),
                   CPATH=str(BASE / "targets/x86_64-linux/include"),
                   LIBRARY_PATH=f"{BASE}/lib:{BASE}/targets/x86_64-linux/lib/stubs")
        if key == "pixal3d":
            run([python, "-m", "pip", "install", "-r", "requirements.txt"], cwd=source, env=env)
            arch = subprocess.check_output([python, "-c", "import torch; print(''.join(map(str, torch.cuda.get_device_capability())))"], text=True).strip()
            env.update(NATTEN_CUDA_ARCH=arch, NATTEN_N_WORKERS="2")
            run([python, "-m", "pip", "install", "natten==0.21.0", "--no-build-isolation"], cwd=source, env=env)
            run([python, "-m", "pip", "install", "https://github.com/LDYang694/Storages/releases/download/20260430/utils3d-0.0.2-py3-none-any.whl"], cwd=source, env=env)
            run([python, "-c", "import torch, pixal3d, natten; assert torch.cuda.is_available()"], cwd=source, env=env)
        elif key == "supra-tex":
            run([BASE / "bin/python", "scripts/install_runtime.py", "--venv", root / "env", "--python", BASE / "bin/python"], cwd=source, env=env)
            run([python, "scripts/prepare_models.py"], cwd=source, env=env)
            run([python, "scripts/check_runtime.py"], cwd=source, env=env)
        else:
            run([python, "-m", "pip", "install", "numpy", "safetensors", "pillow", "tqdm", "httpx"], cwd=source, env=env)
            if key == "idol":
                checkout("VAST-AI-Research/TripoSplat", root / "triposplat", body["source_pins"])
            helper = base64.b64decode(body["asset_helper"]).decode("utf-8-sig")
            download_script = helper + f"\nchecked_snapshot('VAST-AI/TripoSplat', Path({str(root / 'weights')!r}))\n"
            if key == "idol":
                download_script += f"checked_snapshot('Comfy-Org/Idol', Path({str(root / 'idol-weights')!r}))\n"
            run([python, "-c", download_script], cwd=source, env=env)
            run([python, "-c", "import triposplat, torch; assert torch.cuda.is_available()"], cwd=root / "triposplat" if key == "idol" else source, env=env)
    elif key == "effigy":
        tools_python = root / "tools/bin/python"
        if not tools_python.is_file():
            run([BASE / "bin/python", "-m", "venv", root / "tools"])
        run([tools_python, "-m", "pip", "install", "uv"])
        uv = root / "tools/bin/uv"
        run([uv, "sync", "--frozen"], cwd=source)
        run([uv, "sync", "--frozen", "--group", "ml"], cwd=source,
            env={**os.environ, "UV_PROJECT_ENVIRONMENT": ".venv-ml"})
        run([uv, "run", "python", "-m", "server.blender_install"], cwd=source)
        run([uv, "run", "python", "-m", "server.gltfpack_install"], cwd=source)
        run(["npm", "ci"], cwd=source / "ui")
        run(["npm", "run", "build"], cwd=source / "ui")
    elif key == "opencartridge":
        run(["docker", "info"], cwd=source)
        package_manager = ["corepack", "pnpm"] if shutil.which("corepack") else ["npx", "--yes", "pnpm@10"]
        run([*package_manager, "install", "--frozen-lockfile"], cwd=source)
        run([*package_manager, "run", "build"], cwd=source)
    receipt = {"engine": key, "source_commit": commit, "installed_at": time.time(),
               "generation_verified": False, "weights_verified": key in ("idol", "triposplat")}
    (root / "installed.json").write_text(json.dumps(receipt))
    return receipt


def launch(key):
    root = ROOT / key
    if not ready(root, key):
        raise ValueError("Install the companion studio first.")
    source = root / "source"
    port = 8000 if key == "effigy" else 3000
    url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            if response.status == 200:
                receipt = json.loads((root / "running.json").read_text()) if (root / "running.json").is_file() else {}
                if managed_process(root, receipt.get("pid")):
                    return {"url": url, "reused": True}
                raise RuntimeError("Another application occupies the companion studio port.")
    except OSError:
        pass
    command = [str(root / "tools/bin/uv"), "run", "python", "-m", "server"] if key == "effigy" else ["npm", "run", "start", "--", "--hostname", "127.0.0.1"]
    env = dict(os.environ)
    if key == "effigy":
        env["PATH"] = str(root / "tools/bin") + os.pathsep + env.get("PATH", "")
        env["EFFIGY_PORT"] = "8000"
    log = (root / "studio.log").open("ab")
    try:
        process = subprocess.Popen(command, cwd=source, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        log.close()
    (root / "running.json").write_text(json.dumps({"pid": process.pid, "url": url}))
    # Wait for this process to become reachable; return no guessed success URL on failure.
    for _ in range(45):
        if process.poll() is not None:
            raise RuntimeError("The companion studio exited. See studio.log.")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return {"url": url, "pid": process.pid}
        except OSError:
            time.sleep(1)
    os.killpg(process.pid, signal.SIGTERM)
    raise RuntimeError("The companion studio did not become reachable within 45 seconds.")


def managed_process(root, pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        return (Path("/proc") / str(pid) / "cwd").resolve(strict=True) == (root / "source").resolve()
    except OSError:
        return False


def stop(key):
    root = ROOT / key
    marker = root / "running.json"
    receipt = json.loads(marker.read_text()) if marker.is_file() else {}
    if managed_process(root, receipt.get("pid")):
        os.killpg(receipt["pid"], signal.SIGTERM)
    marker.unlink(missing_ok=True)
    return {"stopped": True}


def generate(body):
    key = body["engine"]
    root = ROOT / key
    if not ready(root, key):
        raise RuntimeError("The 3D runtime is not installed.")
    python = root / "env/bin/python"
    env = dict(os.environ)
    env.update(HF_HOME=str(ROOT / "cache"), ATTN_BACKEND="sdpa")
    with tempfile.TemporaryDirectory(prefix="job-", dir=root) as temporary:
        job = Path(temporary)
        image = job / "reference.png"
        image.write_bytes(base64.b64decode(body["image_data"], validate=True))
        output = job / "output.glb"
        if key == "pixal3d":
            command = [python, "inference.py", "--image", image, "--output", output, "--low_vram", "--resolution", str(body["resolution"])]
        elif key == "supra-tex":
            command = [python, "scripts/run_example.py", "--input", image, "--output", output]
        else:
            output = job / "output.ply"
            script = '''import sys
from pathlib import Path
from triposplat import TripoSplatPipeline
root = Path(sys.argv[1])
def one(folder, name):
    matches = list(folder.rglob(name))
    if len(matches) != 1: raise ValueError('Expected exactly one model file: ' + name)
    return str(matches[0])
weights = root / 'weights'
idol = sys.argv[7] == 'idol'
pipe = TripoSplatPipeline(
    ckpt_path=one(root / 'idol-weights', 'idol_fp16.safetensors') if idol else one(weights, 'triposplat_fp16.safetensors'),
    decoder_path=one(root / 'idol-weights', 'idol_decoder_fp16.safetensors') if idol else one(weights, 'triposplat_vae_decoder_fp16.safetensors'),
    dinov3_path=one(weights, 'dino_v3_vit_h.safetensors'),
    flux2_vae_encoder_path=one(weights, 'flux2-vae.safetensors'),
    rmbg_path=one(weights, 'birefnet.safetensors'), device='cuda')
gaussian, _ = pipe.run(sys.argv[2], seed=int(sys.argv[4]), steps=int(sys.argv[5]),
                       num_gaussians=int(sys.argv[6]), guidance_scale=4.0 if idol else 3.0, show_progress=True)
gaussian.save_ply(sys.argv[3])
'''
            command = [python, "-c", script, root, image, output, str(body["seed"]), str(body["steps"]), str(body["num_gaussians"]), key]
        run(command, cwd=root / "triposplat" if key == "idol" else root / "source", env=env)
        if not output.is_file():
            raise ValueError("The upstream CLI did not produce the expected file.")
        receipt = json.loads((root / "installed.json").read_text())
        revisions = {}
        for folder in ("weights", "idol-weights"):
            manifest = root / folder / "verified.json"
            if manifest.is_file():
                data = json.loads(manifest.read_text())
                revisions[data["repository"]] = data["revision"]
        print("WIR_METADATA:" + json.dumps({"source_commit": receipt["source_commit"], "model_revisions": revisions or None,
                                             "recipe": "compatible TripoSplat pipeline with Idol weights" if key == "idol" else "upstream CLI"}))
        print("WIR_RESULT:" + base64.b64encode(output.read_bytes()).decode())


def main():
    body = json.load(sys.stdin)
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", GRADIO_ANALYTICS_ENABLED="False",
                      GIT_TERMINAL_PROMPT="0", PIP_DISABLE_PIP_VERSION_CHECK="1", NEXT_TELEMETRY_DISABLED="1")
    catalog = body["engines"]
    if body["action"] == "probe":
        result = {}
        for key in catalog:
            root = ROOT / key
            data = {"ready": ready(root, key)}
            runtime = root / "installed.json"
            if data["ready"]:
                data.update(json.loads(runtime.read_text()))
            result[key] = data
        print(json.dumps(result))
        return
    key = body["engine"]
    if key not in catalog:
        raise ValueError("Unknown local 3D engine.")
    if body["action"] == "install":
        print("WIR_METADATA:" + json.dumps(install(key, catalog, body)))
    elif body["action"] == "launch":
        print("WIR_METADATA:" + json.dumps(launch(key)))
    elif body["action"] == "stop":
        print("WIR_METADATA:" + json.dumps(stop(key)))
    elif body["action"] == "generate":
        generate(body)
    else:
        raise ValueError("Unknown local 3D action.")


if __name__ == "__main__":
    main()
