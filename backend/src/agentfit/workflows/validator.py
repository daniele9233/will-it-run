"""Static validation of a workflow definition before it is compiled or saved."""

from __future__ import annotations

from collections import deque

from pydantic import BaseModel, Field

from agentfit.workflows import checks as check_mod
from agentfit.workflows import conditions, schemas
from agentfit.workflows.dsl import END, START, WorkflowDef
from agentfit.workflows.schemas import SCHEMAS
from agentfit.workflows.tools import TOOLS


class Issue(BaseModel):
    level: str  # "error" or "warning"
    message: str
    node: str | None = None


class ValidationResult(BaseModel):
    ok: bool
    issues: list[Issue] = Field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]


def _reachable(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        for nxt in adjacency.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _has_unbounded_cycle(definition: WorkflowDef) -> list[str]:
    """Find cycles made only of edges without a loop bound: those could run forever."""
    adjacency: dict[str, set[str]] = {}
    for edge in definition.edges:
        if edge.max_loops is not None:
            continue
        for src in edge.sources:
            adjacency.setdefault(src, set()).add(edge.target)
    # Approval rejection paths are bounded by max_rejections, so they are left out here.
    color: dict[str, int] = {}
    cycle: list[str] = []

    def visit(node: str, stack: list[str]) -> bool:
        color[node] = 1
        for nxt in adjacency.get(node, ()):
            if color.get(nxt) == 1:
                cycle.extend([*stack[stack.index(nxt):], nxt] if nxt in stack else [node, nxt])
                return True
            if color.get(nxt, 0) == 0 and visit(nxt, [*stack, nxt]):
                return True
        color[node] = 2
        return False

    for node in list(adjacency):
        if color.get(node, 0) == 0 and visit(node, [node]):
            return cycle
    return []


def validate(definition: WorkflowDef, *, require_models: bool = False) -> ValidationResult:
    """Check structure, references, conditions and loop bounds.

    ``require_models`` additionally demands that every model slot has a model assigned,
    which is needed to run a workflow but not to save a template.
    """
    issues: list[Issue] = []

    def error(message: str, node: str | None = None) -> None:
        issues.append(Issue(level="error", message=message, node=node))

    def warn(message: str, node: str | None = None) -> None:
        issues.append(Issue(level="warning", message=message, node=node))

    ids = definition.node_ids
    duplicates = {i for i in ids if ids.count(i) > 1}
    for dup in sorted(duplicates):
        error(f"Duplicate node id '{dup}'.", dup)
    nodes = set(ids)
    if not definition.agents:
        error("A workflow needs at least one agent.")

    for agent in definition.agents:
        if agent.model not in definition.models:
            error(f"Agent '{agent.id}' uses model slot '{agent.model}', which is not defined.", agent.id)
        if agent.output_schema not in SCHEMAS:
            error(f"Agent '{agent.id}' has unknown output schema '{agent.output_schema}'.", agent.id)
        for tool in agent.tools:
            if tool not in TOOLS:
                error(f"Agent '{agent.id}' uses unknown tool '{tool}'.", agent.id)
        if len(agent.system_prompt.strip()) < 40:
            warn(f"Agent '{agent.id}' has a very short system prompt; role behaviour will be weak.", agent.id)
        if not agent.goal:
            warn(f"Agent '{agent.id}' has no goal.", agent.id)
        if agent.max_retries < 0 or agent.max_retries > 10:
            error(f"Agent '{agent.id}': max_retries must be between 0 and 10.", agent.id)
        for entry in agent.reads or []:
            for source in entry.partition(".")[0].split("|"):
                if source not in nodes:
                    error(f"Agent '{agent.id}' reads '{entry}', but there is no node '{source}'.", agent.id)
                elif source == agent.id:
                    error(f"Agent '{agent.id}' lists itself in what it reads.", agent.id)
        if agent.max_input_tokens is not None and agent.max_input_tokens < 256:
            error(f"Agent '{agent.id}': the input budget must be at least 256 tokens.", agent.id)
        allowed_fields = schemas.choice_fields(agent.output_schema)
        for field_name, values in agent.choices.items():
            if field_name not in allowed_fields:
                error(f"Agent '{agent.id}' restricts '{field_name}', which is not a text field of {agent.output_schema}.", agent.id)
            elif not values:
                error(f"Agent '{agent.id}' restricts '{field_name}' to an empty list.", agent.id)

    for check in definition.checks:
        if check.kind not in check_mod.CHECKS:
            error(f"Check '{check.id}' has unknown kind '{check.kind}'.", check.id)
            continue
        for ref in check_mod.node_refs(check):
            if ref not in nodes:
                error(f"Check '{check.id}' points at node '{ref}', which does not exist.", check.id)

    for slot_id, slot in definition.models.items():
        if not any(a.model == slot_id for a in definition.agents):
            warn(f"Model slot '{slot_id}' is not used by any agent.")
        if require_models and not slot.model_key:
            error(f"Model slot '{slot_id}' has no model assigned. Pick a model before running.")

    for approval in definition.human_approval:
        if approval.show and approval.show not in nodes:
            error(f"Approval '{approval.id}' shows output of unknown node '{approval.show}'.", approval.id)
        if approval.on_reject != END and approval.on_reject not in nodes:
            error(f"Approval '{approval.id}' rejects to unknown node '{approval.on_reject}'.", approval.id)

    adjacency: dict[str, set[str]] = {}
    has_start = False
    for edge in definition.edges:
        for src in edge.sources:
            if src == START:
                has_start = True
            elif src not in nodes:
                error(f"Edge starts at unknown node '{src}'.")
            if src == END:
                error("An edge cannot start at END.")
            adjacency.setdefault(src, set()).add(edge.target)
        if edge.target != END and edge.target not in nodes:
            error(f"Edge points to unknown node '{edge.target}'.")
        if edge.target == START:
            error("An edge cannot point to START.")
        if edge.on_exhausted and edge.on_exhausted != END and edge.on_exhausted not in nodes:
            error(f"Edge on_exhausted points to unknown node '{edge.on_exhausted}'.")
        if edge.max_loops is not None and edge.max_loops < 1:
            error("max_loops must be at least 1.")
        if len(edge.sources) > 1 and edge.when:
            error("A join edge (several sources) cannot carry a condition.")
        expression = definition.resolve_condition(edge.when)
        if expression:
            problem = conditions.check_syntax(expression)
            if problem:
                error(f"Invalid condition '{edge.when}': {problem}", edge.sources[0])
    for approval in definition.human_approval:
        if approval.on_reject != END:
            adjacency.setdefault(approval.id, set()).add(approval.on_reject)

    if not has_start:
        error("No edge leaves START: the workflow has no entry point.")
    else:
        reachable = _reachable(START, adjacency)
        for node in sorted(nodes - reachable):
            error(f"Node '{node}' cannot be reached from START.", node)
        if END not in reachable:
            error("END is not reachable: the workflow can never finish.")

    join_sources = {src for e in definition.edges if len(e.sources) > 1 for src in e.sources}
    for node in sorted(join_sources & nodes):
        if any(len(e.sources) == 1 for e in definition.outgoing(node)):
            error(f"Node '{node}' feeds a join and also has its own outgoing edge; use one or the other.", node)

    for node in sorted(nodes):
        outgoing = definition.outgoing(node)
        if not outgoing:
            error(f"Node '{node}' has no outgoing edge. Connect it to another node or to END.", node)
            continue
        conditional = [e for e in outgoing if e.when]
        unconditional = [e for e in outgoing if not e.when]
        if conditional and not unconditional and len(conditional) == 1:
            warn(f"Node '{node}' has a single conditional edge and no fallback; the run ends if it is false.", node)

    cycle = _has_unbounded_cycle(definition)
    if cycle:
        error(
            "Unbounded loop: " + " -> ".join(cycle) + ". Give the edge that goes back a max_loops value.",
            cycle[0],
        )
    if definition.workflow.max_steps < 2 or definition.workflow.max_steps > 500:
        error("workflow.max_steps must be between 2 and 500.")

    return ValidationResult(ok=not any(i.level == "error" for i in issues), issues=issues)
