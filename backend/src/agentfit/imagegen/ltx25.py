"""LTX-2.5 text and keyframe workflows on native ComfyUI nodes.

Stage one samples at half resolution with the distilled 8-step schedule. The x2 latent upsampler
doubles it and stage two refines with three steps. When used, keyframes are pinned in both stages.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentfit.i18n import tr
from agentfit.imagegen.keyframes import anchor_sentence, frame_indices

if TYPE_CHECKING:
    from agentfit.imagegen.video import VideoRequest

KEY = "ltx25-keyframes"
MAX_IMAGES = 9
FPS = 24
# The distilled checkpoint's published schedules: full 8-step pass, then the 3-step refinement.
SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
REFINE_SIGMAS = "0.85, 0.7250, 0.4219, 0.0"
# Measured on the reference drone sequence: full strength on the inner keyframes freezes the camera
# on them and then jumps; 0.6 lets the camera fly through them without cuts.
FIRST_STRENGTH, LAST_STRENGTH, INNER_STRENGTH = 1.0, 0.9, 0.6
IMAGE_COMPRESSION = 18
NEGATIVE = ("blurry, out of focus, overexposed, underexposed, low contrast, washed out colors, excessive noise, "
            "flickering, morphing, warping geometry, cuts, scene change, jump cut, cross dissolve, duplicated "
            "objects, deformed hands, extra fingers, text, watermark, static frame")


def assets() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parent / "data" / "ltx25_assets.json").read_text(encoding="utf-8"))


def prompt(text: str, indices: list[int], frames: int) -> str:
    text = text.strip()
    if "provided start image" in text.lower() or "provided end image" in text.lower():
        return text
    anchor = anchor_sentence(indices, frames)
    return f"{anchor} {text}".strip()


def strength(frame: int, frames: int) -> float:
    if frame == 0:
        return FIRST_STRENGTH
    if frame == frames - 1:
        return LAST_STRENGTH
    return INNER_STRENGTH


def workflow(r: VideoRequest, files: dict[str, str], job_id: str) -> dict[str, Any]:
    """Sample audio and video together and refine the upscaled video latent."""
    def node(kind: str, **inputs: Any) -> dict[str, Any]:
        return {"class_type": kind, "inputs": inputs}

    images = list(r.images) or ([r.image] if r.image else [])
    if r.input_mode == "keyframes" and not 1 <= len(images) <= MAX_IMAGES:
        raise ValueError(tr("LTX-2.5 uses between 1 and {count} keyframe pictures.").format(count=MAX_IMAGES))
    indices = frame_indices(r.times(), r.seconds(), r.frames)
    half = (r.width // 2, r.height // 2)
    graph: dict[str, Any] = {
        "1": node("UNETLoader", unet_name=files["high"], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=files["encoder"], type="ltxv", device="default"),
        "3": node("VAELoader", vae_name=files["vae"]),
        "4": node("VAELoader", vae_name=files["audio_vae"]),
        "5": node("CLIPTextEncode", clip=["2", 0], text=prompt(r.prompt, indices, r.frames)),
        "6": node("CLIPTextEncode", clip=["2", 0], text=NEGATIVE),
        "7": node("LTXVConditioning", positive=["5", 0], negative=["6", 0], frame_rate=float(FPS)),
        "8": node("EmptyLTXVLatentVideo", width=half[0], height=half[1], length=r.frames, batch_size=1),
        "9": node("LTXVEmptyLatentAudio", frames_number=r.frames, frame_rate=FPS, batch_size=1, audio_vae=["4", 0]),
        "13": node("SamplerEulerAncestral", eta=0.0, s_noise=1.0),
        "30": node("LatentUpscaleModelLoader", model_name=files["upscaler"]),
    }
    for index, name in enumerate(images):
        graph[str(100 + index)] = node("LoadImage", image=name)

    def guides(positive: list[Any], negative: list[Any], latent: list[Any], base: int,
               size: tuple[int, int]) -> tuple[list[Any], list[Any], list[Any]]:
        for index, frame in enumerate(indices):
            scaled, prepared, guide = str(base + index), str(base + 20 + index), str(base + 40 + index)
            graph[scaled] = node("ImageScale", image=[str(100 + index), 0], upscale_method="lanczos",
                                 width=size[0], height=size[1], crop="center")
            graph[prepared] = node("LTXVPreprocess", image=[scaled, 0], img_compression=IMAGE_COMPRESSION)
            graph[guide] = node("LTXVAddGuide", positive=positive, negative=negative, vae=["3", 0], latent=latent,
                                image=[prepared, 0], frame_idx=-1 if frame == r.frames - 1 else frame,
                                strength=strength(frame, r.frames))
            positive, negative, latent = [guide, 0], [guide, 1], [guide, 2]
        return positive, negative, latent

    positive, negative, latent = guides(["7", 0], ["7", 1], ["8", 0], 200, half)
    graph.update({
        "10": node("LTXVConcatAVLatent", video_latent=latent, audio_latent=["9", 0]),
        "11": node("LTXVDualCFGGuider", model=["1", 0], positive=positive, negative=negative, video_cfg=1.0, audio_cfg=1.0),
        "12": node("RandomNoise", noise_seed=r.seed),
        "14": node("ManualSigmas", sigmas=SIGMAS),
        "15": node("SamplerCustomAdvanced", noise=["12", 0], guider=["11", 0], sampler=["13", 0], sigmas=["14", 0],
                   latent_image=["10", 0]),
        "16": node("LTXVSeparateAVLatent", av_latent=["15", 0]),
    })
    latent = ["16", 0]
    if images:
        graph["17"] = node("LTXVCropGuides", positive=positive, negative=negative, latent=latent)
        positive, negative, latent = ["17", 0], ["17", 1], ["17", 2]
    graph["31"] = node("LTXVLatentUpsampler", samples=latent, upscale_model=["30", 0], vae=["3", 0])
    positive, negative, latent = guides(positive, negative, ["31", 0], 300, (r.width, r.height))
    graph.update({
        "32": node("LTXVConcatAVLatent", video_latent=latent, audio_latent=["16", 1]),
        "33": node("LTXVDualCFGGuider", model=["1", 0], positive=positive, negative=negative, video_cfg=1.0, audio_cfg=1.0),
        "34": node("RandomNoise", noise_seed=(r.seed + 1) % 2147483648),
        "35": node("ManualSigmas", sigmas=REFINE_SIGMAS),
        "36": node("SamplerCustomAdvanced", noise=["34", 0], guider=["33", 0], sampler=["13", 0], sigmas=["35", 0],
                   latent_image=["32", 0]),
        "37": node("LTXVSeparateAVLatent", av_latent=["36", 0]),
    })
    latent = ["37", 0]
    if images:
        graph["38"] = node("LTXVCropGuides", positive=positive, negative=negative, latent=latent)
        latent = ["38", 2]
    graph.update({
        "18": node("VAEDecodeTiled", samples=latent, vae=["3", 0], tile_size=512, overlap=64,
                   temporal_size=64, temporal_overlap=16),
        "19": node("LTXVAudioVAEDecode", samples=["37", 1], audio_vae=["4", 0]),
        "20": node("CreateVideo", images=["18", 0], audio=["19", 0], fps=float(FPS)),
        "21": node("SaveVideo", video=["20", 0], filename_prefix=f"WillItRun/video/{job_id}", format="mp4"),
    })
    return graph
