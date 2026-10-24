"""Structured outputs exchanged between agents.

Agents hand each other typed data, not prose. Each schema here can be named in an
agent's ``output_schema`` and is enforced through the runtime's JSON-schema response
format. Schemas are deliberately flat: small local models follow them more reliably.
"""

from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, Field


class TaskBrief(BaseModel):
    """A lead's reading of the request."""

    objective: str = Field(description="One-sentence statement of what must be delivered")
    scope: list[str] = Field(default_factory=list, description="What is in scope")
    out_of_scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Conditions for done that the team can check by reading files and running code. "
        "It cannot open a browser or look at a screen: say what must be true of the files, not what must be seen",
    )
    open_questions: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    title: str
    detail: str
    owner: str = Field(default="", description="Role expected to carry out the step")


class Plan(BaseModel):
    summary: str
    steps: list[PlanStep] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    files_or_components: list[str] = Field(default_factory=list)


class ArchitectureDesign(BaseModel):
    summary: str
    components: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list, description="Key decisions with their rationale")
    risks: list[str] = Field(default_factory=list)
    rollout_steps: list[str] = Field(default_factory=list)
    rollback_plan: str = ""


class FileChange(BaseModel):
    path: str
    purpose: str


class CodeChange(BaseModel):
    summary: str
    files: list[FileChange] = Field(default_factory=list)
    how_to_test: str = ""
    notes: list[str] = Field(default_factory=list)


class TestReport(BaseModel):
    passed: bool = Field(
        description="True when the test suite ran and every test succeeded. It is about the tests only; "
        "when you ran them with run_tests the system sets it from that run"
    )
    summary: str
    checks_run: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list, description="Each test or check that failed, with its error")
    unverified: list[str] = Field(
        default_factory=list,
        description="What you could not check with the tools you have, such as anything that needs a browser. "
        "These are not failures and do not make passed false",
    )
    evidence: str = Field(default="", description="Leave empty; the system writes the result of the last test run here")


class Finding(BaseModel):
    severity: str = Field(description="critical, major, minor or nit")
    location: str = ""
    issue: str
    suggestion: str = ""


class CodeReview(BaseModel):
    approved: bool = Field(
        description="True when no critical or major finding remains in what you read. "
        "Something nobody on the team can check with its tools is not a finding and does not block approval"
    )
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    security_issues: list[str] = Field(default_factory=list)
    requested_changes: list[str] = Field(default_factory=list, description="Changes the developer can make with its tools")
    unverified: list[str] = Field(
        default_factory=list, description="What could not be checked here, such as behaviour that needs a browser. For the user to check"
    )


class Implementation(BaseModel):
    summary: str
    actions_taken: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list, description="Files, manifests or commands produced")
    pending_manual_steps: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    passed: bool
    summary: str
    checks: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    root_cause: str = Field(default="", description="design, implementation or environment, when failed")


class Documentation(BaseModel):
    title: str
    audience: str = ""
    markdown: str = Field(description="The complete document in Markdown")
    assumptions: list[str] = Field(default_factory=list)


class TechnicalReview(BaseModel):
    approved: bool
    summary: str
    inaccuracies: list[str] = Field(default_factory=list)
    missing_topics: list[str] = Field(default_factory=list)
    requested_changes: list[str] = Field(default_factory=list)


class SpecialistAnalysis(BaseModel):
    summary: str
    recommendations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    details: str = ""


class EditorialBrief(BaseModel):
    premise: str
    audience: str
    tone: str
    structure: list[str] = Field(default_factory=list, description="Ordered outline")
    constraints: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    title: str
    text: str
    word_count: int = 0
    notes_for_editor: list[str] = Field(default_factory=list)


class EditReview(BaseModel):
    needs_rewrite: bool
    summary: str
    strengths: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    edited_text: str = Field(default="", description="The line-edited text when no rewrite is needed")


class FactCheck(BaseModel):
    summary: str
    verified_claims: list[str] = Field(default_factory=list)
    unverifiable_claims: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SceneDesign(BaseModel):
    concept: str
    scene_graph: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    performance_budget: str = ""
    work_packages: list[str] = Field(default_factory=list)


class PerformanceReport(BaseModel):
    passed: bool
    summary: str
    measurements: list[str] = Field(default_factory=list)
    bottlenecks: list[str] = Field(default_factory=list)


