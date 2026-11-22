"""Explicit local video profiles; the selected quantizations never change silently."""
from __future__ import annotations

from dataclasses import dataclass

from agentfit.i18n import tr
from agentfit.imagegen import ltx25, minimax_h3

DEFAULT = minimax_h3.KEY


@dataclass(frozen=True)
class VideoProfile:
    key: str
    name: str
    family: str
    fps: int
    steps: int
    size_multiple: int
    width: int
    height: int
    frame_stride: int
    frame_offset: int
    max_images: int
    cfg: float = 1.0
    input_mode: str = "keyframes"
    supports_text_to_video: bool = False
    min_seconds: int = 5
    turbo_steps: int | None = None
    recommended_vram_gb: int = 24

    def frames_for(self, seconds: int) -> int:
        count = seconds * self.fps
        return count + (self.frame_offset - count) % self.frame_stride

    def requirements(self) -> list[tuple[str, str, str]]:
        module = minimax_h3 if self.family == "minimax-h3" else ltx25
        return [(a["role"], a["filename"], a["folder"]) for a in module.assets()]


PROFILES = (
    VideoProfile(minimax_h3.KEY, "MiniMax H3 FL2VA · INT8", "minimax-h3", fps=minimax_h3.FPS,
                 steps=minimax_h3.STEPS, turbo_steps=minimax_h3.TURBO_STEPS, size_multiple=32,
                 width=1344, height=768, frame_stride=17, frame_offset=5, max_images=minimax_h3.MAX_IMAGES,
                 supports_text_to_video=True),
    VideoProfile(ltx25.KEY, "LTX-2.5 · two-stage · INT8", "ltx25", fps=ltx25.FPS, steps=8,
                 size_multiple=64, width=1280, height=704, frame_stride=8, frame_offset=1,
                 max_images=ltx25.MAX_IMAGES, supports_text_to_video=True),
)
# Folder of each family under D:\Models\video.
FOLDERS = {"minimax-h3": "minimaxh3", "ltx25": "ltx25"}


def profile(key: str) -> VideoProfile:
    for item in PROFILES:
        if item.key == key:
            return item
    raise ValueError(tr("Unknown video model: {key}.").format(key=key))


def assets() -> list[dict]:
    """Pinned URLs, byte lengths and SHA-256, verified against the publishers' Hub trees."""
    return minimax_h3.assets() + ltx25.assets()
