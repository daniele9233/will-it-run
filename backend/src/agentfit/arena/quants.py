"""One model in several quantizations, side by side.

A run that holds ``Qwen3 30B IQ3_XXS``, ``Q3_K_M`` and ``Q4_K_M`` has three models for the ranking and one model for the
person who is choosing which file to keep. This module finds the models that are the same model (the same name once the
quantization is taken away, the same size in parameters and architecture), puts them in order of precision, and compares
each against the most precise one: score, difference per category, whether the difference is real or noise (``paired``),
the size of the file, the graphics memory it took, and the speed.

Nothing here asks a model anything: it reads what the run stored.
"""

from __future__ import annotations

from typing import Any

from agentfit.arena import paired, scoring
from agentfit.arena.categories import order_of
from agentfit.arena.store import ArenaStore
from agentfit.fit.quant import normalize_quant, quality_rank, quant_bits, quant_in, strip_quant

# Bits per weight of each GGUF type, as llama.cpp reports them for a typical model. Used only to put the quantizations of a
# model in order of precision: a file's real size depends on the model.
NOMINAL_BPW: dict[str, float] = {
    "F32": 32.0, "F16": 16.0, "BF16": 16.0, "Q8_0": 8.5, "Q6_K": 6.56,
    "Q5_1": 6.0, "Q5_K_M": 5.69, "Q5_K_S": 5.54, "Q5_0": 5.5,
    "Q4_1": 5.0, "Q4_K_M": 4.85, "Q4_K_S": 4.58, "Q4_0": 4.5, "IQ4_NL": 4.5, "IQ4_XS": 4.25, "MXFP4": 4.25,
    "Q3_K_L": 4.27, "Q3_K_M": 3.91, "Q3_K_S": 3.5, "IQ3_M": 3.66, "IQ3_S": 3.44, "IQ3_XS": 3.3, "IQ3_XXS": 3.06,
    "Q2_K": 2.63, "IQ2_M": 2.7, "IQ2_S": 2.5, "IQ2_XS": 2.31, "IQ2_XXS": 2.06, "IQ1_M": 1.75, "IQ1_S": 1.56,
}
SCORED = ("passed", "partial", "failed")


def bits_per_weight(quant: str | None) -> float | None:
    """Nominal bits per weight of a quantization, None when it is not a GGUF type this knows."""
    if not quant:
        return None
    name = normalize_quant(quant)
    if name in NOMINAL_BPW:
        return NOMINAL_BPW[name]
    return round(quality_rank(name) + 0.7, 2) if quant_bits(name) is not None else None


def _quant_of(model: dict[str, Any]) -> str | None:
    info = model.get("info") or {}
    return info.get("quant") or quant_in(model["model_key"]) or quant_in(model["label"] or "")


