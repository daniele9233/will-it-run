"""The Project Brain: the permanent memory of each project, and how a run uses it.

A project is where the work happens: a folder of the user's or a GitHub repository. Its
brain holds how the project is built, what was decided, which files were changed and why,
what went wrong and what fixed it. It outlives every run and every chat.

``BrainService`` is the whole brain: projects, memories, search, the graph.
``BrainSession`` is one run's view of one project: it gives an agent the few memories
its task needs, serves the ``recall`` and ``remember`` tools, watches what the run does
and writes it down when the run ends.

Design notes (see docs/MEMORY.md): typed observations captured from what the agents do
and given back a little at a time are the approach of claude-mem; one store searched by
vectors and by a graph at once, where a new fact replaces the old one instead of piling
up, is mem0's; keeping what was replaced, marked as replaced, instead of deleting it, is
Graphiti's. None of their code is used: the brain is tables in Will It Run's own SQLite
file, and its vectors come from LM Studio.
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentfit.brain import codemap, text
from agentfit.brain.capture import Recorder
from agentfit.brain.embed import Embedder, NoEmbedder
from agentfit.brain.models import (
    KINDS,
    WRITABLE_KINDS,
    EmbedStatus,
    Graph,
    Hit,
    Memory,
    Project,
    SearchResult,
)
from agentfit.brain.search import Searcher
from agentfit.brain.store import BrainStore, memory_node
from agentfit.db import Database, utcnow
from agentfit.logging_setup import get_logger, redact_text

log = get_logger("brain")

EmitFn = Callable[[str, dict[str, Any]], None]

RECALL = "recall"
REMEMBER = "remember"
MAX_TITLE = 200
MAX_BODY = 8000
# Kinds that state something about the project. A new one that says the same as an old one replaces it.
KNOWLEDGE_KINDS = ("decision", "architecture", "note")
# Two statements this close are the same statement in other words. Set high on purpose: telling a
# fact that changed ("port 9000, not 8000") from a different fact that reads alike takes judgement,
# and a wrong merge hides a memory from search, so anything less alike stays a separate entry.
DUPLICATE_SIMILARITY = 0.97
# What an agent is handed without asking: never more than this, and never more than this share of its context.
MAX_CONTEXT_CHARS = 6000
CONTEXT_SHARE = 0.08
CHARS_PER_TOKEN = 3.5
# Tokens added to what an agent asks its model for, so the memory has room.
CONTEXT_ALLOWANCE = 1500
CODEMAP_SLOT = "codemap"
_CODEMAP_KV = "brain_codemap"

GUIDE = (
    "## Project memory\n"
    "This project has a permanent memory that outlives this session: its architecture, the decisions taken, the files "
    "changed and why, the errors met and what fixed them.\n"
    "- recall searches it. Use it before you change a file you did not write, and when you meet an error: it may have "
    "been solved before.\n"
    "- remember saves one fact for later sessions: a decision with its reason, how a part works, an error with its "
    "fix. Save what someone starting tomorrow would need, not a log of your steps.\n"
    "The memory can be out of date. When a file says otherwise, the file is right."
)


class BrainError(Exception):
    pass


def project_identity(source: dict[str, Any] | None, workspace: str | Path | None) -> tuple[str, str, str, str | None] | None:
    """(key, kind, name, folder) of the project a run works on. None when it works on no project.

    A run that was given neither a folder nor a repository works in a workspace made for
    it and thrown away: there is no project to remember.
    """
    source = source or {}
    kind = source.get("kind")
    if kind == "github" and source.get("url"):
        name = str(source["url"]).strip().removeprefix("https://github.com/").removesuffix("/").removesuffix(".git")
        return f"github:{name.lower()}", "github", name, str(workspace) if workspace else None
    if kind == "folder" and workspace:
        folder = Path(workspace).resolve()
        return f"folder:{os.path.normcase(str(folder))}", "folder", folder.name or str(folder), str(folder)
    return None


def _day(stamp: str) -> str:
    return stamp[:10]


def _cut(body: str, limit: int) -> tuple[str, bool]:
    body = body.strip()
    if len(body) <= limit:
        return body, False
    return body[:limit].rstrip() + " …", True


class BrainService:
    def __init__(self, db: Database, embedder: Embedder | None = None, *, enabled: Callable[[], bool] = lambda: True) -> None:
        self.db = db
        self.store = BrainStore(db)
        self.embedder: Embedder = embedder or NoEmbedder()
        self.searcher = Searcher(self.store, self.embedder)
        self._enabled = enabled
        self._lock = threading.RLock()

    def enabled(self) -> bool:
        return bool(self._enabled())

    def embed_status(self) -> EmbedStatus:
        try:
            return self.embedder.status()
        except Exception as exc:
            return EmbedStatus(reason=f"The embedding model could not be checked ({exc}).", code="failed")

    # -- projects -----------------------------------------------------------
    def projects(self) -> list[Project]:
        return self.store.projects()

    def project(self, project_id: str) -> Project:
        project = self.store.project(project_id)
        if project is None:
            raise BrainError("This project has no memory.")
        return project

    def lookup(self, source: dict[str, Any] | None, workspace: str | Path | None) -> Project | None:
        """The brain of the project a run would work on, if it has one already."""
        identity = project_identity(source, workspace)
        return self.store.project_by_key(identity[0]) if identity else None

    def ensure_project(self, source: dict[str, Any] | None, workspace: str | Path | None) -> Project | None:
        identity = project_identity(source, workspace)
        return self.store.upsert_project(*identity) if identity else None

    def forget(self, project_id: str) -> None:
        self.project(project_id)
        self.store.delete_project(project_id)

    def overview(self, project_id: str) -> dict[str, Any]:
        """A project with what the interface says about its memory."""
        project = self.project(project_id)
        status = self.embed_status()
        return {
            "project": project.model_dump(),
            "semantic": status.model_dump(),
            "embedded": self.store.embedded_count(project_id, status.model) if status.model else 0,
            "codemap": self.db.kv_get(_CODEMAP_KV, project_id),
            "keyword_index": self.store.fts,
        }

    # -- memories -----------------------------------------------------------
    def get(self, memory_id: int) -> Memory:
        memory = self.store.get(memory_id, self.embed_status().model)
        if memory is None:
            raise BrainError(f"There is no memory #{memory_id}.")
        return memory

    def memories(self, project_id: str, *, kind: str | None = None, include_superseded: bool = False, limit: int = 300) -> list[Memory]:
        return self.store.memories(
            project_id, kind=kind, include_superseded=include_superseded, limit=limit, model=self.embed_status().model
        )

    def _link(self, memory: Memory, fixes: int | None, session: int | None) -> None:
        node = memory_node(memory.id)
        relation = "touches" if memory.kind == "change" else "about"
        for name in memory.files:
            self.store.link(memory.project_id, node, f"e:{self.store.entity(memory.project_id, 'file', name)}", relation)
        for tag in memory.tags:
            if tag != "open":  # a state, not something the memory is about
                self.store.link(memory.project_id, node, f"e:{self.store.entity(memory.project_id, 'concept', tag.lower())}", "about")
        if fixes is not None and fixes != memory.id:
            self.store.link(memory.project_id, memory_node(fixes), node, "fixed_by")
        if session is not None and session != memory.id:
            self.store.link(memory.project_id, node, memory_node(session), "from_session")

    def _replace(self, old: Memory, new: Memory) -> None:
        """``new`` says what ``old`` said, as of now. The old one is kept, marked as replaced."""
        for edge in self.store.edges_of(old.project_id, memory_node(old.id)):
            if edge.relation == "fixed_by":
                source = memory_node(new.id) if edge.source == memory_node(old.id) else edge.source
                target = memory_node(new.id) if edge.target == memory_node(old.id) else edge.target
                self.store.link(old.project_id, source, target, "fixed_by")
        self.store.supersede(old.id, new.id)

    def add(
        self, project_id: str, *, kind: str, title: str, body: str, files: list[str] | None = None, tags: list[str] | None = None,
        source: str = "user", agent: str = "", run_id: str | None = None, slot: str | None = None, fixes: int | None = None,
        session: int | None = None, embed: bool = True,
    ) -> tuple[Memory, str]:
        """Write a memory. Returns it with what happened: "added", "known" or "updated".

        Nothing is stored twice. The same text again only refreshes its date. A statement
        about the project (a decision, how a part works, a note) with the title of an older
        one, or that is the older one again in nearly the same words, replaces it; the
        older one is kept and marked as replaced.
        """
        if kind not in KINDS:
            raise BrainError(f"A memory is one of: {', '.join(KINDS)}.")
        title = redact_text(" ".join((title or "").split()))[:MAX_TITLE]
        body = redact_text((body or "").strip())[:MAX_BODY]
        if not title:
            raise BrainError("A memory needs a title.")
        files = list(dict.fromkeys(p for p in (text.clean_path(str(f)) for f in files or []) if p))[:60]
        tags = list(dict.fromkeys(" ".join(str(t).split())[:40] for t in tags or [] if str(t).strip()))[:12]
        with self._lock:
            self.project(project_id)
            if fixes is not None:
                target = self.store.get(fixes)
                if target is None or target.project_id != project_id or target.kind != "error":
                    raise BrainError(f"#{fixes} is not an error recorded for this project.")
            if slot:
                held = self.store.by_slot(project_id, slot)
                if held is not None:
                    if text.content_hash(kind, title, body) == text.content_hash(held.kind, held.title, held.body):
                        self.store.seen_again(held.id)
                        return held, "known"
                    self.store.unlink_memory(held, ("about", "touches"))
                    memory = self.store.rewrite(held.id, kind=kind, title=title, body=body, files=files, tags=tags)
                    self._link(memory, None, None)
                    return memory, "updated"
            known = self.store.by_hash(project_id, text.content_hash(kind, title, body))
            if known is not None:
                self.store.seen_again(known.id)
                self._link(known.model_copy(update={"files": files or known.files, "tags": tags or known.tags}), fixes, session)
                if fixes is not None:
                    self._mark_fixed(fixes)
                return known, "known"
            # Looked up before the insert, or the new memory would find itself.
            same_title = self.store.by_title(project_id, kind, title) if not slot and kind in (*KNOWLEDGE_KINDS, "error") else None
            memory = self.store.insert(
                project_id, kind=kind, title=title, body=body, files=files, tags=tags, source=source, agent=agent, run_id=run_id, slot=slot,
            )
            self._link(memory, fixes, session)
            if fixes is not None:
                self._mark_fixed(fixes)
            state = "added"
            if same_title is not None:
                self._replace(same_title, memory)
                state = "updated"
            elif embed and kind in KNOWLEDGE_KINDS:
                self.searcher.ensure_vectors(project_id, 16)
                closest = self.searcher.closest(project_id, memory, kind)
                if closest is not None and closest[1] >= DUPLICATE_SIMILARITY:
                    older = self.store.get(closest[0])
                    if older is not None and older.slot is None:
                        self._replace(older, memory)
                        state = "updated"
            return memory, state

    def _mark_fixed(self, error_id: int) -> None:
        error = self.store.get(error_id)
        if error is not None and "open" in error.tags:
            self.db.execute(
                "UPDATE brain_memory SET tags = ?, updated_at = ? WHERE id = ?",
                (json.dumps([t for t in error.tags if t != "open"]), utcnow(), error_id),
            )

    def edit(
        self, memory_id: int, *, kind: str | None = None, title: str | None = None, body: str | None = None,
        files: list[str] | None = None, tags: list[str] | None = None,
    ) -> Memory:
        with self._lock:
            memory = self.get(memory_id)
            if memory.slot:
                raise BrainError("This entry is rewritten from the files of the project and cannot be edited by hand.")
            new_kind = kind or memory.kind
            if new_kind not in KINDS:
                raise BrainError(f"A memory is one of: {', '.join(KINDS)}.")
            new_title = redact_text(" ".join((title if title is not None else memory.title).split()))[:MAX_TITLE]
            if not new_title:
                raise BrainError("A memory needs a title.")
            new_files = memory.files if files is None else list(dict.fromkeys(p for p in (text.clean_path(f) for f in files) if p))[:60]
            new_tags = memory.tags if tags is None else list(dict.fromkeys(t.strip()[:40] for t in tags if t.strip()))[:12]
            self.store.unlink_memory(memory, ("about", "touches"))
            updated = self.store.rewrite(
                memory_id, kind=new_kind, title=new_title, body=redact_text((body if body is not None else memory.body).strip())[:MAX_BODY],
                files=new_files, tags=new_tags,
            )
            self._link(updated, None, None)
            return updated

    def delete(self, memory_id: int) -> None:
        with self._lock:
            self.get(memory_id)
            self.store.delete(memory_id)

    def links(self, memory: Memory) -> list[dict[str, Any]]:
        """What a memory is connected to in the graph, as rows the interface and the tools can print."""
        node = memory_node(memory.id)
        entities = self.store.entities(memory.project_id)
        rows: list[dict[str, Any]] = []
        for edge in self.store.edges_of(memory.project_id, node):
            outgoing = edge.source == node
            other = edge.target if outgoing else edge.source
            if other.startswith("e:"):
                kind, name = entities.get(int(other[2:]), ("", ""))
                rows.append({"relation": edge.relation, "outgoing": outgoing, "type": "entity", "kind": kind, "label": name, "id": other})
            else:
                linked = self.store.get(int(other[2:]))
                if linked is not None:
                    rows.append({
                        "relation": edge.relation, "outgoing": outgoing, "type": "memory", "kind": linked.kind, "label": linked.title,
                        "id": other, "memory_id": linked.id,
                    })
        return rows

    def search(self, project_id: str, query: str, *, kinds: list[str] | None = None, limit: int = 8, strict: bool = False) -> SearchResult:
        self.project(project_id)
        return self.searcher.search(project_id, query, kinds=kinds, limit=limit, strict=strict)

    def graph(self, project_id: str, limit: int = 160) -> Graph:
        self.project(project_id)
        return self.store.graph(project_id, limit)

    def reindex(self, project_id: str) -> int:
        """Embed every memory that has no vector for the model in use. Returns how many were embedded."""
        self.project(project_id)
        done = 0
        while True:
            batch = self.searcher.ensure_vectors(project_id, 64)
            done += batch
            if batch < 64:
                return done

    # -- the project map ----------------------------------------------------
    def map_project(self, project: Project, root: Path) -> dict[str, Any] | None:
        """Read the files under ``root`` again if they changed since the map was made."""
        if not root.is_dir():
            return None
        known = self.db.kv_get(_CODEMAP_KV, project.id) or {}
        signature = codemap.signature_of(root)
        if known.get("signature") == signature:
            return known
        code = codemap.scan(root)
        self.store.replace_code_edges(project.id, code.imports)
        if code.files:
            self.add(
                project.id, kind="architecture", title="Project map", body=codemap.overview(code, project.name, utcnow()[:10]),
                source="engine", slot=CODEMAP_SLOT, embed=False,
            )
        info = {
            "signature": signature, "files": len(code.files), "imports": len(code.imports), "truncated": code.truncated,
            "scanned_at": utcnow(), "root": str(root),
        }
        self.db.kv_set(_CODEMAP_KV, project.id, info)
        return info

    # -- runs ---------------------------------------------------------------
    def open(self, run_id: str, definition: Any, source: dict[str, Any] | None, workspace: Path, task: str) -> BrainSession | None:
        """A run's view of its project's brain. None when the run has no project, or must not have a memory.

        A verification workflow answers from the evidence it is given and nothing else:
        what an earlier run concluded must not reach it, and it leaves nothing behind.
        """
        if not self.enabled():
            return None
        verification = definition.workflow.category in ("verification", "clone") or any(c.kind == "evidence_dossier" for c in definition.checks)
        if verification:
            return None
        project = self.ensure_project(source, workspace)
        if project is None:
            return None
        return BrainSession(self, project, run_id, definition, workspace, task)


def render_entry(memory: Memory, limit: int, links: list[dict[str, Any]] | None = None, hit: Hit | None = None) -> str:
    """One memory as an agent reads it."""
    who = f", by {memory.agent}" if memory.agent else ""
    state = ""
    if memory.kind == "error":
        fix = next((row for row in links or [] if row["relation"] == "fixed_by" and row["outgoing"]), None)
        if fix and "open" in memory.tags:
            state = f", open again; it was fixed before by #{fix['memory_id']}"
        else:
            state = f", fixed by #{fix['memory_id']}" if fix else (", still open" if "open" in memory.tags else "")
    elif memory.kind == "solution":
        target = next((row for row in links or [] if row["relation"] == "fixed_by" and not row["outgoing"]), None)
        state = f", fixes #{target['memory_id']}" if target else ""
    how = ""
    if hit is not None:
        reasons = []
        if hit.semantic is not None:
            reasons.append(f"meaning {hit.semantic:.2f}")
        if hit.keyword:
            reasons.append("words")
        reasons += hit.via
        how = f" [{'; '.join(reasons)}]" if reasons else ""
    body, cut = _cut(memory.body, limit)
    lines = [f"#{memory.id} {memory.kind}, {_day(memory.updated_at)}{who}{state}{how}", memory.title]
    if body and body != memory.title:
        lines.append(body)
    if cut:
        lines.append(f"(recall with id={memory.id} for all of it)")
    if memory.files:
        shown = ", ".join(memory.files[:8]) + (f" and {len(memory.files) - 8} more" if len(memory.files) > 8 else "")
        lines.append(f"files: {shown}")
    return "\n".join(lines)


_STR = {"type": "string"}
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    RECALL: {
        "type": "function",
        "function": {
            "name": RECALL,
            "description": (
                "Search this project's permanent memory: its architecture, decisions, file changes, errors and their fixes "
                "from earlier sessions. Give query to search in your own words, file for everything known about one file, "
                "or id to read one entry in full."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {**_STR, "description": "What you want to know, in your own words"},
                    "file": {**_STR, "description": "A file path: what imports it, what it imports, and every memory about it"},
                    "id": {"type": "integer", "description": "Number of one entry, to read it whole"},
                    "kind": {**_STR, "enum": list(KINDS), "description": "Only entries of this kind"},
                },
                "required": [],
            },
        },
    },
    REMEMBER: {
        "type": "function",
        "function": {
            "name": REMEMBER,
            "description": (
                "Save one fact in this project's permanent memory, for later sessions: a decision and its reason, how a "
                "part of the architecture works, an error and what fixed it. One fact per call, written so that someone "
                "who was not here understands it. Not for progress notes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {**_STR, "enum": list(WRITABLE_KINDS)},
                    "title": {**_STR, "description": "The fact in one line"},
                    "text": {**_STR, "description": "The fact in full, with the reason"},
                    "files": {"type": "array", "items": _STR, "description": "Paths of the files it is about"},
                    "fixes": {"type": "integer", "description": "For a solution: the number of the error entry it fixes"},
                },
                "required": ["kind", "title", "text"],
            },
        },
    },
}
TOOL_LABELS = {RECALL: "Recall", REMEMBER: "Remember"}


def how_searched(service: BrainService, result: SearchResult) -> str:
    if result.semantic:
        return "Searched by meaning, by words and through the graph."
    why = getattr(service.embedder, "last_skip", "") or service.embed_status().reason
    return f"Searched by words and through the graph only. {why}".strip()


def recall_text(service: BrainService, project: Project, args: dict[str, Any]) -> str:
    """What the ``recall`` tool answers: to an agent of a run, to a coding agent over MCP, to a person at the command line."""
    store = service.store
    raw_id = args.get("id")
    if raw_id not in (None, "", 0):
        try:
            memory = store.get(int(raw_id))
        except (TypeError, ValueError):
            memory = None
        if memory is None or memory.project_id != project.id:
            return f"ERROR: there is no entry #{raw_id} in this project's memory."
        store.used([memory.id])
        links = service.links(memory)
        lines = [render_entry(memory, 6000, links)]
        related = [f"- {row['relation'].replace('_', ' ')} #{row['memory_id']} ({row['kind']}): {row['label']}" for row in links if row["type"] == "memory"]
        if related:
            lines.append("Linked entries:\n" + "\n".join(related[:12]))
        if memory.superseded_by:
            lines.append(f"This entry was replaced by #{memory.superseded_by}: read that one.")
        return "\n".join(lines)
    path = str(args.get("file") or "").strip()
    if path:
        card = service.searcher.about_file(project.id, path)
        if card is None:
            return f"Nothing is recorded about '{path}': no memory mentions it and the project map has no imports for it."
        lines = [f"{card['file']}"]
        if card["imports"]:
            lines.append("imports: " + ", ".join(card["imports"][:20]))  # type: ignore[index]
        if card["imported_by"]:
            lines.append("imported by: " + ", ".join(card["imported_by"][:20]))  # type: ignore[index]
        memories: list[Memory] = card["memories"]  # type: ignore[assignment]
        if not memories:
            lines.append("No memory is about this file.")
        for memory in memories[:6]:
            lines.append("")
            lines.append(render_entry(memory, 400, service.links(memory)))
        if len(memories) > 6:
            lines.append(f"\n{len(memories) - 6} older entries are about this file too.")
        if card["others"]:
            lines.append("\nOther files with this name: " + ", ".join(card["others"]))  # type: ignore[arg-type]
        store.used(m.id for m in memories[:6])
        return "\n".join(lines)
    query = str(args.get("query") or "").strip()
    if not query:
        return "ERROR: give query (what you want to know), file (a path) or id (the number of an entry)."
    kind = str(args.get("kind") or "").strip()
    result = service.searcher.search(project.id, query, kinds=[kind] if kind in KINDS else None, limit=6)
    if not result.hits:
        return f"Nothing matches among the {result.total} entries of this project's memory. {how_searched(service, result)}"
    store.used(hit.memory.id for hit in result.hits)
    blocks = [render_entry(hit.memory, 500, service.links(hit.memory), hit) for hit in result.hits]
    return f"{len(result.hits)} of {result.total} entries. {how_searched(service, result)}\n\n" + "\n\n".join(blocks)


def remember_text(service: BrainService, project: Project, args: dict[str, Any], *, agent: str, run_id: str | None) -> tuple[str, int | None]:
    """What the ``remember`` tool does and answers. Returns the words for the agent and the id of the entry, when there is one."""
    kind = str(args.get("kind") or "note").strip().lower()
    if kind not in WRITABLE_KINDS:
        return f"ERROR: kind must be one of: {', '.join(WRITABLE_KINDS)}.", None
    title = str(args.get("title") or "").strip()
    body = str(args.get("text") or args.get("body") or "").strip()
    if not title or not body:
        return "ERROR: give a title (the fact in one line) and text (the fact in full, with the reason).", None
    files = args.get("files") or []
    if isinstance(files, str):
        files = [part for part in files.replace(";", ",").split(",") if part.strip()]
    fixes: int | None = None
    if args.get("fixes") not in (None, "", 0):
        try:
            fixes = int(args["fixes"])
        except (TypeError, ValueError):
            return "ERROR: fixes is the number of an error entry, as recall shows it.", None
    try:
        memory, state = service.add(
            project.id, kind=kind, title=title, body=body, files=[str(f) for f in files], source="agent",
            agent=agent, run_id=run_id, fixes=fixes,
        )
    except BrainError as exc:
        return f"ERROR: {exc}", None
    if state == "known":
        return f"Already in the project's memory as #{memory.id}. Nothing was added.", memory.id
    if state == "updated":
        return f"Saved as #{memory.id}. It replaces an older entry that said the same.", memory.id
    return f"Saved as #{memory.id}.", memory.id


class BrainSession:
    def __init__(self, service: BrainService, project: Project, run_id: str, definition: Any, workspace: Path, task: str) -> None:
        self.service = service
        self.project = project
        self.run_id = run_id
        self.workspace = workspace
        self.task = task
        self.emit: EmitFn | None = None
        self.recorder = Recorder(
            task=task, workflow_name=definition.workflow.name,
            roles={a.id: a.role for a in definition.agents}, schemas={a.id: a.output_schema for a in definition.agents},
            conversation=definition.workflow.category == "chat",
        )
        # Memories agents wrote during this run. They are tied to the session when it is recorded.
        self._written: list[int] = []
        self._lock = threading.Lock()

    def describe(self) -> dict[str, Any]:
        """What the run's first event says about its memory."""
        project = self.service.store.project(self.project.id) or self.project
        return {"project": project.id, "name": project.name, "kind": project.kind, "memories": project.memories}

    # -- which agents -------------------------------------------------------
    def applies_to(self, agent: Any) -> bool:
        """An agent with a reading list is shown what the list says and nothing else."""
        return agent.reads is None

    def has_tools(self, agent: Any) -> bool:
        return self.applies_to(agent) and bool(agent.tools)

    def tool_schemas(self, agent: Any) -> list[dict[str, Any]]:
        return list(TOOL_SCHEMAS.values()) if self.has_tools(agent) else []

    @staticmethod
    def is_tool(name: str) -> bool:
        return name in TOOL_SCHEMAS

    # -- what an agent is handed --------------------------------------------
    def _query(self, agent: Any, state: dict[str, Any]) -> str:
        # The request itself, without what the engine appended to it (earlier turns, earlier runs).
        task = str(state.get("task") or self.task).split("\n\n## ", 1)[0]
        parts = [task[:1200], agent.role, (agent.goal or "")[:200]]
        outputs = state.get("outputs") or {}
        last = state.get("last_node")
        if last and last != agent.id and isinstance(outputs.get(last), dict):
            # What the step before reported: when it is a failure, this is what finds the fix of last time.
            report = outputs[last]
            parts.append(str(report.get("summary") or "")[:300])
            for name in ("failures", "errors", "requested_changes", "required_changes", "findings"):
                if report.get(name):
                    parts.append(" ".join(str(item) for item in report[name][:4])[:400])
        return "\n".join(p for p in parts if p)

    def find(self, agent: Any, state: dict[str, Any]) -> SearchResult | None:
        """The memories that match this agent's task. None when it is shown no memory or there is none."""
        if not self.applies_to(agent):
            return None
        project = self.service.store.project(self.project.id)
        if project is None or not project.memories:
            return None
        return self.service.searcher.search(project.id, self._query(agent, state), limit=8, strict=True)

    def context_for(
        self, agent: Any, state: dict[str, Any], context_tokens: int, found: SearchResult | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """The memories this agent's task needs, as a section of its prompt, and what they are.

        Only what matches the task is given, inside a fixed share of the context. The rest
        stays one ``recall`` away. ``found`` is the search already made with ``find``.
        """
        if not self.applies_to(agent):
            return "", []
        budget = min(MAX_CONTEXT_CHARS, int(context_tokens * CHARS_PER_TOKEN * CONTEXT_SHARE))
        project = self.service.store.project(self.project.id)
        if project is None or not project.memories or budget < 600:
            return "", []
        entries: list[str] = []
        items: list[dict[str, Any]] = []
        used = 0
        project_map = self.service.store.by_slot(project.id, CODEMAP_SLOT)
        if project_map is not None and budget >= 2400:
            body, _ = _cut(project_map.body, 800)
            entries.append(f"#{project_map.id} project map\n{body}")
            items.append({"id": project_map.id, "kind": project_map.kind, "title": project_map.title})
            used += len(entries[-1])
        result = found if found is not None else self.find(agent, state)
        for hit in result.hits if result is not None else []:
            if project_map is not None and hit.memory.id == project_map.id:
                continue
            entry = render_entry(hit.memory, 420, self.service.links(hit.memory))
            if used + len(entry) > budget:
                break
            entries.append(entry)
            items.append({"id": hit.memory.id, "kind": hit.memory.kind, "title": hit.memory.title})
            used += len(entry)
        if not items:
            return "", []
        self.service.store.used(item["id"] for item in items)
        more = " recall finds the rest." if self.has_tools(agent) else ""
        head = (
            f"## Project memory\nFrom earlier sessions on {project.name}: the {len(items)} of {project.memories} entries that match this task."
            f"{more} It can be out of date: when a file says otherwise, the file is right."
        )
        return head + "\n\n" + "\n\n".join(entries), items

    # -- tools --------------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any], agent_role: str) -> str:
        if name == RECALL:
            return self._recall(arguments)
        if name == REMEMBER:
            return self._remember(arguments, agent_role)
        raise BrainError(f"Unknown memory tool '{name}'")

    def _recall(self, args: dict[str, Any]) -> str:
        return recall_text(self.service, self.project, args)

    def _remember(self, args: dict[str, Any], agent_role: str) -> str:
        text, memory_id = remember_text(self.service, self.project, args, agent=agent_role, run_id=self.run_id)
        if memory_id is not None:
            with self._lock:
                self._written.append(memory_id)
        return text

    # -- watching and recording ---------------------------------------------
    def scan(self) -> None:
        try:
            self.service.map_project(self.project, self.workspace)
        except Exception:
            log.exception("the project map of %s could not be made", self.project.name)

    def observe(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            self.recorder.observe(event_type, payload)
        except Exception:
            log.exception("a run event could not be recorded for the project memory")

    def close(self, status: str, error: str | None, result: dict[str, Any] | None) -> dict[str, int]:
        """Write down what the run established. Returns how many memories of each kind were new."""
        saved: Counter[str] = Counter()
        keys: dict[str, int] = {}
        session: int | None = None
        for draft in self.recorder.drafts(status, error, result):
            try:
                memory, state = self.service.add(
                    self.project.id, kind=draft.kind, title=draft.title, body=draft.body, files=draft.files, tags=draft.tags,
                    source=draft.source, agent=draft.agent, run_id=self.run_id, fixes=keys.get(draft.fixes), session=session,
                )
            except BrainError as exc:
                log.warning("a memory of run %s was not saved: %s", self.run_id, exc)
                continue
            if draft.key:
                keys[draft.key] = memory.id
            if draft.kind == "session":
                session = memory.id
            if state != "known":
                saved[draft.kind] += 1
        if session is not None:
            for memory_id in self._written:
                self.service.store.link(self.project.id, memory_node(memory_id), memory_node(session), "from_session")
        if self.workspace.is_dir():
            # The files are what the run left them as: the map follows.
            self.scan()
        self.service.searcher.ensure_vectors(self.project.id, 64)
        written = len(self._written)
        if (saved or written) and self.emit is not None:
            project = self.service.store.project(self.project.id) or self.project
            self.emit("memory.saved", {
                "project": project.id, "name": project.name, "saved": dict(saved), "by_agents": written, "total": project.memories,
            })
        return dict(saved)
