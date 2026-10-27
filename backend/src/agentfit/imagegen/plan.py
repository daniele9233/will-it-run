"""Will this model fit on this card, and what to change when it does not.

The numbers here are estimates and say so. They come from three things that are known: the
size of the weights on disk (measured, not guessed), the memory this PC can safely give the
GPU (from the hardware profile), and how much of it ComfyUI reports free right now. The cost
per megapixel is the one modelled figure, and it is labelled as an estimate wherever it is
shown.

Nothing here decides for the person: a plan is a sentence with numbers in it, and the
suggestions are codes the interface turns into its own language.
"""

from __future__ import annotations

from typing import Any

#: Rough VRAM cost of one megapixel of latents plus attention, in GB, per family.
#: Measured nowhere in particular and labelled as an estimate in the interface: it is used to
#: rank resolutions, not to promise one.
COST_PER_MEGAPIXEL = {
    "sdxl": 1.3,
    "krea2": 2.1,
    "qwen_image": 2.3,
}

#: What is left to add on top of the weights: the latents, the attention workspace and the
#: VAE decode at one megapixel. The weight figure of a catalogued model already includes the
#: text encoder when the model loads it, so counting it again here would double it.
OVERHEAD_GB = 0.5

#: Below this much free VRAM a picture is expected to spill into system memory.
TIGHT_GB = 0.8

#: ComfyUI streams weights in and out of the card, so a model that overshoots the budget by a
#: little still runs: KreaNoct (14.5 GB estimated against 14.2 GB usable) made a 1024x1536
#: picture in 14 s on a 16 GB card. Past this overshoot the run starts to swap, and the plan says so.
OVERSHOOT_SLACK_GB = 0.5


def cost_per_megapixel(family: str) -> float:
    return COST_PER_MEGAPIXEL.get(family, 1.6)


def _staged_weights(components: list[dict[str, Any]]) -> float | None:
    """Peak weight in GB when the model has a separate text encoder, else None.

    ComfyUI encodes the prompt first and moves the encoder out before it samples, so what
    has to sit in the card at once is the diffusion model plus the VAE, or the encoder alone,
    whichever is bigger. Every size must be known: a guess here would be a lie.
    """
    encoder = 0.0
    rest = 0.0
    for item in components:
        role = str(item.get("role") or "")
        if role == "custom_node":
            continue
        size = item.get("installed_size") or item.get("size_bytes") or 0
        if not size:
            return None
        if role == "text_encoder":
            encoder += size / 1e9 * 1.02
        else:
            rest += size / 1e9 * 1.02
    if not encoder or not rest:
        return None
    return max(encoder, rest)


def estimate(
    *,
    family: str,
    vram_gb: float | None,
    components: list[dict[str, Any]],
    usable_gb: float | None,
    free_vram_gb: float | None,
    engine_running: bool,
    lmstudio_loaded: bool,
    node_missing: bool = False,
) -> dict[str, Any]:
    """What this model needs, what this PC can give, and what to do about the difference."""
    weights = 0.0
    known = vram_gb is not None
    staged = _staged_weights(components)
    if staged is not None:
        # The text encoder reads the prompt and is then unloaded: it is never in the card
        # together with the diffusion model, so the peak is the larger of the two.
        weights, known = staged, True
    elif vram_gb is not None:
        weights = float(vram_gb)
    else:
        for item in components:
            size = item.get("size_bytes") or item.get("installed_size") or 0
            if not size:
                known = False
                continue
            weights += size / 1e9 * 1.02
    needed = round(weights + OVERHEAD_GB, 1) if known else None
    usable = round(float(usable_gb), 1) if usable_gb else None
    headroom = round((usable or 0) - (needed or 0), 1) if usable and needed else None
    per_mp = cost_per_megapixel(family)
    max_mp = None
    if headroom is not None:
        max_mp = round(max(0.5, min(4.0, headroom / per_mp)), 2)

    warnings: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    if not engine_running:
        warnings.append({"code": "engine_stopped"})
    if node_missing:
        warnings.append({"code": "node_missing"})
    if needed is None:
        warnings.append({"code": "size_unknown"})
    if needed and usable and needed > usable + OVERSHOOT_SLACK_GB:
        warnings.append({"code": "does_not_fit", "values": {"needed": needed, "usable": usable}})
        suggestions.append({"code": "lower_resolution", "values": {"megapixels": max_mp}})
        suggestions.append({"code": "lighter_model"})
        if lmstudio_loaded:
            suggestions.append({"code": "close_lmstudio"})
        suggestions.append({"code": "enable_offload"})
    elif needed and usable and headroom is not None and headroom < TIGHT_GB:
        warnings.append({"code": "tight", "values": {"headroom": headroom}})
        suggestions.append({"code": "close_lmstudio"})
    if free_vram_gb is not None and needed and free_vram_gb < needed:
        warnings.append({"code": "free_vram_low", "values": {"free": round(free_vram_gb, 1), "needed": needed}})
    if lmstudio_loaded and not any(item["code"] in ("does_not_fit", "tight") for item in warnings):
        warnings.append({"code": "lmstudio_loaded"})
    if max_mp is not None:
        if max_mp >= 2.0 and not any(item["code"] == "does_not_fit" for item in warnings):
            suggestions.append({"code": "can_go_large", "values": {"megapixels": max_mp}})
        elif max_mp < 1.0 and not any(item["code"] == "does_not_fit" for item in warnings):
            suggestions.append({"code": "stay_small", "values": {"megapixels": max_mp}})
    return {
        "fits": (needed <= usable + OVERSHOOT_SLACK_GB) if (needed is not None and usable is not None) else None,
        "needed_gb": needed,
        "usable_gb": usable,
        "headroom_gb": headroom,
        "max_megapixels": max_mp,
        "cost_per_megapixel_gb": per_mp,
        "basis": "estimate" if known else "unknown",
        "warnings": warnings,
        "suggestions": suggestions,
    }


#: What ComfyUI says when it runs out of memory, in the shapes it says it.
_OOM_MARKERS = (
    "out of memory",
    "outofmemory",
    "allocation on device",
    "cuda error",
    "not enough memory",
    "insufficient memory",
)


def is_out_of_memory(message: str | None) -> bool:
    low = (message or "").lower()
    return any(marker in low for marker in _OOM_MARKERS)


def out_of_memory_hints(message: str | None) -> list[dict[str, Any]]:
    """The codes the interface turns into "what to try now", when a run died of VRAM."""
    if not is_out_of_memory(message):
        return []
    return [
        {"code": "lower_resolution"},
        {"code": "lighter_model"},
        {"code": "close_lmstudio"},
        {"code": "enable_offload"},
    ]
