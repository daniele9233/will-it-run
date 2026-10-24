"""Built-in workflow templates and the generator for custom pipelines."""

from __future__ import annotations

from typing import Any

from agentfit.teams import roles as R
from agentfit.teams import verification
from agentfit.teams.roles import ROLES, RoleTemplate
from agentfit.workflows.dsl import (
    END,
    START,
    AgentDef,
    ApprovalDef,
    EdgeDef,
    Escalation,
    LoadingPolicy,
    ModelSlot,
    WorkflowDef,
    WorkflowMeta,
)

SLOT_LABELS = {
    R.REASONING: ("Reasoning", "Planning, architecture, review and judgement"),
    R.CODING: ("Coding", "Writing and verifying code and infrastructure"),
    R.WRITING: ("Writing", "Documentation and prose"),
    R.VISION: ("Vision", "Understanding images and screenshots"),
}

# Context each slot is planned with. Coding agents carry file contents and tool output.
SLOT_CONTEXT = {R.REASONING: 16384, R.CODING: 16384, R.WRITING: 16384, R.VISION: 8192}


def agent_from_role(role: RoleTemplate, *, focus: str = "", agent_id: str | None = None) -> AgentDef:
    prompt = role.system_prompt()
    if focus:
        prompt += f"\n\n## Domain focus\nThis team specializes in {focus}. Apply that expertise in everything you produce."
    return AgentDef(
        id=agent_id or role.id,
        role=role.role,
        goal=role.goal,
        responsibilities=list(role.responsibilities),
        model=role.slot,
        system_prompt=prompt,
        tools=list(role.tools),
        input=role.input,
        output_schema=role.output_schema,
        loading_policy=LoadingPolicy.SHARED_INSTANCE,
        max_retries=role.max_retries,
        escalation=Escalation(role.escalation),
    )


def slots_for(agents: list[AgentDef]) -> dict[str, ModelSlot]:
    out: dict[str, ModelSlot] = {}
    for agent in agents:
        if agent.model not in out:
            label, purpose = SLOT_LABELS.get(agent.model, (agent.model.title(), ""))
            out[agent.model] = ModelSlot(label=label, purpose=purpose, context_length=SLOT_CONTEXT.get(agent.model, 8192))
    return out


def _edge(source: str | list[str], target: str, **kwargs: Any) -> EdgeDef:
    return EdgeDef.model_validate({"from": source, "to": target, **kwargs})


def _layout(columns: list[list[str]]) -> dict[str, dict[str, float]]:
    """Top-to-bottom layout: each inner list is one row of nodes."""
    layout: dict[str, dict[str, float]] = {}
    for row, nodes in enumerate(columns):
        width = (len(nodes) - 1) * 260
        for col, node in enumerate(nodes):
            layout[node] = {"x": 400 - width / 2 + col * 260, "y": 40 + row * 130}
    return layout


def _build(
    meta: WorkflowMeta,
    role_ids: list[str],
    edges: list[EdgeDef],
    approvals: list[ApprovalDef] | None = None,
    rows: list[list[str]] | None = None,
    focus: str = "",
) -> WorkflowDef:
    agents = [agent_from_role(ROLES[r], focus=focus) for r in role_ids]
    return WorkflowDef(
        workflow=meta,
        models=slots_for(agents),
        agents=agents,
        human_approval=approvals or [],
        edges=edges,
        layout=_layout(rows) if rows else {},
    )


def coding(focus: str = "") -> WorkflowDef:
    return _build(
        WorkflowMeta(
            id="coding", name="Coding Team", specializations=["coding"],
            description="Tech lead, planner, human-approved plan, developer, test loop and code review.",
        ),
        ["tech_lead", "planner", "developer", "test_runner", "code_reviewer"],
        [
            _edge(START, "tech_lead"),
            _edge("tech_lead", "planner"),
            _edge("planner", "approve_plan"),
            _edge("approve_plan", "developer"),
            _edge("developer", "test_runner"),
            _edge("test_runner", "developer", when="passed == false", label="FAIL", max_loops=3),
            _edge("test_runner", "code_reviewer", when="passed == true", label="PASS"),
            _edge("code_reviewer", "developer", when="approved == false", label="CHANGES", max_loops=2),
            _edge("code_reviewer", END, when="approved == true", label="APPROVED"),
        ],
        [ApprovalDef(id="approve_plan", prompt="Review the implementation plan. Approve to start coding.", show="planner", on_reject="planner")],
        [["tech_lead"], ["planner"], ["approve_plan"], ["developer"], ["test_runner"], ["code_reviewer"]],
        focus,
    )


