"""Verification workflows: agents that are not trusted, and checks that are code.

The team templates assume each role does its job and the next one builds on it. These
assume the opposite: any model may invent, so nothing an agent says counts until
something it cannot influence has checked it. Three mechanisms carry that, all in the
engine rather than in a prompt:

* **Isolation** (``reads``): an agent is shown only the outputs listed for it. The
  skeptic gets the claims, not the reasoning behind them.
* **Deterministic checks** (``checks``): steps that are code. They verify that a quote
  exists word for word in the evidence, count votes, compare hashes, and drop whatever
  fails. See ``agentfit.workflows.checks`` for what each one does and does not prove.
* **The ledger**: every step records the SHA-256 of what it produced and of what it read.

Give the roles that check each other different models when you can: two instances of one
model tend to make the same mistake.
"""

from __future__ import annotations

from typing import Any

from agentfit.workflows.dsl import (
    END,
    START,
    AgentDef,
    CheckDef,
    EdgeDef,
    LoadingPolicy,
    ModelSlot,
    WorkflowDef,
    WorkflowMeta,
)

EVIDENCE_RULES = (
    "The Evidence section is everything you know about this subject. Use nothing you remember from elsewhere.",
    "A quote copies words from the evidence exactly: the same words in the same order. You may leave words out "
    "only by writing ... between two parts.",
    "If the evidence does not say something, you do not say it.",
    "Leave every id field empty when you create an item, and copy ids exactly when you refer to one.",
)


def _prompt(rules: tuple[str, ...], *, evidence: bool = True) -> str:
    lines = [*rules, *(EVIDENCE_RULES if evidence else ())]
    return "## How you work\n" + "\n".join(f"- {line}" for line in lines)


def _agent(
    agent_id: str, role: str, slot: str, goal: str, duties: tuple[str, ...], rules: tuple[str, ...], schema: str,
    *, receives: str, **extra: Any,
) -> AgentDef:
    evidence = extra.pop("evidence", True)
    return AgentDef(
        id=agent_id, role=role, goal=goal, responsibilities=list(duties), model=slot, input=receives,
        system_prompt=_prompt(rules, evidence=evidence), output_schema=schema,
        loading_policy=LoadingPolicy.LOAD_ON_DEMAND, max_retries=2, **extra,
    )


def _slot(label: str, kind: str, purpose: str, context: int = 16384) -> ModelSlot:
    return ModelSlot(label=label, kind=kind, purpose=purpose, context_length=context)


def _edge(source: str | list[str], target: str, **kwargs: Any) -> EdgeDef:
    return EdgeDef.model_validate({"from": source, "to": target, **kwargs})


def _layout(rows: list[list[str]]) -> dict[str, dict[str, float]]:
    layout: dict[str, dict[str, float]] = {}
    for row, nodes in enumerate(rows):
        width = (len(nodes) - 1) * 250
        for col, node in enumerate(nodes):
            layout[node] = {"x": 400 - width / 2 + col * 250, "y": 40 + row * 125}
    return layout


def _writer(agent_id: str, role: str, slot: str, reads: list[str], what: str) -> AgentDef:
    return _agent(
        agent_id, role, slot,
        f"Answer the request using only {what}.",
        ("Write the answer from the accepted items", "Name every item the answer relies on", "Say what could not be established"),
        (
            f"Your only material is {what}, listed under Work so far. Nothing else exists for you.",
            "Write the answer in Markdown. After each statement put the id of the item it rests on in square brackets.",
            "List in used every id the answer relies on, copied exactly. An id that is not in your material makes the "
            "whole answer be withheld.",
            "If the material does not answer part of the request, say so in caveats instead of filling the gap.",
        ),
        "GroundedAnswer", receives=f"The request and {what}. Nothing that was discarded.", reads=reads, evidence=False,
        temperature=0.2,
    )


# -- 1. adversarial blackboard ------------------------------------------------


