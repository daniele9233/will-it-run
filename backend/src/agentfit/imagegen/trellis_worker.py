"""Executed by the isolated TRELLIS.2 runtime, never imported by the core."""
import base64
import gc
import hashlib
import io
import json
import os
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path.home() / ".local/share/willitrun-trellis2"


def progress(percent, phase, **details):
    print("WIR_PROGRESS:" + json.dumps({"percent": percent, "phase": phase, **details}), flush=True)


def configure_pipeline(pipeline, torch, steps):
    """Bound decoder normalization scratch memory without changing spatial resolution."""
    from types import MethodType

    from trellis2.modules.norm import LayerNorm32

    original_norm = torch.nn.functional.layer_norm
    decoding = {"active": False}
    limit = 512 * 1024

    def normalize(x, shape, weight=None, bias=None, eps=1e-5, cast=False):
        if x.ndim != 2 or tuple(shape) != (x.shape[-1],) or x.numel() <= 8 * 1024**2:
            result = original_norm(x.float() if cast else x, shape, weight, bias, eps)
            return result.to(x.dtype) if cast else result
        output = torch.empty(x.shape, dtype=x.dtype, device=x.device)
        rows = max(1, limit // x.shape[-1])
        for start in range(0, x.shape[0], rows):
            part = x[start:start + rows]
            values = original_norm(part.float() if cast else part, shape, weight, bias, eps)
            output[start:start + rows] = values.to(x.dtype)
        return output

    def layer_forward(module, x):
        return normalize(x, module.normalized_shape, module.weight, module.bias, module.eps, cast=True)

    for name in ("shape_slat_decoder", "tex_slat_decoder"):
        for module in pipeline.models[name].modules():
            if isinstance(module, LayerNorm32):
                module.forward = MethodType(layer_forward, module)

    def functional_norm(x, normalized_shape, weight=None, bias=None, eps=1e-5):
        if decoding["active"]:
            return normalize(x, normalized_shape, weight, bias, eps)
        return original_norm(x, normalized_shape, weight, bias, eps)

    torch.nn.functional.layer_norm = functional_norm

    def decoder(method, start, end, phase):
        original = getattr(pipeline, method)

        def run(*args, **kwargs):
            progress(start, phase)
            gc.collect()
            torch.cuda.empty_cache()
            decoding["active"] = True
            try:
                result = original(*args, **kwargs)
            finally:
                decoding["active"] = False
            progress(end, phase)
            return result

        setattr(pipeline, method, run)

    decoder("decode_shape_slat", 78, 87, "geometry_decode")
    decoder("decode_tex_slat", 87, 93, "texture_decode")

    def sampler(instance, ranges):
        original = instance.sample
        calls = {"n": 0}

        def run(*args, **kwargs):
            start, end, phase = ranges[min(calls["n"], len(ranges) - 1)]
            calls["n"] += 1
            count = {"n": 0}
            once = instance.sample_once
            total = kwargs.get("steps", steps)

            def sample_once(*a, **k):
                result = once(*a, **k)
                count["n"] += 1
                progress(start + (end - start) * count["n"] / total, phase, step=count["n"], total=total)
                return result

            instance.sample_once = sample_once
            progress(start, phase, step=0, total=total)
            kwargs["verbose"] = False
            try:
                return original(*args, **kwargs)
            finally:
                instance.sample_once = once

        instance.sample = run

    sampler(pipeline.sparse_structure_sampler, [(15, 23, "structure")])
    sampler(pipeline.shape_slat_sampler, [(23, 38, "shape_coarse"), (38, 60, "shape_detail")])
    sampler(pipeline.tex_slat_sampler, [(60, 78, "materials")])
    return normalize


def asset_path(asset):
    return ROOT / "models" / asset["repo"] / asset["file"]


def configure_export(cumesh):
    """Keep UV charts small without decimating geometry or lowering texture size."""
    original = cumesh.CuMesh.uv_unwrap

    def unwrap(mesh, *args, **kwargs):
        progress(95, "export_uv")
        charts = dict(kwargs.get("compute_charts_kwargs", {}))
        # Huge 90-degree clusters make xatlas pathological on intricate meshes.
        charts["threshold_cone_half_angle_rad"] = 0.5235987755982988
        kwargs["compute_charts_kwargs"] = charts
        result = original(mesh, *args, **kwargs)
        progress(96, "export_textures")
        return result

    cumesh.CuMesh.uv_unwrap = unwrap


def prepare(assets):
    missing = [a for a in assets if not asset_path(a).is_file() or asset_path(a).stat().st_size != a["bytes"]]
    needed = sum(a["bytes"] for a in missing)
    if shutil.disk_usage(ROOT).free < needed + 10 * 1024**3:
        raise RuntimeError("Not enough space for TRELLIS.2 weights and the 10 GB reserve.")
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(prepare_asset, assets))