def devops(focus: str = "") -> WorkflowDef:
    return _build(
        WorkflowMeta(
            id="devops", name="DevOps Team", specializations=["devops"],
            description="Supervisor, architect, human-approved design, engineer, validation loop, documentation and technical review.",
        ),
        ["supervisor", "architect", "devops_engineer", "validation_engineer", "documentation_specialist", "technical_reviewer"],
        [
            _edge(START, "supervisor"),
            _edge("supervisor", "architect"),
            _edge("architect", "approve_design"),
            _edge("approve_design", "devops_engineer"),
            _edge("devops_engineer", "validation_engineer"),
            _edge("validation_engineer", "architect", when="passed == false and root_cause == 'design'", label="DESIGN ERROR", max_loops=1),
            _edge("validation_engineer", "devops_engineer", when="passed == false", label="ERROR", max_loops=3),
            _edge("validation_engineer", "documentation_specialist", when="passed == true", label="VALID"),
            _edge("documentation_specialist", "technical_reviewer"),
            _edge("technical_reviewer", "documentation_specialist", when="approved == false", label="CHANGES", max_loops=2, on_exhausted=END),
            _edge("technical_reviewer", END, when="approved == true", label="APPROVED"),
        ],
        [ApprovalDef(id="approve_design", prompt="Review the architecture and rollout plan. Approve to start implementation.", show="architect", on_reject="architect")],
        [["supervisor"], ["architect"], ["approve_design"], ["devops_engineer"], ["validation_engineer"], ["documentation_specialist"], ["technical_reviewer"]],
        focus,
    )


def documentation(focus: str = "") -> WorkflowDef:
    return _build(
        WorkflowMeta(
            id="documentation", name="Documentation Team", specializations=["enterprise_docs"],
            description="Supervisor, documentation specialist and a technical review loop.",
        ),
        ["supervisor", "documentation_specialist", "technical_reviewer"],
        [
            _edge(START, "supervisor"),
            _edge("supervisor", "documentation_specialist"),
            _edge("documentation_specialist", "technical_reviewer"),
            _edge("technical_reviewer", "documentation_specialist", when="approved == false", label="CHANGES", max_loops=2, on_exhausted=END),
            _edge("technical_reviewer", END, when="approved == true", label="APPROVED"),
        ],
        None,
        [["supervisor"], ["documentation_specialist"], ["technical_reviewer"]],
        focus,
    )


def threejs(focus: str = "") -> WorkflowDef:
    return _build(
        WorkflowMeta(
            id="threejs", name="Three.js Team", specializations=["threejs"],
            description="Art direction, scene architecture, three parallel builders, integration, browser test and performance loop.",
        ),
        ["art_director", "scene_architect", "modeler_3d", "shader_engineer", "animation_engineer", "integrator",
         "browser_tester", "performance_checker", "performance_engineer"],
        [
            _edge(START, "art_director"),
            _edge("art_director", "scene_architect"),
            _edge("scene_architect", "modeler_3d"),
            _edge("scene_architect", "shader_engineer"),
            _edge("scene_architect", "animation_engineer"),
            _edge(["modeler_3d", "shader_engineer", "animation_engineer"], "integrator"),
            _edge("integrator", "browser_tester"),
            _edge("browser_tester", "integrator", when="passed == false", label="FAIL", max_loops=2),
            _edge("browser_tester", "performance_checker", when="passed == true", label="PASS"),
            _edge("performance_checker", "performance_engineer", when="passed == false", label="FAIL", max_loops=2, on_exhausted=END),
            _edge("performance_checker", END, when="passed == true", label="BUILD"),
            _edge("performance_engineer", "performance_checker"),
        ],
        None,
        [["art_director"], ["scene_architect"], ["modeler_3d", "shader_engineer", "animation_engineer"], ["integrator"],
         ["browser_tester"], ["performance_checker"], ["performance_engineer"]],
        focus or "Three.js and WebGL",
    )