def candidates(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The local models of a run, with what is needed to tell whether two are one model."""
    out = []
    for model in models:
        if model["cloud"]:
            continue
        info = model.get("info") or {}
        quant = _quant_of(model)
        name = strip_quant(model["label"] or model["model_key"]) or strip_quant(model["model_key"])
        out.append({
            "key": model["model_key"], "label": model["label"] or model["model_key"], "quant": quant, "bits": bits_per_weight(quant),
            "size_bytes": info.get("size_bytes"), "params": info.get("params"), "architecture": info.get("architecture"),
            "vram_mib": info.get("vram_mib"), "vram_total_mib": info.get("vram_total_mib"),
            "spilled": bool(info.get("partial_offload") or info.get("saturated")),
            "family": "|".join((name, str(info.get("params") or "").lower(), str(info.get("architecture") or "").lower())),
            "position": model["position"],
        })
    return out


def _by_precision(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda c: (-(c["bits"] if c["bits"] is not None else -1.0), -(c["size_bytes"] or 0), c["position"]))


def find_families(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The models that are one model in different quantizations: two or more with the same name, size and architecture, each in
    a quantization of its own. The first member is the most precise."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in cands:
        groups.setdefault(item["family"], []).append(item)
    families = []
    for family, items in groups.items():
        distinct: dict[str, dict[str, Any]] = {}
        for item in items:
            distinct.setdefault(item["quant"] or item["key"], item)
        if len(distinct) < 2:
            continue
        members = _by_precision(list(distinct.values()))
        families.append({"id": family, "label": members[0]["label"], "members": [m["key"] for m in members], "reference": members[0]["key"]})
    families.sort(key=lambda f: min(next(c["position"] for c in cands if c["key"] == k) for k in f["members"]))
    return families


def _question_values(rows: list[dict[str, Any]], keys: set[str]) -> dict[str, dict[str, float]]:
    """For each model, each question it was scored on: the mean over its repeats."""
    scores: dict[str, dict[str, list[float]]] = {k: {} for k in keys}
    for row in rows:
        if row["model_key"] in keys and row["status"] in SCORED and row["score"] is not None:
            scores[row["model_key"]].setdefault(row["question_id"], []).append(float(row["score"]))
    return {k: {q: sum(v) / len(v) for q, v in by_question.items()} for k, by_question in scores.items()}


def compare(store: ArenaStore, run_id: str, *, members: list[str] | None = None, reference: str | None = None, family: str | None = None) -> dict[str, Any] | None:
    """The families a run holds and, for the chosen one, every member against the reference. None when the run does not exist.

    ``members`` names the models to compare when the person chose them by hand (two models are the same model only by their
    names, and a name can be unlike); otherwise ``family`` picks one of the families found, or the first.
    """
    run = store.get_run(run_id)
    if run is None:
        return None
    models = store.models(run_id)
    cands = candidates(models)
    by_key = {c["key"]: c for c in cands}
    families = find_families(cands)
    if members:
        chosen = [k for k in dict.fromkeys(members) if k in by_key]
    elif family and any(f["id"] == family for f in families):
        chosen = next(f["members"] for f in families if f["id"] == family)
    else:
        chosen = families[0]["members"] if families else []
    out: dict[str, Any] = {"families": families, "candidates": cands, "members": chosen, "reference": None, "comparison": None, "problem": None}
    if len(chosen) < 2:
        out["problem"] = "needs_two"
        return out
    order = [c["key"] for c in _by_precision([by_key[k] for k in chosen])]
    ref = reference if reference in chosen else order[0]
    out["reference"] = ref

    seed = int(run["seed"])
    questions = store.questions(run_id)
    category_of = {q["id"]: q["category"] for q in questions}
    rows = store.results(run_id, light=True)
    values = _question_values(rows, set(chosen))
    asked = [q["id"] for q in questions]
    shared = [q for q in asked if all(q in values[k] for k in chosen)]
    touched = [q for q in asked if any(q in values[k] for k in chosen)]
    if not shared:
        out["problem"] = "no_common"
        return out

    others = [k for k in order if k != ref]
    gaps = {k: paired.difference(values[ref], values[k], shared, category_of, seed, f"quant:{ref}:{k}") for k in others}
    adjusted = dict(zip(others, paired.holm([gaps[k]["p"] for k in others]), strict=True))

    rows_of = {k: [r for r in rows if r["model_key"] == k] for k in chosen}
    stored = {m["model_key"]: m for m in models}
    result: dict[str, dict[str, Any]] = {}
    for key in order:
        item = by_key[key]
        summary = scoring.summarize(rows_of[key], category_of, seed)
        entry: dict[str, Any] = {
            **{k: item[k] for k in ("key", "label", "quant", "bits", "size_bytes", "vram_mib", "vram_total_mib", "spilled")},
            "is_reference": key == ref,
            **paired.score_of(values[key], shared, category_of, seed, f"quant:{key}"),
            "tps_mean": summary["speed"]["tps_mean"], "ttft_median_s": summary["speed"]["ttft_median_s"],
            "load_seconds": stored[key]["load_seconds"], "context_granted": stored[key]["context_granted"],
        }
        if key != ref:
            gap = gaps[key]
            # ``ci`` is the interval of the model's own score; ``delta_ci`` that of its difference from the reference.
            entry.update({("delta_ci" if name == "ci" else name): value for name, value in gap.items()},
                         p_adjusted=adjusted[key], verdict=paired.verdict(gap["delta"], gap["ci"], adjusted[key]))
        result[key] = entry

    per_category: dict[str, dict[str, Any]] = {}
    category_gaps = {k: paired.category_gaps(values[ref], values[k], shared, category_of) for k in others}
    for key in others:
        for category, row in category_gaps[key].items():
            cell = per_category.setdefault(category, {"id": category, "n": row["n"], "models": {}})
            cell["models"][ref] = {"score": row["ref"], "delta": None, "better": None, "worse": None}
            cell["models"][key] = {"score": row["other"], "delta": row["delta"], "better": row["better"], "worse": row["worse"]}

    out["comparison"] = {
        "order": order, "reference": ref, "models": result,
        "categories": sorted(per_category.values(), key=lambda c: order_of(c["id"])),
        "questions": len(shared), "dropped": len(touched) - len(shared),
        "settings": {"repeats": run["settings"].get("repeats"), "temperature": run["settings"].get("temperature"), "seed": run["settings"].get("seed")},
        "suite_seed": seed, "alpha": paired.ALPHA, "draws": paired.DRAWS,
        # Speeds that differ because a model overflowed into system RAM are not a property of the quantization.
        "speed_comparable": not any(result[k]["spilled"] for k in chosen),
    }
    return out