def prepare_asset(asset):
    path = asset_path(asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.stat().st_size != asset["bytes"]:
        url = f"https://huggingface.co/{asset['repo']}/resolve/{asset['revision']}/{asset['file']}"
        print(f"Downloading {asset['repo']}/{asset['file']} ({asset['bytes']} bytes)", flush=True)
        temporary = path.with_suffix(path.suffix + ".part")
        for attempt in range(3):
            try:
                offset = temporary.stat().st_size if temporary.exists() else 0
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                request = urllib.request.Request(url + f"?download=true&attempt={time.time_ns()}", headers=headers)
                with urllib.request.urlopen(request, timeout=120) as response:
                    mode = "ab" if offset and getattr(response, "status", 200) == 206 else "wb"
                    with temporary.open(mode) as target:
                        shutil.copyfileobj(response, target, 1024 * 1024)
                break
            except OSError:
                if attempt == 2:
                    raise
        if temporary.stat().st_size != asset["bytes"]:
            raise RuntimeError(f"Incomplete model download: {asset['file']}")
        temporary.replace(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != asset["sha256"]:
        path.unlink()
        raise RuntimeError(f"Model checksum mismatch: {asset['file']}")
    print(f"Verified {asset['repo']}/{asset['file']}", flush=True)


def main():
    body = json.load(sys.stdin)
    ROOT.mkdir(parents=True, exist_ok=True)
    if body.get("prepare"):
        (ROOT / "weights-ready").unlink(missing_ok=True)
        prepare(body["assets"])
        (ROOT / "weights-ready").write_text(body["model_revision"], encoding="utf-8")
        return
    progress(1, "loading")
    os.chdir(ROOT / "repo")
    sys.path.insert(0, str(ROOT / "repo"))
    os.environ.update(ATTN_BACKEND="xformers", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
                      PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    os.environ.setdefault("CUDA_HOME", str(ROOT / "env"))
    os.environ["PATH"] = str(ROOT / "env/bin") + os.pathsep + os.environ["PATH"]
    os.environ.setdefault("CXX", str(ROOT / "env/bin/x86_64-conda-linux-gnu-c++"))
    os.environ.setdefault("CC", str(ROOT / "env/bin/x86_64-conda-linux-gnu-cc"))
    os.environ.setdefault("MAX_JOBS", "2")
    os.environ.setdefault("CPATH", str(ROOT / "env/targets/x86_64-linux/include"))
    os.environ.setdefault("LIBRARY_PATH", str(ROOT / "env/lib") + ":" + str(ROOT / "env/targets/x86_64-linux/lib/stubs"))
    import cumesh
    import numpy as np
    import o_voxel
    import timm
    import torch
    from PIL import Image
    from rembg import new_session, remove
    from safetensors.torch import load_file
    from trellis2.modules import image_feature_extractor
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    from trellis2.pipelines import rembg as trellis_rembg

    major, minor = torch.cuda.get_device_capability()
    os.environ.setdefault("TORCH_CUDA_ARCH_LIST", f"{major}.{minor}")

    class LocalDinoV3:
        """Same DINOv3 ViT-L/16 weights distributed publicly by Hugging Face timm."""
        def __init__(self):
            self.model = timm.create_model("vit_large_patch16_dinov3.lvd1689m", pretrained=False, dynamic_img_size=True)
            self.model.load_state_dict(load_file(str(ROOT / "models/timm/vit_large_patch16_dinov3.lvd1689m/model.safetensors")), strict=True)
            # Upstream uses unweighted layer_norm on the final hidden states.
            self.model.norm = torch.nn.Identity()
            self.model.eval()
            self.image_size = 512

        def to(self, device):
            self.model.to(device)

        def cpu(self):
            self.model.cpu()

        @torch.no_grad()
        def __call__(self, images):
            arrays = [np.asarray(i.convert("RGB").resize((self.image_size, self.image_size), Image.Resampling.LANCZOS)).copy() for i in images]
            tensor = torch.from_numpy(np.stack(arrays)).permute(0, 3, 1, 2).cuda().float() / 255
            mean = tensor.new_tensor([0.485, 0.456, 0.406])[None, :, None, None]
            std = tensor.new_tensor([0.229, 0.224, 0.225])[None, :, None, None]
            features = self.model.forward_features((tensor - mean) / std)
            return torch.nn.functional.layer_norm(features, features.shape[-1:])

    image_feature_extractor.WillItRunDinoV3 = LocalDinoV3
    trellis_rembg.WillItRunNoBackground = lambda: None
    model_root = ROOT / "models/microsoft/TRELLIS.2-4B"
    config = json.loads((model_root / "pipeline.json").read_text())
    # Only the 1024 texture flow is used by the 1536 cascade.
    config["args"]["models"].pop("tex_slat_flow_model_512", None)
    for name, value in config["args"]["models"].items():
        config["args"]["models"][name] = str(ROOT / "models" / value) if value.startswith("microsoft/") else str(model_root / value)
    config["args"]["image_cond_model"] = {"name": "WillItRunDinoV3", "args": {}}
    config["args"]["rembg_model"] = {"name": "WillItRunNoBackground", "args": {}}
    config["args"]["low_vram"] = True
    (model_root / "willitrun-pipeline.json").write_text(json.dumps(config))
    image = Image.open(io.BytesIO(base64.b64decode(body["image"])))
    # Reuse the existing free local background remover; no gated model or account.
    progress(4, "background")
    image = remove(image.convert("RGBA"), session=new_session("u2net"))
    progress(8, "loading")
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(model_root), config_file="willitrun-pipeline.json")
    pipeline.low_vram = True
    pipeline.cuda()
    configure_pipeline(pipeline, torch, body["steps"])
    progress(14, "conditioning")
    resolution = body["resolution"]
    mesh = pipeline.run(image, seed=body["seed"], pipeline_type=f"{resolution}_cascade",
                        sparse_structure_sampler_params={"steps": body["steps"]},
                        shape_slat_sampler_params={"steps": body["steps"]},
                        tex_slat_sampler_params={"steps": body["steps"]},
                        max_num_tokens=1000000)[0]
    actual_resolution = round(1 / float(mesh.voxel_size))
    if actual_resolution != body["resolution"]:
        raise RuntimeError(f"Requested {body['resolution']} cubed, received {actual_resolution} cubed.")
    del pipeline
    gc.collect()
    torch.cuda.empty_cache()
    progress(94, "export_original")
    configure_export(cumesh)
    mesh.simplify(16777216)
    glb = o_voxel.postprocess.to_glb(vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
                                    coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                                    aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]], decimation_target=1000000,
                                    texture_size=body["texture_size"], remesh=True, remesh_band=1,
                                    remesh_project=0, verbose=True)
    payload = glb.export(file_type="glb")
    progress(98, "validating")
    print("WIR_METADATA:" + json.dumps({"resolution": actual_resolution, "decoder_memory": "bounded_normalization"}), flush=True)
    print("WIR_RESULT:" + base64.b64encode(payload).decode(), flush=True)


if __name__ == "__main__":
    main()
