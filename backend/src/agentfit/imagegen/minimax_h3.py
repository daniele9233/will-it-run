"""MiniMax H3 FL2VA text and keyframe workflows on native ComfyUI nodes.

The first and last pictures go to MiniMaxH3ImageToVideo (the trained first/last-frame task); every
picture in between is pinned to its own frame with a chained MiniMaxH3AddGuide, so one generation
passes through all of them as a single continuous shot.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentfit.i18n import tr
from agentfit.imagegen.keyframes import frame_indices

if TYPE_CHECKING:
    from agentfit.imagegen.video import VideoRequest

KEY = "minimax-h3-fl2va"
MAX_IMAGES = 9
FPS = 24
STEPS = 20
TURBO_STEPS = 8
SAMPLER = "res_multistep"
DEFAULT_SOUND = "Natural ambient sound that follows the action and the camera movement on screen."


def assets() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parent / "data" / "minimax_h3_assets.json").read_text(encoding="utf-8"))


def _seconds(value: float) -> str:
    return f"{value:.2f}"


def structured_prompt(text: str, indices: list[int], frames: int) -> str:
    """Wrap a plain description in the official H3 T2VA / FL2VA / I2VA / L2VA prompt layout.

    The text encoder sees only the first-frame and last-frame pictures (in that order); a prompt
    that already uses the official fields is passed through unchanged.
    """
    text = text.strip()
    if "integrated_multimodal_description:" in text:
        return text
    first, last = 0 in indices, frames - 1 in indices
    end = _seconds(frames / FPS)
    if first and last:
        head = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
                f"0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the {end}-second mark "
                "of the target video.")
    elif first:
        head = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    elif last:
        head = ("How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) aligns with the "
                f"{end}-second mark of the target video.")
    else:
        head = ""
    body = text if text.startswith("[Shot 1]") else f"[Shot 1] {text}"
    sound = DEFAULT_SOUND
    music = "N/A"
    # Optional trailing "Sound: ..." / "Music: ..." lines written by the owner.
    for label, pattern in (("sound", r"(?im)^\s*sound\s*:\s*(.+)$"), ("music", r"(?im)^\s*music\s*:\s*(.+)$")):
        match = re.search(pattern, body)
        if match:
            body = (body[:match.start()] + body[match.end():]).strip()
            if label == "sound":
                sound = match.group(1).strip()
            else:
                music = match.group(1).strip()
    parts = [head] if head else []
    parts += [f"integrated_multimodal_description: {body}", f"overall_soundscape: {sound}", f"non_diegetic_music: {music}"]
    return "\n\n".join(parts)


def workflow(r: VideoRequest, files: dict[str, str], job_id: str) -> dict[str, Any]:
    """Condition empty AV latents on text and any supplied keyframes."""
    def node(kind: str, **inputs: Any) -> dict[str, Any]:
        return {"class_type": kind, "inputs": inputs}

    images = list(r.images) or ([r.image] if r.image else [])
    if r.input_mode == "keyframes" and not 1 <= len(images) <= MAX_IMAGES:
        raise ValueError(tr("MiniMax H3 uses between 1 and {count} keyframe pictures.").format(count=MAX_IMAGES))
    indices = frame_indices(r.times(), r.seconds(), r.frames)
    graph: dict[str, Any] = {
        "1": node("UNETLoader", unet_name=files["high"], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=files["encoder"], type="minimax", device="default"),
        "3": node("VAELoader", vae_name=files["vae"]),
        "4": node("VAELoader", vae_name=files["audio_vae"]),
    }
    model: list[Any] = ["1", 0]
    if r.turbo:
        graph["5"] = node("LoraLoaderModelOnly", model=model, lora_name=files["turbo"], strength_model=1.0)
        model = ["5", 0]
    conditioning = node("MiniMaxH3ImageToVideo", clip=["2", 0], vae=["3", 0],
                        prompt=structured_prompt(r.prompt, indices, r.frames),
                        width=r.width, height=r.height, length=r.frames)
    guides: list[tuple[str, int]] = []
    for index, (name, frame) in enumerate(zip(images, indices, strict=True)):
        load = str(100 + index)
        graph[load] = node("LoadImage", image=name)
        if frame == 0 and "first_frame" not in conditioning["inputs"]:
            conditioning["inputs"]["first_frame"] = [load, 0]
        elif frame == r.frames - 1 and "last_frame" not in conditioning["inputs"]:
            conditioning["inputs"]["last_frame"] = [load, 0]
        else:
            guides.append((load, frame))
    graph["6"] = conditioning
    positive: list[Any] = ["6", 0]
    for index, (load, frame) in enumerate(guides):
        guide = str(200 + index)
        graph[guide] = node("MiniMaxH3AddGuide", positive=positive, latent=["6", 1], frame_idx=frame,
                            vae=["3", 0], image=[load, 0])
        positive = [guide, 0]
    graph.update({
        "8": node("RandomNoise", noise_seed=r.seed),
        "9": node("KSamplerSelect", sampler_name=SAMPLER),
        "10": node("BasicScheduler", model=model, scheduler="simple", steps=r.steps, denoise=1.0),
        "12": node("BasicGuider", model=model, conditioning=positive),
        "11": node("SamplerCustomAdvanced", noise=["8", 0], guider=["12", 0], sampler=["9", 0],
                   sigmas=["10", 0], latent_image=["6", 1]),
        "13": node("VAEDecode", samples=["11", 0], vae=["3", 0]),
        "14": node("VAEDecodeAudio", samples=["11", 0], vae=["4", 0]),
        "15": node("CreateVideo", images=["13", 0], audio=["14", 0], fps=FPS),
        "16": node("SaveVideo", video=["15", 0], filename_prefix=f"WillItRun/video/{job_id}", format="mp4"),
    })
    return graph
