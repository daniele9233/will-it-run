"""Pinned MiniMax H3 Ref2VA-VSA workflow. GPU code runs only inside ComfyUI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentfit.imagegen.video import VideoRequest

KEY = "minimax-h3-ref2va-vsa"
REPOSITORY = "Kablex/ComfyUI-Ref2VA-VSA"
COMMIT = "323a0a5f7c636cc70f95464145732f81f04a72e9"


def assets() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parent / "data/ref2va_assets.json").read_text(encoding="utf-8"))


def workflow(r: VideoRequest, files: dict[str, str], job_id: str) -> dict[str, Any]:
    """Bind the author's 4-step API graph; preserve AV decoding and the dense reference prefix."""
    def node(kind: str, **inputs: Any) -> dict[str, Any]:
        return {"class_type": kind, "inputs": inputs}

    return {
        "1": node("UNETLoader", unet_name=files["high"], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=files["encoder"], type="minimax", device="default"),
        "4": node("VAELoader", vae_name=files["vae"]),
        "5": node("VAELoader", vae_name=files["audio_vae"]),
        "7": node("LoadImage", image=r.image),
        "6": node("MiniMaxH3ReferenceToVideo", clip=["2", 0], vae=["4", 0], audio_vae=["5", 0],
                  prompt=r.prompt, width=r.width, height=r.height, length=r.frames,
                  ref_image_size="match", **{"ref_images.ref_image_0": ["7", 0]}),
        "30": node("LoraLoaderModelOnly", model=["1", 0], lora_name=files["turbo"], strength_model=1.0),
        "31": node("Ref2VAVSAGatePatch", model=["30", 0], gate_file=files["gate"], sparsity=r.vsa_sparsity),
        "32": node("MiniMaxH3SigmaShift", model=["31", 0], shift_video=12.0, shift_audio=3.0),
        "8": node("RandomNoise", noise_seed=r.seed),
        "9": node("KSamplerSelect", sampler_name="euler"),
        "10": node("BasicScheduler", model=["32", 0], scheduler="simple", steps=4, denoise=1.0),
        "12": node("BasicGuider", model=["32", 0], conditioning=["6", 0]),
        "11": node("SamplerCustomAdvanced", noise=["8", 0], guider=["12", 0], sampler=["9", 0],
                   sigmas=["10", 0], latent_image=["6", 1]),
        "13": node("VAEDecode", samples=["11", 0], vae=["4", 0]),
        "24": node("VAEDecodeAudio", samples=["11", 0], vae=["5", 0]),
        "14": node("CreateVideo", images=["13", 0], audio=["24", 0], fps=24),
        "15": node("SaveVideo", video=["14", 0], filename_prefix=f"WillItRun/video/{job_id}", format="mp4"),
    }
