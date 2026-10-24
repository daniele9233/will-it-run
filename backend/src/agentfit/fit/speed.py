"""Tokens-per-second estimation.

Token generation is memory-bandwidth bound: every token reads the active weights once,
so ``tok/s ~= bandwidth / bytes_read_per_token * efficiency``. The roofline model, its
0.55 efficiency factor, the MoE overhead tiers and the fallback constants are adapted
from llmfit ``fit.rs::estimate_tps`` (MIT, Copyright (c) Alex Jones).
"""

from __future__ import annotations

from agentfit.fit import quant as q
from agentfit.fit.models import ModelSpec, Placement, SpeedEstimate, VariantSpec
from agentfit.hardware.budget import ResourceBudget
from agentfit.hardware.models import Confidence, GpuBackend
from agentfit.i18n import tr

DEFAULT_EFFICIENCY = 0.55
# Tokens/s x billions-of-params constants for GPUs missing from the bandwidth table.
_FALLBACK_K = {GpuBackend.CUDA: 220.0, GpuBackend.VULKAN: 150.0, GpuBackend.NONE: 70.0}
_CPU_K = 70.0
# Fraction of theoretical DDR bandwidth that CPU inference sustains.
_CPU_EFFICIENCY = 0.6
# Prompt processing is compute-bound and batched; these ratios to decode speed are coarse.
_PROMPT_RATIO_GPU = 25.0
_PROMPT_RATIO_CPU = 4.0
_ASSUMED_PROMPT_TOKENS = 256


def _moe_overhead(num_experts: int | None) -> float:
    if num_experts is None:
        return 0.60
    if num_experts <= 8:
        return 0.90
    if num_experts <= 16:
        return 0.85
    if num_experts <= 32:
        return 0.80
    if num_experts <= 64:
        return 0.70
    return 0.40


def _moe_calibration(architecture: str | None, num_experts: int | None, efficiency: float) -> tuple[float, float]:
    arch = (architecture or "").lower().replace("-", "_")
    if arch.startswith(("gpt_oss", "gptoss")):
        return 0.72, 0.80
    if arch.startswith(("deepseek_v3", "deepseek_v4")):
        return 0.62, 0.70
    return efficiency, _moe_overhead(num_experts)


def _speed_params_b(model: ModelSpec, params_b: float) -> float:
    """MoE models only execute their active experts per token."""
    if model.is_moe and model.active_params_b:
        return max(0.1, model.active_params_b)
    return max(0.1, params_b)


def estimate_speed(
    model: ModelSpec,
    variant: VariantSpec,
    budget: ResourceBudget,
    placement: Placement,
    *,
    params_b: float,
    gpu_weights_gb: float = 0.0,
    ram_weights_gb: float = 0.0,
    efficiency: float = DEFAULT_EFFICIENCY,
) -> SpeedEstimate:
    notes: list[str] = []
    speed_params = _speed_params_b(model, params_b)
    active_gb = speed_params * q.speed_bytes_per_param(variant.quant)
    backend = budget.gpus[0].backend if budget.gpus else GpuBackend.NONE
    bandwidth = budget.gpu_bandwidth_gbps
    on_gpu = placement in (Placement.GPU, Placement.GPU_SPLIT)

    if placement == Placement.NONE:
        return SpeedEstimate(tokens_per_second=0, low=0, high=0, method="none", confidence=Confidence.UNKNOWN)

    if placement == Placement.CPU:
        if budget.ddr_bandwidth_gbps:
            tps = budget.ddr_bandwidth_gbps / active_gb * _CPU_EFFICIENCY
            method, confidence = "ddr_bandwidth_roofline", Confidence.LOW
        else:
            tps = _CPU_K / speed_params * q.speed_multiplier(variant.quant) * 0.3
            method, confidence = "cpu_constant", Confidence.LOW
        notes.append(tr("CPU-only inference; speed depends heavily on RAM speed and thread count."))
    elif bandwidth and on_gpu:
        if model.is_moe:
            eff, overhead = _moe_calibration(model.architecture, model.num_experts, efficiency)
            tps = bandwidth / active_gb * eff * overhead
            method, confidence = "gpu_bandwidth_roofline_moe", Confidence.LOW
            notes.append(tr("Mixture-of-experts model: only active experts are read per token."))
        else:
            tps = bandwidth / active_gb * efficiency
            method, confidence = "gpu_bandwidth_roofline", Confidence.MEDIUM
        if placement == Placement.GPU_SPLIT:
            tps *= 0.9
            notes.append(tr("Split across GPUs: about 10% slower than a single card."))
    elif bandwidth and placement == Placement.PARTIAL_OFFLOAD:
        # GPU-resident bytes move at the GPU roofline, spilled bytes at DDR speed.
        total = max(gpu_weights_gb + ram_weights_gb, 1e-6)
        gpu_share = gpu_weights_gb / total
        gpu_time = active_gb * gpu_share / (bandwidth * efficiency)
        ram_time = active_gb * (1 - gpu_share) / max(budget.ddr_bandwidth_gbps * _CPU_EFFICIENCY, 1.0)
        tps = 1.0 / max(gpu_time + ram_time, 1e-9)
        method, confidence = "gpu_ddr_split_roofline", Confidence.LOW
        notes.append(tr("{share:.0f}% of the weights run from system RAM, which is much slower than VRAM.", share=(1 - gpu_share) * 100))
    else:
        k = _FALLBACK_K.get(backend, 70.0)
        tps = k / speed_params * q.speed_multiplier(variant.quant)
        if budget.cpu_cores >= 8:
            tps *= 1.1
        if placement == Placement.PARTIAL_OFFLOAD:
            tps *= 0.5
        method, confidence = "backend_constant", Confidence.LOW
        notes.append(tr("GPU memory bandwidth unknown; estimate uses a per-backend constant."))

    tps = max(0.1, tps)
    prompt_ratio = _PROMPT_RATIO_CPU if placement == Placement.CPU else _PROMPT_RATIO_GPU
    if placement == Placement.PARTIAL_OFFLOAD:
        prompt_ratio = 10.0
    prompt_tps = tps * prompt_ratio
    return SpeedEstimate(
        tokens_per_second=round(tps, 1),
        low=round(tps * 0.85, 1),
        high=round(tps * 1.1, 1),
        method=method,
        confidence=confidence,
        prompt_tokens_per_second=round(prompt_tps, 0),
        ttft_ms=round(_ASSUMED_PROMPT_TOKENS / prompt_tps * 1000 + 40, 0),
        notes=notes,
    )
