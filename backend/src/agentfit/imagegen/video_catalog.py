"""Explicit local video profiles; the selected quantizations never change silently."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentfit.i18n import tr
from agentfit.imagegen import ref2va

HIGH = "Wan2.2-I2V-A14B-HighNoise-Q6_K.gguf"
LOW = "Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf"
ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE = "wan_2.1_vae.safetensors"
DEFAULT = "wan22-i2v-q6"


@dataclass(frozen=True)
class VideoProfile:
    key: str
    name: str
    family: str
    high: str
    low: str = ""
    vae: str = VAE
    vision: str = ""
    fps: int = 16
    steps: int = 20
    cfg: float = 3.5
    sampler: str = "euler"
    required_end: bool = False
    size_multiple: int = 16
    input_mode: str = "single_or_first_last"
    width: int = 832
    height: int = 480
    frame_stride: int = 4
    frame_offset: int = 1
    min_seconds: int = 6

    def frames_for(self, seconds: int) -> int:
        count = seconds * self.fps + (0 if self.input_mode == "reference" else 1)
        return count + (self.frame_offset - count) % self.frame_stride

    def requirements(self) -> list[tuple[str, str, str]]:
        if self.input_mode == "reference":
            return [(a["role"], a["filename"], a["folder"]) for a in ref2va.assets()]
        parts = [("high", self.high, "unet_gguf"), ("encoder", ENCODER, "text_encoders"),
                 ("vae", self.vae, "vae")]
        if self.low:
            parts.insert(1, ("low", self.low, "unet_gguf"))
        if self.vision:
            parts.append(("vision", self.vision, "clip_vision"))
        return parts


PROFILES = (
    VideoProfile(DEFAULT, "Wan 2.2 I2V A14B · Q6_K", "wan22-i2v", HIGH, LOW),
    VideoProfile("wan21-flf-q4", "Wan 2.1 FLF2V 14B 720P · Q4_K_M", "wan21-flf",
                 "wan2.1-flf2v-14b-720p-Q4_K_M.gguf", vision="clip_vision_h.safetensors",
                 cfg=3.0, sampler="uni_pc", required_end=True, input_mode="first_last", width=1280, height=720),
    VideoProfile(ref2va.KEY, "MiniMax H3 Ref2VA-VSA · INT8 · 4 steps", "ref2va",
                 "minimax_h3_ref2va_pruned_int8_convrot.safetensors", fps=24, steps=4, cfg=1.0,
                 size_multiple=32, input_mode="reference", width=1344, height=768,
                 frame_stride=17, frame_offset=5, min_seconds=5),
)


def profile(key: str) -> VideoProfile:
    for item in PROFILES:
        if item.key == key:
            return item
    raise ValueError(tr("Unknown video model: {key}.").format(key=key))


def assets() -> list[dict]:
    """Pinned URLs, byte lengths and SHA-256, verified against the publishers' Hub trees."""
    return json.loads((Path(__file__).parent / "data" / "video_assets.json").read_text(encoding="utf-8")) + ref2va.assets()


def local_path(root: Path, name: str, folder: str) -> Path:
    if name in {a["filename"] for a in ref2va.assets()}:
        return root / "video" / "ref2va" / folder / name
    if folder == "unet_gguf":
        return root / name
    return root / "video" / folder / name
