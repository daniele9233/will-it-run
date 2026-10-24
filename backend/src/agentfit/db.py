"""SQLite storage with forward-only numbered migrations.

Migrations are ``migrations/NNNN_name.sql`` files applied in order inside a
transaction; the applied version is tracked in ``schema_migrations``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from agentfit import paths


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _migration_files() -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    for entry in resources.files("agentfit.migrations").iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        version = int(name.split("_", 1)[0])
        out.append((version, name, entry.read_text(encoding="utf-8")))
    return sorted(out)


class Database:
    """Thin, thread-safe wrapper around one SQLite file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = str(path) if path is not None else str(paths.db_path())
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    # -- migrations ---------------------------------------------------------
    def schema_version(self) -> int:
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    def migrate(self) -> int:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            current = self.schema_version()
            for version, name, sql in _migration_files():
                if version <= current:
                    continue
                try:
                    self._conn.execute("BEGIN")
                    for statement in _split_sql(sql):
                        self._conn.execute(statement)
                    self._conn.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                        (version, name, utcnow()),
                    )
                    self._conn.execute("COMMIT")
                except sqlite3.Error:
                    self._conn.execute("ROLLBACK")
                    raise
            return self.schema_version()

    # -- primitives ---------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- key/value JSON documents ------------------------------------------
    def kv_get(self, namespace: str, key: str) -> Any | None:
        row = self.query_one("SELECT value FROM kv WHERE namespace = ? AND key = ?", (namespace, key))
        return json.loads(row["value"]) if row else None

    def kv_get_with_age(self, namespace: str, key: str) -> tuple[Any, float] | None:
        row = self.query_one(
            "SELECT value, updated_at FROM kv WHERE namespace = ? AND key = ?", (namespace, key)
        )
        if not row:
            return None
        updated = datetime.fromisoformat(row["updated_at"])
        age_hours = (datetime.now(UTC) - updated).total_seconds() / 3600
        return json.loads(row["value"]), age_hours

    def kv_set(self, namespace: str, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(namespace, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (namespace, key, json.dumps(value, default=str), utcnow()),
        )

    def kv_delete(self, namespace: str, key: str) -> None:
        self.execute("DELETE FROM kv WHERE namespace = ? AND key = ?", (namespace, key))

    def kv_list(self, namespace: str) -> dict[str, Any]:
        rows = self.query("SELECT key, value FROM kv WHERE namespace = ? ORDER BY key", (namespace,))
        return {r["key"]: json.loads(r["value"]) for r in rows}


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    for chunk in sql.split(";"):
        lines = [ln for ln in chunk.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        if lines:
            statements.append("\n".join(lines))
    return statements


_default: Database | None = None
_default_lock = threading.Lock()


def get_db() -> Database:
    global _default
    with _default_lock:
        if _default is None:
            _default = Database()
        return _default


def reset_default_db() -> None:
    global _default
    with _default_lock:
        if _default is not None:
            _default.close()
        _default = None