class PermissionCheck(BaseModel):
    allowed: bool
    summary: str
    required_capabilities: list[str] = Field(default_factory=list)
    refused_reason: str = ""


class Observation(BaseModel):
    summary: str
    active_window: str = ""
    visible_elements: list[str] = Field(default_factory=list)
    goal_progress: str = ""


class ActionDecision(BaseModel):
    action: str = Field(description="The single next action to take, or 'done'")
    target: str = ""
    arguments: str = ""
    rationale: str = ""
    done: bool = False


class RiskAssessment(BaseModel):
    risky: bool = Field(description="True when the action needs explicit human approval")
    level: str = Field(description="low, medium or high")
    reasons: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    success: bool
    summary: str
    output: str = ""


class Verification(BaseModel):
    complete: bool = Field(description="True when the user's goal has been fully achieved")
    summary: str
    remaining: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    summary: str
    deliverables: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


class GenericOutput(BaseModel):
    summary: str
    details: str = ""
    items: list[str] = Field(default_factory=list)


class Text(BaseModel):
    """An answer in the model's own words: plain text or Markdown, not a JSON object.

    The one schema the engine does not enforce through the decoder: the agent's last
    message is taken as it is. It is what a conversation needs.
    """

    text: str


# Name of the free-text output, for the places that treat it differently.
TEXT = Text.__name__


# -- verification workflows ---------------------------------------------------
# Ids in these schemas are written by the engine (a hash of the content), never by the
# model: whatever the model puts there is replaced before anyone else reads it.


class Claim(BaseModel):
    id: str = Field(default="", description="Leave empty; the system assigns it")
    statement: str = Field(description="One checkable statement")
    quote: str = Field(description="Words copied exactly from the evidence that support the statement")
    source: str = Field(default="", description="Id of the evidence source the quote comes from, e.g. E1")


class ClaimSet(BaseModel):
    summary: str
    claims: list[Claim] = Field(default_factory=list)
    reasoning: str = Field(default="", description="Your working notes. Nobody who checks the claims sees them")
    gaps: list[str] = Field(default_factory=list, description="What the evidence does not let you say")


class ClaimVerdict(BaseModel):
    claim: str = Field(description="Id of the claim, copied exactly")
    holds: bool = Field(description="True only if the quote says what the statement says")
    objection: str = Field(default="", description="Why it does not hold, or what it overstates")


class Audit(BaseModel):
    summary: str
    verdicts: list[ClaimVerdict] = Field(default_factory=list)


class Routing(BaseModel):
    expert: str = Field(description="The expert to activate, from the allowed list only")
    question: str = Field(description="The precise question for that expert")
    reason: str = ""


class Proposition(BaseModel):
    id: str = Field(default="", description="Leave empty; the system assigns it")
    statement: str = Field(description="One statement that is either supported by the evidence or not")


class PropositionSet(BaseModel):
    question: str
    propositions: list[Proposition] = Field(default_factory=list)


class Vote(BaseModel):
    proposition: str = Field(description="Id of the proposition, copied exactly")
    vote: Literal["AGREE", "DISAGREE", "UNSURE"]
    quote: str = Field(default="", description="Words copied exactly from the evidence that justify the vote")
    source: str = Field(default="", description="Id of the evidence source the quote comes from")


class Ballot(BaseModel):
    votes: list[Vote] = Field(default_factory=list)
    note: str = ""


class Commit(BaseModel):
    summary: str
    content: str = Field(description="The complete piece of work, not a description of it")
    assumptions: list[str] = Field(default_factory=list)


class CommitVerdict(BaseModel):
    commit: str = Field(description="Id of the commit that was checked, e.g. branch_a")
    approved: bool
    issues: list[str] = Field(default_factory=list)


class CommitReview(BaseModel):
    summary: str
    verdicts: list[CommitVerdict] = Field(default_factory=list)


class GroundedAnswer(BaseModel):
    answer: str = Field(description="The answer in Markdown, built only from the accepted items")
    used: list[str] = Field(default_factory=list, description="Ids of the accepted items the answer relies on")
    caveats: list[str] = Field(default_factory=list, description="What could not be established")