def writing(focus: str = "") -> WorkflowDef:
    return _build(
        WorkflowMeta(
            id="writing", name="Writing Team", specializations=["creative_writing"],
            description="Editorial direction, author, editor rewrite loop, fact check, proofreading and final review.",
        ),
        ["editorial_director", "author", "editor", "fact_checker", "proofreader", "final_reviewer"],
        [
            _edge(START, "editorial_director"),
            _edge("editorial_director", "author"),
            _edge("author", "editor"),
            _edge("editor", "author", when="needs_rewrite == true", label="REWRITE", max_loops=2, on_exhausted="fact_checker"),
            _edge("editor", "fact_checker", when="needs_rewrite == false", label="ACCEPTED"),
            _edge("fact_checker", "proofreader"),
            _edge("proofreader", "final_reviewer"),
            _edge("final_reviewer", END),
        ],
        None,
        [["editorial_director"], ["author"], ["editor"], ["fact_checker"], ["proofreader"], ["final_reviewer"]],
        focus,
    )


def computer_use(focus: str = "") -> WorkflowDef:
    return _build(
        WorkflowMeta(
            id="computer_use", name="Computer Use Team", specializations=["computer_use"], max_steps=80,
            description="Plan, permission check, observe-decide-classify-execute-verify loop with human approval for risky actions.",
        ),
        ["cu_planner", "permission_checker", "observer", "decision_agent", "risk_classifier", "executor", "verifier", "reporter"],
        [
            _edge(START, "cu_planner"),
            _edge("cu_planner", "permission_checker"),
            _edge("permission_checker", "reporter", when="allowed == false", label="REFUSED"),
            _edge("permission_checker", "observer", when="allowed == true", label="ALLOWED"),
            _edge("observer", "decision_agent"),
            _edge("decision_agent", "reporter", when="done == true", label="DONE"),
            _edge("decision_agent", "risk_classifier", when="done == false"),
            _edge("risk_classifier", "approve_action", when="risky == true", label="RISKY"),
            _edge("risk_classifier", "executor", when="risky == false", label="SAFE"),
            _edge("approve_action", "executor"),
            _edge("executor", "verifier"),
            _edge("verifier", "observer", when="complete == false", label="INCOMPLETE", max_loops=8, on_exhausted="reporter"),
            _edge("verifier", "reporter", when="complete == true", label="COMPLETE"),
            _edge("reporter", END),
        ],
        [ApprovalDef(id="approve_action", prompt="This action was classified as risky. Approve to let the assistant perform it.", show="decision_agent", on_reject="reporter", max_rejections=1)],
        [["cu_planner"], ["permission_checker"], ["observer"], ["decision_agent"], ["risk_classifier"], ["approve_action"],
         ["executor"], ["verifier"], ["reporter"]],
        focus,
    )


BUILTIN: dict[str, Any] = {
    "coding": coding,
    "devops": devops,
    "documentation": documentation,
    "threejs": threejs,
    "writing": writing,
    "computer_use": computer_use,
}

# Workflows whose roles check each other instead of building on each other.
BUILTIN.update(verification.BUILTIN)

# How a verifier or reviewer sends work back, keyed by its output schema.
_REWORK: dict[str, tuple[str, str, str, str]] = {
    # schema: (fail condition, fail label, pass condition, pass label)
    "TestReport": ("passed == false", "FAIL", "passed == true", "PASS"),
    "ValidationReport": ("passed == false", "ERROR", "passed == true", "VALID"),
    "PerformanceReport": ("passed == false", "FAIL", "passed == true", "PASS"),
    "EditReview": ("needs_rewrite == true", "REWRITE", "needs_rewrite == false", "ACCEPTED"),
    "CodeReview": ("approved == false", "CHANGES", "approved == true", "APPROVED"),
    "TechnicalReview": ("approved == false", "CHANGES", "approved == true", "APPROVED"),
    "Verification": ("complete == false", "INCOMPLETE", "complete == true", "COMPLETE"),
}