def adversarial_blackboard(focus: str = "") -> WorkflowDef:
    agents = [
        _agent(
            "researcher", "Researcher", "research",
            "Extract from the evidence the claims that answer the request, each with the quote that proves it.",
            ("Find what in the evidence answers the request", "State it as separate claims", "Attach to each claim the quote that proves it"),
            (
                "Write each claim as one statement that can be checked on its own.",
                "Give every claim one quote that, read alone, shows the statement is true.",
                "Prefer five claims you can prove to fifteen you cannot. A claim without a real quote is discarded.",
                "Put your working notes in reasoning. The skeptic never sees them, so a claim must stand without them.",
                "When claims come back discarded, fix the quote or drop the claim. Do not argue.",
            ),
            "ClaimSet", receives="The request, the evidence, and any claims that were discarded with the reason.",
            reads=["dossier"], temperature=0.2,
        ),
        _agent(
            "skeptic", "Skeptic", "skeptic",
            "Try to break every claim. Let one stand only when its quote really says what the statement says.",
            ("Compare each statement with its quote", "Reject what overstates, generalises or assumes", "Give a verdict on every claim"),
            (
                "You see the claims and the evidence, not how the researcher got there. Judge only what is written.",
                "For each claim, read its quote in the evidence and ask: does this say exactly that, or less?",
                "holds is false when the statement goes beyond the quote, generalises it, or depends on something not quoted.",
                "Give one verdict for every claim, using its id. A claim you skip is discarded.",
                "You are not here to be agreeable. When in doubt, the claim does not hold.",
            ),
            "Audit", receives="The request, the evidence and the researcher's claims. Not the researcher's reasoning.",
            reads=["dossier", "researcher.claims"], temperature=0.1,
        ),
        _writer("writer", "Writer", "writer", ["filter.accepted"], "the accepted claims"),
    ]
    return WorkflowDef(
        workflow=WorkflowMeta(
            id="adversarial_blackboard", name="Adversarial Blackboard", category="verification", max_steps=40,
            description=(
                "A researcher posts claims with quotes; a skeptic who never sees the reasoning tries to break them; "
                "code keeps only the claims whose quote is really in the evidence and survived; the answer is written "
                "from those alone."
            ),
        ),
        models={
            "research": _slot("Researcher", "reasoning", "Reading the evidence and extracting claims"),
            "skeptic": _slot("Skeptic", "reasoning", "Attacking the claims. Best on a different model than the researcher"),
            "writer": _slot("Writer", "writing", "Writing the answer from the accepted claims"),
        },
        agents=agents,
        checks=[
            CheckDef(id="dossier", kind="evidence_dossier", label="Evidence dossier"),
            CheckDef(id="filter", kind="verify_claims", label="Claim filter",
                     params={"claims": "researcher", "audit": "skeptic", "evidence": "dossier"}),
            CheckDef(id="seal", kind="audit_trail", label="Audit trail", params={"answer": "writer", "accepted": "filter"}),
        ],
        edges=[
            _edge(START, "dossier"),
            _edge("dossier", "researcher", when="passed == true", label="EVIDENCE"),
            _edge("dossier", END, when="passed == false", label="NO EVIDENCE"),
            _edge("researcher", "skeptic"),
            _edge("skeptic", "filter"),
            _edge("filter", "writer", when="passed == true", label="VERIFIED"),
            _edge("filter", "researcher", when="passed == false", label="NOTHING HELD", max_loops=1),
            _edge("writer", "seal"),
            _edge("seal", "writer", when="passed == false", label="UNSUPPORTED", max_loops=1),
            _edge("seal", END),
        ],
        layout=_layout([["dossier"], ["researcher"], ["skeptic"], ["filter"], ["writer"], ["seal"]]),
    )


# -- 2. commit DAG ------------------------------------------------------------


