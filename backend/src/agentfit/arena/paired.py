"""Is the difference between two models real, or noise?

Two quantizations of one model answer the same questions, so the honest comparison is *paired*: question by question, not
two scores side by side. Two scores of 71.2 and 68.9 mean nothing alone; "the Q4 got 9 questions right that the IQ3 missed,
and the IQ3 got 4 right that the Q4 missed" can be tested.

Three things are computed, all of them plain arithmetic on the stored verdicts (no model, no randomness except a bootstrap
that starts from a fixed seed, so the same results always give the same numbers):

- the **difference** in score, on the questions both were scored on, each category weighing the same (as in the ranking);
- an **interval** for that difference, from a bootstrap that resamples the questions of each category, keeping the pairs;
- an exact **sign test** (the exact McNemar test for right/wrong answers): among the questions on which the two parted, is
  one of them ahead more often than a coin would give? The p-values of the several comparisons against one reference are
  corrected with Holm's method, so that asking three questions of the data does not triple the chance of a false alarm.

A difference is called *real* only when the interval leaves out zero **and** the corrected test agrees. Everything else is
*noise*, and the report says how small a difference this run could not have seen (``detectable``), because "no difference
found" and "too few questions to tell" are different things.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from agentfit.arena.rng import Rng

DRAWS = 1000
ALPHA = 0.05
# 1.96 + 0.84: the difference that a two-sided test at 5 % finds four times out of five.
POWER_FACTOR = 2.8
EPSILON = 1e-9


def sign_test(better: int, worse: int) -> float:
    """Exact two-sided p-value: among ``better + worse`` questions on which two models parted, one was ahead on ``better`` of them.

    A coin is the null hypothesis. 1.0 when they never parted.
    """
    total = better + worse
    if total == 0:
        return 1.0
    tail = sum(math.comb(total, k) for k in range(min(better, worse) + 1))
    return min(1.0, 2 * tail / (1 << total))


def holm(pvalues: list[float]) -> list[float]:
    """Holm's step-down correction for several comparisons, in the order given."""
    count = len(pvalues)
    order = sorted(range(count), key=lambda i: pvalues[i])
    adjusted = [0.0] * count
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (count - rank) * pvalues[index]))
        adjusted[index] = running
    return adjusted


def macro(strata: dict[str, list[float]]) -> float:
    """The mean of the category means, in percent: how a model's score is made."""
    means = [sum(values) / len(values) for values in strata.values() if values]
    return 100 * sum(means) / len(means) if means else 0.0


def bootstrap(strata: dict[str, list[float]], seed: int, key: str, draws: int = DRAWS) -> list[float]:
    """The macro mean over ``draws`` resamples that keep every category's size, sorted."""
    rng = Rng(seed, key)
    out = []
    for _ in range(draws):
        resampled = {c: [values[rng.below(len(values))] for _ in values] for c, values in strata.items() if values}
        out.append(macro(resampled))
    out.sort()
    return out


def interval_of(draws: list[float]) -> list[float] | None:
    if not draws:
        return None
    low, high = draws[int(0.025 * len(draws))], draws[max(0, int(0.975 * len(draws)) - 1)]
    return [round(low, 2), round(high, 2)]


def _strata(values: dict[str, float], questions: list[str], category_of: dict[str, str]) -> dict[str, list[float]]:
    strata: dict[str, list[float]] = {}
    for question in questions:
        strata.setdefault(category_of[question], []).append(values[question])
    return strata


def score_of(values: dict[str, float], questions: list[str], category_of: dict[str, str], seed: int, key: str) -> dict[str, Any]:
    """A model's score on the shared questions, with its interval."""
    strata = _strata(values, questions, category_of)
    return {"score": round(macro(strata), 2), "ci": interval_of(bootstrap(strata, seed, key))}


def difference(reference: dict[str, float], other: dict[str, float], questions: list[str], category_of: dict[str, str], seed: int, key: str) -> dict[str, Any]:
    """``other`` against ``reference`` on the shared questions: the gap, its interval and the sign test, before correction."""
    strata: dict[str, list[float]] = {}
    better = worse = 0
    for question in questions:
        gap = other[question] - reference[question]
        strata.setdefault(category_of[question], []).append(gap)
        if gap > EPSILON:
            better += 1
        elif gap < -EPSILON:
            worse += 1
    draws = bootstrap(strata, seed, key)
    spread = statistics.pstdev(draws) if len(draws) > 1 else 0.0
    return {
        "delta": round(macro(strata), 2), "ci": interval_of(draws), "better": better, "worse": worse, "same": len(questions) - better - worse,
        "p": sign_test(better, worse),
        # The gap this run could not have seen: below it, "no difference" only means "too few questions to tell".
        "detectable": round(POWER_FACTOR * spread, 2),
        "identical": better == 0 and worse == 0,
    }


def verdict(delta: float, ci: list[float] | None, p_adjusted: float) -> str:
    """``worse``, ``better`` or ``noise``. A difference is real only if the interval leaves out zero and the corrected test agrees."""
    if ci is None or p_adjusted >= ALPHA or ci[0] <= 0 <= ci[1]:
        return "noise"
    return "better" if delta > 0 else "worse"


def category_gaps(reference: dict[str, float], other: dict[str, float], questions: list[str], category_of: dict[str, str]) -> dict[str, dict[str, Any]]:
    """The same comparison inside each category. Too few questions to test: only the gap and who was ahead how often are given."""
    out: dict[str, dict[str, Any]] = {}
    for question in questions:
        row = out.setdefault(category_of[question], {"n": 0, "ref": 0.0, "other": 0.0, "better": 0, "worse": 0})
        row["n"] += 1
        row["ref"] += reference[question]
        row["other"] += other[question]
        gap = other[question] - reference[question]
        row["better"] += gap > EPSILON
        row["worse"] += gap < -EPSILON
    for row in out.values():
        row["ref"] = round(100 * row["ref"] / row["n"], 2)
        row["other"] = round(100 * row["other"] / row["n"], 2)
        row["delta"] = round(row["other"] - row["ref"], 2)
    return out
