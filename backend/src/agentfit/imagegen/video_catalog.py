"""Explicit local video profiles; the selected quantizations never change silently."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentfit.i18n import tr

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

    def requirements(self) -> list[tuple[str, str, str]]:
        parts = [("high", self.high, "unet_gguf"), ("encoder", ENCODER, "text_encoders"),
                 ("vae", self.vae, "vae")]
        if self.low:
            parts.insert(1, ("low", self.low, "unet_gguf"))
        if self.vision:
            parts.append(("vision", self.vision, "clip_vision"))
        return parts


PROFILES = (
    VideoProfile(DEFAULT, "Wan 2.2 I2V A14B · Q6_K", "wan22-i2v", HIGH, LOW),
    VideoProfile("wan22-fun-5b-q6", "Wan 2.2 Fun-InP 5B · Q6_K", "wan22-fun-5b",
                 "Wan2.2-Fun-5B-InP-Q6_K.gguf", vae="Wan2.2_VAE.safetensors",
                 fps=24, cfg=6.0, sampler="uni_pc", size_multiple=32),
    VideoProfile("wan22-fun-a14b-q4", "Wan 2.2 Fun-InP A14B · Q4_K_M", "wan22-fun-a14b",
                 "Wan2.2-Fun-A14B-InP_HighNoise-Q4_K_M.gguf",
                 "Wan2.2-Fun-A14B-InP_LowNoise-Q4_K_M.gguf"),
    VideoProfile("wan21-flf-q4", "Wan 2.1 FLF2V 14B 720P · Q4_K_M", "wan21-flf",
                 "wan2.1-flf2v-14b-720p-Q4_K_M.gguf", vision="clip_vision_h.safetensors",
                 cfg=3.0, sampler="uni_pc", required_end=True),
)


def profile(key: str) -> VideoProfile:
    for item in PROFILES:
        if item.key == key:
            return item
    raise ValueError(tr("Unknown video model: {key}.").format(key=key))


def assets() -> list[dict]:
    """Pinned URLs, byte lengths and SHA-256, verified against the publishers' Hub trees."""
    return json.loads((Path(__file__).parent / "data" / "video_assets.json").read_text(encoding="utf-8"))


def local_path(root: Path, name: str, folder: str) -> Path:
    if folder == "unet_gguf":
        return root / name
    return root / "video" / folder / name