def commit_dag(focus: str = "") -> WorkflowDef:
    def author(agent_id: str, role: str, slot: str, stance: str) -> AgentDef:
        return _agent(
            agent_id, role, slot,
            "Produce one complete piece of work that satisfies the specification.",
            ("Deliver the whole work, not a plan for it", "Meet every acceptance criterion", "Declare every assumption"),
            (
                "You see the specification and nothing else. Another author works on the same specification; you never see their work.",
                stance,
                "content holds the whole deliverable, ready to use: the code, the text or the design itself.",
                "List under assumptions everything you had to decide that the specification left open.",
                "Never claim something was tested or run. Nothing was.",
            ),
            "Commit", receives="The specification only, within a fixed input budget.",
            reads=["spec"], evidence=False, max_input_tokens=3000, temperature=0.3,
        )

    agents = [
        _agent(
            "spec", "Specification", "lead",
            "Turn the request into a specification two people could implement separately and be judged against.",
            ("State the objective and the scope", "Write acceptance criteria that can be checked"),
            (
                "State the objective in one sentence, then what is in scope and what is not.",
                "Write acceptance criteria that can each be checked as true or false on the finished work.",
                "Do not design the solution. Say what must be true of it.",
            ),
            "TaskBrief", receives="The request.", evidence=False,
        ),
        author("branch_a", "Author A", "author_a", "Take the most direct route: the simplest work that meets every criterion."),
        author("branch_b", "Author B", "author_b", "Take the careful route: handle the edge cases and failure modes first."),
        _agent(
            "reviewer", "Independent Reviewer", "reviewer",
            "Decide, commit by commit, whether the work meets the specification.",
            ("Check each commit against every criterion", "Approve or reject each commit separately", "Name each failed criterion"),
            (
                "You did not write either commit and you owe neither author anything.",
                "Check each commit against every acceptance criterion. Approve it only if all of them are met.",
                "Give one verdict per commit, with commit set to its id: branch_a or branch_b. A commit without a verdict is dropped.",
                "List each failed criterion as an issue. Do not rewrite the work.",
                "Your approval is recorded against the exact text you were shown. If the work changes later, it no longer counts.",
            ),
            "CommitReview", receives="The specification and both commits, within a fixed input budget.",
            reads=["spec", "branch_a", "branch_b"], evidence=False, max_input_tokens=8000, temperature=0.1,
        ),
        _agent(
            "merger", "Merger", "merger",
            "Combine the approved commits into the final deliverable.",
            ("Build the deliverable from approved commits only", "Name the commits used", "Report what stays open"),
            (
                "You are shown only commits that passed review and whose hash was verified. A commit you do not see was dropped: do not guess what it said.",
                "Use the best parts of what you have. When the commits disagree, follow the specification.",
                "answer holds the final deliverable in Markdown. List in used the id of every commit you took from.",
                "Put in caveats what the approved commits left open.",
            ),
            "GroundedAnswer", receives="The specification and the commits that passed the commit check. Never a dropped one.",
            reads=["spec", "gate.approved"], evidence=False, max_input_tokens=8000,
        ),
    ]
    return WorkflowDef(
        workflow=WorkflowMeta(
            id="commit_dag", name="Commit DAG", category="verification", max_steps=40,
            description=(
                "Two authors work on the same specification without seeing each other. An independent reviewer approves "
                "or rejects each commit; code recomputes the hashes and passes only what was approved unchanged; the "
                "merger never sees the rest."
            ),
        ),
        models={
            "lead": _slot("Specification", "reasoning", "Writing the specification"),
            "author_a": _slot("Author A", "coding", "First independent attempt"),
            "author_b": _slot("Author B", "coding", "Second independent attempt. Best on a different model than Author A"),
            "reviewer": _slot("Reviewer", "reasoning", "Reviewing both commits. Best on a model neither author uses"),
            "merger": _slot("Merger", "writing", "Merging the approved commits"),
        },
        agents=agents,
        checks=[
            CheckDef(id="gate", kind="verify_commits", label="Commit check",
                     params={"commits": ["branch_a", "branch_b"], "review": "reviewer"}),
            CheckDef(id="seal", kind="audit_trail", label="Audit trail", params={"answer": "merger", "accepted": "gate"}),
        ],
        edges=[
            _edge(START, "spec"),
            _edge("spec", "branch_a"),
            _edge("spec", "branch_b"),
            _edge(["branch_a", "branch_b"], "reviewer"),
            _edge("reviewer", "gate"),
            _edge("gate", "merger", when="passed == true", label="VERIFIED"),
            _edge("gate", END, when="passed == false", label="NOTHING APPROVED"),
            _edge("merger", "seal"),
            _edge("seal", "merger", when="passed == false", label="UNSUPPORTED", max_loops=1),
            _edge("seal", END),
        ],
        layout=_layout([["spec"], ["branch_a", "branch_b"], ["reviewer"], ["gate"], ["merger"], ["seal"]]),
    )