def generate_pipeline(
    workflow_id: str,
    name: str,
    description: str,
    role_list: list[RoleTemplate],
    *,
    focus: str = "",
    specializations: list[str] | None = None,
) -> WorkflowDef:
    """Assemble a workflow from any set of roles, ordered by phase.

    lead -> plan -> human approval -> each builder with its verifier loop -> reviewers
    (looping back to the main builder) -> documentation -> final review (looping back to
    documentation). All loops are bounded.
    """
    by_phase: dict[str, list[RoleTemplate]] = {p: [] for p in R.PHASE_ORDER}
    seen: set[str] = set()
    for role in role_list:
        if role.id not in seen:
            seen.add(role.id)
            by_phase[role.phase].append(role)
    # Verifiers are placed right after the builder they check.
    verifier_ids = {r.verified_by for r in by_phase[R.BUILD] if r.verified_by}
    standalone_verifiers = [r for r in by_phase[R.VERIFY] if r.id not in verifier_ids]
    verifiers = {r.id: r for r in by_phase[R.VERIFY]}

    chain: list[str] = []
    rework_target: dict[str, str] = {}
    approvals: list[ApprovalDef] = []
    rows: list[list[str]] = []

    for role in by_phase[R.LEAD][:1] + by_phase[R.PLAN]:
        chain.append(role.id)
        rows.append([role.id])
    if by_phase[R.PLAN] and by_phase[R.BUILD]:
        last_plan = by_phase[R.PLAN][-1]
        approvals.append(
            ApprovalDef(
                id="approve_plan",
                prompt=f"Review the {last_plan.role}'s output. Approve to start the work.",
                show=last_plan.id,
                on_reject=last_plan.id,
            )
        )
        chain.append("approve_plan")
        rows.append(["approve_plan"])

    main_builder: str | None = None
    for builder in by_phase[R.BUILD]:
        chain.append(builder.id)
        rows.append([builder.id])
        main_builder = main_builder or builder.id
        verifier = verifiers.get(builder.verified_by or "")
        if verifier:
            chain.append(verifier.id)
            rows.append([verifier.id])
            rework_target[verifier.id] = builder.id
    for verifier in standalone_verifiers:
        chain.append(verifier.id)
        rows.append([verifier.id])
        if main_builder:
            rework_target[verifier.id] = main_builder
    for reviewer in by_phase[R.REVIEW]:
        chain.append(reviewer.id)
        rows.append([reviewer.id])
        if main_builder:
            rework_target[reviewer.id] = main_builder
    last_doc: str | None = None
    for doc in by_phase[R.DOCUMENT]:
        chain.append(doc.id)
        rows.append([doc.id])
        last_doc = doc.id
    for final in by_phase[R.FINAL_REVIEW]:
        chain.append(final.id)
        rows.append([final.id])
        if last_doc or main_builder:
            rework_target[final.id] = last_doc or main_builder or ""

    all_roles = {r.id: r for r in role_list}
    edges: list[EdgeDef] = [_edge(START, chain[0])] if chain else []
    for index, node in enumerate(chain):
        nxt = chain[index + 1] if index + 1 < len(chain) else END
        role = all_roles.get(node)
        rework = _REWORK.get(role.output_schema) if role else None
        target = rework_target.get(node)
        if rework and target:
            fail_when, fail_label, pass_when, pass_label = rework
            is_verify = role is not None and role.phase == R.VERIFY
            edges.append(
                _edge(
                    node, target, when=fail_when, label=fail_label,
                    max_loops=3 if is_verify else 2,
                    # A failed verification ends the run; an unresolved review moves on with its findings.
                    on_exhausted=None if is_verify else nxt,
                )
            )
            edges.append(_edge(node, nxt, when=pass_when, label=pass_label))
        else:
            edges.append(_edge(node, nxt))

    agents = [agent_from_role(all_roles[n], focus=focus) for n in chain if n in all_roles]
    return WorkflowDef(
        workflow=WorkflowMeta(
            id=workflow_id, name=name, description=description, specializations=specializations or [],
            max_steps=max(40, len(chain) * 6),
        ),
        models=slots_for(agents),
        agents=agents,
        human_approval=approvals,
        edges=edges,
        layout=_layout(rows),
    )
