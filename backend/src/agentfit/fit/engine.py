"""Fit engine: decide where a model runs on this PC, how well, and explain why."""

from __future__ import annotations

from agentfit.fit import quant as q
from agentfit.fit.memory import RUNTIME_OVERHEAD_GB, effective_params_b, estimate_memory
from agentfit.fit.models import (
    CATEGORY_SCORE,
    FitCategory,
    FitResult,
    LoadOptions,
    ModelSpec,
    Placement,
    Reason,
    SpeedEstimate,
    VariantSpec,
)
from agentfit.fit.speed import DEFAULT_EFFICIENCY, estimate_speed
from agentfit.hardware.budget import ResourceBudget
from agentfit.hardware.models import Confidence
from agentfit.i18n import tr

# Share of the usable VRAM budget above which a fully-offloaded model is "memory tight".
TIGHT_RATIO = 0.97
COMFORT_RATIO = 0.90
# Below this share of weights on the GPU, partial offload is not worth the VRAM.
MIN_USEFUL_GPU_SHARE = 0.15
MIN_CONTEXT = 2048


def _speed_ladder(tps: float, target: float, cap: FitCategory) -> FitCategory:
    """Map speed to a category, never better than ``cap``."""
    if tps >= target * 1.5:
        level = FitCategory.PERFECT
    elif tps >= target:
        level = FitCategory.EXCELLENT
    elif tps >= target * 0.5:
        level = FitCategory.GOOD
    elif tps >= target * 0.25:
        level = FitCategory.ACCEPTABLE
    elif tps >= 1.5:
        level = FitCategory.SLOW
    else:
        level = FitCategory.NOT_RECOMMENDED
    order = [
        FitCategory.PERFECT,
        FitCategory.EXCELLENT,
        FitCategory.GOOD,
        FitCategory.ACCEPTABLE,
        FitCategory.SLOW,
        FitCategory.NOT_RECOMMENDED,
    ]
    return level if order.index(level) >= order.index(cap) else cap


