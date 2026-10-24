"""What the Arena keeps in the database: runs, the questions each one asked, its models, and every answer.

Nothing here decides anything. A result is stored as the runner produced it (the verdict as JSON, the reason as an English
template and its values), and is turned into words only when it is read, in the language of whoever reads it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from agentfit.db import Database, utcnow

_RUN_JSON = ("categories", "settings", "environment")
_RESULT_JSON = ("verdict", "tool_calls")
# What a run's list and matrix need: the answer's text and reasoning, which are the heavy part, stay out of it.
_LIGHT_RESULT = (
    "model_key, question_id, repeat, status, score, kind, method, finish_reason, input_tokens, output_tokens, reasoning_tokens, "
    "ttft_s, duration_s, tps, manual, created_at"
)
RUN_FIELDS = {"title", "status", "started_at", "finished_at", "done", "total", "note", "environment", "settings"}
MODEL_FIELDS = {"status", "started_at", "finished_at", "load_seconds", "context_asked", "context_granted", "error", "info", "vision"}


def new_run_id() -> str:
    return "arena-" + uuid.uuid4().hex[:12]


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class ArenaStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- runs ---------------------------------------------------------------
    def create_run(self, run_id: str, *, title: str, suite: Any, settings: dict[str, Any], environment: dict[str, Any],
                   models: list[dict[str, Any]], questions: list[dict[str, Any]], total: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO arena_run(id, title, status, created_at, suite_id, suite_version, suite_hash, bank_hash, seed, preset, categories, "
                "settings, environment, total) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, title, utcnow(), suite.id, suite.version, suite.hash, suite.bank_hash, suite.seed, suite.preset,
                 _dump(suite.categories), _dump(settings), _dump(environment), total),
            )
            for position, model in enumerate(models):
                conn.execute(
                    "INSERT INTO arena_model(run_id, model_key, position, label, cloud, vision, context_asked, info) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, model["model_key"], position, model["label"], int(bool(model.get("cloud"))), int(bool(model.get("vision"))),
                     model.get("context_asked"), _dump(model.get("info") or {})),
                )
            for position, q in enumerate(questions):
                conn.execute(
                    "INSERT INTO arena_question(run_id, question_id, position, category, title, tier, difficulty, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, q["id"], position, q["category"], q["title"], q["tier"], q["difficulty"], _dump(q)),
                )

    def update_run(self, run_id: str, **fields: Any) -> None:
        unknown = set(fields) - RUN_FIELDS
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [_dump(v) if k in _RUN_JSON else v for k, v in fields.items()]
        self.db.execute(f"UPDATE arena_run SET {sets} WHERE id = ?", (*values, run_id))

    @staticmethod
    def _run(row: dict[str, Any]) -> dict[str, Any]:
        for key in _RUN_JSON:
            row[key] = _load(row.get(key), [] if key == "categories" else {})
        return row

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM arena_run WHERE id = ?", (run_id,))
        return self._run(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        return [self._run(r) for r in self.db.query("SELECT * FROM arena_run ORDER BY created_at DESC, id DESC")]

    def delete_run(self, run_id: str) -> bool:
        return self.db.execute("DELETE FROM arena_run WHERE id = ?", (run_id,)).rowcount > 0

    def interrupt_running(self) -> int:
        """A run that was working when the app closed cannot be working now: it is stopped, and can be resumed."""
        stamp = utcnow()
        cursor = self.db.execute(
            "UPDATE arena_run SET status = 'stopped', finished_at = ?, note = 'interrupted' WHERE status IN ('queued', 'running')", (stamp,)
        )
        self.db.execute("UPDATE arena_model SET status = 'stopped' WHERE status = 'running'")
        return cursor.rowcount

    # -- models -------------------------------------------------------------
    def models(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM arena_model WHERE run_id = ? ORDER BY position", (run_id,))
        for row in rows:
            row["info"] = _load(row.get("info"), {})
            row["cloud"] = bool(row["cloud"])
            row["vision"] = bool(row["vision"])
        return rows

    def update_model(self, run_id: str, model_key: str, **fields: Any) -> None:
        unknown = set(fields) - MODEL_FIELDS
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [_dump(v) if k == "info" else v for k, v in fields.items()]
        self.db.execute(f"UPDATE arena_model SET {sets} WHERE run_id = ? AND model_key = ?", (*values, run_id, model_key))

    def merge_model_info(self, run_id: str, model_key: str, **fields: Any) -> None:
        """Add what was measured while the model was loaded (graphics memory used) to what is known about it."""
        row = self.db.query_one("SELECT info FROM arena_model WHERE run_id = ? AND model_key = ?", (run_id, model_key))
        if row is None:
            return
        info = _load(row["info"], {})
        info.update(fields)
        self.db.execute("UPDATE arena_model SET info = ? WHERE run_id = ? AND model_key = ?", (_dump(info), run_id, model_key))

    # -- questions ----------------------------------------------------------
    def questions(self, run_id: str) -> list[dict[str, Any]]:
        """Which questions the run asked, without their text and pictures."""
        return self.db.query(
            "SELECT question_id AS id, position, category, title, tier, difficulty FROM arena_question WHERE run_id = ? ORDER BY position", (run_id,)
        )

    def question(self, run_id: str, question_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT data FROM arena_question WHERE run_id = ? AND question_id = ?", (run_id, question_id))
        return _load(row["data"], None) if row else None

    def all_questions(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT data FROM arena_question WHERE run_id = ? ORDER BY position", (run_id,))
        return [_load(r["data"], {}) for r in rows]

    # -- results ------------------------------------------------------------
    def save_result(self, run_id: str, model_key: str, question_id: str, repeat: int, result: dict[str, Any]) -> None:
        columns = ("status", "score", "kind", "method", "verdict", "answer", "raw", "reasoning", "tool_calls", "finish_reason", "input_tokens",
                   "output_tokens", "reasoning_tokens", "ttft_s", "duration_s", "tps", "error")
        values = []
        for column in columns:
            value = result.get(column)
            if column in _RESULT_JSON:
                value = _dump(value if value is not None else ({} if column == "verdict" else []))
            elif column in ("answer", "raw", "reasoning"):
                value = value or ""
            values.append(value)
        self.db.execute(
            f"INSERT OR REPLACE INTO arena_result(run_id, model_key, question_id, repeat, {', '.join(columns)}, created_at) "
            f"VALUES (?, ?, ?, ?, {', '.join('?' for _ in columns)}, ?)",
            (run_id, model_key, question_id, repeat, *values, utcnow()),
        )

    def results(self, run_id: str, *, model_key: str | None = None, question_id: str | None = None, light: bool = False) -> list[dict[str, Any]]:
        where, params = ["run_id = ?"], [run_id]
        if model_key is not None:
            where.append("model_key = ?")
            params.append(model_key)
        if question_id is not None:
            where.append("question_id = ?")
            params.append(question_id)
        rows = self.db.query(f"SELECT {_LIGHT_RESULT if light else '*'} FROM arena_result WHERE {' AND '.join(where)} ORDER BY model_key, question_id, repeat", tuple(params))
        if not light:
            for row in rows:
                for key in _RESULT_JSON:
                    row[key] = _load(row.get(key), {} if key == "verdict" else [])
        return rows

    def result_keys(self, run_id: str) -> dict[tuple[str, str, int], str]:
        rows = self.db.query("SELECT model_key, question_id, repeat, status FROM arena_result WHERE run_id = ?", (run_id,))
        return {(r["model_key"], r["question_id"], int(r["repeat"])): r["status"] for r in rows}

    def count_results(self, run_id: str) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM arena_result WHERE run_id = ?", (run_id,))
        return int(row["n"]) if row else 0

    def count_errors(self, run_id: str) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM arena_result WHERE run_id = ? AND status = 'error'", (run_id,))
        return int(row["n"]) if row else 0

    def forget_errors(self, run_id: str) -> int:
        """Drop the answers the run itself broke, so a resumed run asks them again."""
        return self.db.execute("DELETE FROM arena_result WHERE run_id = ? AND status = 'error'", (run_id,)).rowcount

    def set_manual(self, run_id: str, model_key: str, question_id: str, repeat: int, mark: str | None, note: str = "") -> bool:
        """A person's own verdict on an answer. It is kept beside the result and never enters a score."""
        cursor = self.db.execute(
            "UPDATE arena_result SET manual = ?, manual_note = ? WHERE run_id = ? AND model_key = ? AND question_id = ? AND repeat = ?",
            (mark, note, run_id, model_key, question_id, repeat),
        )
        return cursor.rowcount > 0
