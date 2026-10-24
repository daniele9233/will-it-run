"""Inputs and outputs of the fit engine."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from agentfit.hardware.models import Confidence


class FitCategory(str, Enum):
    PERFECT = "PERFECT"
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    ACCEPTABLE = "ACCEPTABLE"
    SLOW = "SLOW"
    MEMORY_TIGHT = "MEMORY TIGHT"
    NOT_RECOMMENDED = "NOT RECOMMENDED"
    IMPOSSIBLE = "IMPOSSIBLE"


# Usefulness ordering, best first. 0..100 feeds the hardware-fit scoring component.
CATEGORY_SCORE: dict[FitCategory, float] = {
    FitCategory.PERFECT: 100,
    FitCategory.EXCELLENT: 90,
    FitCategory.GOOD: 75,
    FitCategory.ACCEPTABLE: 60,
    FitCategory.MEMORY_TIGHT: 45,
    FitCategory.SLOW: 35,
    FitCategory.NOT_RECOMMENDED: 15,
    FitCategory.IMPOSSIBLE: 0,
}

RUNNABLE = {
    FitCategory.PERFECT,
    FitCategory.EXCELLENT,
    FitCategory.GOOD,
    FitCategory.ACCEPTABLE,
    FitCategory.MEMORY_TIGHT,
    FitCategory.SLOW,
}


class Placement(str, Enum):
    GPU = "gpu"
    GPU_SPLIT = "gpu_split"
    PARTIAL_OFFLOAD = "partial_offload"
    CPU = "cpu"
    NONE = "none"


class ModelSpec(BaseModel):
    """What the fit engine needs to know about a model, whatever its source."""

    name: str
    params_b: float | None = None
    active_params_b: float | None = None
    is_moe: bool = False
    num_experts: int | None = None
    active_experts: int | None = None
    architecture: str | None = None
    num_layers: int | None = None
    num_kv_heads: int | None = None
    head_dim: int | None = None
    max_context: int | None = None
    vision: bool = False


class VariantSpec(BaseModel):
    """One concrete file (quantization) of a model."""

    quant: str
    file_size_bytes: int | None = None
    # Vision projector shipped as a separate mmproj-*.gguf, when known.
    mmproj_bytes: int | None = None
    filename: str | None = None


class LoadOptions(BaseModel):
    context_length: int = 8192
    parallel: int = 1
    # Bytes per KV element: 2 = fp16 (llama.cpp default), 1 = q8_0.
    kv_bytes_per_element: float = 2.0
    draft_model_gb: float = 0.0


class MemoryEstimate(BaseModel):
    weights_gb: float
    weights_source: str  # "file" (real size) or "estimate" (params x bytes/param)
    kv_cache_gb: float
    kv_source: str  # "architecture" (exact formula) or "heuristic"
    runtime_overhead_gb: float
    vision_projector_gb: float = 0.0
    vision_source: str | None = None
    draft_model_gb: float = 0.0
    context_length: int
    parallel: int = 1
    total_gb: float
    confidence: Confidence = Confidence.MEDIUM


class SpeedEstimate(BaseModel):
    tokens_per_second: float
    low: float
    high: float
    method: str
    confidence: Confidence
    measured: bool = False
    prompt_tokens_per_second: float | None = None
    ttft_ms: float | None = None
    notes: list[str] = Field(default_factory=list)


class Reason(BaseModel):
    kind: str  # "+", "-" or "i"
    text: str


class FitResult(BaseModel):
    category: FitCategory
    placement: Placement
    quant: str
    context_length: int
    memory: MemoryEstimate
    vram_required_gb: float
    ram_required_gb: float
    vram_usable_gb: float
    vram_headroom_gb: float
    gpu_offload_ratio: float
    speed: SpeedEstimate
    reasons: list[Reason] = Field(default_factory=list)
    fit_score: float = 0.0

    @property
    def runnable(self) -> bool:
        return self.category in RUNNABLE