# -- 3. fail-closed swarm -----------------------------------------------------

EXPERTS: list[tuple[str, str, str, str]] = [
    # (choice, node id, role, what it covers)
    ("code", "expert_code", "Software Expert", "source code, APIs, algorithms and software behaviour"),
    ("data", "expert_data", "Data Expert", "numbers, tables, measurements, dates and calculations"),
    ("systems", "expert_systems", "Systems Expert", "infrastructure, configuration, operations and security"),
    ("language", "expert_language", "Document Expert", "contracts, policies, procedures and other prose"),
]


def failclosed_swarm(focus: str = "") -> WorkflowDef:
    latest_expert = "|".join(node for _, node, _, _ in EXPERTS)
    agents = [
        _agent(
            "router", "Router", "router",
            "Pick the one expert whose field the request belongs to, or none.",
            ("Choose one expert from the allowed list", "Refuse when no expert fits", "Phrase the question for the expert"),
            (
                "The allowed experts are: " + "; ".join(f"{choice} ({covers})" for choice, _, _, covers in EXPERTS) + ".",
                "Answer none when the request fits no expert, or when the evidence has nothing to do with it. Refusing is a correct outcome.",
                "You cannot name any other expert: the system accepts only the values above.",
                "question is the precise question for the expert, in one or two sentences.",
                "If an expert's claims came back discarded, pick a different expert or answer none. Do not send the same question to the same expert.",
            ),
            "Routing", receives="The request, the evidence, and the outcome of earlier attempts.",
            reads=["gate"], temperature=0.0,
            choices={"expert": [*(choice for choice, _, _, _ in EXPERTS), "none"]},
        ),
        *[
            _agent(
                node, role, node,
                f"Answer the router's question as a specialist in {covers}, with claims the evidence proves.",
                ("Answer only within your field", "State the answer as separate claims", "Attach to each claim the quote that proves it"),
                (
                    f"Your field is {covers}. If the question is outside it, return no claims and say why in gaps.",
                    "Write each claim as one statement that can be checked on its own, with one quote that proves it.",
                    "A validator and then a program check every claim. A claim without a real quote is discarded.",
                ),
                "ClaimSet", receives="The request, the evidence and the router's question.",
                reads=["gate", "router.question"], temperature=0.2,
            )
            for _, node, role, covers in EXPERTS
        ],
        _agent(
            "validator", "Domain Validator", "validator",
            "Check each of the expert's claims the way a second specialist would.",
            ("Compare each statement with its quote", "Reject what is wrong for the field or overstated", "Give a verdict on every claim"),
            (
                "You see the question, the claims and the evidence. Judge whether each quote supports its statement.",
                "holds is false when the statement goes beyond the quote, is wrong for the field, or rests on something not quoted.",
                "Give one verdict for every claim, using its id. A claim you skip is discarded.",
                "When in doubt, the claim does not hold.",
            ),
            "Audit", receives="The request, the evidence, the question and the claims of the expert that just answered.",
            reads=["gate", "router.question", f"{latest_expert}.claims"], temperature=0.1,
        ),
        _writer("writer", "Writer", "writer", ["router.question", "filter.accepted"], "the accepted claims"),
    ]
    experts = [node for _, node, _, _ in EXPERTS]
    return WorkflowDef(
        workflow=WorkflowMeta(
            id="failclosed_swarm", name="Fail-Closed Swarm", category="verification", max_steps=60,
            description=(
                "A gate runs before any model. The router can only name an expert from a fixed list, or refuse. The "
                "expert's claims go to a validator and a quote check; if nothing survives another expert is tried, "
                "three activations at most, and then the run refuses instead of answering."
            ),
        ),
        models={
            "router": _slot("Router", "reasoning", "Choosing the expert. A small fast model is enough", 8192),
            "expert_code": _slot("Software Expert", "coding", "Questions about software"),
            "expert_data": _slot("Data Expert", "reasoning", "Questions about numbers and data"),
            "expert_systems": _slot("Systems Expert", "coding", "Questions about systems and operations"),
            "expert_language": _slot("Document Expert", "writing", "Questions about documents"),
            "validator": _slot("Validator", "reasoning", "Checking the expert. Best on a model no expert uses"),
            "writer": _slot("Writer", "writing", "Writing the answer from the accepted claims"),
        },
        agents=agents,
        checks=[
            CheckDef(id="gate", kind="evidence_dossier", label="Pre-inference gate", params={"max_task_tokens": 6000}),
            CheckDef(id="filter", kind="verify_claims", label="Claim filter",
                     params={"claims": experts, "audit": "validator", "evidence": "gate"}),
            CheckDef(id="refuse", kind="refuse", label="Refusal",
                     params={"verdict": "NO_TRUSTED_ANSWER",
                             "message": "No expert on the allowed list produced a claim that survived validation. The run refuses rather than guess."}),
            CheckDef(id="seal", kind="audit_trail", label="Compliance report", params={"answer": "writer", "accepted": "filter"}),
        ],
        edges=[
            _edge(START, "gate"),
            _edge("gate", "router", when="passed == true", label="ADMITTED"),
            _edge("gate", END, when="passed == false", label="REFUSED"),
            *[_edge("router", node, when=f"expert == '{choice}'", label=choice.upper()) for choice, node, _, _ in EXPERTS],
            _edge("router", "refuse", label="NONE"),
            *[_edge(node, "validator") for node in experts],
            _edge("validator", "filter"),
            _edge("filter", "writer", when="passed == true", label="VERIFIED"),
            _edge("filter", "router", when="passed == false", label="RETRY", max_loops=2, on_exhausted="refuse"),
            _edge("writer", "seal"),
            _edge("seal", "writer", when="passed == false", label="UNSUPPORTED", max_loops=1),
            _edge("seal", END),
            _edge("refuse", END),
        ],
        layout=_layout([["gate"], ["router"], experts, ["validator"], ["filter"], ["writer", "refuse"], ["seal"]]),
    )


