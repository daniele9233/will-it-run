"""Native ComfyUI RelayVSR contracts and verified weights; no GPU imports in the core."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from agentfit.i18n import tr
from agentfit.imagegen.video_setup import VideoModelSetup
from agentfit.media_runtime import FLAGS

COMFY_COMMIT = "0decb39ee5b5e17c200334589fc317dbbe98310e"
NODE_TYPES = {"LoadVideo", "GetVideoComponents", "UNETLoader", "VAELoader", "RelayVSRConditioning", "KSampler", "VSRDecode", "CreateVideo", "SaveVideo", "ImageFromBatch", "TrimVideoLatent"}


def assets() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parent / "data/relayvsr_assets.json").read_text(encoding="utf-8"))


def requirements(factor: int) -> list[dict[str, Any]]:
    if factor not in (2, 4):
        raise ValueError(tr("Choose the RelayVSR 2x or 4x model."))
    return [a for a in assets() if a["role"] in (f"x{factor}", f"vae_x{factor}")]


def workflow(factor: int, files: dict[str, str], video: str, job_id: str, *, detail: float = 1.0,
             decode_start: int = 0, decode_length: int | None = None) -> dict[str, Any]:
    """The official one-step, CFG-1 graph with the matching Flash decoder and source FPS."""
    requirements(factor)
    def node(kind: str, **inputs: Any) -> dict[str, Any]:
        return {"class_type": kind, "inputs": inputs}
    graph = {
        "1": node("UNETLoader", unet_name=files[f"x{factor}"], weight_dtype="default"),
        "2": node("LoadVideo", file=video),
        "3": node("GetVideoComponents", video=["2", 0]),
        "4": node("VAELoader", vae_name=files[f"vae_x{factor}"]),
        "8": node("RelayVSRConditioning", model=["1", 0], images=["3", 0]),
        "11": node("KSampler", model=["1", 0], positive=["8", 0], negative=["8", 0],
                   latent_image=["8", 1], seed=0, steps=1, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "13": node("VSRDecode", vae=["4", 0], samples=["11", 0], images=["3", 0], detail=detail),
        "14": node("CreateVideo", images=["13", 0], fps=["3", 2], codec="auto"),
        "15": node("SaveVideo", video=["14", 0], filename_prefix=f"WillItRun/upscale/{job_id}", format="mp4"),
    }
    if decode_start:
        if decode_start % 15 or decode_start < 0 or decode_length is None or decode_length < 2:
            raise ValueError("RelayVSR context trimming must align to its 15-frame keyframe interval.")
        graph["9"] = node("ImageFromBatch", image=["3", 0], batch_index=decode_start, length=decode_length)
        graph["10"] = node("TrimVideoLatent", samples=["11", 0], trim_amount=decode_start // 15)
        graph["13"]["inputs"].update(images=["9", 0], samples=["10", 0])
    return graph


class RelayVSRSetup(VideoModelSetup):
    def __init__(self, engine: Any, models_dir: Path) -> None:
        super().__init__(engine, models_dir / "video" / "relayvsr", assets())

    def environment(self) -> dict[str, Any]:
        if time.monotonic() - self._environment_at < 60:
            return dict(self._environment)
        result: dict[str, Any] = {"ready": False, "error": None, "vram_gb": None, "native_nodes": False}
        install = self.engine.install() if self.engine is not None else None
        if install is None or install.python is None:
            result["error"] = tr("ComfyUI was not found on this PC.")
        else:
            query = (
                "import json,pathlib,torch,comfy_kitchen as ck; "
                "cuda=ck.list_backends().get('cuda',{}); "
                "native=pathlib.Path('comfy/ldm/wan/model_vsr.py').is_file(); "
                "print(json.dumps({'ready':bool(native and torch.cuda.is_available() and torch.cuda.is_bf16_supported() "
                "and cuda.get('available') and 'window_attention' in cuda.get('capabilities',[])), 'native_nodes':native, "
                "'vram_gb':torch.cuda.get_device_properties(0).total_memory/1024**3 if torch.cuda.is_available() else None}))"
            )
            try:
                probe = subprocess.run([str(install.python), "-c", query], cwd=install.root,
                                       capture_output=True, timeout=60, creationflags=FLAGS)
                if probe.returncode:
                    raise ValueError(probe.stderr.decode("utf-8", errors="replace")[-1000:])
                result.update(json.loads(probe.stdout.decode("utf-8").splitlines()[-1]))
                if not result["ready"]:
                    result["error"] = tr("RelayVSR needs native ComfyUI support, CUDA BF16 and comfy-kitchen window_attention.")
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                result.update(ready=False, error=str(exc))
        self._environment, self._environment_at = result, time.monotonic()
        return dict(result)
