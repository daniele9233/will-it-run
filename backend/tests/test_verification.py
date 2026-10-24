"""Verification workflows: isolation, deterministic checks and the ledger.

The agents here are scripted to lie, skip and overreach on purpose. What is under test
is that the engine, not a prompt, stops it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentfit.events import EventBus
from agentfit.teams import templates as T
from agentfit.teams import verification as V
from agentfit.workflows import checks, schemas
from agentfit.workflows.compiler import build_messages, parse_structured, visible_outputs
from agentfit.workflows.dsl import AgentDef, CheckDef, WorkflowDef, dump_yaml, load_yaml
from agentfit.workflows.runner import RunError, RunManager
from agentfit.workflows.store import WorkflowStore
from agentfit.workflows.validator import validate
from fakes import FakeRuntime

EVIDENCE = (
    "The Mistral bridge was completed in 1934 and spans 412 metres across the Loire. "
    "It was closed for repairs between 1987 and 1989. Its designer, Anna Roche, also built the Vauban viaduct. "
    "Traffic on the bridge is limited to vehicles under 12 tonnes since the inspection of 2011."
)
TASK = f"When was the Mistral bridge built and who designed it?\n\n{EVIDENCE}"

CLAIM_ID = re.compile(r"c-[0-9a-f]{8}")
PROPOSITION_ID = re.compile(r"p-[0-9a-f]{8}")


def claim(statement: str, quote: str, source: str = "E1") -> dict[str, str]:
    return {"id": "made-up-by-the-model", "statement": statement, "quote": quote, "source": source}


TRUE_1 = claim("The bridge was completed in 1934.", "The Mistral bridge was completed in 1934")
TRUE_2 = claim("Anna Roche designed the bridge.", "Its designer, Anna Roche, also built the Vauban viaduct")
INVENTED = claim("The bridge cost 4 million francs.", "the construction cost reached 4 million francs")
OVERREACH = claim("The bridge has never been closed.", "It was closed for repairs between 1987 and 1989")


def user_text(messages: list[dict[str, Any]]) -> str:
    return str(messages[1]["content"])


def ids_in(messages: list[dict[str, Any]], pattern: re.Pattern[str] = CLAIM_ID) -> list[str]:
    return list(dict.fromkeys(pattern.findall(user_text(messages))))


def near(messages: list[dict[str, Any]], item_id: str) -> str:
    """The text of one item in a prompt: from its id up to the next item's id."""
    text = user_text(messages)
    start = text.index(item_id)
    following = [m.start() for m in re.finditer(r"[cp]-[0-9a-f]{8}", text) if m.start() > start and m.group() != item_id]
    return text[start: following[0] if following else start + 400]


