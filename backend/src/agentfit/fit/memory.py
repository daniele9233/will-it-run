"""Memory calculator: weights + KV cache + runtime overhead + vision projector.

The KV-cache formulas are adapted from llmfit ``LlmModel::kv_cache_gb`` (MIT,
Copyright (c) Alex Jones).
"""

from __future__ import annotations

from agentfit.fit import quant as q
from agentfit.fit.models import LoadOptions, MemoryEstimate, ModelSpec, VariantSpec
from agentfit.hardware.models import GIB, Confidence

# CUDA/Vulkan context, compute buffers and scratch space of one loaded llama.cpp instance.
RUNTIME_OVERHEAD_GB = 0.5
# Typical mmproj size when a vision model's projector file is not listed.
DEFAULT_VISION_PROJECTOR_GB = 0.9


def effective_params_b(model: ModelSpec, variant: VariantSpec | None = None) -> float | None:
    """Parameter count in billions, inferred from the file size when the catalog lacks it."""
    if model.params_b:
        return model.params_b
    if variant and variant.file_size_bytes:
        return variant.file_size_bytes / GIB / q.bytes_per_param(variant.quant)
    return None


def weights_gb(model: ModelSpec, variant: VariantSpec) -> tuple[float, str]:
    if variant.file_size_bytes:
        return variant.file_size_bytes / GIB, "file"
    params = model.params_b or 7.0
    return q.estimate_file_gb(params, variant.quant), "estimate"


def kv_cache_gb(model: ModelSpec, context_length: int, bytes_per_element: float = 2.0) -> tuple[float, str]:
    """KV cache for one sequence at ``context_length`` tokens.

    Exact when the layer geometry is known:
    ``2 (K and V) x layers x kv_heads x head_dim x ctx x bytes``.
    Otherwise a linear approximation in parameter count.
    """
    if model.num_layers and model.head_dim and model.num_kv_heads:
        total = 2.0 * model.num_layers * model.num_kv_heads * model.head_dim * context_length * bytes_per_element
        return total / GIB, "architecture"
    params = model.params_b or 7.0
    return 0.000008 * params * context_length * (bytes_per_element / 2.0), "heuristic"


def estimate_memory(model: ModelSpec, variant: VariantSpec, options: LoadOptions | None = None) -> MemoryEstimate:
    options = options or LoadOptions()
    w_gb, w_source = weights_gb(model, variant)
    kv_one, kv_source = kv_cache_gb(model, options.context_length, options.kv_bytes_per_element)
    kv_total = kv_one * max(1, options.parallel)

    vision_gb, vision_source = 0.0, None
    if variant.mmproj_bytes:
        vision_gb, vision_source = variant.mmproj_bytes / GIB, "file"
    elif model.vision:
        vision_gb, vision_source = DEFAULT_VISION_PROJECTOR_GB, "estimate"

    total = w_gb + kv_total + RUNTIME_OVERHEAD_GB + vision_gb + options.draft_model_gb
    if w_source == "file" and kv_source == "architecture":
        confidence = Confidence.HIGH
    elif w_source == "file" or model.params_b:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW
    return MemoryEstimate(
        weights_gb=round(w_gb, 2),
        weights_source=w_source,
        kv_cache_gb=round(kv_total, 2),
        kv_source=kv_source,
        runtime_overhead_gb=RUNTIME_OVERHEAD_GB,
        vision_projector_gb=round(vision_gb, 2),
        vision_source=vision_source,
        draft_model_gb=options.draft_model_gb,
        context_length=options.context_length,
        parallel=max(1, options.parallel),
        total_gb=round(total, 2),
        confidence=confidence,
    )
