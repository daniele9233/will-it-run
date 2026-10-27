"""Which image models this PC can generate with, and which components each one needs.

A model is a name, a family (which workflow templates it can run) and a list of **component
ids** — one per role. The components themselves (file, folder, source, size) live in
``components.json``, because the same text encoder belongs to several models and must be
downloaded once.

The second source of models is live: any SDXL-style checkpoint the person dropped into
ComfyUI's ``checkpoints`` folder becomes a model, with settings guessed from its name only to
give a starting point, and the interface says it is a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).with_name("data") / "models.json"


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class ModelProfile:
    key: str
    name: str
    family: str
    kinds: tuple[str, ...]  # txt2img / edit / upscale
    #: role -> component id, e.g. {"diffusion_model": "krea2-kreanoct", "vae": "krea2-vae"}
    components: dict[str, str] = field(default_factory=dict)
    #: Components the model needs but that do not appear in its graph (a custom node).
    requires: tuple[str, ...] = ()
    #: What the loaders of this family are called and how they are configured.
    loader: dict[str, Any] = field(default_factory=dict)
    sampling: dict[str, Any] = field(default_factory=dict)
    #: VRAM the weights are expected to hold, in GB. None when unknown.
    vram_gb: float | None = None
    licence: str = ""
    source: str = ""
    notes: str = ""
    #: "native" (uncensored out of the box), "lora" (needs LoRAs), "standard".
    nsfw: str = "standard"
    #: The one to offer first in its family.
    recommended: bool = False
    #: The workflow this model uses when nobody chooses one.
    template: str = ""
    #: Named configurations: a LoRA to switch on and the sampling that goes with it.
    presets: tuple[dict[str, Any], ...] = ()
    #: What this model is told not to draw when the person writes nothing of their own.
    negative: str = ""
    #: Words every prompt for this model must begin with (Pony's quality tags), added by the app.
    prompt_prefix: str = ""
    #: The shape and size its author recommends, so the page can start from it.
    aspect: str = ""
    megapixels: float | None = None
    #: LoRA components that are trained for this family, and may be attached to it.
    loras: tuple[str, ...] = ()
    #: Why this model cannot run here (missing engine support), and what is missing.
    blocked: dict[str, Any] | None = None
    #: True for entries built from a file found on this PC rather than from models.json.
    discovered: bool = False

    def component_ids(self) -> list[str]:
        """Every component this model needs, the graph ones first, without repeats."""
        return list(dict.fromkeys([*self.components.values(), *self.requires]))

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "family": self.family,
            "kinds": list(self.kinds),
            "components": dict(self.components),
            "requires": list(self.requires),
            "vram_gb": self.vram_gb,
            "licence": self.licence,
            "source": self.source,
            "notes": self.notes,
            "nsfw": self.nsfw,
            "recommended": self.recommended,
            "discovered": self.discovered,
            "template": self.template,
            "presets": [dict(item) for item in self.presets],
            "negative": self.negative,
            "prompt_prefix": self.prompt_prefix,
            "aspect": self.aspect,
            "megapixels": self.megapixels,
            "loras": list(self.loras),
            "blocked": dict(self.blocked) if self.blocked else None,
            "sampling": dict(self.sampling),
        }


def _profile(raw: dict[str, Any]) -> ModelProfile:
    return ModelProfile(
        key=str(raw["key"]),
        name=str(raw["name"]),
        family=str(raw["family"]),
        kinds=tuple(raw.get("kinds") or raw.get("workflows") or ("txt2img",)),
        components={str(role): str(cid) for role, cid in (raw.get("components") or {}).items()},
        requires=tuple(str(cid) for cid in (raw.get("requires") or ())),
        loader=dict(raw.get("loader") or {}),
        sampling=dict(raw.get("sampling") or {}),
        vram_gb=raw.get("vram_gb"),
        licence=str(raw.get("licence") or ""),
        source=str(raw.get("source") or ""),
        notes=str(raw.get("notes") or ""),
        nsfw=str(raw.get("nsfw") or "standard"),
        recommended=bool(raw.get("recommended", False)),
        template=str(raw.get("template") or ""),
        presets=tuple(raw.get("presets") or ()),
        negative=str(raw.get("negative") or ""),
        prompt_prefix=str(raw.get("prompt_prefix") or ""),
        aspect=str(raw.get("aspect") or ""),
        megapixels=(float(raw["megapixels"]) if raw.get("megapixels") else None),
        loras=tuple(str(cid) for cid in (raw.get("loras") or ())),
        blocked=dict(raw["blocked"]) if raw.get("blocked") else None,
    )


_PROFILES: list[ModelProfile] | None = None


def profiles() -> list[ModelProfile]:
    global _PROFILES
    if _PROFILES is None:
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CatalogError(f"The image model catalog could not be read: {exc}") from exc
        _PROFILES = [_profile(item) for item in raw.get("models") or []]
    return _PROFILES


def by_key(key: str) -> ModelProfile | None:
    return next((p for p in profiles() if p.key == key), None)


# -- discovered checkpoints -------------------------------------------------

#: Starting settings per checkpoint flavour. The family is a guess from the file name:
#: every one of these is an SDXL checkpoint and runs the same graph, but a photorealism
#: finetune and a plain SDXL base want different guidance. This catalog is photographic only:
#: no anime or illustration flavour is offered.
FLAVOURS: dict[str, dict[str, Any]] = {
    "photoreal": {
        "label": "Photoreal",
        "sampling": {"steps": 30, "cfg": 4.5, "sampler": "dpmpp_2m_sde", "scheduler": "karras"},
        "negative": "lowres, bad anatomy, bad hands, worst quality, jpeg artifacts, watermark, cartoon, anime, illustration, 3d render",
    },
    "sdxl": {
        "label": "SDXL",
        "sampling": {"steps": 28, "cfg": 5.5, "sampler": "dpmpp_2m", "scheduler": "karras"},
        "negative": "lowres, bad anatomy, bad hands, worst quality, watermark, cartoon, anime, illustration",
    },
}

_FLAVOUR_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("photoreal", ("lustify", "bigasp", "cyber", "realvis", "juggernaut", "epicrealism", "chillout", "dreamshaper")),
)


def flavour_of(filename: str) -> str:
    low = filename.lower()
    for flavour, words in _FLAVOUR_WORDS:
        if any(word in low for word in words):
            return flavour
    return "sdxl"


def from_checkpoint(filename: str) -> ModelProfile:
    """A checkpoint found on this PC, treated as an SDXL-family model."""
    flavour = flavour_of(filename)
    settings = FLAVOURS[flavour]
    return ModelProfile(
        key=f"ckpt:{filename}",
        name=Path(filename).stem,
        family="sdxl",
        kinds=("txt2img", "edit", "upscale"),
        components={"checkpoint": f"local:{filename}"},
        loader={},
        sampling=dict(settings["sampling"]),
        negative=str(settings["negative"]),
        vram_gb=None,
        licence="",
        source="",
        notes=(
            f"Found in ComfyUI's checkpoints folder. Starting settings are {settings['label']} ones, "
            "guessed from the file name: change them if the pictures say otherwise."
        ),
        nsfw="unknown",
        discovered=True,
    )
