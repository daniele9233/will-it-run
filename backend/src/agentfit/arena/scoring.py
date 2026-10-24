"""From the results of a model to numbers a person can compare.

The rules, all of them about not fooling anyone:

- A question that was not scored (it needs a person, a tool this PC lacks), skipped (the model cannot see) or broken
  by the run itself is *not an error and not in the score*. It is counted apart and shown.
- Repeats of one question are averaged first, so a question asked three times does not weigh three.
- A category is the mean of its scored questions; a model's score is the mean of its categories, each weighing the same.
- Models are ranked on the categories **every** model of the run could be scored on, so one that answers an extra
  category (it can see) does not win or lose by that. Its score over everything it did is kept as ``score_all``.
- Every score comes with a bootstrap interval from a fixed seed: the same results always give the same interval.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from agentfit.arena.categories import AREAS, CATEGORIES, area_of, order_of
from agentfit.arena.model import FAIL_KINDS
from agentfit.arena.rng import Rng

BOOTSTRAP = 400
# An answer shorter than this says nothing about speed: the first token's delay swamps it.
MIN_TOKENS_FOR_SPEED = 20


def _interval(values: list[float], seed: int, key: str) -> list[float] | None:
    """A 95 % bootstrap interval of the mean, in percent. None when there are too few questions to say anything."""
    if len(values) < 2:
        return None
    rng = Rng(seed, key)
    n = len(values)
    means = sorted(sum(values[rng.below(n)] for _ in range(n)) / n for _ in range(BOOTSTRAP))
    low, high = means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]
    return [round(100 * low, 1), round(100 * high, 1)]


def question_value(rows: list[dict[str, Any]]) -> tuple[str, float | None]:
    """What a question came to over its repeats: (status, score)."""
    scored = [r["score"] for r in rows if r.get("score") is not None and r["status"] in ("passed", "partial", "failed")]
    if scored:
        value = sum(scored) / len(scored)
        return ("passed" if value >= 1.0 else "failed" if value <= 0.0 else "partial"), value
    statuses = {r["status"] for r in rows}
    for status in ("unscored", "error", "skipped"):
        if status in statuses:
            return status, None
    return "error", None


def summarize(rows: list[dict[str, Any]], question_categories: dict[str, str], seed: int) -> dict[str, Any]:
    """The numbers of one model: ``rows`` are its results (one per question and repeat), ``question_categories`` maps
    each question of the run to its category."""
    by_question: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_question.setdefault(row["question_id"], []).append(row)
    totals: dict[str, int] = {}
    for category in question_categories.values():
        totals[category] = totals.get(category, 0) + 1

    counts = {s: 0 for s in ("passed", "partial", "failed", "unscored", "skipped", "error")}
    fail_kinds: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] == "failed" and row.get("kind") in FAIL_KINDS:
            fail_kinds[row["kind"]] = fail_kinds.get(row["kind"], 0) + 1

    categories: dict[str, dict[str, Any]] = {}
    for category, total in totals.items():
        entry = {"total": total, "scored": 0, "passed": 0, "partial": 0, "failed": 0, "unscored": 0, "skipped": 0, "error": 0, "values": []}
        for question, category_of in question_categories.items():
            if category_of != category or question not in by_question:
                continue
            status, value = question_value(by_question[question])
            entry[status] += 1
            if value is not None:
                entry["scored"] += 1
                entry["values"].append(round(value, 4))
        values = entry["values"]
        entry["score"] = round(100 * sum(values) / len(values), 2) if values else None
        entry["ci"] = _interval(values, seed, f"category:{category}")
        categories[category] = entry

    timed = [r for r in rows if r.get("tps") and (r.get("output_tokens") or 0) >= MIN_TOKENS_FOR_SPEED]
    speeds = [float(r["tps"]) for r in timed]
    firsts = [float(r["ttft_s"]) for r in rows if r.get("ttft_s") is not None]
    speed = {
        "tps_mean": round(statistics.fmean(speeds), 2) if speeds else None,
        "tps_median": round(statistics.median(speeds), 2) if speeds else None,
        "ttft_median_s": round(statistics.median(firsts), 3) if firsts else None,
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
        "time_s": round(sum(float(r.get("duration_s") or 0) for r in rows), 1),
        "timed": len(timed),
    }

    repeats = max((len(v) for v in by_question.values()), default=0)
    consistency = None
    if repeats >= 2:
        steady = sum(1 for v in by_question.values() if len({r["status"] for r in v}) == 1)
        consistency = round(steady / len(by_question), 4)

    scored_questions = sum(c["scored"] for c in categories.values())
    return {
        "counts": counts,
        "answers": len(rows),
        "questions": {"total": len(question_categories), "answered": len(by_question), "scored": scored_questions},
        "categories": categories,
        "fail_kinds": fail_kinds,
        "speed": speed,
        "consistency": consistency,
        "coverage": round(scored_questions / len(question_categories), 4) if question_categories else 0.0,
    }


def areas_of(categories: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The radar axes: each is the mean of the scored categories that belong to it."""
    out: dict[str, dict[str, Any]] = {}
    for area in AREAS:
        members = [c for c, e in categories.items() if area_of(c) == area and e.get("score") is not None]
        scores = [categories[c]["score"] for c in members]
        out[area] = {"score": round(sum(scores) / len(scores), 2) if scores else None, "categories": sorted(members, key=order_of)}
    return out