def audit_all(holds: bool = True, reject: str = ""):
    """An auditor that gives every claim it is shown a verdict."""

    def reply(messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
        verdicts = []
        for claim_id in ids_in(messages):
            rejected = bool(reject) and reject in near(messages, claim_id)
            verdicts.append({"claim": claim_id, "holds": holds and not rejected, "objection": "goes beyond the quote" if rejected or not holds else ""})
        return {"summary": "audited", "verdicts": verdicts}

    return reply


def cite_all(messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
    # Only what the writer was given as material, not what it wrote itself on an earlier attempt.
    material = [{}, {"content": user_text(messages).split("## Your previous output")[0]}]
    used = ids_in(material) or ids_in(material, PROPOSITION_ID) or re.findall(r"branch_[ab]", user_text(material))
    return {"answer": "Built in 1934 " + " ".join(f"[{u}]" for u in used), "used": list(dict.fromkeys(used)), "caveats": []}


def execute(db, tmp_path: Path, definition: WorkflowDef, script: dict[str, list[Any]], task: str = TASK, evidence_dir: str | None = None):
    for slot_id, slot in definition.models.items():
        slot.model_key = f"model-{slot_id}"
    runtime = FakeRuntime(script)
    manager = RunManager(db, runtime, bus=EventBus(), vram_budget_gb=lambda: 14.2, checkpoint_path=":memory:")
    record = manager.start(definition, task, workspace=str(tmp_path / "ws"), evidence_dir=evidence_dir, background=False)
    return record, runtime, manager


def roles_called(runtime: FakeRuntime) -> list[str]:
    return list(dict.fromkeys(c["role"] for c in runtime.calls))


def prompt_of(runtime: FakeRuntime, role: str, index: int = 0) -> str:
    return user_text([c for c in runtime.calls if c["role"] == role][index]["messages"])


# -- building blocks ----------------------------------------------------------


def test_a_quote_must_be_in_the_evidence_word_for_word():
    documents = [{"id": "E1", "text": "The bridge was “completed”\n in 1934 — and spans 412 metres."}, {"id": "E2", "text": "Anna Roche designed it in her studio."}]
    assert checks.find_quote('the bridge was "completed" in 1934', documents) == ("E1", "")  # case, spacing, typographic quotes
    assert checks.find_quote("The bridge was ... spans 412 metres", documents)[0] == "E1"  # words left out, order kept
    assert checks.find_quote("Anna Roche designed it", documents, prefer="E1")[0] == "E2"  # found where it really is
    assert checks.find_quote("spans 412 metres ... The bridge was", documents)[0] is None  # order matters
    assert checks.find_quote("The bridge was finished in 1934", documents) == (None, "quote not found in the evidence")
    assert checks.find_quote("in 1934", documents)[0] is None  # too short to prove anything
    assert checks.find_quote("", documents) == (None, "no quote")


def test_ids_come_from_the_content_not_from_the_model():
    output = checks.stamp_ids({"claims": [dict(TRUE_1), dict(TRUE_1), dict(TRUE_2)], "propositions": [{"id": "x", "statement": "It is old."}]})
    ids = [c["id"] for c in output["claims"]]
    assert len(ids) == 2 and all(CLAIM_ID.fullmatch(i) for i in ids)  # duplicate dropped, ids rewritten
    assert ids[0] == checks.stamp_ids({"claims": [dict(TRUE_1)]})["claims"][0]["id"]  # same content, same id
    assert PROPOSITION_ID.fullmatch(output["propositions"][0]["id"])
    assert checks.digest({"b": 1, "a": 2}) == checks.digest({"a": 2, "b": 1})  # canonical, order-independent


def test_choices_restrict_a_field_for_the_decoder_and_for_the_parser():
    fmt = schemas.response_format("Routing", {"expert": ["code", "none"]})
    assert fmt["json_schema"]["schema"]["properties"]["expert"] == {"type": "string", "enum": ["code", "none"]}
    ok = parse_structured('{"expert": "code", "question": "q"}', "Routing", {"expert": ["code", "none"]})
    assert ok["expert"] == "code"
    with pytest.raises(ValueError, match="exactly one of: code, none"):
        parse_structured('{"expert": "astrology", "question": "q"}', "Routing", {"expert": ["code", "none"]})


def test_an_agent_is_shown_only_what_it_reads():
    definition = V.adversarial_blackboard()
    state: dict[str, Any] = {
        "task": TASK, "history": ["dossier", "researcher"],
        "outputs": {
            "dossier": {"passed": True, "documents": [{"id": "E1", "name": "Request", "text": EVIDENCE}], "sources": []},
            "researcher": {"summary": "s", "claims": [dict(TRUE_1, id="c-00000001")], "reasoning": "SECRET WORKING NOTES", "gaps": []},
        },
    }
    skeptic = definition.agent("skeptic")
    assert skeptic is not None
    assert [key for key, _, _ in visible_outputs(skeptic, state)] == ["dossier", "researcher.claims"]  # type: ignore[arg-type]
    prompt = build_messages(skeptic, state, definition, 16384)[1]["content"]  # type: ignore[arg-type]
    assert "SECRET WORKING NOTES" not in prompt and "c-00000001" in prompt
    assert "## Evidence" in prompt and "### E1 - Request" in prompt and "spans 412 metres" in prompt
    # An agent without a reading list still sees everything, as before.
    plain = AgentDef(id="x", role="X", model="research")
    assert "SECRET WORKING NOTES" in build_messages(plain, state, definition, 16384)[1]["content"]  # type: ignore[arg-type]


def test_reads_can_name_the_latest_of_several_nodes_and_an_input_budget_caps_the_prompt():
    definition = V.delphi_consensus()
    rapporteur = definition.agent("rapporteur")
    assert rapporteur is not None
    outputs = {"tally": {"accepted": [{"id": "p-1", "statement": "first round"}]}, "final_tally": {"accepted": [{"id": "p-2", "statement": "final round"}]}}
    first_only = {"task": "t", "history": ["tally"], "outputs": {"tally": outputs["tally"]}}
    both = {"task": "t", "history": ["tally", "final_tally"], "outputs": outputs}
    assert visible_outputs(rapporteur, first_only)[0][1] == "tally"  # type: ignore[arg-type]
    assert visible_outputs(rapporteur, both)[0][1] == "final_tally"  # type: ignore[arg-type]

    big = {"task": "t", "history": ["a"], "outputs": {"a": {"summary": "x" * 60000}}}
    free = AgentDef(id="r", role="R", model="m", reads=["a"])
    capped = AgentDef(id="r", role="R", model="m", reads=["a"], max_input_tokens=1000)
    assert len(build_messages(capped, big, definition, 32768)[1]["content"]) < 4500  # type: ignore[arg-type]
    assert len(build_messages(free, big, definition, 32768)[1]["content"]) > 40000  # type: ignore[arg-type]


def test_evidence_dossier_reads_text_files_and_records_their_hashes(tmp_path):
    folder = tmp_path / "docs"
    (folder / "sub").mkdir(parents=True)
    (folder / "report.md").write_text("# Report\n" + EVIDENCE, encoding="utf-8")
    (folder / "sub" / "notes.txt").write_text("Inspection of 2011 limited traffic to 12 tonnes on the bridge deck.", encoding="utf-8")
    (folder / "scan.pdf").write_bytes(b"%PDF-1.4 binary")
    check = CheckDef(id="dossier", kind="evidence_dossier", params={"include_task": False})
    ctx = checks.CheckContext(task="q", outputs={}, ledger=[], history=[], definition=V.adversarial_blackboard(), evidence_dir=folder)
    out = checks.run_check(check, ctx)
    assert out["passed"] and out["verdict"] == "EVIDENCE_READY"
    assert [s["name"] for s in out["sources"]] == ["report.md", "sub/notes.txt"]
    assert all(len(s["sha256"]) == 64 for s in out["sources"]) and out["dossier_sha256"]
    assert out["skipped"] == ["scan.pdf: only plain-text files are read"]  # said, not silently ignored
    # Too little evidence stops the run before any model; so does a request that is too long.
    empty = checks.run_check(check, checks.CheckContext(task="q", outputs={}, ledger=[], history=[], definition=ctx.definition))
    assert not empty["passed"] and empty["verdict"] == "NO_EVIDENCE"
    long = checks.run_check(CheckDef(id="g", kind="evidence_dossier", params={"max_task_tokens": 50}),
                            checks.CheckContext(task="word " * 500, outputs={}, ledger=[], history=[], definition=ctx.definition))
    assert long["verdict"] == "REQUEST_TOO_LONG"


def test_a_commit_changed_after_its_review_is_dropped():
    definition = V.commit_dag()
    commit_a = {"summary": "a", "content": "def f(): return 1", "assumptions": []}
    commit_b = {"summary": "b", "content": "def f(): return 2", "assumptions": []}
    ledger = [{"node": "reviewer", "kind": "agent", "sha256": "x", "read": {"branch_a": checks.digest(commit_a), "branch_b": checks.digest(commit_b)}}]
    review = {"summary": "both fine", "verdicts": [{"commit": "branch_a", "approved": True, "issues": []}, {"commit": "branch_b", "approved": True, "issues": []}]}
    gate = definition.check("gate")
    assert gate is not None

    def run(outputs: dict[str, Any]) -> dict[str, Any]:
        return checks.run_check(gate, checks.CheckContext(task="t", outputs=outputs, ledger=ledger, history=[], definition=definition))

    clean = run({"branch_a": commit_a, "branch_b": commit_b, "reviewer": review})
    assert clean["approved_count"] == 2 and clean["verdict"] == "COMMITS_VERIFIED"
    tampered = run({"branch_a": commit_a, "branch_b": {**commit_b, "content": "def f(): return 3"}, "reviewer": review})
    assert [c["commit"] for c in tampered["approved"]] == ["branch_a"]
    assert "is not what is there now" in tampered["dropped"][0]["reason"]


def test_votes_without_evidence_weigh_less_and_unbacked_dissent_weighs_nothing():
    definition = V.delphi_consensus()
    documents = {"passed": True, "documents": [{"id": "E1", "name": "Request", "text": EVIDENCE}]}
    propositions = checks.stamp_ids({"propositions": [{"statement": "The bridge was completed in 1934."}, {"statement": "The bridge is closed today."}]})
    p1, p2 = (p["id"] for p in propositions["propositions"])
    backed = {"vote": "AGREE", "quote": "The Mistral bridge was completed in 1934", "source": "E1"}
    tally = definition.check("tally")
    final = definition.check("final_tally")
    assert tally is not None and final is not None

    def ballot(*votes: tuple[str, dict[str, str]]) -> dict[str, Any]:
        return {"votes": [{"proposition": pid, **vote} for pid, vote in votes]}

    outputs: dict[str, Any] = {
        "dossier": documents, "framer": propositions,
        "panelist_a": ballot((p1, backed), (p2, {"vote": "AGREE", "quote": "", "source": ""})),
        "panelist_b": ballot((p1, backed), (p2, {"vote": "AGREE", "quote": "the bridge is closed to all traffic", "source": "E1"})),
        "panelist_c": ballot((p1, {"vote": "UNSURE"}), (p2, {"vote": "DISAGREE", "quote": "", "source": ""})),
    }
    ctx = checks.CheckContext(task="t", outputs=outputs, ledger=[], history=[], definition=definition)
    first = checks.run_check(tally, ctx)
    by_id = {r["id"]: r for r in first["results"]}
    assert by_id[p1]["outcome"] == "ACCEPTED" and by_id[p1]["agree_weight"] == 2.0
    # Two unbacked AGREE votes are worth 1.0 of 3 and hold no real quote: not a consensus.
    assert by_id[p2]["outcome"] == "CONTESTED" and by_id[p2]["agree_weight"] == 1.0 and by_id[p2]["leaning"] == "AGREE"
    assert first["contested_count"] == 1 and first["verdict"] == "CONSENSUS"  # half decided meets the default quorum
    # The feedback for round two shows counts and quotes, never names.
    assert "panelist" not in json.dumps(first["feedback"]) and first["feedback"][0]["agree"] == 2

    outputs["tally"] = first
    outputs.update({
        "second_a": ballot((p2, {"vote": "DISAGREE", "quote": "", "source": ""})),  # against the majority, no quote: weight 0
        "second_b": ballot((p2, {"vote": "DISAGREE", "quote": "Traffic on the bridge is limited to vehicles under 12 tonnes", "source": "E1"})),
        "second_c": ballot((p2, {"vote": "DISAGREE", "quote": "limited to vehicles under 12 tonnes since the inspection of 2011", "source": "E1"})),
    })
    second = checks.run_check(final, ctx)
    result = {r["id"]: r for r in second["results"]}
    assert result[p1]["outcome"] == "ACCEPTED" and result[p1]["round"] == 1  # settled earlier, not reopened
    assert result[p2]["outcome"] == "REJECTED" and result[p2]["disagree_weight"] == 2.0 and result[p2]["penalized_votes"] == 1
    assert second["passed"] and second["contested_count"] == 0


def test_validator_rejects_broken_isolation_and_unknown_checks():
    definition = V.adversarial_blackboard()
    assert validate(definition).ok
    broken = definition.model_copy(deep=True)
    broken.agents[1].reads = ["dossier", "ghost.claims"]
    broken.agents[0].choices = {"nonexistent": ["a"]}
    broken.checks.append(CheckDef(id="mystery", kind="trust_me"))
    broken.checks[1].params["audit"] = "nobody"
    messages = " | ".join(i.message for i in validate(broken).errors)
    assert "there is no node 'ghost'" in messages and "not a text field of ClaimSet" in messages
    assert "unknown kind 'trust_me'" in messages and "points at node 'nobody'" in messages


def test_verification_templates_survive_yaml_and_are_listed_apart(db):
    for name in V.BUILTIN:
        definition = T.BUILTIN[name]()
        assert load_yaml(dump_yaml(definition)).model_dump() == definition.model_dump()
        assert definition.workflow.category == "verification" and definition.checks
    listed = {w.id: w for w in WorkflowStore(db).list()}
    assert listed["template:delphi_consensus"].category == "verification" and listed["template:delphi_consensus"].evidence
    assert listed["template:commit_dag"].checks == 2 and not listed["template:commit_dag"].evidence
    assert listed["template:coding"].category == "team" and listed["template:coding"].checks == 0


# -- adversarial blackboard ---------------------------------------------------


def test_blackboard_keeps_only_claims_that_are_quoted_and_survive_the_skeptic(db, tmp_path):
    script = {
        "Researcher": [{"summary": "three claims", "claims": [TRUE_1, INVENTED, OVERREACH], "reasoning": "SECRET WORKING NOTES", "gaps": []}],
        "Skeptic": [audit_all(reject="never been closed")],
        "Writer": [cite_all],
    }
    record, runtime, manager = execute(db, tmp_path, V.adversarial_blackboard(), script)
    assert record.status == "completed", record.error
    result = record.result or {}
    kept = result["outputs"]["filter"]
    assert [c["statement"] for c in kept["accepted"]] == [TRUE_1["statement"]]
    reasons = {d["statement"]: d["reason"] for d in kept["discarded"]}
    assert reasons[INVENTED["statement"]] == "quote not found in the evidence"
    assert reasons[OVERREACH["statement"]].startswith("the auditor rejected it")
    # Isolation held in the real prompts, not only in the definition.
    assert "SECRET WORKING NOTES" not in prompt_of(runtime, "Skeptic")
    writer_prompt = prompt_of(runtime, "Writer")
    assert TRUE_1["statement"] in writer_prompt
    assert INVENTED["statement"] not in writer_prompt and OVERREACH["statement"] not in writer_prompt and "## Evidence" not in writer_prompt
    # The released answer and its trail.
    final = result["final"]
    assert final["verdict"] == "SEALED" and final["markdown"].startswith("Built in 1934")
    assert [s["node"] for s in final["steps"]] == ["dossier", "researcher", "skeptic", "filter", "writer"]
    ledger = {e["node"]: e for e in result["ledger"]}
    assert set(ledger["skeptic"]["read"]) == {"dossier", "researcher.claims"}
    assert ledger["skeptic"]["read"]["dossier"] == ledger["dossier"]["sha256"]  # it read exactly what the dossier produced
    assert ledger["filter"]["read"]["skeptic"] == ledger["skeptic"]["sha256"]
    assert ledger["researcher"]["model"] == "model-research" and ledger["filter"]["model"] == ""
    completed = {e["payload"]["node"]: e["payload"] for e in manager.events(record.id) if e["type"] == "node.completed"}
    assert completed["researcher"]["sha256"] == ledger["researcher"]["sha256"]
    assert completed["dossier"]["kind"] == "check" and "documents" not in completed["dossier"]["output"]


def test_blackboard_refuses_when_nothing_can_be_verified(db, tmp_path):
    script = {"Researcher": [{"summary": "s", "claims": [INVENTED]}], "Skeptic": [audit_all()], "Writer": [cite_all]}
    record, runtime, _ = execute(db, tmp_path, V.adversarial_blackboard(), script)
    assert record.status == "blocked"
    assert "0 of 1 claims kept" in (record.error or "")
    assert roles_called(runtime) == ["Researcher", "Skeptic"]  # the writer never ran
    assert [c["role"] for c in runtime.calls].count("Researcher") == 2  # one bounded second attempt
    assert "quote not found in the evidence" in prompt_of(runtime, "Researcher", 1)  # told why, as feedback
    assert (record.result or {})["final"]["verdict"] == "NO_VERIFIED_CLAIMS"


def test_an_agent_that_finds_nothing_ends_the_run_with_its_own_words_not_an_answer(db, tmp_path):
    nothing = {"summary": "The text does not say.", "claims": [], "gaps": ["The price is not mentioned."]}
    record, runtime, _ = execute(db, tmp_path, V.adversarial_blackboard(), {"Researcher": [nothing], "Skeptic": [audit_all()], "Writer": [cite_all]})
    assert record.status == "blocked" and "Writer" not in roles_called(runtime)
    assert record.error == (
        "No claim was offered with a quote, so there is nothing to answer from. "
        "The agent reported that the evidence does not say: The price is not mentioned."
    )
    assert (record.result or {})["final"]["gaps"] == ["The price is not mentioned."]


def test_a_claim_the_auditor_skipped_is_discarded(db, tmp_path):
    def skip_second(messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "looked at one", "verdicts": [{"claim": ids_in(messages)[0], "holds": True}, {"claim": "c-ffffffff", "holds": True}]}

    script = {"Researcher": [{"summary": "s", "claims": [TRUE_1, TRUE_2]}], "Skeptic": [skip_second], "Writer": [cite_all]}
    record, _, _ = execute(db, tmp_path, V.adversarial_blackboard(), script)
    kept = (record.result or {})["outputs"]["filter"]
    assert record.status == "completed" and kept["accepted_count"] == 1
    assert kept["discarded"][0]["reason"] == "the auditor gave no verdict on it"
    assert kept["unknown_verdicts"] == ["c-ffffffff"]  # a verdict on a claim that does not exist changes nothing


def test_no_model_is_called_without_evidence(db, tmp_path):
    record, runtime, _ = execute(db, tmp_path, V.adversarial_blackboard(), {}, task="Tell me about bridges.")
    assert record.status == "blocked" and runtime.calls == [] and runtime.load_calls == []
    assert "No model was called" in (record.error or "")


def test_an_answer_citing_something_never_accepted_is_withheld(db, tmp_path):
    bad = {"answer": "It cost 4 million francs [c-deadbeef].", "used": ["c-deadbeef"], "caveats": []}
    script = {"Researcher": [{"summary": "s", "claims": [TRUE_1]}], "Skeptic": [audit_all()], "Writer": [bad, cite_all]}
    record, runtime, _ = execute(db, tmp_path, V.adversarial_blackboard(), script)
    assert record.status == "completed" and [c["role"] for c in runtime.calls].count("Writer") == 2
    assert "c-deadbeef" in prompt_of(runtime, "Writer", 1)  # sent back once with the reason

    script["Writer"] = [bad]
    record, _, _ = execute(db, tmp_path, V.adversarial_blackboard(), script)
    final = (record.result or {})["final"]
    assert record.status == "blocked" and final["verdict"] == "UNSUPPORTED_ANSWER"
    assert "markdown" not in final and final["unknown_citations"] == ["c-deadbeef"]


def test_documents_folder_is_evidence_and_must_exist(db, tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "bridge.txt").write_text(EVIDENCE, encoding="utf-8")
    script = {"Researcher": [{"summary": "s", "claims": [claim(TRUE_2["statement"], TRUE_2["quote"], "E2")]}], "Skeptic": [audit_all()], "Writer": [cite_all]}
    record, runtime, _ = execute(db, tmp_path, V.adversarial_blackboard(), script, task="Who designed the Mistral bridge?", evidence_dir=str(folder))
    assert record.status == "completed", record.error
    assert "### E2 - bridge.txt" in prompt_of(runtime, "Researcher")
    assert (record.result or {})["outputs"]["filter"]["accepted"][0]["source"] == "E2"
    with pytest.raises(RunError, match="documents folder does not exist"):
        execute(db, tmp_path, V.adversarial_blackboard(), script, evidence_dir=str(tmp_path / "missing"))


# -- commit DAG ---------------------------------------------------------------


def test_commit_dag_merges_only_what_was_approved(db, tmp_path):
    review = {"summary": "A meets the spec, B does not", "verdicts": [
        {"commit": "branch_a", "approved": True, "issues": []},
        {"commit": "branch_b", "approved": False, "issues": ["criterion 2 is not met"]},
    ]}
    script = {
        "Specification": [{"objective": "a function", "acceptance_criteria": ["returns 1", "handles None"]}],
        "Author A": [{"summary": "direct", "content": "CONTENT-OF-A", "assumptions": []}],
        "Author B": [{"summary": "careful", "content": "CONTENT-OF-B", "assumptions": []}],
        "Independent Reviewer": [review],
        "Merger": [cite_all],
    }
    record, runtime, _ = execute(db, tmp_path, V.commit_dag(), script, task="Write a function that returns 1.")
    assert record.status == "completed", record.error
    result = record.result or {}
    gate = result["outputs"]["gate"]
    assert [c["commit"] for c in gate["approved"]] == ["branch_a"]
    assert gate["dropped"][0]["reason"] == "the reviewer rejected it: criterion 2 is not met"
    merger_prompt = prompt_of(runtime, "Merger")
    assert "CONTENT-OF-A" in merger_prompt and "CONTENT-OF-B" not in merger_prompt
    # The authors worked blind to each other, and the reviewer's approval is tied to the text it saw.
    assert "CONTENT-OF-B" not in prompt_of(runtime, "Author A") and "CONTENT-OF-A" not in prompt_of(runtime, "Author B")
    ledger = {e["node"]: e for e in result["ledger"]}
    assert ledger["reviewer"]["read"]["branch_a"] == ledger["branch_a"]["sha256"] == gate["approved"][0]["sha256"]
    assert result["final"]["used"] == ["branch_a"] and result["final"]["verdict"] == "SEALED"


def test_commit_dag_stops_when_nothing_is_approved(db, tmp_path):
    script = {
        "Specification": [{"objective": "a function"}],
        "Author A": [{"summary": "a", "content": "x"}], "Author B": [{"summary": "b", "content": "y"}],
        "Independent Reviewer": [{"summary": "no", "verdicts": [{"commit": "branch_a", "approved": False, "issues": ["wrong"]}]}],
        "Merger": [cite_all],
    }
    record, runtime, _ = execute(db, tmp_path, V.commit_dag(), script, task="Write a function.")
    assert record.status == "blocked" and "Merger" not in roles_called(runtime)
    dropped = {d["commit"]: d["reason"] for d in (record.result or {})["final"]["dropped"]}
    assert dropped["branch_b"] == "the reviewer gave no verdict on it"  # silence is not approval


# -- fail-closed swarm --------------------------------------------------------


def swarm_script(**overrides: list[Any]) -> dict[str, list[Any]]:
    script: dict[str, list[Any]] = {
        "Router": [{"expert": "data", "question": "When was it built?", "reason": "a date"}],
        "Data Expert": [{"summary": "s", "claims": [TRUE_1]}],
        "Software Expert": [{"summary": "s", "claims": [INVENTED]}],
        "Domain Validator": [audit_all()],
        "Writer": [cite_all],
    }
    script.update(overrides)
    return script


def test_swarm_activates_only_the_expert_the_router_may_name(db, tmp_path):
    router = [{"expert": "astrology", "question": "q"}, {"expert": "data", "question": "When was it built?", "reason": "a date"}]
    record, runtime, _ = execute(db, tmp_path, V.failclosed_swarm(), swarm_script(Router=router))
    assert record.status == "completed", record.error
    called = roles_called(runtime)
    assert "Data Expert" in called and not {"Software Expert", "Systems Expert", "Document Expert"} & set(called)
    # The decoder is given the allowed list, and an answer outside it is refused and retried.
    router_calls = [c for c in runtime.calls if c["role"] == "Router"]
    assert router_calls[0]["kwargs"]["response_format"]["json_schema"]["schema"]["properties"]["expert"]["enum"] == ["code", "data", "systems", "language", "none"]
    assert "expert must be exactly one of" in router_calls[1]["messages"][-1]["content"]
    final = (record.result or {})["final"]
    assert final["verdict"] == "SEALED" and final["activations"]["expert_data"] == 1
    assert "When was it built?" in prompt_of(runtime, "Data Expert")


def test_swarm_refuses_when_the_router_names_no_expert(db, tmp_path):
    record, runtime, _ = execute(db, tmp_path, V.failclosed_swarm(), swarm_script(Router=[{"expert": "none", "question": "", "reason": "off topic"}]))
    assert record.status == "blocked" and roles_called(runtime) == ["Router"]
    assert (record.result or {})["final"]["verdict"] == "NO_TRUSTED_ANSWER"


def test_swarm_stops_at_its_activation_budget(db, tmp_path):
    router = [{"expert": "code", "question": "q", "reason": "r"}]
    record, runtime, _ = execute(db, tmp_path, V.failclosed_swarm(), swarm_script(Router=router))
    assert record.status == "blocked"
    assert [c["role"] for c in runtime.calls].count("Software Expert") == 3  # first try plus two retries, never a fourth
    final = (record.result or {})["final"]
    assert final["verdict"] == "NO_TRUSTED_ANSWER" and final["activations"]["expert_code"] == 3
    assert "Writer" not in roles_called(runtime)


def test_swarm_gate_refuses_an_oversized_request_before_any_model(db, tmp_path):
    record, runtime, _ = execute(db, tmp_path, V.failclosed_swarm(), swarm_script(), task="word " * 6000)
    assert record.status == "blocked" and runtime.calls == []
    assert (record.result or {})["final"]["verdict"] == "REQUEST_TOO_LONG"


# -- Delphi -------------------------------------------------------------------

PROPOSITIONS = {"question": "q", "propositions": [{"statement": "The bridge was completed in 1934."}, {"statement": "Anna Roche designed the bridge."}]}
QUOTES = {"1934": TRUE_1["quote"], "Roche": TRUE_2["quote"]}


def voter(*plan: tuple[str, str, bool]):
    """A panelist: for each proposition containing ``key``, vote ``choice`` with or without a real quote."""

    def reply(messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
        votes = []
        for pid in ids_in(messages, PROPOSITION_ID):
            for key, choice, backed in plan:
                if key in near(messages, pid):
                    votes.append({"proposition": pid, "vote": choice, "quote": QUOTES[key] if backed else "", "source": "E1"})
        return {"votes": votes, "note": ""}

    return reply


def test_delphi_reaches_consensus_in_one_blind_round(db, tmp_path):
    agree = voter(("1934", "AGREE", True), ("Roche", "AGREE", True))
    script = {"Framer": [PROPOSITIONS], "Panelist A": [agree], "Panelist B": [agree], "Panelist C": [voter(("1934", "AGREE", True), ("Roche", "UNSURE", False))], "Rapporteur": [cite_all]}
    record, runtime, _ = execute(db, tmp_path, V.delphi_consensus(), script)
    assert record.status == "completed", record.error
    result = record.result or {}
    assert result["outputs"]["tally"]["contested_count"] == 0 and len(result["outputs"]["tally"]["accepted"]) == 2
    assert not [r for r in roles_called(runtime) if "round 2" in r]  # no second round when nothing is contested
    # Blind: no panelist's prompt carries another panelist's ballot, and none can look anything up.
    for role in ("Panelist A", "Panelist B", "Panelist C"):
        call = next(c for c in runtime.calls if c["role"] == role)
        assert '"vote"' not in user_text(call["messages"]) and not call["kwargs"].get("tools")
    assert result["final"]["verdict"] == "SEALED" and len(result["final"]["used"]) == 2


def test_delphi_second_round_settles_what_split_the_panel(db, tmp_path):
    script = {
        "Framer": [PROPOSITIONS],
        "Panelist A": [voter(("1934", "AGREE", True), ("Roche", "AGREE", True))],
        "Panelist B": [voter(("1934", "AGREE", True), ("Roche", "DISAGREE", False))],
        "Panelist C": [voter(("1934", "AGREE", True), ("Roche", "UNSURE", False))],
        "Panelist A, round 2": [voter(("Roche", "AGREE", True))],
        "Panelist B, round 2": [voter(("Roche", "AGREE", True))],
        "Panelist C, round 2": [voter(("Roche", "DISAGREE", False))],
        "Rapporteur": [cite_all],
    }
    record, runtime, _ = execute(db, tmp_path, V.delphi_consensus(), script)
    assert record.status == "completed", record.error
    result = record.result or {}
    assert result["outputs"]["tally"]["contested_count"] == 1
    final_tally = result["outputs"]["final_tally"]
    assert len(final_tally["accepted"]) == 2 and final_tally["contested_count"] == 0
    second = prompt_of(runtime, "Panelist B, round 2")
    assert "Anna Roche designed the bridge." in second and "completed in 1934." not in second.split("## Work so far")[1]  # only what is contested
    assert "panelist_" not in second  # counts, not names
    assert len(ids_in([{}, {"content": prompt_of(runtime, "Rapporteur")}], PROPOSITION_ID)) == 2  # the final result, both settled


def test_delphi_ends_with_insufficient_consensus_instead_of_an_answer(db, tmp_path):
    split = {
        "Framer": [PROPOSITIONS],
        "Panelist A": [voter(("1934", "AGREE", False), ("Roche", "AGREE", False))],
        "Panelist B": [voter(("1934", "DISAGREE", False), ("Roche", "DISAGREE", False))],
        "Panelist C": [voter(("1934", "UNSURE", False), ("Roche", "UNSURE", False))],
        "Rapporteur": [cite_all],
    }
    for role in ("A", "B", "C"):
        split[f"Panelist {role}, round 2"] = split[f"Panelist {role}"]
    record, runtime, _ = execute(db, tmp_path, V.delphi_consensus(), split)
    assert record.status == "blocked" and "Rapporteur" not in roles_called(runtime)
    final = (record.result or {})["final"]
    assert final["verdict"] == "INSUFFICIENT_CONSENSUS" and final["contested_count"] == 2 and final["accepted"] == []
