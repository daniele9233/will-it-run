"""Where each keyframe picture lands on a video's timeline, shared by the keyframe video models."""
from __future__ import annotations


def default_times(count: int, seconds: float) -> list[float]:
    """First picture at 0, last at the end, the others evenly spaced in between."""
    if count <= 0:
        return []
    if count == 1:
        return [0.0]
    return [round(seconds * index / (count - 1), 2) for index in range(count)]


def frame_indices(times: list[float], seconds: float, frames: int) -> list[int]:
    """Pixel frame of each time; 0 s is the first frame and the full duration is exactly the last one."""
    last = frames - 1
    return [min(last, max(0, round(time / seconds * last))) for time in times]


def anchor_sentence(indices: list[int], frames: int) -> str:
    """The plain instruction LTX's own keyframe templates put first in the prompt."""
    first = 0 in indices
    last = frames - 1 in indices
    middle = len([i for i in indices if 0 < i < frames - 1])
    parts = []
    if first:
        parts.append("Use the provided start image as the first frame")
    if middle:
        parts.append(f"pass through the {middle} provided intermediate keyframe image{'s' if middle > 1 else ''} in order")
    if last:
        parts.append("end exactly on the provided end image as the final frame anchor")
    if not parts:
        return ""
    sentence = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    return sentence[0].upper() + sentence[1:] + "."