@dataclass
class Ranking:
    common: list[str]
    models: list[dict[str, Any]]


def _macro(values_by_category: dict[str, list[float]], rng: Rng | None) -> float:
    means = []
    for values in values_by_category.values():
        if not values:
            continue
        if rng is None:
            means.append(sum(values) / len(values))
        else:
            n = len(values)
            means.append(sum(values[rng.below(n)] for _ in range(n)) / n)
    return 100 * sum(means) / len(means) if means else 0.0


def _macro_interval(values_by_category: dict[str, list[float]], seed: int, key: str) -> list[float] | None:
    if not values_by_category or all(len(v) < 2 for v in values_by_category.values()):
        return None
    rng = Rng(seed, key)
    draws = sorted(_macro(values_by_category, rng) for _ in range(BOOTSTRAP))
    return [round(draws[int(0.025 * BOOTSTRAP)], 1), round(draws[int(0.975 * BOOTSTRAP) - 1], 1)]


def rank(summaries: dict[str, dict[str, Any]], seed: int) -> Ranking:
    """Order the models of a run. ``summaries`` maps a model key to its ``summarize`` output."""
    scorable = [c for c, info in CATEGORIES.items() if info.scored]
    common = [
        c for c in scorable
        if summaries and all((s["categories"].get(c) or {}).get("score") is not None for s in summaries.values())
    ]
    rows: list[dict[str, Any]] = []
    for model, summary in summaries.items():
        categories = summary["categories"]
        own = [c for c in scorable if (categories.get(c) or {}).get("score") is not None]
        on_common = {c: categories[c]["values"] for c in common}
        own_values = {c: categories[c]["values"] for c in own}
        score = _macro(on_common, None) if common else None
        score_all = _macro(own_values, None) if own else None
        rows.append({
            "model": model,
            "score": round(score, 2) if score is not None else None,
            "ci": _macro_interval(on_common, seed, f"model:{model}:common") if common else None,
            "score_all": round(score_all, 2) if score_all is not None else None,
            "ci_all": _macro_interval(own_values, seed, f"model:{model}:all") if own else None,
            "categories_scored": len(own),
            "areas": areas_of(categories),
            "tps": (summary.get("speed") or {}).get("tps_mean"),
        })

    def sort_key(r: dict[str, Any]) -> tuple[float, float, float]:
        primary = r["score"] if r["score"] is not None else (r["score_all"] if r["score_all"] is not None else -1.0)
        return (-primary, -(r["tps"] or 0.0), 0.0)

    rows.sort(key=sort_key)
    previous: dict[str, Any] | None = None
    for position, r in enumerate(rows, start=1):
        key = r["score"] if r["score"] is not None else r["score_all"]
        same = previous is not None and key == (previous["score"] if previous["score"] is not None else previous["score_all"])
        r["rank"] = previous["rank"] if same and previous is not None else position
        r["tied"] = False
        if same and previous is not None:
            r["tied"] = previous["tied"] = True
        previous = r
    return Ranking(common=common, models=rows)