class CloneReview(BaseModel):
    status: Literal["PERFECT", "ACCEPTABLE", "NEEDS_WORK"] = Field(
        description="PERFECT: nothing visible differs. ACCEPTABLE: only minor differences. NEEDS_WORK: critical or major differences remain"
    )
    summary: str = Field(description="What was compared and how close the clone is, in a few sentences")
    issues: list[str] = Field(
        default_factory=list,
        description="The critical and major differences still to fix, most important first: which element, where, and what it should look like",
    )


SCHEMAS: dict[str, type[BaseModel]] = {
    cls.__name__: cls
    for cls in (
        TaskBrief, Plan, ArchitectureDesign, CodeChange, TestReport, CodeReview, Implementation,
        ValidationReport, Documentation, TechnicalReview, SpecialistAnalysis, EditorialBrief, Draft,
        EditReview, FactCheck, SceneDesign, PerformanceReport, PermissionCheck, Observation,
        ActionDecision, RiskAssessment, ExecutionResult, Verification, FinalReport, GenericOutput, Text,
        ClaimSet, Audit, Routing, PropositionSet, Ballot, Commit, CommitReview, GroundedAnswer, CloneReview,
    )
}


def get_schema(name: str | None) -> type[BaseModel]:
    return SCHEMAS.get(name or "", GenericOutput)


def choice_fields(name: str | None) -> set[str]:
    """Top-level string fields of a schema: the ones an agent's ``choices`` may restrict."""
    return {field for field, info in get_schema(name).model_fields.items() if info.annotation is str}


# Annotations a decoder does not need. They are schema keywords only where a schema is
# expected: under ``properties`` the same words are names of fields and must stay.
_ANNOTATIONS = ("$defs", "title", "default")


def _inline_refs(node: Any, defs: dict[str, Any], *, field_names: bool = False) -> Any:
    """Inline ``$ref`` definitions: grammar-constrained decoders handle flat schemas best.

    ``field_names`` is true for the mapping under ``properties``, whose keys are the names
    of the fields. Dropping the annotation ``title`` there would drop a field called
    ``title`` from the schema while it stays in ``required``: the decoder then cannot
    produce it and the answer can never validate.
    """
    if isinstance(node, dict):
        if not field_names and "$ref" in node:
            target = defs.get(node["$ref"].rsplit("/", 1)[-1], {})
            return _inline_refs(target, defs)
        out = {
            key: _inline_refs(value, defs, field_names=(key == "properties" and not field_names))
            for key, value in node.items()
            if field_names or key not in _ANNOTATIONS
        }
        if not field_names and out.get("type") == "object" and "properties" in out:
            # Only the fields of the schema: a decoder may otherwise invent keys of its own.
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def response_format(name: str | None, choices: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """OpenAI-style ``response_format`` enforcing the named schema.

    ``choices`` turns string fields into enums, so a grammar-constrained decoder cannot
    produce a value outside the list.
    """
    model = get_schema(name)
    raw = model.model_json_schema()
    schema = _inline_refs(raw, raw.get("$defs", {}))
    for field_name, allowed in (choices or {}).items():
        if field_name in schema.get("properties", {}) and allowed:
            schema["properties"][field_name] = {"type": "string", "enum": list(allowed)}
    return {"type": "json_schema", "json_schema": {"name": model.__name__, "strict": True, "schema": schema}}


def describe(name: str | None) -> str:
    """Compact field list used inside prompts so the model knows what to produce."""
    model = get_schema(name)
    lines = []
    for field_name, field in model.model_fields.items():
        annotation = getattr(field.annotation, "__name__", str(field.annotation)).replace("typing.", "")
        hint = f" - {field.description}" if field.description else ""
        item = next((a for a in get_args(field.annotation) if isinstance(a, type) and issubclass(a, BaseModel)), None)
        if item is None:
            lines.append(f"- {field_name} ({annotation}){hint}")
            continue
        # A list of objects: name the fields of one item, or the model has to guess them.
        lines.append(f"- {field_name} (list of objects, each with){hint}")
        for sub_name, sub in item.model_fields.items():
            choices = get_args(sub.annotation) if get_origin(sub.annotation) is Literal else ()
            detail = sub.description or (" or ".join(str(c) for c in choices) if choices else "")
            lines.append(f"  - {sub_name}" + (f" - {detail}" if detail else ""))
    return "\n".join(lines)
