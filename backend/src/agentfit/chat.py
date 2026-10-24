"""Chat: one model, one conversation, the same tools and the same place of work as a team.

A chat is not a second engine. Each message is a run of a one-agent workflow that is
built on the spot for the model the user chose, so it gets everything a team run has:
the model gateway, tools confined to the workspace, approvals or ignored permissions,
live text, Stop, and a request that waits while the previous one works. What makes it
a conversation is that the agent answers in its own words (the ``Text`` output) and
that every message is given the turns that came before.

A chat is stored as one entry in the key-value table; its turns are the runs whose
project is ``chat:<id>``, so nothing new had to be added to the database.

A session of a team has a chat of its own for the questions asked about its work once it
has ended ("how do I open the site?"): one model answers, in the place the team worked,
knowing what the team was asked and delivered. The team does not run again for that. These
chats are shown under their session, not in the list of chats.
"""

from __future__ import annotations

import contextlib
import shutil
import uuid
from typing import Any

from agentfit import paths
from agentfit.db import Database, utcnow
from agentfit.workflows import schemas
from agentfit.workflows.dsl import END, START, AgentDef, EdgeDef, ModelSlot, WorkflowDef, WorkflowMeta
from agentfit.workflows.runner import TERMINAL, RunManager, RunRecord

# The id every chat run carries as its workflow. Runs with it stay out of the list of sessions.
CHAT_WORKFLOW = "chat"
NAMESPACE = "chat"
# What one model working alone may use. Anything risky still asks, unless permissions are ignored.
TOOLS = ("list_dir", "read_file", "write_file", "delete_file", "run_python", "run_tests", "git", "powershell")
# Characters of earlier turns given to the model with a new message. Older turns go first.
HISTORY_CHARS = 24_000

PROMPT = """## How you work
- You are talking with one person. Answer what they ask, in their language, as briefly as the question allows.
- A question that needs no tool gets a direct answer.
- When the request is about files or code, work in the workspace with your tools: look before you change, write real files with write_file, and run what you wrote when that settles a doubt.
- Say plainly what you did and what you did not check. Never invent file contents, command output or results."""


class ChatError(Exception):
    pass


def session_chat_id(run_id: str) -> str:
    """The id of the chat that holds the questions about one session's work."""
    return f"q{run_id}"


def answering_model(definition: WorkflowDef | None, installed: list[str]) -> str | None:
    """The model that answers questions about a team's work: the one that wrote its files, when it is installed."""
    if definition is not None:
        writers = [a for a in definition.agents if "write_file" in a.tools]
        for agent in [*writers, *definition.agents]:
            slot = definition.models.get(agent.model)
            if slot is not None and slot.model_key in installed:
                return slot.model_key
    return installed[0] if installed else None


def session_background(record: RunRecord, files: list[str]) -> str:
    """What the model answering questions about a session is told about the session."""
    final = (record.result or {}).get("final")
    delivered = ""
    if isinstance(final, dict):
        for key in ("summary", "answer", "text", "markdown", "objective"):
            if isinstance(final.get(key), str) and final[key].strip():
                delivered = final[key].strip()
                break
    lines = [
        "## The work these questions are about",
        f"A team of agents was asked: {record.task.strip()[:800]}",
        f"It ended: {record.status.replace('_', ' ')}." + (f" {record.error}" if record.error and record.status != "completed" else ""),
    ]
    if delivered:
        lines.append(f"It reported:\n{delivered[:2400]}")
    if files:
        lines.append("Files it wrote, in the workspace you are in: " + ", ".join(files[:40]))
    lines.append(
        "You are not that team. Answer what you are asked about this work; look at the files before you say "
        "what is in them. Change files only when you are asked to."
    )
    return "\n".join(lines)


def definition(model_key: str) -> WorkflowDef:
    """The one-agent workflow a chat message runs as."""
    return WorkflowDef(
        workflow=WorkflowMeta(id=CHAT_WORKFLOW, name="Chat", description="One model in conversation.", max_steps=4, category="chat"),
        models={"chat": ModelSlot(label="Chat", model_key=model_key, auto_tune=True)},
        agents=[
            AgentDef(
                id="assistant", role="Assistant", goal="Help the person you are talking with, one message at a time.",
                model="chat", system_prompt=PROMPT, tools=list(TOOLS), output_schema=schemas.TEXT, max_tool_rounds=40,
            )
        ],
        edges=[EdgeDef(source=START, target="assistant"), EdgeDef(source="assistant", target=END)],
    )


def _answer(record: RunRecord) -> str:
    final = (record.result or {}).get("final")
    return str(final.get("text") or "") if isinstance(final, dict) else ""


