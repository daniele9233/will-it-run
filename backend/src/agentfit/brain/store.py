"""Where the Project Brain is kept: tables in Will It Run's SQLite file.

Memories, the things they are about (files, concepts) and the edges between them. The
keyword index is an FTS5 table kept in step by this module; where SQLite was built
without FTS5 the store falls back to a plain scan.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from agentfit.brain import text
from agentfit.brain.models import KINDS, Graph, GraphEdge, GraphNode, Memory, Project
from agentfit.db import Database, utcnow
from agentfit.logging_setup import get_logger

log = get_logger("brain")

_COLUMNS = (
    "id, project_id, kind, title, body, files, tags, source, agent, run_id, slot, created_at, updated_at, "
    "superseded_by, uses, embed_model, embedding IS NOT NULL AS has_vector"
)


def memory_node(memory_id: int) -> str:
    return f"m:{memory_id}"


def entity_node(entity_id: int) -> str:
    return f"e:{entity_id}"


def project_id_for(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _memory(row: dict[str, Any], model: str | None = None) -> Memory:
    return Memory(
        id=row["id"], project_id=row["project_id"], kind=row["kind"], title=row["title"], body=row["body"],
        files=json.loads(row["files"] or "[]"), tags=json.loads(row["tags"] or "[]"), source=row["source"],
        agent=row["agent"] or "", run_id=row["run_id"], slot=row["slot"], created_at=row["created_at"],
        updated_at=row["updated_at"], superseded_by=row["superseded_by"], uses=row["uses"] or 0,
        embedded=bool(row["has_vector"]) and (model is None or row["embed_model"] == model),
    )


class BrainStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.fts = self._ensure_fts()

    # -- keyword index ------------------------------------------------------
    def _ensure_fts(self) -> bool:
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS brain_fts USING fts5(title, body, extra, project UNINDEXED)"
            )
        except sqlite3.Error as exc:
            log.warning("FTS5 is not available (%s): keyword search will scan the memories instead", exc)
            return False
        return True

    def _index(self, memory: Memory) -> None:
        if not self.fts:
            return
        self.db.execute("DELETE FROM brain_fts WHERE rowid = ?", (memory.id,))
        if memory.superseded_by is None:
            self.db.execute(
                "INSERT INTO brain_fts(rowid, title, body, extra, project) VALUES (?, ?, ?, ?, ?)",
                (
                    memory.id, text.index_text(memory.title), text.index_text(memory.body),
                    text.index_text(" ".join(memory.files), " ".join(memory.tags), memory.kind), memory.project_id,
                ),
            )

    def keyword_ranks(self, project_id: str, query: str, limit: int = 60) -> list[int]:
        """Ids of current memories sharing words with ``query``, best first."""
        match = text.match_query(query)
        if match is None:
            return []
        if self.fts:
            try:
                rows = self.db.query(
                    "SELECT rowid AS id FROM brain_fts WHERE brain_fts MATCH ? AND project = ? "
                    "ORDER BY bm25(brain_fts, 4.0, 1.0, 2.0) LIMIT ?",
                    (match, project_id, limit),
                )
                return [r["id"] for r in rows]
            except sqlite3.Error as exc:
                log.warning("keyword search failed (%s); scanning instead", exc)
        wanted = text.stems(query)
        scored: list[tuple[int, int]] = []
        for memory in self.memories(project_id, limit=5000):
            score = text.shared(wanted, f"{memory.title} {memory.body} {' '.join(memory.files)} {' '.join(memory.tags)}")
            if score:
                scored.append((score, memory.id))
        return [memory_id for _, memory_id in sorted(scored, reverse=True)[:limit]]

    # -- projects -----------------------------------------------------------
    def upsert_project(self, key: str, kind: str, name: str, path: str | None) -> Project:
        now = utcnow()
        project_id = project_id_for(key)
        self.db.execute(
            "INSERT INTO brain_project(id, key, kind, name, path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, path = COALESCE(excluded.path, brain_project.path)",
            (project_id, key, kind, name, path, now, now),
        )
        project = self.project(project_id)
        assert project is not None
        return project

    def touch_project(self, project_id: str) -> None:
        self.db.execute("UPDATE brain_project SET updated_at = ? WHERE id = ?", (utcnow(), project_id))

    def counts(self, project_id: str) -> dict[str, int]:
        rows = self.db.query(
            "SELECT kind, COUNT(*) AS n FROM brain_memory WHERE project_id = ? AND superseded_by IS NULL GROUP BY kind", (project_id,)
        )
        return {r["kind"]: r["n"] for r in rows}

    def _project(self, row: dict[str, Any]) -> Project:
        counts = self.counts(row["id"])
        return Project(**row, memories=sum(counts.values()), counts=counts)

    def project(self, project_id: str) -> Project | None:
        row = self.db.query_one("SELECT * FROM brain_project WHERE id = ?", (project_id,))
        return self._project(row) if row else None

    def project_by_key(self, key: str) -> Project | None:
        return self.project(project_id_for(key))

    def projects(self) -> list[Project]:
        rows = self.db.query("SELECT * FROM brain_project ORDER BY updated_at DESC")
        return [self._project(r) for r in rows]

    def delete_project(self, project_id: str) -> None:
        if self.fts:
            self.db.execute("DELETE FROM brain_fts WHERE project = ?", (project_id,))
        self.db.execute("DELETE FROM brain_project WHERE id = ?", (project_id,))
        self.db.kv_delete("brain_codemap", project_id)

    # -- memories -----------------------------------------------------------
    def insert(
        self, project_id: str, *, kind: str, title: str, body: str, files: list[str], tags: list[str], source: str,
        agent: str = "", run_id: str | None = None, slot: str | None = None,
    ) -> Memory:
        if kind not in KINDS:
            raise ValueError(f"unknown kind of memory '{kind}'")
        now = utcnow()
        cursor = self.db.execute(
            "INSERT INTO brain_memory(project_id, kind, title, body, files, tags, source, agent, run_id, slot, content_hash, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id, kind, title, body, json.dumps(files), json.dumps(tags), source, agent, run_id, slot,
                text.content_hash(kind, title, body), now, now,
            ),
        )
        memory = self.get(int(cursor.lastrowid or 0))
        assert memory is not None
        self._index(memory)
        self.touch_project(project_id)
        return memory

    def rewrite(self, memory_id: int, *, kind: str, title: str, body: str, files: list[str], tags: list[str]) -> Memory:
        """Change a memory in place. Its vector no longer describes it, so it is dropped."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind of memory '{kind}'")
        self.db.execute(
            "UPDATE brain_memory SET kind = ?, title = ?, body = ?, files = ?, tags = ?, content_hash = ?, updated_at = ?, "
            "embedding = NULL, embed_model = NULL WHERE id = ?",
            (kind, title, body, json.dumps(files), json.dumps(tags), text.content_hash(kind, title, body), utcnow(), memory_id),
        )
        memory = self.get(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        self._index(memory)
        self.touch_project(memory.project_id)
        return memory

    def seen_again(self, memory_id: int) -> None:
        """The same thing was recorded once more: it is current as of now."""
        self.db.execute("UPDATE brain_memory SET updated_at = ? WHERE id = ?", (utcnow(), memory_id))

    def supersede(self, old_id: int, new_id: int) -> None:
        self.db.execute("UPDATE brain_memory SET superseded_by = ? WHERE id = ?", (new_id, old_id))
        old = self.get(old_id)
        if old is not None:
            self._index(old)
            self.link(old.project_id, memory_node(new_id), memory_node(old_id), "supersedes")

    def delete(self, memory_id: int) -> None:
        memory = self.get(memory_id)
        if memory is None:
            return
        node = memory_node(memory_id)
        if self.fts:
            self.db.execute("DELETE FROM brain_fts WHERE rowid = ?", (memory_id,))
        self.db.execute("DELETE FROM brain_edge WHERE project_id = ? AND (source = ? OR target = ?)", (memory.project_id, node, node))
        # What it had replaced becomes current again only if nothing else replaced it: simplest is to leave it replaced.
        self.db.execute("DELETE FROM brain_memory WHERE id = ?", (memory_id,))

    def get(self, memory_id: int, model: str | None = None) -> Memory | None:
        row = self.db.query_one(f"SELECT {_COLUMNS} FROM brain_memory WHERE id = ?", (memory_id,))
        return _memory(row, model) if row else None

    def get_many(self, ids: Iterable[int], model: str | None = None) -> dict[int, Memory]:
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return {}
        marks = ", ".join("?" for _ in wanted)
        rows = self.db.query(f"SELECT {_COLUMNS} FROM brain_memory WHERE id IN ({marks})", tuple(wanted))
        return {r["id"]: _memory(r, model) for r in rows}

    def by_slot(self, project_id: str, slot: str) -> Memory | None:
        row = self.db.query_one(f"SELECT {_COLUMNS} FROM brain_memory WHERE project_id = ? AND slot = ?", (project_id, slot))
        return _memory(row) if row else None

    def by_hash(self, project_id: str, content_hash: str) -> Memory | None:
        row = self.db.query_one(
            f"SELECT {_COLUMNS} FROM brain_memory WHERE project_id = ? AND content_hash = ? AND superseded_by IS NULL", (project_id, content_hash)
        )
        return _memory(row) if row else None

    def by_title(self, project_id: str, kind: str, title: str) -> Memory | None:
        row = self.db.query_one(
            f"SELECT {_COLUMNS} FROM brain_memory WHERE project_id = ? AND kind = ? AND title = ? COLLATE NOCASE "
            "AND superseded_by IS NULL AND slot IS NULL ORDER BY id DESC",
            (project_id, kind, title),
        )
        return _memory(row) if row else None

    def memories(
        self, project_id: str, *, kind: str | None = None, include_superseded: bool = False, limit: int = 200, offset: int = 0,
        model: str | None = None,
    ) -> list[Memory]:
        where, params = ["project_id = ?"], [project_id]
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if not include_superseded:
            where.append("superseded_by IS NULL")
        rows = self.db.query(
            f"SELECT {_COLUMNS} FROM brain_memory WHERE {' AND '.join(where)} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [_memory(r, model) for r in rows]

    def vectors(self, project_id: str, model: str, kinds: list[str] | None = None) -> list[tuple[int, bytes]]:
        """(id, stored vector) of every current memory embedded with ``model``."""
        where, params = ["project_id = ?", "superseded_by IS NULL", "embedding IS NOT NULL", "embed_model = ?"], [project_id, model]
        if kinds:
            where.append(f"kind IN ({', '.join('?' for _ in kinds)})")
            params.extend(kinds)
        rows = self.db.query(f"SELECT id, embedding FROM brain_memory WHERE {' AND '.join(where)}", tuple(params))
        return [(r["id"], r["embedding"]) for r in rows]

    def without_vector(self, project_id: str, model: str, limit: int) -> list[Memory]:
        rows = self.db.query(
            f"SELECT {_COLUMNS} FROM brain_memory WHERE project_id = ? AND superseded_by IS NULL "
            "AND (embedding IS NULL OR embed_model IS NOT ?) ORDER BY id DESC LIMIT ?",
            (project_id, model, limit),
        )
        return [_memory(r) for r in rows]

    def set_vector(self, memory_id: int, model: str, blob: bytes) -> None:
        self.db.execute("UPDATE brain_memory SET embedding = ?, embed_model = ? WHERE id = ?", (blob, model, memory_id))

    def vector_of(self, memory_id: int, model: str) -> bytes | None:
        row = self.db.query_one("SELECT embedding FROM brain_memory WHERE id = ? AND embed_model = ?", (memory_id, model))
        return row["embedding"] if row else None

    def embedded_count(self, project_id: str, model: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM brain_memory WHERE project_id = ? AND superseded_by IS NULL AND embedding IS NOT NULL AND embed_model = ?",
            (project_id, model),
        )
        return int(row["n"]) if row else 0

    def used(self, ids: Iterable[int]) -> None:
        for memory_id in ids:
            self.db.execute("UPDATE brain_memory SET uses = uses + 1 WHERE id = ?", (memory_id,))

    # -- graph --------------------------------------------------------------
    def entity(self, project_id: str, kind: str, name: str) -> int:
        self.db.execute("INSERT OR IGNORE INTO brain_entity(project_id, kind, name) VALUES (?, ?, ?)", (project_id, kind, name))
        row = self.db.query_one("SELECT id FROM brain_entity WHERE project_id = ? AND kind = ? AND name = ?", (project_id, kind, name))
        assert row is not None
        return int(row["id"])

    def find_entities(self, project_id: str, kind: str, name: str) -> list[tuple[int, str]]:
        """Entities with this name; a file is also found by its last part ("auth.py" finds "src/auth.py")."""
        if kind == "file":
            rows = self.db.query(
                "SELECT id, name FROM brain_entity WHERE project_id = ? AND kind = 'file' AND (name = ? COLLATE NOCASE OR name LIKE ? ESCAPE '!') LIMIT 8",
                (project_id, name, "%/" + name.replace("!", "!!").replace("%", "!%").replace("_", "!_")),
            )
        else:
            rows = self.db.query(
                "SELECT id, name FROM brain_entity WHERE project_id = ? AND kind = ? AND name = ? COLLATE NOCASE", (project_id, kind, name)
            )
        return [(r["id"], r["name"]) for r in rows]

    def entities(self, project_id: str, kind: str | None = None) -> dict[int, tuple[str, str]]:
        """Entity id -> (kind, name)."""
        if kind:
            rows = self.db.query("SELECT id, kind, name FROM brain_entity WHERE project_id = ? AND kind = ?", (project_id, kind))
        else:
            rows = self.db.query("SELECT id, kind, name FROM brain_entity WHERE project_id = ?", (project_id,))
        return {r["id"]: (r["kind"], r["name"]) for r in rows}

    def link(self, project_id: str, source: str, target: str, relation: str, origin: str = "memory") -> None:
        if source == target:
            return
        self.db.execute(
            "INSERT OR IGNORE INTO brain_edge(project_id, source, target, relation, origin) VALUES (?, ?, ?, ?, ?)",
            (project_id, source, target, relation, origin),
        )

    def unlink_memory(self, memory: Memory, relations: tuple[str, ...]) -> None:
        marks = ", ".join("?" for _ in relations)
        self.db.execute(
            f"DELETE FROM brain_edge WHERE project_id = ? AND source = ? AND relation IN ({marks})",
            (memory.project_id, memory_node(memory.id), *relations),
        )

    def edges_of(self, project_id: str, node: str) -> list[GraphEdge]:
        rows = self.db.query(
            "SELECT source, target, relation, origin FROM brain_edge WHERE project_id = ? AND (source = ? OR target = ?)", (project_id, node, node)
        )
        return [GraphEdge(**r) for r in rows]

    def edges(self, project_id: str, origin: str | None = None) -> list[GraphEdge]:
        if origin:
            rows = self.db.query("SELECT source, target, relation, origin FROM brain_edge WHERE project_id = ? AND origin = ?", (project_id, origin))
        else:
            rows = self.db.query("SELECT source, target, relation, origin FROM brain_edge WHERE project_id = ?", (project_id,))
        return [GraphEdge(**r) for r in rows]

    def memories_about(self, project_id: str, entity_id: int) -> list[int]:
        """Current memories linked to an entity, newest first."""
        rows = self.db.query(
            "SELECT m.id FROM brain_edge e JOIN brain_memory m ON e.source = 'm:' || m.id "
            "WHERE e.project_id = ? AND e.target = ? AND m.superseded_by IS NULL ORDER BY m.updated_at DESC, m.id DESC",
            (project_id, entity_node(entity_id)),
        )
        return [r["id"] for r in rows]

    def replace_code_edges(self, project_id: str, imports: list[tuple[str, str]]) -> None:
        """The import graph read from the files replaces the one read last time."""
        with self.db.transaction():
            self.db.execute("DELETE FROM brain_edge WHERE project_id = ? AND origin = 'codemap'", (project_id,))
            ids: dict[str, int] = {}
            for source, target in imports:
                for name in (source, target):
                    if name not in ids:
                        ids[name] = self.entity(project_id, "file", name)
                self.link(project_id, entity_node(ids[source]), entity_node(ids[target]), "imports", "codemap")
            # A file nothing points at any more is not part of the graph.
            self.db.execute(
                "DELETE FROM brain_entity WHERE project_id = ? AND kind = 'file' AND NOT EXISTS ("
                "SELECT 1 FROM brain_edge e WHERE e.project_id = brain_entity.project_id "
                "AND (e.source = 'e:' || brain_entity.id OR e.target = 'e:' || brain_entity.id))",
                (project_id,),
            )

    def graph(self, project_id: str, limit: int = 160) -> Graph:
        """The knowledge graph, cut to its most connected nodes when it is larger than ``limit``."""
        edges = [e for e in self.edges(project_id) if e.relation != "supersedes"]
        degree: dict[str, int] = {}
        for edge in edges:
            degree[edge.source] = degree.get(edge.source, 0) + 1
            degree[edge.target] = degree.get(edge.target, 0) + 1
        entities = self.entities(project_id)
        current = {m.id: m for m in self.memories(project_id, limit=5000)}
        nodes: dict[str, GraphNode] = {}
        for memory in current.values():
            node = memory_node(memory.id)
            nodes[node] = GraphNode(id=node, type="memory", kind=memory.kind, label=memory.title, degree=degree.get(node, 0))
        for entity_id, (kind, name) in entities.items():
            node = entity_node(entity_id)
            if node in degree:
                nodes[node] = GraphNode(id=node, type="entity", kind=kind, label=name, degree=degree[node])
        edges = [e for e in edges if e.source in nodes and e.target in nodes]
        total_nodes, total_edges = len(nodes), len(edges)
        if len(nodes) > limit:
            # Memories first: they are what the graph is for. Then the files most things point at.
            ranked = sorted(nodes.values(), key=lambda n: (n.type != "memory", -n.degree, n.label))
            kept = {n.id for n in ranked[:limit]}
            nodes = {k: v for k, v in nodes.items() if k in kept}
            edges = [e for e in edges if e.source in kept and e.target in kept]
        return Graph(nodes=list(nodes.values()), edges=edges, total_nodes=total_nodes, total_edges=total_edges)