# -- 4. deferred-consensus Delphi ---------------------------------------------

PANEL: list[tuple[str, str, str]] = [
    ("a", "Panelist A", "Read literally. Agree only with what the evidence states in so many words."),
    ("b", "Panelist B", "Read against the grain. For each proposition, look first for the passage that contradicts it."),
    ("c", "Panelist C", "Read as a practitioner. Ask what the evidence commits its author to, and no more."),
]


def delphi_consensus(focus: str = "") -> WorkflowDef:
    def panelist(agent_id: str, role: str, slot: str, stance: str, second_round: bool) -> AgentDef:
        rules = (
            stance,
            "You see the same evidence as the other panelists and nothing else. You cannot look anything up.",
            "Vote AGREE when the evidence supports the proposition, DISAGREE when it contradicts it, UNSURE when it does neither.",
            "Back every AGREE or DISAGREE with a quote. A vote without a real quote counts half.",
            "Give one vote per proposition, with proposition set to its id.",
        )
        if second_round:
            rules = (
                *rules[:2],
                "These propositions split the panel. You see how many voted each way and the quotes given, not who voted what.",
                "Vote again on each one. Change your vote only if a quote convinces you, not because others disagreed.",
                "A vote against the earlier majority counts for nothing unless it comes with a real quote.",
                *rules[2:],
            )
        return _agent(
            agent_id, role, slot, "Vote on each proposition from the evidence alone.",
            ("Vote on every proposition", "Back each vote with a quote", "Stay independent of the other panelists"),
            rules, "Ballot",
            receives=(
                "The request, the evidence and the contested propositions with anonymous vote counts." if second_round
                else "The request, the evidence and the propositions. Never another panelist's vote."
            ),
            reads=["dossier", "tally.feedback"] if second_round else ["dossier", "framer.propositions"], temperature=0.2,
        )

    first = [panelist(f"panelist_{key}", role, f"panel_{key}", stance, False) for key, role, stance in PANEL]
    second = [panelist(f"second_{key}", f"{role}, round 2", f"panel_{key}", stance, True) for key, role, stance in PANEL]
    agents = [
        _agent(
            "framer", "Framer", "moderator",
            "Break the request into propositions the panel can vote on.",
            ("Cover what the request asks", "Write statements that can be supported or contradicted", "Stay neutral"),
            (
                "Write three to six propositions. Each is one statement the evidence either supports or contradicts.",
                "Together they must cover what the request asks. Do not include anything the request does not ask.",
                "Phrase them neutrally. You are not answering; you are setting the questions.",
            ),
            "PropositionSet", receives="The request and the evidence.", reads=["dossier"], temperature=0.1,
        ),
        *first,
        *second,
        _agent(
            "rapporteur", "Rapporteur", "moderator",
            "Report what the panel decided, and only that.",
            ("Report accepted and rejected propositions", "List contested ones as unresolved", "Add nothing of your own"),
            (
                "Your only material is the panel's result under Work so far: accepted, rejected and contested propositions.",
                "State accepted propositions as established and rejected ones as contradicted by the evidence, each with its id in square brackets.",
                "List every contested proposition in caveats as unresolved. Never settle one yourself.",
                "List in used the id of every accepted or rejected proposition the answer relies on, copied exactly.",
            ),
            "GroundedAnswer", receives="The request and the panel's result: accepted, rejected and contested propositions.",
            evidence=False,
            reads=["final_tally|tally.accepted", "final_tally|tally.rejected", "final_tally|tally.contested"],
        ),
    ]
    first_ids, second_ids = [a.id for a in first], [a.id for a in second]
    return WorkflowDef(
        workflow=WorkflowMeta(
            id="delphi_consensus", name="Deferred-Consensus Delphi", category="verification", max_steps=60,
            description=(
                "Three panelists vote on the same propositions from the same evidence, blind to each other. Code counts "
                "the votes afterwards; what splits the panel gets one more round with anonymous counts; what is still "
                "contested is reported as unresolved, and without a quorum the run ends with INSUFFICIENT_CONSENSUS."
            ),
        ),
        models={
            "moderator": _slot("Moderator", "reasoning", "Framing the propositions and reporting the result"),
            "panel_a": _slot("Panelist A", "reasoning", "First vote"),
            "panel_b": _slot("Panelist B", "reasoning", "Second vote. Best on a different model"),
            "panel_c": _slot("Panelist C", "reasoning", "Third vote. Best on a third model"),
        },
        agents=agents,
        checks=[
            CheckDef(id="dossier", kind="evidence_dossier", label="Evidence dossier"),
            CheckDef(id="tally", kind="tally_votes", label="Consensus tally",
                     params={"panel": first_ids, "propositions": "framer", "evidence": "dossier"}),
            CheckDef(id="final_tally", kind="tally_votes", label="Final tally",
                     params={"panel": second_ids, "propositions": "framer", "evidence": "dossier", "previous": "tally"}),
            CheckDef(id="seal", kind="audit_trail", label="Audit trail",
                     params={"answer": "rapporteur", "accepted": ["final_tally", "tally"]}),
        ],
        edges=[
            _edge(START, "dossier"),
            _edge("dossier", "framer", when="passed == true", label="EVIDENCE"),
            _edge("dossier", END, when="passed == false", label="NO EVIDENCE"),
            *[_edge("framer", node) for node in first_ids],
            _edge(first_ids, "tally"),
            _edge("tally", "rapporteur", when="contested_count == 0 and passed == true", label="CONSENSUS"),
            _edge("tally", END, when="contested_count == 0 and passed == false", label="NOTHING TO DECIDE"),
            # Otherwise: one more round, on the contested propositions only.
            *[_edge("tally", node) for node in second_ids],
            _edge(second_ids, "final_tally"),
            _edge("final_tally", "rapporteur", when="passed == true", label="QUORUM"),
            _edge("final_tally", END, when="passed == false", label="INSUFFICIENT"),
            _edge("rapporteur", "seal"),
            _edge("seal", "rapporteur", when="passed == false", label="UNSUPPORTED", max_loops=1),
            _edge("seal", END),
        ],
        layout=_layout([["dossier"], ["framer"], first_ids, ["tally"], second_ids, ["final_tally"], ["rapporteur"], ["seal"]]),
    )


BUILTIN: dict[str, Any] = {
    "adversarial_blackboard": adversarial_blackboard,
    "commit_dag": commit_dag,
    "failclosed_swarm": failclosed_swarm,
    "delphi_consensus": delphi_consensus,
}
