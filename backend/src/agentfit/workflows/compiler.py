"""Compile a workflow definition into a LangGraph ``StateGraph``.

Every agent, every deterministic check and every human-approval gate becomes a node. A
node writes its routing decision into the state and a conditional edge follows it, which
is how conditions, bounded loops and fan-out are expressed. Each node also appends one
entry to the ledger: the SHA-256 of what it produced and of everything it was shown. Join edges (several sources, one target) wait
for all sources. Human approval uses LangGraph ``interrupt`` and resumes through
``Command(resume=...)`` with a checkpointer, so a paused run survives in SQLite.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.errors import GraphInterrupt
from langgraph.graph import END as LG_END
from langgraph.graph import START as LG_START
from langgraph.graph import StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from agentfit.brain import service as memory_tools
from agentfit.runtime import ModelRuntime
from agentfit.workflows import checks, conditions, schemas, tools
from agentfit.workflows.dsl import END, START, AgentDef, ApprovalDef, CheckDef, Escalation, WorkflowDef
from agentfit.workflows.model_manager import AUTO_WORKING_NEED, ModelLoadError, ModelManager

EmitFn = Callable[[str, dict[str, Any]], None]

CHARS_PER_TOKEN = 3.5
MIN_OUTPUT_TOKENS = 1024
MAX_OUTPUT_TOKENS = 16384
# Below this many prompt tokens a measured ratio says more about the chat template than about the text.
CALIBRATION_MIN_TOKENS = 1024
# A completion that stopped on the length limit having used less than this share of its output
# budget was stopped by the context filling up, not by the budget.
CONTEXT_FULL_SHARE = 0.9
# Turns cut short by a full context that are sent again without counting as an attempt.
CONTEXT_FULL_TURNS = 2


class RunCancelled(Exception):
    pass


class AgentFailed(Exception):
    def __init__(self, agent_id: str, message: str) -> None:
        super().__init__(f"Agent '{agent_id}' failed: {message}")
        self.agent_id = agent_id


def _merge(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


def _merge_max(left: dict[str, int] | None, right: dict[str, int] | None) -> dict[str, int]:
    out = dict(left or {})
    for key, value in (right or {}).items():
        out[key] = max(out.get(key, 0), value)
    return out


def _append(left: list[str] | None, right: list[str] | None) -> list[str]:
    return [*(left or []), *(right or [])]


def _last(left: str | None, right: str | None) -> str | None:
    """Last write wins. Needed because parallel branches update these keys in the same step."""
    return right if right is not None else left


class WorkflowState(TypedDict, total=False):
    task: str
    # node id -> that node's latest structured output
    outputs: Annotated[dict[str, Any], _merge]
    # node id -> next node id(s), written by the node, read by its conditional edge
    routes: Annotated[dict[str, Any], _merge]
    # "source->target" -> times that loop edge was taken
    loop_counts: Annotated[dict[str, int], _merge_max]
    approvals: Annotated[dict[str, Any], _merge]
    # order in which nodes completed
    history: Annotated[list[str], _append]
    # one entry per completed node: its output's hash and the hashes of what it read
    ledger: Annotated[list[dict[str, Any]], _append]
    status: Annotated[str, _last]
    last_node: Annotated[str, _last]


@dataclass
class AgentProgress:
    """Where an agent is in its tool loop, so an approval interrupt can resume exactly there."""

    messages: list[dict[str, Any]]
    instance_id: str
    # Context of the model the prompt was built for, and what this agent asks the gateway for.
    context_length: int = 8192
    need_context: int = 0
    rounds: int = 0
    queue: list[dict[str, Any]] = field(default_factory=list)
    approval_seq: int = 0
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    generation_seconds: float = 0.0
    # True when the last completion stopped because it hit the output-token limit.
    truncated: bool = False
    # Output tokens the last completion was allowed, and tool rounds in which nothing could be run.
    budget: int = 0
    wasted_rounds: int = 0
    # Counts of rounds left at which the agent has already been told so.
    told_rounds_left: set[int] = field(default_factory=set)
    # Exit code and closing line of the last run of the test suite, when the agent ran it.
    test_run: tuple[int, str] | None = None
    # Hash of each earlier output that was put in this agent's prompt.
    read: dict[str, str] = field(default_factory=dict)
    # What the model thought before answering, over every call of this agent. Kept for the session.
    reasoning: str = ""
    # Turns the runtime could not read as a tool call or an answer (see ``unreadable_turn``).
    unreadable: int = 0
    # Tool-loop turns that ran out of output while reasoning. Counted apart from ``attempts``,
    # so the final answer keeps every attempt of its own however the tool loop ended.
    truncations: int = 0
    # Characters per token of this agent's own prompts, as the model's tokenizer counted them.
    chars_per_token: float = CHARS_PER_TOKEN
    # True when the last completion hit the length limit before its output budget: the context was full.
    context_full: bool = False
    context_full_turns: int = 0
    # Tool rounds of this visit (``tool_budget``); 0 = the agent's max_tool_rounds.
    max_rounds: int = 0
    # The agent's work came back to it (a fix): its idle rounds count from the start.
    revising: bool = False
    # Rounds in a row in which no file was changed, and files changed in this visit.
    idle_rounds: int = 0
    changes: int = 0
    told_idle: bool = False
    # Read-only calls already answered in this visit: call -> (round, result) while the result is whole in the conversation.
    seen: dict[str, tuple[int, int, str]] = field(default_factory=dict)
    repeats: int = 0
    # True while the agent has only its writing tools (``AgentDef.focus_after_idle_rounds``); a change of a file ends it.
    focused: bool = False
    focuses: int = 0


@dataclass
class RunContext:
    run_id: str
    definition: WorkflowDef
    runtime: ModelRuntime
    models: ModelManager
    emit: EmitFn
    tool_ctx: tools.ToolContext
    require_approval: bool = True
    # The user started the run with permissions ignored: nothing waits for a decision. Plan
    # gates are approved as they are reached, and with ``require_approval`` off no tool asks.
    auto_approve: bool = False
    cancelled: threading.Event = field(default_factory=threading.Event)
    pending: dict[str, AgentProgress] = field(default_factory=dict)
    announced: set[str] = field(default_factory=set)
    llm_lock: threading.Lock = field(default_factory=threading.Lock)
    # Read-only folder of documents for the evidence dossier. File tools never touch it.
    evidence_dir: Path | None = None
    # What each agent's last run read and which model served it, for the ledger.
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Sends an event to whoever is watching, without storing it: text as it is generated.
    emit_live: EmitFn | None = None
    # The run's view of its project's permanent memory (``agentfit.brain.BrainSession``). None: no memory.
    brain: Any = None

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise RunCancelled()


# -- routing ------------------------------------------------------------------


def decide_next(
    definition: WorkflowDef, node_id: str, outputs: dict[str, Any], loop_counts: dict[str, int]
) -> tuple[str | list[str], dict[str, int], str | None]:
    """Pick the next node(s) for ``node_id``.

    Returns (target, loop_count_updates, status). Conditional edges are tried in order;
    a loop edge that has used up ``max_loops`` is skipped (or diverted to
    ``on_exhausted``). Unconditional edges are the fallback; several of them fan out.
    """
    edges = [e for e in definition.outgoing(node_id) if len(e.sources) == 1]
    exhausted = False
    fallback: list[str] = []
    for edge in edges:
        expression = definition.resolve_condition(edge.when)
        if expression is None:
            if edge.max_loops is not None:
                key = f"{node_id}->{edge.target}"
                if loop_counts.get(key, 0) >= edge.max_loops:
                    exhausted = True
                    if edge.on_exhausted:
                        return edge.on_exhausted, {}, None
                    continue
                return edge.target, {key: loop_counts.get(key, 0) + 1}, None
            fallback.append(edge.target)
            continue
        try:
            matched = conditions.evaluate(expression, outputs, node_id)
        except conditions.ConditionError:
            matched = False
        if not matched:
            continue
        if edge.max_loops is not None:
            key = f"{node_id}->{edge.target}"
            if loop_counts.get(key, 0) >= edge.max_loops:
                exhausted = True
                if edge.on_exhausted:
                    return edge.on_exhausted, {}, None
                continue
            return edge.target, {key: loop_counts.get(key, 0) + 1}, None
        return edge.target, {}, None
    if fallback and not exhausted:
        return (fallback[0] if len(fallback) == 1 else fallback), {}, None
    if exhausted:
        return END, {}, "retry_limit"
    if fallback:
        return (fallback[0] if len(fallback) == 1 else fallback), {}, None
    return END, {}, "no_route"


# -- prompts ------------------------------------------------------------------


def tool_budget(agent: AgentDef, state: WorkflowState | None = None) -> int:
    """Tool rounds this agent has this time: fewer when its work came back to it, if the workflow says so."""
    revising = state is not None and agent.id in (state.get("outputs") or {})
    return agent.revision_tool_rounds if revising and agent.revision_tool_rounds else agent.max_tool_rounds


def render_system_prompt(agent: AgentDef, memory_guide: str = "", rounds: int | None = None) -> str:
    """The agent's instructions. ``memory_guide`` is added when the run gives it the project's memory tools.

    ``rounds`` is the tool budget of this visit (``tool_budget``), when it differs from the agent's usual one.
    """
    parts = [f"# Role: {agent.role}"]
    if agent.goal:
        parts.append(f"## Goal\n{agent.goal}")
    if agent.responsibilities:
        parts.append("## Responsibilities\n" + "\n".join(f"- {r}" for r in agent.responsibilities))
    if agent.system_prompt.strip():
        parts.append(agent.system_prompt.strip())
    if agent.tools:
        # Without this an agent learns of its budget when it is gone, with half the work unwritten.
        parts.append(
            "## Tools\nYou work in rounds: in each one you call one or several tools, then you see their results. "
            f"You have {rounds or agent.max_tool_rounds} rounds this time. Do first what the work needs "
            "and check it afterwards. Calls that do not depend on each other go in the same round."
        )
    if memory_guide:
        parts.append(memory_guide)
    if agent.input:
        parts.append(f"## Input you receive\n{agent.input}")
    if agent.output_schema == schemas.TEXT:
        parts.append(
            "## Output\nAnswer in plain text or Markdown, in the language you were written to. Be factual: "
            "if something is unknown or was not verified, say so instead of inventing it."
        )
    else:
        parts.append(
            "## Output\nYour final answer must be a single JSON object with exactly these fields:\n"
            + schemas.describe(agent.output_schema)
            + "\nDo not wrap the JSON in prose. Be factual: if something is unknown or was not verified, say so "
            "in the relevant field instead of inventing it."
        )
    return "\n\n".join(parts)


def _without_documents(value: Any) -> Any:
    """Evidence text is shown once, as evidence; elsewhere only its list of sources."""
    if isinstance(value, dict) and "documents" in value:
        return {k: v for k, v in value.items() if k != "documents"}
    return value


def _compact(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=1) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + "\n... [truncated]"


def visible_outputs(agent: AgentDef, state: WorkflowState) -> list[tuple[str, str, Any]]:
    """Earlier outputs this agent may be shown, as (what was read, node id, value).

    Without ``reads`` an agent sees everything so far, oldest first. With it, only the
    listed nodes or single fields, in the listed order: the rest never reaches its prompt.
    An entry is ``node``, ``node.field`` or ``a|b.field`` (the latest of several nodes).
    """
    outputs = state.get("outputs") or {}
    if agent.reads is None:
        history = dict.fromkeys(state.get("history") or [])
        return [(node, node, outputs[node]) for node in history if node != agent.id and node in outputs]
    shown: list[tuple[str, str, Any]] = []
    history = list(state.get("history") or [])
    for entry in agent.reads:
        names, _, field_name = entry.partition(".")
        # "a|b" means whichever of the two ran most recently.
        candidates = [n for n in names.split("|") if n in outputs and n != agent.id]
        node = next((n for n in reversed(history) if n in candidates), candidates[0] if candidates else "")
        if not node:
            continue
        value = outputs[node]
        if field_name:
            value = value.get(field_name) if isinstance(value, dict) else None
            if value is None:
                continue
        shown.append((entry, node, value))
    return shown


def _is_evidence(definition: WorkflowDef, key: str, node: str) -> bool:
    check = definition.check(node)
    return check is not None and check.kind == "evidence_dossier" and key == node


def read_receipt(agent: AgentDef, state: WorkflowState) -> dict[str, str]:
    """SHA-256 of each earlier output that is put in front of this agent."""
    return {key: checks.digest(value) for key, _, value in visible_outputs(agent, state)}


def prompt_demand(agent: AgentDef, state: WorkflowState, definition: WorkflowDef) -> int:
    """Context, in tokens, that would hold this agent's whole input without trimming anything.

    ``build_messages`` cuts earlier outputs to fit the model. This is the size before
    the cut, so the gateway can tell a request that fits from one that needs the slot's
    wide-context variant.
    """
    chars = len(render_system_prompt(agent)) + len(str(state.get("task", "")))
    for key, node, value in visible_outputs(agent, state):
        if _is_evidence(definition, key, node):
            chars += sum(len(str(d.get("text", ""))) for d in value.get("documents") or [])
        else:
            chars += len(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    if agent.max_input_tokens:
        chars = min(chars, int(agent.max_input_tokens * CHARS_PER_TOKEN) + len(render_system_prompt(agent)))
    # The prompt may use 55% of the context; the rest is left for the answer.
    return int(chars / CHARS_PER_TOKEN / 0.55)


def _render_evidence(documents: list[dict[str, Any]], limit: int) -> str:
    parts: list[str] = []
    room = limit
    for document in documents:
        text = str(document.get("text", ""))
        head = f"### {document.get('id')} - {document.get('name')}"
        if room <= 0:
            parts.append(f"{head}\n[not shown: no room left in this model's context]")
            continue
        shown = text[:room]
        room -= len(shown)
        parts.append(f"{head}\n{shown}" + ("\n[cut here: the rest does not fit this model's context]" if len(shown) < len(text) else ""))
    return (
        "## Evidence\nThis is all the evidence there is. A quote must copy its words exactly and name the source id (E1, E2, ...).\n\n"
        + "\n\n".join(parts)
    )


def build_messages(
    agent: AgentDef, state: WorkflowState, definition: WorkflowDef, context_tokens: int | None = None,
    *, memory: str = "", memory_guide: str = "",
) -> list[dict[str, Any]]:
    """Build the prompt for the context the model is actually loaded with.

    ``memory`` is the section of the project's memory chosen for this agent's task. It is
    part of the budget like everything else the agent is shown.
    """
    if context_tokens is None:
        slot = definition.models.get(agent.model)
        context_tokens = slot.context_length if slot else 8192
    budget_chars = int(context_tokens * CHARS_PER_TOKEN * 0.55)
    if agent.max_input_tokens:
        # An explicit input budget: this agent is never handed more than this, whatever fits.
        budget_chars = min(budget_chars, int(agent.max_input_tokens * CHARS_PER_TOKEN))
    outputs = state.get("outputs") or {}
    task = str(state.get("task", ""))

    visible = visible_outputs(agent, state)
    evidence: list[dict[str, Any]] = []
    prior: list[tuple[str, str, Any]] = []
    for key, node, value in visible:
        if _is_evidence(definition, key, node):
            evidence = [d for d in value.get("documents") or [] if isinstance(d, dict)]
        else:
            prior.append((key, node, value))

    request_is_evidence = any(d.get("name") == "Request" for d in evidence)
    if request_is_evidence and len(task) > 1500:
        # A long request is already in the evidence in full: do not spend the context on it twice.
        sections = [f"## Task\n{task[:600]}\n[The full request is evidence source {next(d['id'] for d in evidence if d.get('name') == 'Request')} below.]"]
    else:
        sections = [f"## Task\n{task}"]
    if evidence:
        sections.append(_render_evidence(evidence, int(budget_chars * 0.7)))
        budget_chars -= min(int(budget_chars * 0.7), sum(len(str(d.get("text", ""))) for d in evidence))
    if memory:
        sections.append(memory)
        budget_chars = max(0, budget_chars - len(memory))

    if prior:
        share = budget_chars // max(1, len(prior))
        # An agent with an explicit reading list gets those items whole when they fit.
        per_item = max(800, share if agent.reads is not None else min(6000, share))
        rendered = []
        for key, node, value in prior:
            title = definition.node_label(node)
            rendered.append(f"### {title} ({key})\n{_compact(_without_documents(value), per_item)}")
        sections.append("## Work so far\n" + "\n\n".join(rendered))

    if agent.id in outputs:
        sections.append(f"## Your previous output\n{_compact(outputs[agent.id], 4000)}")
        last = state.get("last_node")
        if last and last != agent.id and last in outputs:
            sections.append(
                f"## Feedback to address (from {definition.node_label(last)})\n"
                f"{_compact(_without_documents(outputs[last]), 4000)}\nRevise your work so every point above is resolved."
            )

    comments = [
        f"- {node}: {decision.get('comment')}"
        for node, decision in (state.get("approvals") or {}).items()
        if isinstance(decision, dict) and decision.get("comment")
    ]
    if comments:
        sections.append("## Human feedback\n" + "\n".join(comments))
    return [
        {"role": "system", "content": render_system_prompt(agent, memory_guide, tool_budget(agent, state))},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def parse_structured(content: str, schema_name: str, choices: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Extract and validate the JSON object an agent produced.

    ``choices`` lists the only values allowed for some fields. The decoder is given the
    same lists, but the answer is checked again here in case the engine ignored them.
    """
    text = _THINK.sub("", content or "").strip()
    text = _FENCE.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in the answer")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    try:
        parsed = schemas.get_schema(schema_name).model_validate(data).model_dump()
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        raise ValueError(f"JSON does not match the schema ({problems})") from exc
    for field_name, allowed in (choices or {}).items():
        if allowed and field_name in parsed and parsed[field_name] not in allowed:
            raise ValueError(f"{field_name} must be exactly one of: {', '.join(allowed)}")
    return parsed