class ChatService:
    # Where the chats are kept (key-value namespace, run project prefix, workspace folder prefix).
    namespace = NAMESPACE

    def __init__(self, db: Database, runs: RunManager) -> None:
        self.db = db
        self.runs = runs

    def definition(self, model_key: str, entry: dict[str, Any]) -> WorkflowDef:
        """The workflow one message of this conversation runs as."""
        return definition(model_key)

    # -- storage ------------------------------------------------------------
    def _project(self, chat_id: str) -> str:
        return f"{self.namespace}:{chat_id}"

    def _runs(self, chat_id: str) -> list[RunRecord]:
        rows = self.db.query("SELECT id FROM workflow_run WHERE project = ? ORDER BY started_at, rowid", (self._project(chat_id),))
        return [self.runs.get(r["id"]) for r in rows]

    def _entry(self, chat_id: str) -> dict[str, Any]:
        entry = self.db.kv_get(self.namespace, chat_id)
        if not isinstance(entry, dict):
            raise ChatError("Chat not found")
        return entry

    def list(self) -> list[dict[str, Any]]:
        """Every chat, the one written to last first, with whether it is answering right now."""
        chats = []
        for entry in self.db.kv_list(self.namespace).values():
            if entry.get("session"):
                continue  # questions about a session are shown with the session
            turns = self._runs(entry["id"])
            chats.append({**entry, "turns": len(turns), "active": any(t.status not in TERMINAL for t in turns)})
        return sorted(chats, key=lambda c: c.get("updated_at") or "", reverse=True)

    def get(self, chat_id: str) -> dict[str, Any]:
        """A chat with its turns: what was asked, what was answered and what was done to answer."""
        entry = self._entry(chat_id)
        turns = []
        for record in self._runs(chat_id):
            events = [
                e for e in self.runs.events(record.id)
                if e["type"] in ("tool.call", "node.retry", "node.failed", "memory.recalled", "memory.saved")
                or (e["type"] == "model.switch" and e["payload"].get("phase") in ("ready", "failed", "spilling") and not e["payload"].get("already_loaded"))
            ]
            turns.append({
                "id": record.id, "message": record.task, "status": record.status, "error": record.error, "answer": _answer(record),
                "started_at": record.started_at, "finished_at": record.finished_at, "events": events,
                "pending_approvals": [a.model_dump() for a in record.pending_approvals], "queued": record.queued,
            })
        return {**entry, "turns": turns, "active": any(t["status"] not in TERMINAL for t in turns)}

    def active_run(self, chat_id: str) -> str | None:
        return next((t.id for t in reversed(self._runs(chat_id)) if t.status not in TERMINAL), None)

    def create(self, message: str, model_key: str) -> dict[str, Any]:
        chat_id = uuid.uuid4().hex[:12]
        now = utcnow()
        entry = {"id": chat_id, "title": " ".join(message.split())[:60], "model_key": model_key, "source": {"kind": "none"}, "created_at": now, "updated_at": now}
        self.db.kv_set(self.namespace, chat_id, entry)
        return entry

    def for_session(self, run_id: str, title: str, model_key: str, source: dict[str, Any]) -> dict[str, Any]:
        """The chat of the questions about a session, made the first time one is asked."""
        chat_id = session_chat_id(run_id)
        entry = self.db.kv_get(self.namespace, chat_id)
        if isinstance(entry, dict):
            return entry
        now = utcnow()
        entry = {"id": chat_id, "title": title, "model_key": model_key, "source": source, "session": run_id, "created_at": now, "updated_at": now}
        self.db.kv_set(self.namespace, chat_id, entry)
        return entry

    def of_session(self, run_id: str) -> dict[str, Any] | None:
        """The questions asked about a session, or None when there are none."""
        try:
            return self.get(session_chat_id(run_id))
        except ChatError:
            return None

    def delete(self, chat_id: str) -> None:
        self._entry(chat_id)
        turns = self._runs(chat_id)
        if any(t.status not in TERMINAL for t in turns):
            raise ChatError("Stop the answer before deleting the chat.")
        for turn in turns:
            self.runs.delete(turn.id)
        self.db.kv_delete(self.namespace, chat_id)
        # Its own workspace goes with it. A folder or repository of the user's is never touched.
        with contextlib.suppress(OSError):
            shutil.rmtree(self.workspace(chat_id), ignore_errors=True)

    # -- a turn -------------------------------------------------------------
    def workspace(self, chat_id: str) -> str:
        """Where a chat with no folder of the user's works: one place for all its turns, so files outlive a message."""
        return str(paths.workspaces_dir() / f"{self.namespace}-{chat_id}")

    def history(self, chat_id: str, limit: int = HISTORY_CHARS) -> str | None:
        """The turns so far, as the model is told them. The oldest are left out when they do not fit."""
        lines: list[str] = []
        for turn in self._runs(chat_id):
            answer = _answer(turn) or (f"[no answer: {turn.error}]" if turn.error else "[no answer]")
            lines.append(f"The person: {turn.task.strip()}\nYou: {answer.strip()}")
        if not lines:
            return None
        kept: list[str] = []
        room = limit
        for line in reversed(lines):
            if kept and len(line) > room:
                break
            kept.append(line[-room:] if len(line) > room else line)
            room -= len(line)
        dropped = len(lines) - len(kept)
        head = "## The conversation so far\n" + (f"[{dropped} earlier turns are not shown.]\n\n" if dropped else "")
        return head + "\n\n".join(reversed(kept)) + "\n\nThe message above this section is the new one: answer it."

    def send(
        self, chat_id: str, message: str, *, model_key: str, workspace: str | None, protect_existing: bool,
        source: dict[str, Any], approval: str, background: str | None = None, read_roots: list[str] | None = None,
    ) -> RunRecord:
        """Start the turn that answers ``message``. Called when nothing else of this chat is working.

        ``background`` is what the model is told before the conversation itself: for the questions
        about a session, what the team was asked and delivered.
        """
        entry = self._entry(chat_id)
        context = "\n\n".join(part for part in (background, self.history(chat_id)) if part) or None
        place = workspace or self.workspace(chat_id)
        record = self.runs.start(
            self.definition(model_key, entry), message, title=entry["title"], project=self._project(chat_id),
            workspace=place, protect_existing=protect_existing, source=source,
            context=context, approval=approval, keep_models=True, read_roots=read_roots,
        )
        # Where the last message wrote, so the interface can say it and open it.
        self.db.kv_set(self.namespace, chat_id, {**entry, "model_key": model_key, "source": source, "approval": approval,
                                                 "workspace": place, "updated_at": utcnow()})
        return record