def analyze(
    model: ModelSpec,
    variant: VariantSpec,
    budget: ResourceBudget,
    options: LoadOptions | None = None,
    *,
    target_tps: float = 20.0,
    efficiency: float = DEFAULT_EFFICIENCY,
    measured_tps: float | None = None,
) -> FitResult:
    """Evaluate one model file at one context length against the resource budget."""
    options = options or LoadOptions()
    if model.max_context and options.context_length > model.max_context:
        options = options.model_copy(update={"context_length": model.max_context})
    memory = estimate_memory(model, variant, options)
    params_b = effective_params_b(model, variant) or 7.0
    vram_usable = budget.vram_usable_gb
    reasons: list[Reason] = []
    non_weight_gb = memory.total_gb - memory.weights_gb

    placement = Placement.NONE
    vram_required = 0.0
    ram_required = 0.0
    gpu_weights = 0.0
    ram_weights = 0.0

    if budget.has_gpu and memory.total_gb <= vram_usable:
        single_ok = memory.total_gb <= budget.single_gpu_usable_gb
        placement = Placement.GPU if single_ok else Placement.GPU_SPLIT
        vram_required = memory.total_gb
        gpu_weights = memory.weights_gb
    elif budget.has_gpu:
        gpu_weights = max(0.0, vram_usable - non_weight_gb)
        ram_weights = memory.weights_gb - gpu_weights
        share = gpu_weights / memory.weights_gb if memory.weights_gb else 0.0
        ram_need = ram_weights + RUNTIME_OVERHEAD_GB
        if share >= MIN_USEFUL_GPU_SHARE and ram_need <= budget.ram_usable_gb:
            placement = Placement.PARTIAL_OFFLOAD
            vram_required = gpu_weights + non_weight_gb
            ram_required = ram_need
        elif memory.total_gb <= budget.ram_usable_gb:
            placement = Placement.CPU
            ram_required = memory.total_gb
            gpu_weights, ram_weights = 0.0, memory.weights_gb
    elif memory.total_gb <= budget.ram_usable_gb:
        placement = Placement.CPU
        ram_required = memory.total_gb
        ram_weights = memory.weights_gb

    speed = estimate_speed(
        model,
        variant,
        budget,
        placement,
        params_b=params_b,
        gpu_weights_gb=gpu_weights,
        ram_weights_gb=ram_weights,
        efficiency=efficiency,
    )
    if measured_tps:
        speed = SpeedEstimate(
            tokens_per_second=round(measured_tps, 1),
            low=round(measured_tps, 1),
            high=round(measured_tps, 1),
            method="local_benchmark",
            confidence=Confidence.HIGH,
            measured=True,
            prompt_tokens_per_second=speed.prompt_tokens_per_second,
            ttft_ms=speed.ttft_ms,
            notes=[tr("Measured on this PC.")],
        )
    tps = speed.tokens_per_second
    headroom = vram_usable - vram_required if placement != Placement.CPU else vram_usable
    offload_ratio = gpu_weights / memory.weights_gb if memory.weights_gb else 0.0

    if placement == Placement.NONE:
        category = FitCategory.IMPOSSIBLE
        reasons.append(
            Reason(
                kind="-",
                text=tr(
                    "Needs {need:.1f} GB but only {vram:.1f} GB VRAM and {ram:.1f} GB RAM are safely usable.",
                    need=memory.total_gb, vram=vram_usable, ram=budget.ram_usable_gb,
                ),
            )
        )
    elif placement in (Placement.GPU, Placement.GPU_SPLIT):
        ratio = vram_required / vram_usable if vram_usable else 1.0
        reasons.append(Reason(kind="+", text=tr("Fits completely on your GPU.")))
        if placement == Placement.GPU_SPLIT:
            reasons.append(Reason(kind="i", text=tr("Too large for one card; split across your GPUs.")))
        if ratio > TIGHT_RATIO:
            category = FitCategory.MEMORY_TIGHT
            reasons.append(
                Reason(kind="-", text=tr("Leaves only {mb:.0f} MB of the recommended VRAM budget.", mb=headroom * 1024))
            )
        else:
            cap = FitCategory.PERFECT if ratio <= COMFORT_RATIO else FitCategory.EXCELLENT
            category = _speed_ladder(tps, target_tps, cap)
            reasons.append(Reason(kind="+", text=tr("Leaves {gb:.1f} GB of VRAM headroom for Windows and other apps.", gb=headroom)))
            if ratio > COMFORT_RATIO:
                reasons.append(Reason(kind="i", text=tr("Little room left for a second model or a larger context.")))
    elif placement == Placement.PARTIAL_OFFLOAD:
        category = _speed_ladder(tps, target_tps, FitCategory.GOOD)
        if ram_required > budget.ram_usable_gb * 0.9 and category in (FitCategory.GOOD, FitCategory.ACCEPTABLE):
            category = FitCategory.MEMORY_TIGHT
        reasons.append(
            Reason(
                kind="-",
                text=tr(
                    "Requires partial CPU offload: {share:.0f}% of the weights on the GPU, {ram:.1f} GB in system RAM.",
                    share=offload_ratio * 100, ram=ram_weights,
                ),
            )
        )
        reasons.append(Reason(kind="-", text=tr("Leaves almost no VRAM reserve; poor choice for concurrent agents.")))
    else:
        category = _speed_ladder(tps, target_tps, FitCategory.ACCEPTABLE)
        reasons.append(Reason(kind="-", text=tr("Runs on the CPU only, using {gb:.1f} GB of system RAM.", gb=ram_required)))

    if placement != Placement.NONE:
        kind = "+" if tps >= target_tps else "-"
        text = (
            tr("Measured {tps:.0f} tok/s on this PC.", tps=tps)
            if speed.measured
            else tr("Estimated ~{tps:.0f} tok/s ({confidence} confidence).", tps=tps, confidence=tr(speed.confidence.value))
        )
        reasons.append(Reason(kind=kind, text=text))
    if model.max_context and options.context_length >= 16384 and placement in (Placement.GPU, Placement.GPU_SPLIT):
        reasons.append(Reason(kind="+", text=tr("{k}K context fits comfortably.", k=options.context_length // 1024)))

    return FitResult(
        category=category,
        placement=placement,
        quant=q.normalize_quant(variant.quant),
        context_length=options.context_length,
        memory=memory,
        vram_required_gb=round(vram_required, 2),
        ram_required_gb=round(ram_required, 2),
        vram_usable_gb=round(vram_usable, 2),
        vram_headroom_gb=round(headroom, 2),
        gpu_offload_ratio=round(offload_ratio, 3),
        speed=speed,
        reasons=reasons,
        fit_score=CATEGORY_SCORE[category],
    )


def variant_utility(fit: FitResult, target_tps: float = 20.0) -> float:
    """Trade quantization quality against how well and how fast the file runs here."""
    quant_quality = max(0.0, 100 + 4 * q.quality_penalty(fit.quant))
    speed = min(100.0, fit.speed.tokens_per_second / target_tps * 100) if target_tps else 0.0
    return 0.45 * CATEGORY_SCORE[fit.category] + 0.35 * quant_quality + 0.20 * speed


_SOLID = {FitCategory.PERFECT, FitCategory.EXCELLENT, FitCategory.GOOD}


def _preferred_pool(runnable: list[tuple[VariantSpec, FitResult]]) -> list[tuple[VariantSpec, FitResult]]:
    """Quantizations below 3 bits lose too much quality: use them only as a last resort."""
    solid = [vf for vf in runnable if (q.quant_bits(vf[1].quant) or 4) >= 3 and vf[1].category in _SOLID]
    if solid:
        return solid
    three_bit = [vf for vf in runnable if (q.quant_bits(vf[1].quant) or 4) >= 3]
    return three_bit or runnable


def default_variants(model: ModelSpec) -> list[VariantSpec]:
    """Synthetic variants for catalog entries whose real file list is not known yet."""
    return [VariantSpec(quant=label) for label in q.QUANT_HIERARCHY]


class VariantChoice:
    """Outcome of picking a quantization, with the reasoning kept for the UI."""

    def __init__(self, best: tuple[VariantSpec, FitResult], evaluated: list[tuple[VariantSpec, FitResult]]):
        self.variant, self.fit = best
        self.evaluated = evaluated

    def why(self) -> list[str]:
        """Plain-language account of why this quantization beat its neighbours."""
        lines: list[str] = []
        chosen = self.fit
        better = [
            (v, f) for v, f in self.evaluated if q.quality_rank(f.quant) > q.quality_rank(chosen.quant)
        ]
        better.sort(key=lambda vf: q.quality_rank(vf[1].quant))
        if better:
            _, alt = better[0]
            if alt.placement in (Placement.GPU, Placement.GPU_SPLIT):
                lines.append(
                    tr(
                        "Will It Run selected {chosen} instead of {alt}: {alt} would leave only {mb:.0f} MB of usable "
                        "VRAM, while {chosen} leaves about {gb:.1f} GB and is more stable on this computer.",
                        chosen=chosen.quant, alt=alt.quant, mb=max(alt.vram_headroom_gb, 0) * 1024, gb=chosen.vram_headroom_gb,
                    )
                )
            elif alt.placement == Placement.PARTIAL_OFFLOAD:
                lines.append(
                    tr(
                        "Will It Run selected {chosen} instead of {alt}: {alt} needs partial CPU offload and would run "
                        "at about {alt_tps:.0f} tok/s instead of {tps:.0f} tok/s.",
                        chosen=chosen.quant, alt=alt.quant, alt_tps=alt.speed.tokens_per_second, tps=chosen.speed.tokens_per_second,
                    )
                )
            else:
                lines.append(tr("{quant} does not fit this computer ({category}).", quant=alt.quant, category=tr(alt.category.value)))
        worse = [
            (v, f) for v, f in self.evaluated if q.quality_rank(f.quant) < q.quality_rank(chosen.quant)
        ]
        if worse and chosen.runnable:
            lines.append(
                tr(
                    "Smaller quantizations would also run, but {quant} keeps more of the model's quality while still fitting.",
                    quant=chosen.quant,
                )
            )
        return lines


def choose_variant(
    model: ModelSpec,
    variants: list[VariantSpec],
    budget: ResourceBudget,
    options: LoadOptions | None = None,
    *,
    target_tps: float = 20.0,
    efficiency: float = DEFAULT_EFFICIENCY,
) -> VariantChoice | None:
    """Pick the quantization that best balances quality and fit; shrink context if needed."""
    options = options or LoadOptions()
    variants = variants or default_variants(model)
    context = options.context_length
    while True:
        opts = options.model_copy(update={"context_length": context})
        evaluated = [
            (v, analyze(model, v, budget, opts, target_tps=target_tps, efficiency=efficiency)) for v in variants
        ]
        runnable = [vf for vf in evaluated if vf[1].runnable]
        if runnable or context <= MIN_CONTEXT:
            break
        context = max(MIN_CONTEXT, context // 2)
    if not evaluated:
        return None
    pool = _preferred_pool(runnable) if runnable else evaluated
    best = max(pool, key=lambda vf: (variant_utility(vf[1], target_tps), q.quality_rank(vf[1].quant)))
    if context < options.context_length and best[1].runnable:
        best[1].reasons.append(
            Reason(kind="i", text=tr("Context reduced to {n} tokens so the model fits in memory.", n=context))
        )
    return VariantChoice(best, evaluated)
