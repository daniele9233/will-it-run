"""Catalog data model."""

from __future__ import annotations

import os
from enum import Enum

from pydantic import BaseModel, Field

from agentfit.fit.models import ModelSpec, VariantSpec
from agentfit.hardware.models import Confidence


class Alignment(str, Enum):
    STANDARD = "standard"
    UNCENSORED = "uncensored"


def hub_endpoint() -> str:
    """Base URL of the Hugging Face Hub. ``HF_ENDPOINT`` selects a mirror, as in the official tools."""
    return (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")


class ModelFile(BaseModel):
    """A real, downloadable file in a model repository."""

    repo: str
    filename: str
    quant: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    revision: str | None = None

    @property
    def url(self) -> str:
        revision = self.revision or "main"
        return f"{hub_endpoint()}/{self.repo}/resolve/{revision}/{self.filename}"


class TaskQuality(BaseModel):
    """Relative task strength, 0-100. An estimate from model family and size, not a benchmark run."""

    coding: float = 0
    reasoning: float = 0
    writing: float = 0
    general: float = 0
    basis: str = "size_heuristic"
    confidence: Confidence = Confidence.LOW


class ModelEntry(BaseModel):
    # Hugging Face repo holding the GGUF files: the download source and stable id.
    repo: str
    name: str
    publisher: str
    # Upstream model this was fine-tuned / quantized from, when the card declares it.
    base_models: list[str] = Field(default_factory=list)
    quantizer: str | None = None
    family: str | None = None
    architecture: str | None = None
    params_b: float | None = None
    active_params_b: float | None = None
    is_moe: bool = False
    num_experts: int | None = None
    active_experts: int | None = None
    num_layers: int | None = None
    num_kv_heads: int | None = None
    head_dim: int | None = None
    context_length: int | None = None
    license: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    alignment: Alignment = Alignment.STANDARD
    alignment_evidence: list[str] = Field(default_factory=list)
    downloads: int | None = None
    likes: int | None = None
    release_date: str | None = None
    last_modified: str | None = None
    pipeline_tag: str | None = None
    tags: list[str] = Field(default_factory=list)
    files: list[ModelFile] = Field(default_factory=list)
    mmproj_files: list[ModelFile] = Field(default_factory=list)
    # True once the real file list (names, sizes, hashes) was fetched from the source.
    files_complete: bool = False
    revision: str | None = None
    source: str = "seed"
    metadata_confidence: Confidence = Confidence.MEDIUM
    unknown_fields: list[str] = Field(default_factory=list)
    quality: TaskQuality = Field(default_factory=TaskQuality)
    # Set when this entry is installed in LM Studio.
    installed_keys: list[str] = Field(default_factory=list)

    @property
    def model_card_url(self) -> str:
        return f"https://huggingface.co/{self.repo}"

    @property
    def vision(self) -> bool:
        return "vision" in self.capabilities

    @property
    def tool_use(self) -> bool:
        return "tool_use" in self.capabilities

    def to_spec(self) -> ModelSpec:
        return ModelSpec(
            name=self.name,
            params_b=self.params_b,
            active_params_b=self.active_params_b,
            is_moe=self.is_moe,
            num_experts=self.num_experts,
            active_experts=self.active_experts,
            architecture=self.architecture,
            num_layers=self.num_layers,
            num_kv_heads=self.num_kv_heads,
            head_dim=self.head_dim,
            max_context=self.context_length,
            vision=self.vision,
        )

    def variants(self) -> list[VariantSpec]:
        """Concrete files as fit-engine variants; empty when the file list is not fetched yet."""
        mmproj = min((f.size_bytes for f in self.mmproj_files if f.size_bytes), default=None)
        return [
            VariantSpec(quant=f.quant, file_size_bytes=f.size_bytes, mmproj_bytes=mmproj, filename=f.filename)
            for f in self.files
            if f.quant
        ]

    def file_for(self, quant: str) -> ModelFile | None:
        wanted = quant.upper()
        matches = [f for f in self.files if (f.quant or "").upper() == wanted]
        if not matches:
            return None
        # Several files can share a quant (e.g. "-mtp" draft variants); prefer the plain one.
        return min(matches, key=lambda f: len(f.filename))
