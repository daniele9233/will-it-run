"""Run manager: executes compiled workflows, streams events, handles approvals.

Each run executes on its own thread. Events are published on the bus (topic
``run.<id>``) for the live UI and persisted in SQLite so a session can be replayed.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from pydantic import BaseModel, Field

from agentfit import paths
from agentfit.brain import BrainService, BrainSession
from agentfit.db import Database, utcnow
from agentfit.events import EventBus
from agentfit.events import bus as default_bus
from agentfit.logging_setup import get_logger, log_event
from agentfit.runtime import ModelRuntime
from agentfit.workflows import tools
from agentfit.workflows.compiler import (
    AgentFailed,
    RunCancelled,
    RunContext,
    compile_workflow,
    slot_completion,
)
from agentfit.workflows.dsl import WorkflowDef
from agentfit.workflows.model_manager import (
    GatewayOptions,
    MemoryProfileStore,
    ModelManager,
    SpillProbe,
    VramProbe,
)
from agentfit.workflows.validator import validate

log = get_logger("runs")

# "blocked": a deterministic check did not pass and ended the run.
TERMINAL = {"completed", "failed", "rejected", "cancelled", "retry_limit", "no_route", "blocked"}


class RunError(Exception):
    pass


class PendingApproval(BaseModel):
    id: str
    run_id: str
    node_id: str
    kind: str
    prompt: str
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_at: str = ""


class RunRecord(BaseModel):
    id: str
    workflow_id: str
    project: str = "Default"
    title: str
    task: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: str
    finished_at: str | None = None
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
    # The request the user wrote while this run was working. It starts when the run ends.
    queued: str | None = None


class _Live:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.decisions: dict[str, dict[str, Any]] = {}
        self.wake = threading.Event()
        self.seq = 0
        self.thread: threading.Thread | None = None


class RunManager:
    def __init__(
        self,
        db: Database,
        runtime: ModelRuntime,
        *,
        bus: EventBus | None = None,
        require_approval: Callable[[], bool] | bool = True,
        vram_budget_gb: Callable[[], float | None] | None = None,
        checkpoint_path: str | None = None,
        vram_probe: VramProbe | None = None,
        gateway_options: Callable[[], GatewayOptions] | None = None,
        spill_probe: SpillProbe | None = None,
        brain: BrainService | None = None,
        clone_session: Callable[[str, Path, Callable[[], bool], Callable[[str, dict[str, Any]], None]], Any] | None = None,
        clone_session_close: Callable[[str], None] | None = None,
    ) -> None:
        self.db = db
        self.runtime = runtime
        # The permanent memory of the project a run works on. None: runs remember nothing.
        self._brain = brain
        # The site cloner's session of a run (run id, workspace, Stop, events) -> its browser, dev server and question, and how it is closed.
        # None: the browser tools answer that there is no browser here.
        self._clone_session = clone_session
        self._clone_session_close = clone_session_close
        self.bus = bus or default_bus
        self._require_approval = require_approval
        self._vram_budget = vram_budget_gb
        # Model gateway: how free VRAM is measured (None = planned sizes only), its
        # thresholds, and what each model was found to cost, remembered across runs.
        self._vram_probe = vram_probe
        self._spill_probe = spill_probe
        self._gateway_options = gateway_options
        self._profiles = MemoryProfileStore(db)
        self._live: dict[str, _Live] = {}
        self._lock = threading.RLock()
        # run id -> (request, how to start it): what waits for that run to end.
        self._queued: dict[str, tuple[str, Callable[[], RunRecord]]] = {}
        path = checkpoint_path or str(paths.checkpoint_db_path())
        self._checkpoint_conn = sqlite3.connect(path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self._checkpoint_conn)
        # Runs that were active when the app closed can no longer make progress.
        self.db.execute(
            "UPDATE workflow_run SET status = 'failed', error = 'Interrupted when Will It Run closed.', finished_at = ? "
            "WHERE status IN ('running', 'waiting_approval', 'queued')",
            (utcnow(),),
        )

    @property
    def profiles(self) -> MemoryProfileStore:
        """What each model was measured to cost, shared by every run."""
        return self._profiles

    # -- events -------------------------------------------------------------
    def _emit(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        live = self._live.get(run_id)
        with self._lock:
            seq = (live.seq + 1) if live else 1
            if live:
                live.seq = seq
        self.db.execute(
            "INSERT INTO run_event(run_id, seq, type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event_type, json.dumps(payload, default=str), utcnow()),
        )
        if event_type == "tool.call" and payload.get("status") not in ("running", None):
            self.db.execute(
                "INSERT INTO tool_call(run_id, agent_id, tool, arguments, status, output, risk, duration_s, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, payload.get("node", ""), payload.get("tool", ""), json.dumps(payload.get("arguments", {})),
                    payload.get("status", ""), str(payload.get("output", ""))[:4000], payload.get("risk", "low"),
                    payload.get("duration"), utcnow(),
                ),
            )
        if event_type == "node.completed" and payload.get("kind") != "check":
            self.db.execute(
                "INSERT INTO model_usage(run_id, agent_id, model_key, input_tokens, output_tokens, tps, duration_s, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, payload.get("node", ""), payload.get("model", ""), payload.get("input_tokens"),
                    payload.get("output_tokens"), payload.get("tokens_per_second"), payload.get("seconds"), utcnow(),
                ),
            )
        self.bus.publish(f"run.{run_id}", {"run_id": run_id, "seq": seq, "type": event_type, "payload": payload})

    def _emit_live(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Publish without storing: text a model is generating, for a session view that is open now."""
        self.bus.publish(f"run.{run_id}", {"run_id": run_id, "seq": None, "type": event_type, "payload": payload, "live": True}, keep=False)

    def started(self, run_id: str) -> dict[str, Any]:
        """What a run's first event says: where it worked, from what source, how it treated permissions."""
        row = self.db.query_one("SELECT payload FROM run_event WHERE run_id = ? AND type = 'run.started' ORDER BY seq LIMIT 1", (run_id,))
        return json.loads(row["payload"]) if row else {}

    def files_written(self, run_id: str) -> list[str]:
        """Paths the run wrote, in the order it first wrote them."""
        rows = self.db.query(
            "SELECT arguments FROM tool_call WHERE run_id = ? AND tool = 'write_file' AND status = 'success' ORDER BY id", (run_id,)
        )
        paths: dict[str, None] = {}
        for row in rows:
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                path = str(json.loads(row["arguments"]).get("path") or "")
                if path:
                    paths.setdefault(path)
        return list(paths)

    def events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT seq, type, payload, created_at FROM run_event WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after_seq)
        )
        return [
            {"seq": r["seq"], "type": r["type"], "payload": json.loads(r["payload"]), "created_at": r["created_at"]}
            for r in rows
        ]

    # -- queries ------------------------------------------------------------
    def _pending(self, run_id: str) -> list[PendingApproval]:
        rows = self.db.query(
            "SELECT * FROM approval WHERE run_id = ? AND decision IS NULL ORDER BY requested_at", (run_id,)
        )
        return [
            PendingApproval(
                id=r["id"], run_id=r["run_id"], node_id=r["node_id"], kind=r["kind"], prompt=r["prompt"],
                payload=json.loads(r["payload"] or "{}"), requested_at=r["requested_at"],
            )
            for r in rows
        ]

    def get(self, run_id: str) -> RunRecord:
        row = self.db.query_one("SELECT * FROM workflow_run WHERE id = ?", (run_id,))
        if not row:
            raise RunError(f"Run {run_id} not found")
        row["result"] = json.loads(row["result"]) if row.get("result") else None
        waiting = self._queued.get(run_id)
        return RunRecord(**row, pending_approvals=self._pending(run_id), queued=waiting[0] if waiting else None)

    def active(self) -> dict[str, str]:
        """Workflow id -> id of its run that is working or waiting right now (the latest, if several)."""
        marks = ", ".join("?" for _ in TERMINAL)
        rows = self.db.query(
            f"SELECT id, workflow_id FROM workflow_run WHERE status NOT IN ({marks}) ORDER BY started_at", tuple(sorted(TERMINAL))
        )
        return {r["workflow_id"]: r["id"] for r in rows}

    def list(self, limit: int = 100) -> list[RunRecord]:
        rows = self.db.query("SELECT id FROM workflow_run ORDER BY started_at DESC LIMIT ?", (limit,))
        return [self.get(r["id"]) for r in rows]

    def delete(self, run_id: str) -> None:
        if self.get(run_id).status not in TERMINAL:
            raise RunError("Stop the run before deleting it.")
        self.db.execute("DELETE FROM workflow_run WHERE id = ?", (run_id,))

    def _set_status(self, run_id: str, status: str, **fields: Any) -> None:
        columns = {"status": status, **fields}
        assignments = ", ".join(f"{k} = ?" for k in columns)
        self.db.execute(f"UPDATE workflow_run SET {assignments} WHERE id = ?", (*columns.values(), run_id))

    # -- lifecycle ----------------------------------------------------------
    def start(
        self,
        definition: WorkflowDef,
        task: str,
        *,
        title: str | None = None,
        project: str = "Default",
        workspace: str | None = None,
        evidence_dir: str | None = None,
        protect_existing: bool = False,
        source: dict[str, Any] | None = None,
        context: str | None = None,
        background: bool = True,
        approval: str = "ask",
        keep_models: bool = False,
        read_roots: list[str] | None = None,
    ) -> RunRecord:
        """Start a run.

        ``workspace`` is where file tools work; with ``protect_existing`` a file that was
        already there is not overwritten without approval. ``context`` is added to the task
        the agents read (what an earlier run of the session did) without becoming part of
        the stored request. ``approval`` is "ask" (plan gates and high-risk actions wait for
        the user) or "bypass" (nothing waits: the user chose to ignore permissions for this run).
        ``keep_models`` leaves the models in memory when the run ends, for a run that is one
        message of a conversation.
        """
        bypass = approval == "bypass"
        result = validate(definition, require_models=True)
        if not result.ok:
            raise RunError("Workflow is not runnable: " + "; ".join(i.message for i in result.errors))
        if not task.strip():
            raise RunError("Describe the task for the team.")
        evidence_path = Path(evidence_dir) if evidence_dir and evidence_dir.strip() else None
        if evidence_path is not None and not evidence_path.is_dir():
            raise RunError(f"The documents folder does not exist: {evidence_dir}")
        run_id = uuid.uuid4().hex[:12]
        self.db.execute(
            "INSERT INTO workflow_run(id, workflow_id, project, title, task, status, started_at) VALUES (?, ?, ?, ?, ?, 'running', ?)",
            (run_id, definition.workflow.id, project, title or task.strip()[:60], task, utcnow()),
        )
        workspace_path = paths.ensure(paths.workspaces_dir() / run_id) if not workspace else paths.ensure(Path(workspace))
        require = self._require_approval() if callable(self._require_approval) else self._require_approval
        budget = self._vram_budget() if self._vram_budget else None

        memory: BrainSession | None = None
        if self._brain is not None:
            try:
                memory = self._brain.open(run_id, definition, source, workspace_path, task)
            except Exception:
                # A run never fails because of its memory: it goes on without one.
                log.exception("the project memory could not be opened for run %s", run_id)
        if memory is not None:
            memory.emit = lambda event_type, payload: self._emit(run_id, event_type, payload)

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            self._emit(run_id, event_type, payload)
            if memory is not None:
                # What the run does is what its project will remember of it.
                memory.observe(event_type, payload)

        tool_ctx = tools.ToolContext(workspace=workspace_path, protect_existing=protect_existing,
                                     read_roots=[Path(r) for r in read_roots or [] if r and Path(r).exists()])
        ctx = RunContext(
            run_id=run_id,
            definition=definition,
            runtime=self.runtime,
            models=ModelManager(
                self.runtime, definition.models, emit, vram_budget_gb=budget, vram_probe=self._vram_probe,
                profiles=self._profiles, options=self._gateway_options() if self._gateway_options else None,
                spill_probe=self._spill_probe, keep_after_run=keep_models,
            ),
            emit=emit,
            tool_ctx=tool_ctx,
            require_approval=bool(require) and not bypass,
            auto_approve=bypass,
            evidence_dir=evidence_path,
            emit_live=lambda event_type, payload: self._emit_live(run_id, event_type, payload),
            brain=memory,
        )
        tool_ctx.cancelled = ctx.cancelled.is_set
        tool_ctx.complete = slot_completion(ctx)
        # The slot whose model can see, for the look tool; none when no model of the workflow can.
        tool_ctx.vision_slot = next((slot_id for slot_id, slot in definition.models.items() if slot.kind == "vision"), "")
        if self._clone_session is not None:
            session = self._clone_session
            tool_ctx.clone = lambda: session(run_id, workspace_path, ctx.cancelled.is_set, emit)
        tool_ctx.progress = lambda payload: self._emit_live(run_id, "tool.progress", payload)
        live = _Live(ctx)
        self._live[run_id] = live
        log_event(log, "run started", run=run_id, workflow=definition.workflow.id)
        emit("run.started", {"workflow": definition.workflow.id, "name": definition.workflow.name, "task": task,
                             "workspace": str(workspace_path), "agents": [a.id for a in definition.agents],
                             "evidence_dir": str(evidence_path) if evidence_path else None,
                             "source": source or {"kind": "none"}, "protect_existing": protect_existing,
                             "continued": bool(context), "approval": "bypass" if bypass else "ask",
                             "memory": memory.describe() if memory is not None else None})
        graph_task = f"{task}\n\n{context}" if context else task
        if background:
            live.thread = threading.Thread(target=self._execute, args=(run_id, graph_task), daemon=True, name=f"run-{run_id}")
            live.thread.start()
        else:
            self._execute(run_id, graph_task)
        return self.get(run_id)

    def _await_decisions(self, run_id: str, interrupts: list[Any]) -> dict[str, dict[str, Any]]:
        """Record approval requests and block until each is decided (or the run is cancelled)."""
        live = self._live[run_id]
        wanted: dict[str, str] = {}
        for item in interrupts:
            value = item.value if isinstance(item.value, dict) else {"prompt": str(item.value)}
            approval_id = value.get("fingerprint") or uuid.uuid4().hex[:16]
            wanted[approval_id] = item.id
            self.db.execute(
                "INSERT OR IGNORE INTO approval(id, run_id, node_id, kind, prompt, payload, requested_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (approval_id, run_id, value.get("node", ""), value.get("kind", "gate"), value.get("prompt", ""),
                 json.dumps(value.get("details", {}), default=str), utcnow()),
            )
        self._set_status(run_id, "waiting_approval")
        self._emit(run_id, "run.waiting", {"approvals": list(wanted)})
        self.bus.publish("notify", {"title": "Workflow requires approval", "body": self.get(run_id).title})
        while not all(a in live.decisions for a in wanted):
            live.wake.wait(timeout=0.5)
            live.wake.clear()
            live.ctx.check_cancelled()
        self._set_status(run_id, "running")
        return {wanted[a]: live.decisions[a] for a in wanted}

    def _execute(self, run_id: str, task: str) -> None:
        live = self._live[run_id]
        ctx = live.ctx
        definition = ctx.definition
        status, error, result = "completed", None, None
        try:
            if ctx.brain is not None:
                # The project map is read again when the files changed since the last run.
                ctx.brain.scan()
            graph = compile_workflow(definition, ctx, self.checkpointer)
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": definition.workflow.max_steps}
            payload: Any = {"task": task, "outputs": {}, "routes": {}, "loop_counts": {}, "approvals": {}, "history": [], "ledger": []}
            while True:
                for _ in graph.stream(payload, config, stream_mode="updates"):
                    ctx.check_cancelled()
                snapshot = graph.get_state(config)
                interrupts = [i for t in snapshot.tasks for i in (t.interrupts or ())]
                if not snapshot.next or not interrupts:
                    break
                decisions = self._await_decisions(run_id, interrupts)
                payload = Command(resume=next(iter(decisions.values())) if len(decisions) == 1 else decisions)
            values = graph.get_state(config).values
            status = values.get("status") or "completed"
            outputs = values.get("outputs") or {}
            last = values.get("last_node")
            result = {
                "final": outputs.get(last) if last else None,
                "final_node": last,
                "outputs": outputs,
                "history": values.get("history") or [],
                "approvals": values.get("approvals") or {},
                "model_switches": ctx.models.switch_count,
                # One entry per step: the hash of its output and of everything it read.
                "ledger": values.get("ledger") or [],
            }
            if status == "retry_limit":
                sender = definition.node_label(last) if last else "A step"
                error = (
                    f"{sender} sent the work back as often as the workflow allows and it still did not pass: "
                    "a loop reached its retry limit. What was written is in the workspace, and the last report says what was left open."
                )
            elif status == "rejected":
                error = "A human reviewer rejected the work."
            elif status == "no_route":
                error = "The workflow reached a node with no matching outgoing edge."
            elif status == "blocked":
                stopped = outputs.get(last) if last else None
                error = (stopped or {}).get("summary") or "A check did not pass and stopped the run."
        except RunCancelled:
            status, error = "cancelled", "Cancelled by the user."
        except GraphRecursionError:
            status, error = "failed", f"Stopped after {definition.workflow.max_steps} steps (workflow.max_steps)."
        except AgentFailed as exc:
            status, error = "failed", str(exc)
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            log.exception("run %s crashed", run_id)
        finally:
            with contextlib.suppress(Exception):
                ctx.models.close()
            if self._clone_session_close is not None:
                with contextlib.suppress(Exception):
                    self._clone_session_close(run_id)
        if ctx.brain is not None:
            # However the run ended, what it changed and found out is written down.
            try:
                ctx.brain.close(status, error, result)
            except Exception:
                log.exception("the project memory could not be written for run %s", run_id)
        self._set_status(
            run_id, status, result=json.dumps(result, default=str) if result else None, error=error, finished_at=utcnow()
        )
        self._emit(run_id, "run.completed", {"status": status, "error": error, "result": result})
        if definition.workflow.category != "chat":
            # An answer in a chat is read where it is written: it needs no notice of its own.
            self.bus.publish("notify", {
                "title": "Workflow complete" if status == "completed" else f"Workflow {status.replace('_', ' ')}",
                "body": self.get(run_id).title,
            })
        log_event(log, "run finished", run=run_id, status=status)
        with self._lock:
            waiting = self._queued.pop(run_id, None)
        if waiting is not None:
            threading.Thread(target=self._start_queued, args=(run_id, waiting[1]), daemon=True, name=f"queued-{run_id}").start()

    # -- a request written while a run was working ---------------------------
    def queue_after(self, run_id: str, task: str, start: Callable[[], RunRecord]) -> bool:
        """Hold a request until ``run_id`` has ended, then call ``start``. False when it has ended already.

        One request waits per run: a second one replaces the first. It is kept in memory, like
        the run it waits for, so closing the app drops it.
        """
        with self._lock:
            if self.get(run_id).status in TERMINAL:
                return False
            self._queued[run_id] = (task, start)
        self.bus.publish(f"run.{run_id}", {"run_id": run_id, "seq": None, "type": "run.queued", "payload": {"task": task}}, keep=False)
        return True

    def unqueue(self, run_id: str) -> bool:
        with self._lock:
            removed = self._queued.pop(run_id, None) is not None
        self.bus.publish(f"run.{run_id}", {"run_id": run_id, "seq": None, "type": "run.queued", "payload": {"task": None}}, keep=False)
        return removed

    def _start_queued(self, run_id: str, start: Callable[[], RunRecord]) -> None:
        try:
            record = start()
        except Exception as exc:
            log.warning("the request queued after run %s could not start: %s", run_id, exc)
            self.bus.publish("notify", {"title": "The queued request could not start", "body": str(exc)})
            return
        # Whoever is watching the run that ended is taken to the one that follows it.
        self.bus.publish(f"run.{run_id}", {"run_id": run_id, "seq": None, "type": "run.followed", "payload": {"next": record.id}}, keep=False)

    def decide(self, run_id: str, approval_id: str, approved: bool, comment: str = "") -> RunRecord:
        live = self._live.get(run_id)
        if live is None:
            raise RunError("This run is no longer active.")
        row = self.db.query_one("SELECT id FROM approval WHERE id = ? AND run_id = ?", (approval_id, run_id))
        if not row:
            raise RunError(f"Approval {approval_id} not found for this run.")
        self.db.execute(
            "UPDATE approval SET decision = ?, comment = ?, decided_at = ? WHERE id = ?",
            ("approved" if approved else "rejected", comment, utcnow(), approval_id),
        )
        live.decisions[approval_id] = {"approved": approved, "comment": comment, "fingerprint": approval_id}
        live.wake.set()
        return self.get(run_id)

    def cancel(self, run_id: str) -> RunRecord:
        live = self._live.get(run_id)
        if live is not None:
            live.ctx.cancelled.set()
            live.wake.set()
        return self.get(run_id)

    def wait(self, run_id: str, timeout: float | None = None) -> RunRecord:
        live = self._live.get(run_id)
        if live and live.thread:
            live.thread.join(timeout)
        return self.get(run_id)
