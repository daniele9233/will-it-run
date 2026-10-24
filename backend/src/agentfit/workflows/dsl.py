"""Workflow DSL: the declarative definition a workflow is authored in.

    workflow.yaml -> validator -> compiler -> LangGraph

The definition is independent of the runtime. It names agents, the model slot each
uses, human approval gates, edges with conditions and loop bounds, and the resource plan.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

START = "START"
END = "END"

# Rounds of tool calls an agent gets each time it runs. One round is one answer of the
# model, which may hold several calls.
DEFAULT_TOOL_ROUNDS = 30
# What every workflow saved before 2026-10-13 carries. No screen let anyone choose it, and
# it was too few: a Developer was out of rounds after three files, before it could test or
# fix them. A saved 6 is therefore read as "the default".
LEGACY_TOOL_ROUNDS = 6


class LoadingPolicy(str, Enum):
    KEEP_LOADED = "keep_loaded"
    LOAD_ON_DEMAND = "load_on_demand"
    UNLOAD_AFTER_USE = "unload_after_use"
    SHARED_INSTANCE = "shared_instance"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class Escalation(str, Enum):
    FAIL = "fail"
    HUMAN = "human"
    CONTINUE = "continue"


class WorkflowMeta(BaseModel):
    id: str
    name: str
    description: str = ""
    version: int = 1
    max_steps: int = 40
    specializations: list[str] = Field(default_factory=list)
    # "team": roles that build on each other. "verification": roles that check each other,
    # with deterministic checks between them.
    category: str = "team"


class ModelSlot(BaseModel):
    """A model instance several agents can share."""

    model_config = ConfigDict(protected_namespaces=())

    label: str = ""
    # What the agents on this slot need from a model: reasoning, coding, writing or vision.
    # Empty for the classic slots, whose id already says it.
    kind: str = ""
    # LM Studio model key. Empty in templates until the team builder assigns a model.
    model_key: str = ""
    repo: str | None = None
    quant: str | None = None
    context_length: int = 8192
    loading_policy: LoadingPolicy = LoadingPolicy.LOAD_ON_DEMAND
    # Automatic: the gateway picks the context for each request (enough for it, never more
    # than the free VRAM holds) and decides by itself what stays loaded. ``context_length``
    # and ``loading_policy`` are then ignored.
    auto_tune: bool = False
    vram_gb: float | None = None
    purpose: str = ""
    # Optional stand-in for requests that do not fit ``context_length``: usually the same
    # model at a smaller quantization, whose saved memory becomes context.
    wide_model_key: str = ""
    wide_context_length: int | None = None


class AgentDef(BaseModel):
    id: str
    role: str
    goal: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    model: str = Field(description="Key into the workflow's models section")
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    input: str = Field(default="", description="What this agent receives")
    output_schema: str = "GenericOutput"
    loading_policy: LoadingPolicy = LoadingPolicy.SHARED_INSTANCE
    max_retries: int = 2
    escalation: Escalation = Escalation.FAIL
    temperature: float = 0.2
    max_tool_rounds: int = DEFAULT_TOOL_ROUNDS
    # Rounds when the work comes back to this agent (a fix after a check or a review). None = max_tool_rounds.
    # A fix is smaller than the first build: seen on a 27B Developer, a fix round given the build's
    # 60 rounds spent 70 minutes reading the same files and wrote nothing.
    revision_tool_rounds: int | None = None
    # Rounds in a row without changing a file before the agent is told to write, and four more
    # before its tools are taken away. Counted on a fix, or once the agent has written something.
    # None = never.
    max_idle_rounds: int | None = None
    # True: a round in which anything in the workspace changed is not idle, whoever changed it. For an agent that works
    # through scripts (the site cloner runs the kit's extract, spec and diff scripts, which write dozens of files),
    # where only counting write_file and edit_file stopped a run that was doing real work (brioni.com, 2026-10-23).
    idle_watches_workspace: bool = False
    # Rounds in a row without changing a file after which the agent keeps only its writing tools, until it changes one.
    # Seen on a 27B Cloner (brioni.com, 2026-10-23): it read the same 9 KB file twenty-two times in a row and wrote nothing, and
    # its closing report showed it knew exactly what to write. A reminder in the conversation did not stop it; no reading tool
    # to call did. 0 = never. Counted from the first round, like the rest of the idle watch, when ``idle_watches_workspace`` is on.
    focus_after_idle_rounds: int = 0
    # Where to start writing, in the words of the agent's task: said when its reading tools are taken away.
    focus_hint: str = ""
    # Which earlier outputs this agent is shown. None = everything so far. Each entry is a
    # node id ("researcher") or one field of its output ("researcher.claims"); anything not
    # listed is never put in this agent's prompt.
    reads: list[str] | None = None
    # Cap, in tokens, on the earlier work put in the prompt. None = what the context allows.
    max_input_tokens: int | None = None
    # Fixed values for string fields of the output, e.g. {"expert": ["code", "data", "none"]}.
    # Enforced by the decoder and checked again on the parsed answer.
    choices: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_is_identifier(cls, value: str) -> str:
        return _identifier(value, "agent")

    @field_validator("max_tool_rounds", mode="before")
    @classmethod
    def _old_budget_becomes_the_default(cls, value: Any) -> Any:
        return DEFAULT_TOOL_ROUNDS if value in (None, LEGACY_TOOL_ROUNDS) else value


def _identifier(value: str, what: str) -> str:
    if not value or not value.replace("_", "").isalnum() or value in (START, END):
        raise ValueError(f"invalid {what} id '{value}': use letters, digits and underscores")
    return value


class CheckDef(BaseModel):
    """A deterministic step: code, not a model, so it cannot be argued with.

    ``kind`` names an operation in ``agentfit.workflows.checks``. Its output is routed on
    like an agent's (``passed == false`` and so on).
    """

    id: str
    kind: str
    label: str = ""
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_is_identifier(cls, value: str) -> str:
        return _identifier(value, "check")


class ApprovalDef(BaseModel):
    id: str
    prompt: str = "Approve to continue?"
    # Agent whose output is shown to the human for the decision.
    show: str | None = None
    on_reject: str = END
    max_rejections: int = 2


class EdgeDef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str | list[str] = Field(alias="from")
    target: str = Field(alias="to")
    when: str | None = None
    label: str | None = None
    # Bound for an edge that goes back to an earlier node. None = not a loop edge.
    max_loops: int | None = None
    # Where to go when the loop bound is hit while the condition still holds.
    on_exhausted: str | None = None

    @property
    def sources(self) -> list[str]:
        return self.source if isinstance(self.source, list) else [self.source]


class WorkflowDef(BaseModel):
    workflow: WorkflowMeta
    models: dict[str, ModelSlot] = Field(default_factory=dict)
    agents: list[AgentDef] = Field(default_factory=list)
    checks: list[CheckDef] = Field(default_factory=list)
    human_approval: list[ApprovalDef] = Field(default_factory=list)
    edges: list[EdgeDef] = Field(default_factory=list)
    conditions: dict[str, str] = Field(default_factory=dict)
    shared_state: dict[str, Any] = Field(default_factory=dict)
    resource_plan: dict[str, Any] = Field(default_factory=dict)
    # Node positions for the visual editor: {node_id: {x, y}}.
    layout: dict[str, dict[str, float]] = Field(default_factory=dict)

    def agent(self, agent_id: str) -> AgentDef | None:
        return next((a for a in self.agents if a.id == agent_id), None)

    def approval(self, node_id: str) -> ApprovalDef | None:
        return next((a for a in self.human_approval if a.id == node_id), None)

    def check(self, node_id: str) -> CheckDef | None:
        return next((c for c in self.checks if c.id == node_id), None)

    def node_label(self, node_id: str) -> str:
        agent = self.agent(node_id)
        if agent:
            return agent.role
        check = self.check(node_id)
        return (check.label or check.id) if check else node_id

    @property
    def node_ids(self) -> list[str]:
        return [a.id for a in self.agents] + [c.id for c in self.checks] + [a.id for a in self.human_approval]

    def outgoing(self, node_id: str) -> list[EdgeDef]:
        return [e for e in self.edges if node_id in e.sources]

    def resolve_condition(self, when: str | None) -> str | None:
        if when is None:
            return None
        return self.conditions.get(when, when)


def load_yaml(text: str) -> WorkflowDef:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("workflow file must be a YAML mapping")
    return WorkflowDef.model_validate(data)


class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper: yaml.Dumper, data: str) -> yaml.Node:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_presenter)


def dump_yaml(definition: WorkflowDef) -> str:
    data = definition.model_dump(mode="json", by_alias=True, exclude_none=True)
    for key in ("conditions", "shared_state", "resource_plan", "layout", "human_approval", "checks"):
        if not data.get(key):
            data.pop(key, None)
    # Leave the optional agent and slot settings out of the file unless they are used.
    for agent in data.get("agents", []):
        if not agent.get("choices"):
            agent.pop("choices", None)
    for slot in data.get("models", {}).values():
        for key in ("kind", "wide_model_key", "auto_tune"):
            if not slot.get(key):
                slot.pop(key, None)
    if data.get("workflow", {}).get("category") == "team":
        data["workflow"].pop("category", None)
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
