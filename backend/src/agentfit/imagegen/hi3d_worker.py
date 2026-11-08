"""Executed only in the isolated Linux Hi3D environment; GPU imports stay inside refine()."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path.home() / ".local/share/willitrun-hi3d"


def progress(percent, phase, **details):
    print("WIR_PROGRESS:" + json.dumps({"percent": percent, "phase": phase, **details}), flush=True)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024**2), b""):
            value.update(chunk)
    return value.hexdigest()


def manifest_digest(body):
    return hashlib.sha256(json.dumps(body["assets"], sort_keys=True).encode()).hexdigest()


def asset_path(asset):
    return ROOT / "models" / asset["repo"] / asset["file"]


def stamps(assets):
    return [{"repo": a["repo"], "file": a["file"], "bytes": asset_path(a).stat().st_size,
             "mtime_ns": asset_path(a).stat().st_mtime_ns} for a in assets]


def ready(body):
    try:
        marker = json.loads((ROOT / "verified.json").read_text(encoding="utf-8"))
        commit = subprocess.run(["git", "-C", str(ROOT / "repo"), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        return (commit == body["source_commit"] and marker["manifest"] == manifest_digest(body)
                and marker["files"] == stamps(body["assets"])
                and all(asset_path(a).stat().st_size == a["bytes"] for a in body["assets"])
                and all(importlib.util.find_spec(name) is not None for name in ("sparc3d", "flash_attn", "spconv", "torch")))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        return False


def prepare(body):
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "verified.json").unlink(missing_ok=True)
    pending = [a for a in body["assets"] if not asset_path(a).is_file()
               or asset_path(a).stat().st_size != a["bytes"] or digest(asset_path(a)) != a["sha256"]]
    required = sum(a["bytes"] for a in pending)
    if required and (body.get("host_free_bytes") is None or body["host_free_bytes"] < required * 1.15 + 30 * 1024**3):
        raise RuntimeError("Cannot download Hi3D without a verified 30 GB backing-disk reserve.")
    for index, asset in enumerate(pending):
        progress(10 + int(75 * index / max(1, len(pending))), "hi3d_download")
        path = asset_path(asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        url = f"https://huggingface.co/{asset['repo']}/resolve/{asset['revision']}/{asset['file']}"
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, 8 * 1024**2)
            if temporary.stat().st_size != asset["bytes"] or digest(temporary) != asset["sha256"]:
                raise RuntimeError(f"Hi3D asset checksum mismatch: {asset['file']}")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    progress(95, "hi3d_verify")
    marker = {"manifest": manifest_digest(body), "files": stamps(body["assets"])}
    (ROOT / "verified.json").write_text(json.dumps(marker), encoding="utf-8")


def transfer_source_colors(source, target):
    """Approximate source appearance on new geometry; never claim generated PBR/UVs."""
    import numpy as np
    import trimesh
    from scipy.spatial import cKDTree

    if source.visual.kind not in ("texture", "vertex", "face"):
        return False
    colors = source.visual.to_color().vertex_colors if source.visual.kind == "texture" else source.visual.vertex_colors
    if colors is None or len(colors) != len(source.vertices):
        return False

    def normalized(vertices):
        low, high = vertices.min(axis=0), vertices.max(axis=0)
        return (vertices - (low + high) / 2) / max(float((high - low).max()), 1e-8)

    tree = cKDTree(normalized(np.asarray(source.vertices)))
    points = normalized(np.asarray(target.vertices))
    output = np.empty((len(points), 4), dtype=np.uint8)
    for start in range(0, len(points), 65536):
        _, indices = tree.query(points[start:start + 65536], workers=2)
        output[start:start + 65536] = colors[indices]
    target.visual = trimesh.visual.ColorVisuals(mesh=target, vertex_colors=output)
    return True


def refine(body):
    if not ready(body):
        raise RuntimeError("Hi3D runtime or verified weights are missing. Run setup again.")
    for asset in body["assets"]:
        if digest(asset_path(asset)) != asset["sha256"]:
            (ROOT / "verified.json").unlink(missing_ok=True)
            raise RuntimeError(f"Hi3D asset checksum mismatch: {asset['file']}")
    base = Path.home() / ".local/share/willitrun-trellis2/env"
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", HF_HUB_OFFLINE="1",
                      PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True", CUDA_HOME=str(base),
                      CC=str(base / "bin/x86_64-conda-linux-gnu-cc"), CXX=str(base / "bin/x86_64-conda-linux-gnu-c++"),
                      CPATH=str(base / "targets/x86_64-linux/include"),
                      LIBRARY_PATH=str(base / "lib") + ":" + str(base / "targets/x86_64-linux/lib/stubs"),
                      PATH=str(base / "bin") + ":" + os.environ["PATH"])
    import numpy as np
    import pyfqmr
    import torch
    import trimesh
    from PIL import Image
    from rembg import new_session, remove
    from sparc3d import Sparc3DPipeline

    started = time.monotonic()
    torch.set_grad_enabled(False)
    progress(2, "hi3d_input")
    scene = trimesh.load(io.BytesIO(base64.b64decode(body["glb"], validate=True)), file_type="glb", force="scene", process=False)
    source_mesh = scene.to_mesh()
    mesh = source_mesh
    mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)
    mesh.merge_vertices()
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise RuntimeError("Source mesh is empty or non-finite.")
    source_triangles = len(mesh.faces)
    if source_triangles > body["triangles"]:
        simplifier = pyfqmr.Simplify()
        simplifier.setMesh(mesh.vertices, mesh.faces)
        simplifier.simplify_mesh(target_count=body["triangles"], aggressiveness=5, preserve_border=True, verbose=False)
        vertices, faces, _ = simplifier.getMesh()
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    coarse_triangles = len(mesh.faces)
    coarse_hash = hashlib.sha256(mesh.export(file_type="glb")).hexdigest()
    image = Image.open(io.BytesIO(base64.b64decode(body["image"], validate=True))).convert("RGBA")
    image = remove(image, session=new_session("u2net"))
    progress(5, "hi3d_loading")
    models = ROOT / "models"
    passes = {512: 1, 1024: 2, 1536: 3}[body["resolution"]]

    def on_step(event):
        fraction = (event.pass_index + event.step / event.num_inference_steps) / passes
        progress(10 + int(78 * fraction), "hi3d_sampling", step=event.pass_index * event.num_inference_steps + event.step,
                 total=passes * event.num_inference_steps)

    pipeline = Sparc3DPipeline.from_pretrained(models / "hi3d-official/Hi3Dv1.0", local_files_only=True,
        dino_model_id=models / "facebook/dinov2-large", clip_model_id=models / "openai/clip-vit-large-patch14", progress_callback=on_step)
    if body["cpu_offload"]:
        pipeline.enable_sequential_cpu_offload("cuda")
    else:
        pipeline.to("cuda")
    torch.cuda.reset_peak_memory_stats()
    result = pipeline(image, mesh, resolution=body["resolution"], num_inference_steps=body["steps"], seed=body["seed"])
    progress(92, "hi3d_export")
    if result.resolution != body["resolution"] or not len(result.mesh.faces) or not np.isfinite(result.mesh.vertices).all():
        raise RuntimeError("Hi3D returned invalid geometry or resolution.")
    colored = transfer_source_colors(source_mesh, result.mesh)
    payload = result.mesh.export(file_type="glb")
    metadata = {"resolution": result.resolution, "triangles": len(result.mesh.faces), "vertices": len(result.mesh.vertices),
                "source_triangles": source_triangles, "coarse_triangles": coarse_triangles, "coarse_sha256": coarse_hash,
                "coarse_reason": "Simplify to the explicitly requested input triangle budget; no subdivision if already below it.",
                "cpu_offload": body["cpu_offload"], "geometry_only": not colored,
                "appearance": "source_vertex_colors" if colored else "geometry_only", "source_commit": body["source_commit"],
                "torch": torch.__version__, "torch_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                "elapsed": round(time.monotonic() - started, 1)}
    print("WIR_METADATA:" + json.dumps(metadata), flush=True)
    print("WIR_RESULT:" + base64.b64encode(payload).decode(), flush=True)


def main():
    body = json.load(sys.stdin)
    if body.get("probe"):
        print(json.dumps({"ready": ready(body)}), flush=True)
    elif body.get("prepare"):
        prepare(body)
    else:
        refine(body)


if __name__ == "__main__":
    main()