# -- approvals ----------------------------------------------------------------


def request_approval(ctx: RunContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Pause the graph until a human decides.

    LangGraph re-runs a node from its start when it resumes, replaying earlier resume
    values in order. Each request carries a fingerprint and replayed values that belong
    to a different request are skipped, so a decision can never be applied to another call.
    """
    if ctx.auto_approve:
        # Recorded like a decision of the user's, and marked as not one.
        ctx.emit("approval.requested", payload)
        ctx.emit("approval.decided", {**payload, "approved": True, "comment": "", "automatic": True})
        return {"approved": True, "comment": "", "fingerprint": payload["fingerprint"]}
    if payload["fingerprint"] not in ctx.announced:
        ctx.announced.add(payload["fingerprint"])
        ctx.emit("approval.requested", payload)
    while True:
        decision = interrupt(payload)
        if isinstance(decision, dict) and decision.get("fingerprint") == payload["fingerprint"]:
            ctx.emit("approval.decided", {**payload, "approved": bool(decision.get("approved")), "comment": decision.get("comment", "")})
            return decision


def _fingerprint(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


# -- agent execution ----------------------------------------------------------


REASONING_KEPT = 24_000
LIVE_INTERVAL = 0.25
_THINK_TAGS = re.compile(r"</?think>", re.IGNORECASE)


class LiveText:
    """Forwards what a model generates, a few times a second, with its speed so far.

    Nothing here is stored: these events go only to an open session view. The complete
    reasoning is kept on the agent's progress and saved once, when the agent finishes.
    """

    def __init__(self, ctx: RunContext, agent: AgentDef, clock: Callable[[], float] = time.perf_counter) -> None:
        self._ctx = ctx
        self._node = agent.id
        self._clock = clock
        self._first: float | None = None
        self._sent = 0.0
        self._tokens = 0
        self._pending = {"reasoning": "", "content": ""}

    def feed(self, kind: str, text: str) -> None:
        now = self._clock()
        if self._first is None:
            self._first = now
        self._tokens += 1  # one chunk of a stream is about one token
        self._pending[kind if kind in self._pending else "content"] += text
        if now - self._sent >= LIVE_INTERVAL:
            self.flush(now)

    def flush(self, now: float | None = None) -> None:
        if self._ctx.emit_live is None or self._first is None:
            return
        now = self._clock() if now is None else now
        elapsed = now - self._first
        self._ctx.emit_live(
            "node.stream",
            {
                "node": self._node,
                "reasoning": _THINK_TAGS.sub("", self._pending["reasoning"]),
                "content": self._pending["content"],
                "tokens": self._tokens,
                # Not meaningful on the first few tokens: wait for half a second of generation.
                "tokens_per_second": round(self._tokens / elapsed, 1) if elapsed >= 0.5 else None,
            },
        )
        self._pending = {"reasoning": "", "content": ""}
        self._sent = now


def _usage(progress: AgentProgress, response: dict[str, Any], seconds: float, prompt_chars: int = 0) -> None:
    message = (response.get("choices") or [{}])[0].get("message") or {}
    thought = str(message.get("reasoning_content") or "")
    if not thought:
        inline = _THINK.search(str(message.get("content") or ""))
        thought = _THINK_TAGS.sub("", inline.group(0)) if inline else ""
    if thought.strip() and len(progress.reasoning) < REASONING_KEPT:
        progress.reasoning = (progress.reasoning + ("\n\n" if progress.reasoning else "") + thought.strip())[:REASONING_KEPT]
    usage = response.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    progress.input_tokens += prompt_tokens
    progress.output_tokens += completion_tokens
    details = usage.get("completion_tokens_details") or {}
    progress.reasoning_tokens += int(details.get("reasoning_tokens") or 0)
    progress.generation_seconds += seconds
    progress.truncated = (response.get("choices") or [{}])[0].get("finish_reason") == "length"
    if prompt_chars and prompt_tokens >= CALIBRATION_MIN_TOKENS:
        # HTML, CSS, code and JSON pack more tokens per character than prose. Seen on a 27B
        # Developer: at 3.5 characters per token a conversation trimmed to "fit" 55296 tokens
        # filled the context, and turns promised 9000 output tokens ended after a few dozen.
        progress.chars_per_token = max(1.0, min(CHARS_PER_TOKEN, prompt_chars / prompt_tokens))
    # Stopped on the length limit with most of its budget unused: the context ended first.
    progress.context_full = progress.truncated and 0 < completion_tokens < progress.budget * CONTEXT_FULL_SHARE


def output_budget(ctx: RunContext, agent: AgentDef, messages: list[dict[str, Any]], extra_chars: int = 0) -> int:
    """Tokens the model may generate: whatever the context window has left after the prompt.

    Reasoning models spend output tokens thinking before they answer, so a small fixed
    cap can be used up before any answer appears. The budget therefore follows the
    model's context length instead of a constant. ``extra_chars`` is what the request
    carries besides the messages: the descriptions of the tools.
    """
    progress = ctx.pending.get(agent.id)
    slot = ctx.definition.models.get(agent.model)
    # The context the model was really loaded with, which can be less than the slot asks for.
    context = progress.context_length if progress else (slot.context_length if slot else 8192)
    prompt_chars = sum(_message_chars(m) for m in messages) + extra_chars
    remaining = context - int(prompt_chars / (progress.chars_per_token if progress else CHARS_PER_TOKEN)) - 256
    return max(MIN_OUTPUT_TOKENS, min(MAX_OUTPUT_TOKENS, remaining))


def _message_chars(message: dict[str, Any]) -> int:
    calls = message.get("tool_calls")
    return len(str(message.get("content") or "")) + (len(json.dumps(calls)) if calls else 0)


# An argument of an earlier tool call longer than this is replaced by its opening when room is short.
_ARGUMENT_KEPT = 200


def _shorten_arguments(message: dict[str, Any]) -> dict[str, Any]:
    """The same assistant turn with the long arguments of its tool calls cut to their opening.

    What a call wrote is on disk and its result is in the next message, so the text it
    carried is the first thing a conversation can do without. The arguments stay valid
    JSON: the runtime parses them again on every request.
    """
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            arguments = None
        if isinstance(arguments, dict):
            for name, value in arguments.items():
                if isinstance(value, str) and len(value) > _ARGUMENT_KEPT + 80:
                    arguments[name] = f"{value[:_ARGUMENT_KEPT]} [... {len(value) - _ARGUMENT_KEPT} more characters were sent, left out here to save context]"
            call = {**call, "function": {**function, "arguments": json.dumps(arguments, ensure_ascii=False)}}
        calls.append(call)
    return {**message, "tool_calls": calls}


def conversation_demand(messages: list[dict[str, Any]], chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """Context, in tokens, that holds the conversation so far and leaves room to answer."""
    return int(sum(_message_chars(m) for m in messages) / chars_per_token / 0.7)


SHORTENED = "\n[... shortened to fit the model's context ...]"
# Characters are a rough count of tokens, and code or JSON packs more tokens per character.
_FIT_SAFETY = 0.85
# When old tool results have to go, this share of the budget is freed beyond what is needed.
# A conversation cut by exactly what overflowed is cut again on the next turn, a little later in
# it; LM Studio then processes the whole prompt again from the cut. Seen on a 27B Developer at
# 57k tokens: every turn began with 21 s of prompt processing from 0 %, against under 1 s when
# the conversation was untouched. With room to spare, the next turns only append.
_TRIM_SLACK = 0.25


def fit_messages(messages: list[dict[str, Any]], context_tokens: int, reserved_chars: int = 0, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """Shorten a conversation in place until it fits the context. Returns the characters removed.

    An agent that calls tools keeps every call and every result in its conversation, so a
    long tool loop can outgrow the model. The text earlier calls carried goes first (a
    file that was written is on disk); then old tool results, cut to their opening lines;
    then the latest call and result; last, the opening summary of earlier work. The
    instructions are never cut. ``reserved_chars`` is room the request needs besides the
    messages, for the descriptions of the tools. ``chars_per_token`` is what the agent's
    earlier prompts measured, when they did.
    """
    budget = max(2000, int((context_tokens - MIN_OUTPUT_TOKENS) * chars_per_token * _FIT_SAFETY) - reserved_chars)
    over = sum(_message_chars(m) for m in messages) - budget
    if over <= 0:
        return 0
    removed = 0

    def shorten(index: int, keep: int) -> None:
        nonlocal over, removed
        content = str(messages[index].get("content") or "")
        if len(content) <= keep + len(SHORTENED):
            return
        messages[index] = {**messages[index], "content": content[:keep] + SHORTENED}
        cut = len(content) - keep - len(SHORTENED)
        over -= cut
        removed += cut

    def shorten_calls(index: int) -> None:
        nonlocal over, removed
        shorter = _shorten_arguments(messages[index])
        cut = _message_chars(messages[index]) - _message_chars(shorter)
        if cut > 0:
            messages[index] = shorter
            over -= cut
            removed += cut

    callers = [i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")]
    for index in callers[:-1]:
        if over <= 0:
            return removed
        shorten_calls(index)
    tool_results = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    slack = int(budget * _TRIM_SLACK) if over > 0 else 0
    over += slack
    for index in tool_results[:-1]:
        if over <= 0:
            return removed
        shorten(index, 400)
    over -= slack
    if over <= 0:
        return removed
    if over > 0 and callers:
        shorten_calls(callers[-1])
    if over > 0 and tool_results:
        last = tool_results[-1]
        shorten(last, max(400, len(str(messages[last].get("content") or "")) - over - len(SHORTENED)))
    if over > 0 and len(messages) > 1 and messages[1].get("role") == "user":
        shorten(1, max(1000, len(str(messages[1].get("content") or "")) - over - len(SHORTENED)))
    return removed


TRUNCATED_FEEDBACK = (
    "You ran out of output space before giving an answer. Keep your reasoning to a few sentences "
    "and reply immediately with only the JSON object."
)
TOOL_TRUNCATED_FEEDBACK = (
    "You ran out of output space before finishing your turn. Keep your reasoning to a few sentences, "
    "then call a tool or give your final answer."
)
# For an agent that answers in its own words: TRUNCATED_FEEDBACK would ask it for JSON.
TEXT_TRUNCATED_FEEDBACK = (
    "You ran out of output space again. Stop using tools: keep your reasoning to a few sentences "
    "and give your final answer now, from what you have already done."
)


def _complete(ctx: RunContext, progress: AgentProgress, agent: AgentDef, **kwargs: Any) -> dict[str, Any]:
    ctx.check_cancelled()
    with ctx.llm_lock:
        # One request at a time, and the right model is in memory before it is sent: another
        # agent, an idle timeout or the user may have unloaded it since this agent started.
        slot = ctx.definition.models.get(agent.model)
        need = progress.need_context
        if slot is not None and slot.auto_tune:
            # An automatic slot follows the conversation: when tool results make it outgrow the
            # context, the model is reloaded with more instead of the request failing.
            need = max(need, conversation_demand(progress.messages, progress.chars_per_token))
        try:
            lease = ctx.models.acquire(agent.model, need)
        except ModelLoadError as exc:
            raise AgentFailed(agent.id, str(exc)) from exc
        progress.instance_id = lease.instance_id
        progress.context_length = lease.context_length
        tool_chars = len(json.dumps(kwargs["tools"])) if kwargs.get("tools") else 0
        removed = fit_messages(progress.messages, lease.context_length, tool_chars, progress.chars_per_token)
        if removed:
            ctx.emit("node.trimmed", {"node": agent.id, "characters": removed, "context_length": lease.context_length})
        kwargs.setdefault("max_tokens", output_budget(ctx, agent, progress.messages, tool_chars))
        progress.budget = int(kwargs["max_tokens"])
        prompt_chars = sum(_message_chars(m) for m in progress.messages) + tool_chars
        live: LiveText | None = None
        if getattr(ctx.runtime, "supports_streaming", False) and ctx.emit_live is not None:
            live = LiveText(ctx, agent)
            feed = live.feed

            def on_delta(kind: str, text: str) -> None:
                # Stop is checked as the text arrives: raising here ends the stream, and with it
                # the generation. Otherwise a stopped run would first finish the answer it was on.
                ctx.check_cancelled()
                feed(kind, text)

            kwargs["on_delta"] = on_delta
        started = time.perf_counter()
        try:
            response = ctx.runtime.complete(progress.instance_id, progress.messages, temperature=agent.temperature, **kwargs)
        finally:
            if live is not None:
                live.flush()
    _usage(progress, response, time.perf_counter() - started, prompt_chars)
    return response


# The runtime could not read what the model wrote as a tool call or an answer. LM Studio says
# "The model produced output that does not match the expected peg-native format": seen with
# gpt-oss-20b in a Test Runner that had no tool for what it wanted to run, and possible whenever
# the arguments of a call are not valid JSON (a backslash in code, unescaped). Sending the same
# conversation again mostly gets the same turn back, so the model is told what went wrong.
_UNREADABLE = re.compile(
    r"does not match the expected .{0,40}format|failed to parse (the )?(tool|function)|invalid (tool|function) call|could not parse (the )?(tool|function)",
    re.IGNORECASE,
)
# Unreadable turns an agent is allowed before it stops using tools and concludes with what it has.
UNREADABLE_TURNS = 3


def unreadable_turn(error: Exception) -> bool:
    return bool(_UNREADABLE.search(str(error)))


def _unreadable_feedback(agent: AgentDef) -> str:
    names = ", ".join(t["function"]["name"] for t in tools.tool_schemas(agent.tools)) or "none"
    return (
        "Your last reply could not be read as a tool call or as an answer, so nothing was done. Call only these tools, "
        f"by their exact name: {names}, plus recall and remember when they are offered. Arguments are one JSON object: "
        "inside a string, write a backslash as \\\\ and a quote as \\\". If none of the tools does what you wanted, "
        "do without it and say so in your answer."
    )


_EXIT_CODE = re.compile(r"exit code (-?\d+)")
# With this many rounds of tool calls left, the agent is told. Only counts below its whole budget apply.
ROUNDS_LEFT_NOTICES = (5, 2)
# Tool rounds in which no call could be run are not charged to the agent's tool budget, this many times.
FREE_WASTED_ROUNDS = 3
# After its output was cut off, an agent asks for this much more context. Only an automatic slot grants it.
CUT_OFF_GROWTH = 1.5


def _ask_for_room(progress: AgentProgress) -> None:
    """After a turn that did not fit its output budget, make the next request ask for more context."""
    if progress.truncated and progress.budget < MAX_OUTPUT_TOKENS:
        progress.need_context = max(progress.need_context, int(progress.context_length * CUT_OFF_GROWTH))


def _full_context_retry(ctx: RunContext, agent: AgentDef, progress: AgentProgress) -> bool:
    """Whether a turn cut short by a full context is sent again without counting as an attempt.

    Such a turn did not use its output budget: the prompt was larger than estimated and the
    context ended first, a few dozen tokens in. ``_usage`` has measured the prompt meanwhile, so
    the next request is trimmed and budgeted for its real size. A conversation that keeps
    filling the context is charged after CONTEXT_FULL_TURNS of these.
    """
    if not progress.context_full or progress.context_full_turns >= CONTEXT_FULL_TURNS:
        return False
    progress.context_full_turns += 1
    ctx.emit("node.retry", {
        "node": agent.id, "attempt": progress.context_full_turns, "reason": "the context filled up before the turn ended",
        "explanation": "The conversation was larger than estimated. It is measured, shortened and sent again; this is not counted as an attempt.",
    })
    return True


def _cut_off_feedback(name: str, budget: int, chars_per_token: float = CHARS_PER_TOKEN) -> str:
    text = (
        "ERROR: this call was cut off before its arguments were complete, so nothing was done. "
        f"One turn holds about {budget} tokens, thinking included."
    )
    if name == "write_file":
        part = max(1000, int(budget * chars_per_token * 0.4))
        return (
            f"{text} Write the file in parts: write_file with the first part (at most about {part} characters), "
            "then write_file with append=true for each part after it. Keep your thinking short."
        )
    return f"{text} Send less in one call and keep your thinking short."


def checked_calls(
    calls: list[dict[str, Any]], round_no: int, cut_off: bool, budget: int, chars_per_token: float = CHARS_PER_TOKEN,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A model's tool calls as (what goes back into the conversation, what is run).

    The runtime renders earlier calls through the model's chat template, which parses
    their arguments again on every request. One call whose arguments are cut-off or
    malformed JSON therefore breaks every later request of the agent: LM Studio answers
    HTTP 500 "Internal Server Error". So the conversation only ever holds calls with an id
    and a JSON object as arguments, and a call that cannot be read is not run: it is
    answered with an error the model can act on.
    """
    echoed: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw = function.get("arguments")
        last_and_cut = cut_off and index == len(calls) - 1
        error = None
        try:
            if isinstance(raw, str):
                parsed = json.loads(raw) if raw.strip() else {}
            else:
                parsed = {} if raw is None else raw
            # A turn that ran out of room right after naming the tool leaves empty arguments behind.
            if not isinstance(parsed, dict) or (last_and_cut and not parsed):
                raise ValueError("not a JSON object")
            arguments = raw if isinstance(raw, str) and raw.strip() else json.dumps(parsed, ensure_ascii=False)
        except (ValueError, TypeError):
            arguments = "{}"
            error = _cut_off_feedback(name, budget, chars_per_token) if last_and_cut else (
                f"ERROR: the arguments for {name or 'this call'} were not a JSON object, so nothing was done. Call it again with valid JSON."
            )
        clean = {"id": str(call.get("id") or f"call_{round_no}_{index}"), "type": "function", "function": {"name": name, "arguments": arguments}}
        echoed.append(clean)
        queue.append({**clean, "error": error} if error else clean)
    return echoed, queue


def _run_tool_call(ctx: RunContext, agent: AgentDef, progress: AgentProgress, call: dict[str, Any]) -> str:
    function = call.get("function") or {}
    name = function.get("name") or ""
    if call.get("error"):
        spec = tools.TOOLS.get(name)
        ctx.emit(
            "tool.call",
            {"node": agent.id, "tool": name, "label": spec.label if spec else name or "tool", "arguments": {},
             "risk": tools.Risk.LOW.value, "reason": "The call could not be read, so nothing was run",
             "status": "error", "output": call["error"]},
        )
        return str(call["error"])
    raw_args = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
    except (json.JSONDecodeError, TypeError):
        return f"ERROR: arguments for {name} were not valid JSON"
    if ctx.brain is not None and ctx.brain.is_tool(name) and ctx.brain.has_tools(agent):
        return _run_memory_call(ctx, agent, name, arguments)
    if name not in agent.tools or name not in tools.TOOLS:
        return f"ERROR: tool '{name}' is not available to this agent"
    if progress.focused and name not in KEPT_WHEN_FOCUSED:
        # The model still calls what it did a moment ago, though it was not offered the tool: not run.
        return (f"ERROR: '{name}' is taken away until you change a file ({progress.idle_rounds} rounds without a change). "
                "Call write_file or edit_file now.")
    spec = tools.TOOLS[name]
    risk, reason = tools.classify(name, arguments, ctx.tool_ctx)
    event = {"node": agent.id, "tool": name, "label": spec.label, "arguments": arguments, "risk": risk.value, "reason": reason}

    if risk == tools.Risk.HIGH and ctx.require_approval:
        fingerprint = _fingerprint(ctx.run_id, agent.id, progress.approval_seq, name, arguments)
        decision = request_approval(
            ctx,
            {
                "kind": "tool",
                "node": agent.id,
                "fingerprint": fingerprint,
                "prompt": f"{agent.role} wants to run {spec.label}. {reason}.",
                "details": {"tool": name, "arguments": arguments, "risk": risk.value, "reason": reason},
            },
        )
        progress.approval_seq += 1
        if not decision.get("approved"):
            ctx.emit("tool.call", {**event, "status": "denied", "output": decision.get("comment") or "Denied by user"})
            return f"DENIED by the user. {decision.get('comment') or ''} Do not retry this action; continue without it.".strip()

    ctx.emit("tool.call", {**event, "status": "running"})
    started = time.perf_counter()
    try:
        output = tools.execute(name, arguments, ctx.tool_ctx)
        status = "success"
    except tools.ToolError as exc:
        output, status = f"ERROR: {exc}", "error"
    except Exception as exc:
        output, status = f"ERROR: {type(exc).__name__}: {exc}", "error"
    if name == "run_tests":
        # Kept for the verdict: a test report says what the suite said, whatever the agent concludes.
        code = _EXIT_CODE.match(output)
        progress.test_run = (int(code.group(1)) if code else -1, checks.suite_closing_line(output)[:160])
    ctx.emit(
        "tool.call",
        {**event, "status": status, "output": output, "duration": round(time.perf_counter() - started, 2)},
    )
    return output


def _run_memory_call(ctx: RunContext, agent: AgentDef, name: str, arguments: dict[str, Any]) -> str:
    """``recall`` or ``remember``: they read and write the project's memory, never the workspace."""
    event = {
        "node": agent.id, "tool": name, "label": memory_tools.TOOL_LABELS.get(name, name), "arguments": arguments,
        "risk": tools.Risk.LOW.value, "reason": "Reads or writes the project's memory only", "memory": True,
    }
    ctx.emit("tool.call", {**event, "status": "running"})
    started = time.perf_counter()
    try:
        output = ctx.brain.call(name, arguments, agent.role)
        status = "error" if output.startswith("ERROR:") else "success"
    except Exception as exc:
        output, status = f"ERROR: {type(exc).__name__}: {exc}", "error"
    ctx.emit("tool.call", {**event, "status": status, "output": output, "duration": round(time.perf_counter() - started, 2)})
    return output


# Tools that change a file. A round in which one of them worked is not an idle round.
WRITING_TOOLS = frozenset({"write_file", "edit_file", "delete_file"})
# The person's answer is news the agent has not acted on yet: a round that got one is not idle, and an agent that only
# has its writing tools keeps this one (the site cloner's first question comes after about ten rounds of looking).
ASKING_TOOLS = frozenset({"ask_user"})
KEPT_WHEN_FOCUSED = WRITING_TOOLS | ASKING_TOOLS
# Read-only tools whose answer, when it is the same as an earlier one still whole in the
# conversation, is not sent a second time. Seen on a 27B Developer at 57k tokens of context:
# the same 12,000 characters of the same file read three rounds in a row, then again and again.
# ``json_outline`` (the site cloner's view of a JSON file) went twelve times in a row on one file of the same Cloner.
REREAD_TOOLS = frozenset({"read_file", "list_dir", "json_outline"})
# After an agent is told it has gone max_idle_rounds without changing a file, this many more before its tools go.
IDLE_GRACE = 4
# Folders that fill with files nobody asked for (installed packages, build output, caches): never part of the stamp.
STAMP_SKIPS = frozenset({"node_modules", ".next", ".git", ".cache", "__pycache__", ".venv", "dist", "build", "target", ".turbo", ".tmp"})
STAMP_LIMIT = 100_000


def workspace_stamp(root: Path | None) -> tuple[int, int, int] | None:
    """(files, bytes, newest change) of the workspace without its installed packages and caches. None: no folder."""
    if root is None or not root.is_dir():
        return None
    files = size = newest = 0
    pending = [root]
    while pending and files < STAMP_LIMIT:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in STAMP_SKIPS:
                                pending.append(Path(entry.path))
                        else:
                            info = entry.stat(follow_symlinks=False)
                            files += 1
                            size += info.st_size
                            newest = max(newest, info.st_mtime_ns)
                    except OSError:
                        continue
        except OSError:
            continue
    return files, size, newest


def _call_key(name: str, call: dict[str, Any]) -> str:
    raw = (call.get("function") or {}).get("arguments") or "{}"
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
        return f"{name} {json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
    except (json.JSONDecodeError, TypeError):
        return f"{name} {raw}"


def _unrepeated(progress: AgentProgress, call: dict[str, Any], name: str, result: str) -> str:
    """``result``, or a short note when the identical call already gave it and that answer is still whole above."""
    if name not in REREAD_TOOLS or call.get("error") or result.startswith("ERROR"):
        return result
    key = _call_key(name, call)
    digest = hashlib.sha1(result.encode("utf-8", "replace")).hexdigest()
    known = progress.seen.get(key)
    if known is not None:
        round_no, index, earlier = known
        whole = index < len(progress.messages) and str(progress.messages[index].get("content") or "").startswith(result)
        if earlier == digest and whole:
            progress.repeats += 1
            return (f"Unchanged: this is exactly what your identical call in round {round_no} returned, and that answer is "
                    "still above in this conversation. Do not call it again: use it, and change the files.")
    # The message about to be appended holds this answer.
    progress.seen[key] = (progress.rounds, len(progress.messages), digest)
    return result


def _idle_note(agent: AgentDef, rounds: int) -> str:
    how = "edit_file for a small change, write_file for a whole file" if "edit_file" in agent.tools else "write_file"
    return (f"[{rounds} rounds in a row without changing any file. Stop reading: what you need is in this conversation. "
            f"Make your changes now ({how}), then give your final answer. After {IDLE_GRACE} more rounds without "
            "a change your tools are taken away.]")


def _focus_note(agent: AgentDef, rounds: int) -> str:
    how = "write_file for a whole file, edit_file for a small change" if "edit_file" in agent.tools else "write_file"
    hint = f" {agent.focus_hint}" if agent.focus_hint else ""
    return (f"[{rounds} rounds in a row without changing any file. What you need is in this conversation: your reading tools "
            f"are taken away until you change a file. Write now ({how}).{hint} Do not wait to know everything: write what "
            "you know, and correct it after.]")


def _watch_idle(ctx: RunContext, agent: AgentDef, progress: AgentProgress, changed: bool) -> bool:
    """Count the rounds in which no file changed. Told once, then the tool loop ends. True = stop using tools.

    An agent with ``focus_after_idle_rounds`` keeps only its writing tools from that round until it changes a file.
    Only on a fix, or after the agent's first change: reading before the first file of a build is work. Not for an
    agent that watches the workspace (the site cloner): its scripts change files while they work, so a long run of
    rounds in which nothing changed, from the first, is a loop.
    """
    if changed:
        progress.idle_rounds = 0
        progress.changes += 1
        progress.focused = False
        return False
    if not (agent.max_idle_rounds or agent.focus_after_idle_rounds):
        return False
    if not (progress.revising or progress.changes or agent.idle_watches_workspace):
        return False
    progress.idle_rounds += 1
    # The tools go IDLE_GRACE rounds after the last warning: the note, or the reading tools taken away, whichever comes later.
    if progress.idle_rounds >= max(agent.max_idle_rounds or 0, agent.focus_after_idle_rounds or 0) + IDLE_GRACE:
        ctx.emit("node.stalled", {"node": agent.id, "rounds": progress.idle_rounds, "stopped": True})
        progress.messages.append({
            "role": "user",
            "content": f"No file was changed in the last {progress.idle_rounds} rounds, so your tools are taken away. "
            "Conclude now: say what you changed and what is still different.",
        })
        return True
    last = progress.messages[-1]
    if agent.focus_after_idle_rounds and progress.idle_rounds >= agent.focus_after_idle_rounds and not progress.focused:
        progress.focused = True
        progress.focuses += 1
        ctx.emit("node.stalled", {"node": agent.id, "rounds": progress.idle_rounds, "stopped": False, "focused": True})
        progress.messages[-1] = {**last, "content": f"{last.get('content') or ''}\n\n{_focus_note(agent, progress.idle_rounds)}"}
    elif agent.max_idle_rounds and progress.idle_rounds >= agent.max_idle_rounds and not progress.told_idle:
        progress.told_idle = True
        ctx.emit("node.stalled", {"node": agent.id, "rounds": progress.idle_rounds, "stopped": False})
        if not progress.focused:
            progress.messages[-1] = {**last, "content": f"{last.get('content') or ''}\n\n{_idle_note(agent, progress.idle_rounds)}"}
    return False


def _tool_loop(ctx: RunContext, agent: AgentDef, progress: AgentProgress) -> None:
    tool_schemas = tools.tool_schemas(agent.tools)
    if ctx.brain is not None:
        # Given by the engine to every agent that works with tools, whatever the workflow lists.
        tool_schemas = [*tool_schemas, *ctx.brain.tool_schemas(agent)]
    writing_schemas = [schema for schema in tool_schemas if (schema.get("function") or {}).get("name") in KEPT_WHEN_FOCUSED]
    budget = progress.max_rounds or agent.max_tool_rounds
    while True:
        ran = bool(progress.queue)
        changed = False
        stamp = workspace_stamp(ctx.tool_ctx.workspace) if ran and agent.idle_watches_workspace else None
        while progress.queue:
            call = progress.queue[0]
            result = _run_tool_call(ctx, agent, progress, call)
            name = str((call.get("function") or {}).get("name") or "")
            if name in KEPT_WHEN_FOCUSED and not call.get("error") and not result.startswith(("ERROR", "DENIED")):
                changed = True
            result = _unrepeated(progress, call, name, result)
            progress.messages.append({"role": "tool", "tool_call_id": call.get("id") or "call", "content": result})
            progress.queue.pop(0)
        if stamp is not None and not changed:
            changed = workspace_stamp(ctx.tool_ctx.workspace) != stamp
        left = budget - progress.rounds
        if ran and left in ROUNDS_LEFT_NOTICES and left < budget and left not in progress.told_rounds_left:
            progress.told_rounds_left.add(left)
            last = progress.messages[-1]
            progress.messages[-1] = {
                **last,
                "content": f"{last.get('content') or ''}\n\n[{left} rounds of tool calls left. Write now whatever the work "
                "still lacks: what is not in a file does not exist. Then give your final answer.]",
            }
        if ran and _watch_idle(ctx, agent, progress, changed):
            return
        if progress.rounds >= budget:
            progress.messages.append(
                {"role": "user", "content": "Tool budget used up. Do not call more tools; conclude with what you have."}
            )
            return
        try:
            response = _complete(ctx, progress, agent, tools=writing_schemas if progress.focused and writing_schemas else tool_schemas)
        except (RunCancelled, GraphInterrupt):
            raise
        except Exception as exc:
            if unreadable_turn(exc):
                progress.unreadable += 1
                ctx.emit("node.retry", {
                    "node": agent.id, "attempt": progress.unreadable, "reason": str(exc),
                    "explanation": "The runtime could not read the model's turn as a tool call or an answer. The model is told so.",
                })
                if progress.unreadable >= UNREADABLE_TURNS:
                    # The tools are what it cannot use: it answers without them, with what it found so far.
                    progress.messages.append({
                        "role": "user",
                        "content": "Your replies could not be read several times. Stop using tools now and give your final "
                        "answer from what you have already found; say what you could not check.",
                    })
                    return
                feedback = {"role": "user", "content": _unreadable_feedback(agent)}
                if progress.messages[-1].get("role") == "user" and str(progress.messages[-1].get("content", "")).startswith("Your last reply could not be read"):
                    progress.messages[-1] = feedback
                else:
                    progress.messages.append(feedback)
                continue
            # A failed model call is retried within the agent's retry budget.
            progress.attempts += 1
            ctx.emit("node.retry", {"node": agent.id, "attempt": progress.attempts, "reason": str(exc)})
            if progress.attempts > agent.max_retries:
                raise AgentFailed(agent.id, f"model call failed after {progress.attempts} attempts ({exc})") from exc
            continue
        message = (response.get("choices") or [{}])[0].get("message") or {}
        calls = message.get("tool_calls") or []
        progress.rounds += 1
        _ask_for_room(progress)
        if not calls:
            if progress.truncated:
                if _full_context_retry(ctx, agent, progress):
                    progress.rounds -= 1  # nothing the agent did: the round is not charged either
                    continue
                # Thinking used the whole budget. Leave the cut-off text out and say so: the
                # agent goes on with its tools while it has retries left, then has to answer.
                # These turns have their own count: seen on a 27B Developer, three of them used
                # up the shared attempts and the run failed without its final answer ever asked for.
                progress.truncations += 1
                ctx.emit(
                    "node.retry",
                    {"node": agent.id, "attempt": progress.truncations, "reason": "ran out of output tokens while reasoning"},
                )
                if progress.truncations <= agent.max_retries:
                    progress.messages.append({"role": "user", "content": TOOL_TRUNCATED_FEEDBACK})
                    continue
                progress.messages.append(
                    {"role": "user", "content": TEXT_TRUNCATED_FEEDBACK if agent.output_schema == schemas.TEXT else TRUNCATED_FEEDBACK}
                )
            elif message.get("content"):
                progress.messages.append({"role": "assistant", "content": message["content"]})
            return
        echoed, queue = checked_calls(calls, progress.rounds, progress.truncated, progress.budget, progress.chars_per_token)
        if all(call.get("error") for call in queue) and progress.wasted_rounds < FREE_WASTED_ROUNDS:
            # Nothing could be run: the agent keeps this round of its tool budget.
            progress.wasted_rounds += 1
            progress.rounds -= 1
        progress.messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": echoed})
        progress.queue = queue


def _final_text(ctx: RunContext, agent: AgentDef, progress: AgentProgress) -> dict[str, Any]:
    """The answer of an agent that replies in its own words: its last message, or one more turn to write it."""
    last = progress.messages[-1]
    if agent.tools and last.get("role") == "assistant" and not last.get("tool_calls"):
        # The turn that ended the tool loop is the answer.
        text = _THINK.sub("", str(last.get("content") or "")).strip()
        if text:
            return {"text": text}
    last_error = "the model gave no answer"
    # The answer's own attempts: whatever the tool loop went through, it is asked for.
    tries = 0
    while tries <= agent.max_retries:
        tries += 1
        progress.attempts += 1
        try:
            response = _complete(ctx, progress, agent)
        except (RunCancelled, GraphInterrupt):
            raise
        except Exception as exc:
            last_error = str(exc)
            ctx.emit("node.retry", {"node": agent.id, "attempt": tries, "reason": last_error})
            continue
        content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        text = _THINK.sub("", str(content)).strip()
        if text:
            return {"text": text}
        if _full_context_retry(ctx, agent, progress):
            tries -= 1
            continue
        last_error = "ran out of output tokens while reasoning" if progress.truncated else "the model gave no answer"
        ctx.emit("node.retry", {"node": agent.id, "attempt": tries, "reason": last_error})
        _ask_for_room(progress)
        progress.messages.append({"role": "user", "content": "You gave no answer. Keep your reasoning to a few sentences and answer now."})
    raise AgentFailed(agent.id, f"no answer after {tries} attempts ({last_error})")


def _final_answer(ctx: RunContext, agent: AgentDef, progress: AgentProgress) -> dict[str, Any]:
    """Ask for the structured answer, retrying with the validation error as feedback."""
    if agent.output_schema == schemas.TEXT:
        return _final_text(ctx, agent, progress)
    if agent.tools:
        # The turn that ended the tool loop often already is the answer: use it and save a call.
        last = progress.messages[-1]
        if last.get("role") == "assistant" and not last.get("tool_calls"):
            try:
                return parse_structured(str(last.get("content") or ""), agent.output_schema, agent.choices)
            except (ValueError, json.JSONDecodeError):
                pass
        progress.messages.append(
            {"role": "user", "content": "Now give your final answer as the JSON object described in your instructions."}
        )
    response_format: dict[str, Any] | None = schemas.response_format(agent.output_schema, agent.choices)
    last_error = "unknown error"
    # The answer's own attempts, as in ``_final_text``.
    tries = 0
    while tries <= agent.max_retries:
        tries += 1
        progress.attempts += 1
        try:
            try:
                response = _complete(ctx, progress, agent, response_format=response_format)
            except Exception as exc:
                # Some models or engines reject JSON-schema output; fall back to prompt-only JSON.
                if response_format is not None and "response_format" in str(exc).lower():
                    response_format = None
                    tries -= 1
                    progress.attempts -= 1
                    continue
                raise
            content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return parse_structured(content, agent.output_schema, agent.choices)
        except (RunCancelled, GraphInterrupt):
            raise
        except (ValueError, json.JSONDecodeError) as exc:
            if _full_context_retry(ctx, agent, progress):
                tries -= 1
                continue
            if progress.truncated:
                last_error = "ran out of output tokens while reasoning"
                feedback = TRUNCATED_FEEDBACK
                _ask_for_room(progress)
            else:
                last_error = str(exc)
                feedback = f"Your answer could not be used: {last_error}. Reply again with only the JSON object."
            ctx.emit("node.retry", {"node": agent.id, "attempt": tries, "reason": last_error})
            progress.messages.append({"role": "user", "content": feedback})
        except Exception as exc:
            last_error = str(exc)
            ctx.emit("node.retry", {"node": agent.id, "attempt": tries, "reason": last_error})
    raise AgentFailed(agent.id, f"no valid answer after {tries} attempts ({last_error})")


def _project_search(ctx: RunContext, agent: AgentDef, state: WorkflowState) -> tuple[Any, bool]:
    """(what the project's memory holds for this agent's task, whether to use it at all)."""
    try:
        return ctx.brain.find(agent, dict(state)), True
    except Exception as exc:
        # The memory is a help, not a dependency: an agent works without it.
        ctx.emit("memory.recalled", {"node": agent.id, "role": agent.role, "items": [], "characters": 0, "error": f"{type(exc).__name__}: {exc}"})
        return None, False


def _project_memory(ctx: RunContext, agent: AgentDef, state: WorkflowState, context_tokens: int, found: Any) -> tuple[str, list[dict[str, Any]]]:
    """The memory section of this agent's prompt, sized for the context its model got. Empty when there is nothing."""
    try:
        return ctx.brain.context_for(agent, dict(state), context_tokens, found)
    except Exception as exc:
        ctx.emit("memory.recalled", {"node": agent.id, "role": agent.role, "items": [], "characters": 0, "error": f"{type(exc).__name__}: {exc}"})
        return "", []


def run_agent(ctx: RunContext, agent: AgentDef, state: WorkflowState) -> dict[str, Any]:
    ctx.check_cancelled()
    progress = ctx.pending.get(agent.id)
    if progress is None:
        slot = ctx.definition.models.get(agent.model)
        ctx.emit(
            "node.started",
            {"node": agent.id, "role": agent.role, "model": slot.model_key if slot else "", "slot": agent.model},
        )
        demand = prompt_demand(agent, state, ctx.definition)
        remembers = ctx.brain is not None and ctx.brain.applies_to(agent)
        found: Any = None
        if remembers:
            demand += memory_tools.CONTEXT_ALLOWANCE
            # Before the agent's model is loaded: the memory's embedding model then finds the card as
            # free as it will be, and the gateway unloads it if the agent's model needs the room.
            found, remembers = _project_search(ctx, agent, state)
        if agent.tools and slot is not None and slot.auto_tune:
            # An agent that calls tools carries every call and result with it. It starts with
            # room to work in, not with what its prompt alone needs: seen with 7168 tokens, a
            # Developer had its own first file cut out of its conversation and read it back.
            demand = max(demand, AUTO_WORKING_NEED)
        try:
            with ctx.llm_lock:
                lease = ctx.models.acquire(agent.model, demand)
        except ModelLoadError as exc:
            raise AgentFailed(agent.id, str(exc)) from exc
        memory, guide = "", ""
        read = read_receipt(agent, state)
        if remembers:
            memory, recalled = _project_memory(ctx, agent, state, lease.context_length, found)
            guide = memory_tools.GUIDE if ctx.brain.has_tools(agent) else ""
            if memory:
                # On the record like everything else this agent was shown.
                read["project_memory"] = checks.digest(memory)
                ctx.emit("memory.recalled", {"node": agent.id, "role": agent.role, "items": recalled, "characters": len(memory), "sha256": read["project_memory"]})
        progress = AgentProgress(
            messages=build_messages(agent, state, ctx.definition, lease.context_length, memory=memory, memory_guide=guide),
            instance_id=lease.instance_id,
            context_length=lease.context_length,
            # Later calls of this agent need the context its prompt was built for, no more.
            need_context=min(demand, lease.context_length),
            read=read,
            max_rounds=tool_budget(agent, state),
            revising=agent.id in (state.get("outputs") or {}),
        )
        ctx.pending[agent.id] = progress
    started = time.perf_counter()
    try:
        if agent.tools:
            _tool_loop(ctx, agent, progress)
        output = _final_answer(ctx, agent, progress)
    except GraphInterrupt:
        raise  # Paused for approval: keep the progress so the node resumes where it stopped.
    except BaseException:
        ctx.pending.pop(agent.id, None)
        ctx.models.release(agent.model)
        raise
    ctx.pending.pop(agent.id, None)
    ctx.models.release(agent.model)
    # Ids of claims and propositions come from their content, never from the model.
    output = checks.stamp_ids(output)
    if agent.output_schema == "TestReport" and progress.test_run is not None:
        output = checks.bind_test_verdict(output, *progress.test_run)
    ctx.receipts[agent.id] = {"read": progress.read, "model": progress.instance_id}
    tps = progress.output_tokens / progress.generation_seconds if progress.generation_seconds else None
    ctx.emit(
        "node.completed",
        {
            "node": agent.id,
            "role": agent.role,
            "output": output,
            "model": progress.instance_id,
            "input_tokens": progress.input_tokens,
            "output_tokens": progress.output_tokens,
            "reasoning_tokens": progress.reasoning_tokens,
            "tokens_per_second": round(tps, 1) if tps else None,
            "seconds": round(time.perf_counter() - started, 2),
            "attempts": progress.attempts,
            "sha256": checks.digest(output),
            "read": progress.read,
            "reasoning": progress.reasoning,
            "context_length": progress.context_length,
        },
    )
    return output


def _escalate(ctx: RunContext, agent: AgentDef, error: AgentFailed, state: WorkflowState) -> dict[str, Any]:
    """Apply the agent's escalation policy after it has run out of retries."""
    ctx.emit("node.failed", {"node": agent.id, "role": agent.role, "error": str(error), "escalation": agent.escalation.value})
    if agent.escalation == Escalation.CONTINUE:
        return {"summary": f"{agent.role} could not complete its step: {error}", "error": str(error)}
    if agent.escalation == Escalation.HUMAN:
        attempt = len([h for h in state.get("history") or [] if h == agent.id])
        decision = request_approval(
            ctx,
            {
                "kind": "escalation",
                "node": agent.id,
                "fingerprint": _fingerprint(ctx.run_id, agent.id, "escalation", attempt),
                "prompt": f"{agent.role} failed: {error}. Approve to continue without this step, or reject to stop the run.",
                "details": {"error": str(error)},
            },
        )
        if decision.get("approved"):
            return {"summary": f"{agent.role} was skipped after failing: {error}", "error": str(error)}
    raise error


# -- graph construction -------------------------------------------------------


def _agent_node(ctx: RunContext, agent: AgentDef) -> Callable[[WorkflowState], dict[str, Any]]:
    def node(state: WorkflowState) -> dict[str, Any]:
        ctx.receipts.pop(agent.id, None)
        try:
            output = run_agent(ctx, agent, state)
        except AgentFailed as exc:
            output = _escalate(ctx, agent, exc, state)
        receipt = ctx.receipts.get(agent.id) or {}
        entry = {
            "node": agent.id, "kind": "agent", "sha256": checks.digest(output),
            "read": receipt.get("read") or {}, "model": receipt.get("model", ""),
        }
        update: dict[str, Any] = {"outputs": {agent.id: output}, "history": [agent.id], "last_node": agent.id, "ledger": [entry]}
        if not any(len(e.sources) == 1 for e in ctx.definition.outgoing(agent.id)):
            # This node only feeds a join edge, which LangGraph follows on its own.
            return update
        outputs = {**(state.get("outputs") or {}), agent.id: output}
        target, loops, status = decide_next(ctx.definition, agent.id, outputs, state.get("loop_counts") or {})
        update.update({"routes": {agent.id: target}, "loop_counts": loops})
        if status:
            update["status"] = status
        return update

    return node


def slot_completion(ctx: RunContext) -> Callable[..., dict[str, Any]]:
    """A check's way to ask one of the workflow's models, through the same gateway as an agent."""

    def complete(slot_id: str, messages: list[dict[str, Any]], *, need_context: int = 8192, **options: Any) -> dict[str, Any]:
        ctx.check_cancelled()
        with ctx.llm_lock:
            try:
                lease = ctx.models.acquire(slot_id, need_context)
            except ModelLoadError as exc:
                raise RuntimeError(str(exc)) from exc
            options.setdefault("temperature", 0.0)
            options.setdefault("max_tokens", 700)
            return ctx.runtime.complete(lease.instance_id, messages, **options)

    return complete


def _check_node(ctx: RunContext, check: CheckDef) -> Callable[[WorkflowState], dict[str, Any]]:
    """A deterministic step. No model is loaded or called."""
    spec = checks.CHECKS.get(check.kind)
    label = check.label or (spec.label if spec else check.id)

    def node(state: WorkflowState) -> dict[str, Any]:
        ctx.check_cancelled()
        ctx.emit("node.started", {"node": check.id, "role": label, "kind": "check", "check": check.kind, "model": "", "slot": ""})
        started = time.perf_counter()
        outputs = state.get("outputs") or {}
        try:
            output = checks.run_check(
                check,
                checks.CheckContext(
                    task=str(state.get("task", "")), outputs=outputs, ledger=list(state.get("ledger") or []),
                    history=list(state.get("history") or []), definition=ctx.definition, evidence_dir=ctx.evidence_dir,
                    run_id=ctx.run_id, workspace=ctx.tool_ctx.workspace, created=ctx.tool_ctx.created, emit=ctx.emit,
                    cancelled=ctx.cancelled.is_set, complete=slot_completion(ctx),
                ),
            )
        except RunCancelled:
            raise
        except Exception as exc:
            # A check that cannot run has verified nothing: it fails, it does not wave things through.
            output = {"passed": False, "verdict": "CHECK_ERROR", "summary": f"{label} could not run: {type(exc).__name__}: {exc}"}
        read = {ref: checks.digest(outputs[ref]) for ref in checks.node_refs(check) if ref in outputs}
        sha = checks.digest(output)
        ctx.emit(
            "node.completed",
            {
                "node": check.id, "role": label, "kind": "check", "check": check.kind,
                # The evidence text is in the run's result; the event carries its list of sources.
                "output": {k: v for k, v in output.items() if k != "documents"},
                "sha256": sha, "read": read, "seconds": round(time.perf_counter() - started, 3),
            },
        )
        update: dict[str, Any] = {
            "outputs": {check.id: output}, "history": [check.id], "last_node": check.id,
            "ledger": [{"node": check.id, "kind": "check", "sha256": sha, "read": read, "model": ""}],
        }
        if not any(len(e.sources) == 1 for e in ctx.definition.outgoing(check.id)):
            return update
        target, loops, status = decide_next(ctx.definition, check.id, {**outputs, check.id: output}, state.get("loop_counts") or {})
        update.update({"routes": {check.id: target}, "loop_counts": loops})
        if target == END and output.get("passed") is False:
            # The run ends here because the check did not pass, not because the work was done.
            status = "blocked"
        if status:
            update["status"] = status
        return update

    return node


def _approval_node(ctx: RunContext, approval: ApprovalDef) -> Callable[[WorkflowState], dict[str, Any]]:
    def node(state: WorkflowState) -> dict[str, Any]:
        ctx.check_cancelled()
        outputs = state.get("outputs") or {}
        rejections = (state.get("loop_counts") or {}).get(f"{approval.id}->reject", 0)
        shown = approval.show or (state.get("last_node") or "")
        decision = request_approval(
            ctx,
            {
                "kind": "gate",
                "node": approval.id,
                "fingerprint": _fingerprint(ctx.run_id, approval.id, rejections, len(state.get("history") or [])),
                "prompt": approval.prompt,
                "details": {"shows": shown, "output": outputs.get(shown)},
            },
        )
        update: dict[str, Any] = {
            "approvals": {approval.id: {"approved": bool(decision.get("approved")), "comment": decision.get("comment", "")}},
            "history": [approval.id],
        }
        if decision.get("approved"):
            target, loops, status = decide_next(ctx.definition, approval.id, outputs, state.get("loop_counts") or {})
            update.update({"routes": {approval.id: target}, "loop_counts": loops})
            if status:
                update["status"] = status
            return update
        if approval.on_reject != END and rejections < approval.max_rejections:
            update.update(
                {"routes": {approval.id: approval.on_reject}, "loop_counts": {f"{approval.id}->reject": rejections + 1}}
            )
            return update
        update.update({"routes": {approval.id: END}, "status": "rejected"})
        return update

    return node


def _lg(name: str) -> str:
    return LG_END if name == END else LG_START if name == START else name


def _router(node_id: str) -> Callable[[WorkflowState], Any]:
    def route(state: WorkflowState) -> Any:
        target = (state.get("routes") or {}).get(node_id, END)
        if isinstance(target, list):
            return [_lg(t) for t in target]
        return _lg(target)

    return route


def compile_workflow(definition: WorkflowDef, ctx: RunContext, checkpointer: Any = None) -> Any:
    """Build and compile the LangGraph for ``definition``."""
    builder = StateGraph(WorkflowState)
    for agent in definition.agents:
        builder.add_node(agent.id, _agent_node(ctx, agent))
    for check in definition.checks:
        builder.add_node(check.id, _check_node(ctx, check))
    for approval in definition.human_approval:
        builder.add_node(approval.id, _approval_node(ctx, approval))

    join_only: set[str] = set()
    for edge in definition.edges:
        if len(edge.sources) > 1:
            builder.add_edge([_lg(s) for s in edge.sources], _lg(edge.target))
            join_only.update(edge.sources)
        elif edge.sources[0] == START:
            builder.add_edge(LG_START, _lg(edge.target))

    for node_id in definition.node_ids:
        targets = {e.target for e in definition.outgoing(node_id) if len(e.sources) == 1}
        targets |= {e.on_exhausted for e in definition.outgoing(node_id) if e.on_exhausted}
        approval = definition.approval(node_id)
        if approval:
            targets.add(approval.on_reject)
        if node_id in join_only and not targets:
            continue
        targets.add(END)
        builder.add_conditional_edges(node_id, _router(node_id), {_lg(t): _lg(t) for t in targets if t})
    return builder.compile(checkpointer=checkpointer)
