"""A run read as a report: who is first, on what, by how much, and where each model failed.

Everything here is arithmetic on the stored results (``scoring`` does the sums), so the same results give the same report,
the screen and the exported file say the same things, and a run that is still working can be read while it works.
"""

from __future__ import annotations

from typing import Any

from agentfit.arena import quants, scoring
from agentfit.arena.categories import AREAS, CATEGORIES, GROUPS
from agentfit.arena.model import Reason
from agentfit.arena.store import ArenaStore


def render_reason(value: dict[str, Any] | None) -> str | None:
    """A stored reason (an English template and its values) in the language of whoever is reading."""
    if not value or not value.get("t"):
        return None
    try:
        return Reason.from_json(value).render()
    except (KeyError, IndexError, ValueError):
        return str(value.get("t"))


def render_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """A stored verdict with every reason turned into words."""
    out = dict(verdict)
    out["reason"] = render_reason(verdict.get("reason"))
    out["checks"] = [{"ok": bool(c.get("ok")), "text": render_reason(c)} for c in verdict.get("checks") or []]
    return out


def _cell(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status, value = scoring.question_value(rows)
    speeds = [r["tps"] for r in rows if r.get("tps")]
    kinds = sorted({r["kind"] for r in rows if r.get("kind")})
    return {
        "status": status, "score": round(value, 4) if value is not None else None, "kind": kinds[0] if kinds else None,
        "repeats": len(rows), "statuses": [r["status"] for r in rows], "tps": round(sum(speeds) / len(speeds), 1) if speeds else None,
        "tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
        "cut": any(r.get("finish_reason") == "length" for r in rows), "manual": next((r["manual"] for r in rows if r.get("manual")), None),
        "method": next((r["method"] for r in rows if r.get("method")), None),
    }


def build_report(store: ArenaStore, run_id: str) -> dict[str, Any] | None:
    run = store.get_run(run_id)
    if run is None:
        return None
    models = store.models(run_id)
    questions = store.questions(run_id)
    results = store.results(run_id, light=True)
    seed = int(run["seed"])
    category_of = {q["id"]: q["category"] for q in questions}
    rows_of: dict[str, list[dict[str, Any]]] = {m["model_key"]: [] for m in models}
    for row in results:
        rows_of.setdefault(row["model_key"], []).append(row)
    summaries = {key: scoring.summarize(rows, category_of, seed) for key, rows in rows_of.items()}
    ranked = {k: s for k, s in summaries.items() if s["questions"]["scored"] > 0}
    ranking = scoring.rank(ranked, seed) if ranked else scoring.Ranking(common=[], models=[])
    standing = {r["model"]: r for r in ranking.models}

    model_rows: list[dict[str, Any]] = []
    for model in models:
        key = model["model_key"]
        summary = summaries[key]
        position = standing.get(key)
        model_rows.append({
            "key": key, "label": model["label"], "cloud": model["cloud"], "vision": model["vision"], "status": model["status"], "error": model["error"],
            "context_asked": model["context_asked"], "context_granted": model["context_granted"], "load_seconds": model["load_seconds"], "info": model["info"],
            "position": model["position"],
            "rank": position["rank"] if position else None, "tied": position["tied"] if position else False,
            "score": position["score"] if position else None, "ci": position["ci"] if position else None,
            "score_all": position["score_all"] if position else None, "ci_all": position["ci_all"] if position else None,
            "categories_scored": position["categories_scored"] if position else 0,
            "areas": {a: (position["areas"][a]["score"] if position else None) for a in AREAS},
            "summary": {k: v for k, v in summary.items() if k != "categories"},
        })
    model_rows.sort(key=lambda m: (m["rank"] is None, m["rank"] or 0, m["position"]))

    categories = []
    for category, info in CATEGORIES.items():
        total = sum(1 for c in category_of.values() if c == category)
        if not total:
            continue
        categories.append({
            "id": category, "group": info.group, "area": info.area, "scored": info.scored, "needs": list(info.needs), "total": total,
            "models": {key: {k: v for k, v in (summaries[key]["categories"].get(category) or {}).items() if k != "values"} for key in summaries},
        })

    cells: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in results:
        cells.setdefault(row["question_id"], {}).setdefault(row["model_key"], []).append(row)
    matrix = [
        {**q, "cells": {key: _cell(rows) for key, rows in cells.get(q["id"], {}).items()}}
        for q in questions
    ]
    run_meta = {k: v for k, v in run.items() if k not in ("categories",)}
    run_meta["categories"] = run["categories"]
    return {
        "run": run_meta, "models": model_rows, "common": ranking.common, "categories": categories, "matrix": matrix,
        "areas": list(AREAS), "groups": list(GROUPS), "provisional": run["status"] in ("queued", "running", "stopping"),
        # Models that are one model in different quantizations: the Quantizations tab compares them.
        "families": quants.find_families(quants.candidates(models)),
    }
