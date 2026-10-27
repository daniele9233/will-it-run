"""Put models that were tested in separate challenges side by side.

A challenge ranks the models it was started with. When one model is tested today and another next week, the two
challenges have each their own ranking, and nothing compares them. This copies the answers of the chosen models into a new
challenge, so the ranking, the matrix of questions, the duel and every report work on it unchanged.

It is honest about what makes two challenges comparable: they must have asked **the same questions** (same suite, version,
hash and seed), because a score on other questions says nothing about this one. The sampling may differ (another
temperature, another context): that does not stop the comparison, but it is written in the new challenge so nobody reads
the figures as if everything had been equal.
"""

from __future__ import annotations

import json
from typing import Any

from agentfit.arena.store import ArenaStore, new_run_id
from agentfit.db import utcnow
from agentfit.i18n import tr

#: What must be identical for two challenges to have asked the same thing.
IDENTITY = ("suite_id", "suite_version", "suite_hash", "bank_hash", "seed")
#: What may differ, and is reported when it does.
SAMPLING = ("temperature", "top_p", "top_k", "repeat_penalty", "seed", "repeats", "context", "max_tokens")
LIVE = ("queued", "running", "stopping")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class CompareError(Exception):
    def __init__(self, message: str, *, code: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _describe(run: dict[str, Any]) -> str:
    return f"{run['title'] or run['id']} ({run['id']})"


def merge(store: ArenaStore, run_ids: list[str], title: str = "") -> dict[str, Any]:
    """Copy the models of these challenges into a new one. Returns its id and what the person should know about it."""
    unique = list(dict.fromkeys(run_ids))
    if len(unique) < 2:
        raise CompareError(tr("Choose at least two challenges to compare."), code="few", status=400)
    runs = []
    for run_id in unique:
        run = store.get_run(run_id)
        if run is None:
            raise CompareError(tr("This run does not exist."), code="missing", status=404)
        if run["status"] in LIVE:
            raise CompareError(tr("{run} is still working: wait for it to finish.", run=_describe(run)), code="busy")
        if (run["settings"] or {}).get("merged"):
            raise CompareError(tr("{run} is already a comparison: choose the challenges it was made from.", run=_describe(run)), code="nested")
        runs.append(run)
    # Oldest first; challenges made within the same second keep the order they were chosen in.
    given = {run_id: position for position, run_id in enumerate(unique)}
    runs.sort(key=lambda r: (r["created_at"], given[r["id"]]))
    first = runs[0]
    for run in runs[1:]:
        different = [name for name in IDENTITY if run[name] != first[name]]
        if different:
            raise CompareError(
                tr("{a} and {b} did not ask the same questions ({what}): their scores cannot be compared.", a=_describe(first), b=_describe(run),
                   what=", ".join(different)),
                code="different_questions",
            )

    # The newest challenge wins when a model was tested twice.
    chosen: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    replaced: list[dict[str, str]] = []
    for run in runs:
        for model in store.models(run["id"]):
            if store.db.query_one("SELECT 1 AS x FROM arena_result WHERE run_id = ? AND model_key = ? LIMIT 1", (run["id"], model["model_key"])) is None:
                continue  # a model that never answered has nothing to compare
            if model["model_key"] in chosen:
                replaced.append({"model": model["label"], "kept": run["id"], "dropped": chosen[model["model_key"]][0]["id"]})
            chosen[model["model_key"]] = (run, model)
    if len(chosen) < 2:
        raise CompareError(tr("These challenges hold fewer than two models with answers."), code="few_models", status=400)

    differences: list[str] = []
    for name in SAMPLING:
        values = {str((run["settings"] or {}).get(name)) for run, _ in chosen.values()}
        if len(values) > 1:
            differences.append(tr("{name} differs between the challenges: {values}.", name=name, values=", ".join(sorted(values))))

    new_id = new_run_id()
    settings = dict(first["settings"] or {})
    settings["merged"] = {"from": [r["id"] for r in runs], "differences": differences, "replaced": replaced}
    stamp = utcnow()
    label = title.strip() or " + ".join(model["label"] for _, model in list(chosen.values())[:3]) + (f" +{len(chosen) - 3}" if len(chosen) > 3 else "")
    with store.db.transaction() as conn:
        conn.execute(
            "INSERT INTO arena_run(id, title, status, created_at, started_at, finished_at, suite_id, suite_version, suite_hash, bank_hash, seed, preset, "
            "categories, settings, environment, total, done, note) VALUES (?, ?, 'done', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'merged')",
            (new_id, label, stamp, runs[0]["started_at"] or runs[0]["created_at"], stamp, first["suite_id"], first["suite_version"], first["suite_hash"],
             first["bank_hash"], first["seed"], first["preset"], _json(first["categories"]), _json(settings),
             _json(first["environment"])),
        )
        conn.execute(
            "INSERT INTO arena_question(run_id, question_id, position, category, title, tier, difficulty, data) "
            "SELECT ?, question_id, position, category, title, tier, difficulty, data FROM arena_question WHERE run_id = ?",
            (new_id, first["id"]),
        )
        for position, (run, model) in enumerate(chosen.values()):
            conn.execute(
                "INSERT INTO arena_model(run_id, model_key, position, label, cloud, vision, status, started_at, finished_at, load_seconds, context_asked, "
                "context_granted, error, info) SELECT ?, model_key, ?, label, cloud, vision, status, started_at, finished_at, load_seconds, context_asked, "
                "context_granted, error, info FROM arena_model WHERE run_id = ? AND model_key = ?",
                (new_id, position, run["id"], model["model_key"]),
            )
            conn.execute(
                "INSERT INTO arena_result(run_id, model_key, question_id, repeat, status, score, kind, method, verdict, answer, raw, reasoning, tool_calls, "
                "finish_reason, input_tokens, output_tokens, reasoning_tokens, ttft_s, duration_s, tps, error, manual, manual_note, created_at) "
                "SELECT ?, model_key, question_id, repeat, status, score, kind, method, verdict, answer, raw, reasoning, tool_calls, finish_reason, "
                "input_tokens, output_tokens, reasoning_tokens, ttft_s, duration_s, tps, error, manual, manual_note, created_at "
                "FROM arena_result WHERE run_id = ? AND model_key = ?",
                (new_id, run["id"], model["model_key"]),
            )
    count = store.count_results(new_id)
    store.update_run(new_id, total=count, done=count)
    return {"id": new_id, "models": [m["label"] for _, m in chosen.values()], "differences": differences, "replaced": replaced}
