"""Model scoring and recommendation ranking.

Score = weighted sum of eight 0-100 components (weights configurable in settings):
hardware fit, task quality, specialization, speed, context, tool use, reliability and
local benchmark.

Alignment (standard / uncensored) is never a scoring component and never a penalty.
It only matters when the user sets an explicit preference; then matching models receive
a visible ``preference_bonus``. With the preference "any" the bonus is zero for all.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from agentfit.catalog.models import Alignment, ModelEntry
from agentfit.config import AlignmentPreference, ScoringWeights
from agentfit.fit import quant as q
from agentfit.fit.models import FitResult
from agentfit.hardware.models import Confidence

PREFERENCE_BONUS = 6.0
# A preferred-alignment model only gets the bonus when it is a credible choice for the task.
PREFERENCE_MIN_QUALITY = 50.0


class TaskProfile(BaseModel):
    """What a job needs from a model."""

    id: str = "general"
    label: str = "General"
    # Relative importance of each quality dimension; normalized internally.
    coding: float = 1.0
    reasoning: float = 1.0
    writing: float = 1.0
    categories: list[str] = Field(default_factory=list)
    needs_tools: bool = False
    needs_vision: bool = False
    min_context: int = 8192
    target_tps: float | None = None


class ScoreBreakdown(BaseModel):
    hardware_fit: float = 0
    task_quality: float = 0
    specialization: float = 0
    speed: float = 0
    context: float = 0
    tool_use: float = 0
    reliability: float = 0
    local_benchmark: float = 0
    preference_bonus: float = 0
    total: float = 0
    # 0-10 headline shown in the UI.
    agentfit_score: float = 0
    confidence: dict[str, str] = Field(default_factory=dict)


def task_quality(entry: ModelEntry, profile: TaskProfile, quant: str) -> float:
    weights = {"coding": profile.coding, "reasoning": profile.reasoning, "writing": profile.writing}
    total = sum(weights.values()) or 1.0
    base = (
        entry.quality.coding * weights["coding"]
        + entry.quality.reasoning * weights["reasoning"]
        + entry.quality.writing * weights["writing"]
    ) / total
    if base <= 0:
        return 0.0
    return max(0.0, min(100.0, base + q.quality_penalty(quant)))


def specialization_match(entry: ModelEntry, profile: TaskProfile) -> float:
    if profile.needs_vision and not entry.vision:
        return 0.0
    if not profile.categories:
        return 70.0
    wanted = set(profile.categories)
    have = set(entry.categories)
    if wanted & have:
        return 100.0 if len(wanted & have) == len(wanted) else 85.0
    return 55.0 if "General" in have else 25.0


def reliability(entry: ModelEntry) -> float:
    base = {Confidence.HIGH: 70.0, Confidence.MEDIUM: 55.0, Confidence.LOW: 35.0, Confidence.UNKNOWN: 30.0}[
        entry.metadata_confidence
    ]
    downloads = entry.downloads or 0
    # Adoption is weak evidence that a build loads and behaves: +0..30 on a log scale.
    popularity = min(30.0, 6.0 * math.log10(downloads + 1)) if downloads else 0.0
    return min(100.0, base + popularity)


def score_model(
    entry: ModelEntry,
    fit: FitResult,
    profile: TaskProfile,
    weights: ScoringWeights,
    *,
    target_tps: float = 20.0,
    preference: AlignmentPreference = AlignmentPreference.ANY,
    measured_tps: float | None = None,
) -> ScoreBreakdown:
    target = profile.target_tps or target_tps
    quality = task_quality(entry, profile, fit.quant)
    context_need = max(profile.min_context, 2048)
    components = {
        "hardware_fit": fit.fit_score,
        "task_quality": quality,
        "specialization": specialization_match(entry, profile),
        "speed": max(0.0, min(100.0, fit.speed.tokens_per_second / target * 100)) if target else 0.0,
        "context": 100.0 if fit.context_length >= context_need else 100.0 * fit.context_length / context_need,
        "tool_use": 100.0 if entry.tool_use else (60.0 if not profile.needs_tools else 0.0),
        "reliability": reliability(entry),
        # Unmeasured models sit at a neutral 50 so benchmarking can only inform, not gate.
        "local_benchmark": (min(100.0, measured_tps / target * 100) if measured_tps and target else 50.0),
    }
    w = weights.normalized()
    total = sum(components[name] * w[name] for name in components)

    bonus = 0.0
    wants = {AlignmentPreference.UNCENSORED: Alignment.UNCENSORED, AlignmentPreference.STANDARD: Alignment.STANDARD}.get(
        preference
    )
    if wants is not None and entry.alignment == wants and fit.runnable and quality >= PREFERENCE_MIN_QUALITY:
        bonus = PREFERENCE_BONUS
    total = max(0.0, min(100.0, total + bonus))

    return ScoreBreakdown(
        **{k: round(v, 1) for k, v in components.items()},
        preference_bonus=bonus,
        total=round(total, 1),
        agentfit_score=round(total / 10, 1),
        confidence={
            "hardware_fit": fit.memory.confidence.value,
            "speed": fit.speed.confidence.value,
            "task_quality": entry.quality.confidence.value,
            "local_benchmark": Confidence.HIGH.value if measured_tps else "NOT MEASURED",
        },
    )
