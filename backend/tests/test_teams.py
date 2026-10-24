"""Team builder, model sharing and the resource optimizer."""

from __future__ import annotations

from typing import Any

import pytest

from agentfit import storage
from agentfit.catalog import Alignment, CatalogService
from agentfit.config import AlignmentPreference, Settings
from agentfit.events import EventBus
from agentfit.hardware.models import DriveInfo
from agentfit.lmstudio.models import InstalledModel
from agentfit.teams import roles as R
from agentfit.teams import specializations as S
from agentfit.teams import templates as T
from agentfit.teams.builder import (
    ModelChoice,
    TeamBuilder,
    TeamError,
    TeamRequest,
    build_workflow,
    plan_to_summary,
    resolve_model_keys,
)
from agentfit.workflows.dsl import LoadingPolicy
from agentfit.workflows.runner import TERMINAL, RunManager
from agentfit.workflows.validator import validate
from conftest import budget_for
from fakes import FakeRuntime, auto_reply
from test_catalog import FakeHub, make_entry

GIB = 1024**3

GENERAL = make_entry("unsloth/Qwen3-14B-GGUF", 14.0, files={"Q4_K_M": 8.4, "Q5_K_M": 9.8})
CODER = make_entry("bartowski/Qwen2.5-Coder-14B-Instruct-GGUF", 14.0, files={"Q4_K_M": 8.4, "Q5_K_M": 9.8})
UNCENSORED = make_entry("mradermacher/Huihui-Qwen3-14B-abliterated-GGUF", 14.0, files={"Q4_K_M": 8.4, "Q5_K_M": 9.8})
SMALL = make_entry("unsloth/Qwen3-4B-GGUF", 4.0, files={"Q4_K_M": 2.5, "Q8_0": 4.3})
BIG = make_entry("unsloth/Qwen3-32B-GGUF", 32.0, files={"Q3_K_M": 15.2, "Q4_K_M": 19.8})


def drives(free_gb: float = 400) -> list[DriveInfo]:
    return [DriveInfo(letter="D:", filesystem="NTFS", total_bytes=931 * GIB, free_bytes=int(free_gb * GIB))]


@pytest.fixture
def make_builder(db, monkeypatch):
    monkeypatch.setattr("agentfit.catalog.service.load_seed", lambda: ())

    def factory(entries=None, *, fixture="rtx4080super_16gb_32gb", installed=None, free_gb=400.0, settings=None) -> TeamBuilder:
        settings = settings or Settings()
        entries = entries if entries is not None else [GENERAL, CODER, UNCENSORED, SMALL]
        catalog = CatalogService(db, settings, hub=FakeHub(entries))  # type: ignore[arg-type]
        for entry in entries:
            catalog._cache_put(entry)
        monkeypatch.setattr(storage, "free_gb_at", lambda path: free_gb)
        return TeamBuilder(catalog, settings, budget_for(fixture, settings), installed or [], drives(free_gb), "D:\\Models")

    return factory


# -- roles and workflows ------------------------------------------------------


def test_single_specialization_uses_its_template():
    workflow, specs, _ = build_workflow(TeamRequest(specializations=["coding"]))
    assert specs == ["coding"]
    assert [a.id for a in workflow.agents] == ["tech_lead", "planner", "developer", "test_runner", "code_reviewer"]
    assert validate(workflow).ok


def test_specialization_focus_reaches_the_prompts():
    workflow, _, _ = build_workflow(TeamRequest(specializations=["rust"]))
    developer = workflow.agent("developer")
    assert developer is not None and "Rust: ownership" in developer.system_prompt


def test_acceptance_coding_devops_documentation_team():
    workflow, specs, _ = build_workflow(TeamRequest(specializations=["coding", "devops", "enterprise_docs"]))
    roles = [a.role for a in workflow.agents]
    assert specs == ["coding", "devops", "enterprise_docs"]
    for expected in ("Supervisor", "Infrastructure Architect", "Developer", "Test Runner", "DevOps Engineer", "Validation Engineer", "Code Reviewer", "Documentation Specialist", "Technical Reviewer"):
        assert expected in roles
    assert set(workflow.models) == {R.REASONING, R.CODING, R.WRITING}
    result = validate(workflow)
    assert result.ok, [i.message for i in result.errors]
    assert workflow.approval("approve_plan") is not None
    labels = {(e.sources[0], e.label) for e in workflow.edges if e.label}
    assert ("test_runner", "FAIL") in labels and ("validation_engineer", "ERROR") in labels and ("code_reviewer", "CHANGES") in labels
    assert all(e.max_loops for e in workflow.edges if e.label in ("FAIL", "ERROR", "CHANGES"))


def test_custom_postgresql_team_matches_the_expected_roster():
    request = TeamRequest(description="I want a team specialized in enterprise PostgreSQL Patroni clusters, WAL management, pgBackRest, performance and documentation.")
    workflow, specs, _ = build_workflow(request)
    assert "postgresql" in specs
    assert [a.role for a in workflow.agents] == [
        "Supervisor", "Database Architect", "PostgreSQL Specialist", "Patroni Specialist",
        "Backup / Recovery Specialist", "Performance Specialist", "Documentation Specialist", "Technical Reviewer",
    ]
    patroni = workflow.agent("patroni_specialist")
    assert patroni is not None and "Patroni high availability" in patroni.goal and len(patroni.system_prompt) > 300
    assert validate(workflow).ok


def test_custom_kubernetes_team():
    workflow, specs, _ = build_workflow(TeamRequest(description="I need an AI team specialized in creating enterprise Kubernetes architecture."))
    roles = [a.role for a in workflow.agents]
    assert specs == ["kubernetes"]
    for expected in ("Supervisor", "Infrastructure Architect", "Kubernetes Architect", "DevOps Engineer", "Validation Engineer", "Documentation Specialist"):
        assert expected in roles
    assert validate(workflow).ok


def test_unrecognised_description_still_builds_a_team():
    workflow, specs, _ = build_workflow(TeamRequest(description="Help me plan a beekeeping business"))
    assert specs == [] and len(workflow.agents) >= 3 and validate(workflow).ok
    specialist = workflow.agent("domain_specialist")
    assert specialist is not None and "beekeeping" in specialist.goal


def test_empty_request_is_rejected():
    with pytest.raises(TeamError, match="Choose at least one"):
        build_workflow(TeamRequest())
    with pytest.raises(TeamError, match="Unknown specialization"):
        build_workflow(TeamRequest(specializations=["telepathy"]))


def test_image_team_says_what_lmstudio_cannot_do():
    workflow, _, notes = build_workflow(TeamRequest(specializations=["image"]))
    assert any("do not run in LM Studio" in n and "ComfyUI" in n for n in notes)
    assert [a.role for a in workflow.agents] == ["Supervisor", "Prompt Engineer", "Vision Critic", "Technical Reviewer"]


def test_specialization_catalog_covers_every_group():
    groups = {g["group"]: [i["id"] for i in g["items"]] for g in S.catalog()}
    assert list(groups) == S.GROUP_ORDER
    for needed in ("coding", "threejs", "kubernetes", "terraform", "postgresql", "enterprise_docs", "translation", "legal_analysis", "video", "computer_use"):
        assert any(needed in ids for ids in groups.values())


def test_free_text_detection():
    text = "I want an uncensored AI team excellent at coding, Linux, Kubernetes, Terraform and technical documentation."
    assert S.detect_specializations(text) == ["coding", "linux", "kubernetes", "terraform", "technical_docs"]
    assert S.wants_uncensored(text) and not S.wants_uncensored("a coding team")


def test_every_role_is_fully_defined():
    for role in R.ROLES.values():
        assert role.goal and role.responsibilities and role.rules and role.input
        assert role.slot in (R.REASONING, R.CODING, R.WRITING, R.VISION) and role.phase in R.PHASE_ORDER
        assert len(role.system_prompt()) > 300


# -- agents are not models ----------------------------------------------------


def test_many_agents_share_few_models(make_builder):
    plan = make_builder().plan(TeamRequest(specializations=["coding", "devops", "enterprise_docs"]))
    assert len(plan.agents) == 9 and 1 <= len(plan.models) <= 3
    assert len(plan.agents) > len(plan.models)
    assert sorted(a for m in plan.models for a in m.agents) == sorted(a.id for a in plan.agents)
    assert "agents run on" in plan.explanations[0]
    summary = plan_to_summary(plan)
    assert summary["agents"] == 9 and summary["models"] == len(plan.models) and summary["can_build"]


def test_single_shared_model_when_specialists_add_little(make_builder):
    plan = make_builder([GENERAL, SMALL]).plan(TeamRequest(specializations=["coding", "devops", "enterprise_docs"]))
    assert len(plan.models) == 1
    assert plan.models[0].loading_policy == LoadingPolicy.KEEP_LOADED
    assert plan.resource_plan.model_switches_per_run == 0
    assert all(a.loading_policy == LoadingPolicy.SHARED_INSTANCE for a in plan.workflow.agents)
    assert set(plan.models[0].slots) == {R.REASONING, R.CODING, R.WRITING}


def test_one_model_for_all_when_asked(make_builder):
    request = TeamRequest(specializations=["coding", "devops", "enterprise_docs"], model_strategy="single")
    plan = make_builder().plan(request)
    assert len(plan.models) == 1 and plan.resource_plan.model_switches_per_run == 0
    assert any("One model for every role, as requested" in line for line in plan.explanations)


def test_a_model_per_role_when_asked_even_if_the_gain_is_small(make_builder):
    spec = ["coding", "devops", "enterprise_docs"]
    automatic = make_builder().plan(TeamRequest(specializations=spec))
    per_role = make_builder().plan(TeamRequest(specializations=spec, model_strategy="specialists"))
    assert len(per_role.models) >= len(automatic.models)
    # Every slot gets the model that scores best for that slot alone.
    by_slot = {slot: m for m in per_role.models for slot in m.slots}
    assert set(by_slot) == {R.REASONING, R.CODING, R.WRITING}
    if len(per_role.models) > 1:
        # Models that cannot share the VRAM are swapped: the plan says how often.
        assert per_role.resource_plan.model_switches_per_run >= 1
        assert per_role.resource_plan.peak_vram_gb <= per_role.resource_plan.usable_vram_gb


def test_resource_plan_fits_the_budget(make_builder):
    plan = make_builder().plan(TeamRequest(specializations=["coding", "devops", "enterprise_docs"]))
    rp = plan.resource_plan
    assert rp.usable_vram_gb == pytest.approx(14.2, abs=0.1)
    assert rp.peak_vram_gb <= rp.usable_vram_gb and rp.status in ("SAFE", "TIGHT")
    assert len(rp.timeline) == len(plan.agents)
    assert all(seg.loaded_vram_gb <= rp.usable_vram_gb for seg in rp.timeline)
    assert plan.fit in ("PERFECT", "EXCELLENT", "GOOD") and plan.expected_speed in ("EXCELLENT", "GOOD")


def test_workflow_slots_receive_the_chosen_models(make_builder):
    plan = make_builder().plan(TeamRequest(specializations=["coding"]))
    for slot in plan.workflow.models.values():
        assert slot.repo and slot.quant and slot.vram_gb and slot.loading_policy
    assert plan.workflow.resource_plan["peak_vram_gb"] == plan.resource_plan.peak_vram_gb
    assert "models:" in plan.workflow_yaml and validate(plan.workflow).ok


# -- the optimizer ------------------------------------------------------------


def three_model_plan(builder: TeamBuilder) -> Any:
    """Force three different models so loading policies have to be planned."""
    workflow, _, _ = build_workflow(TeamRequest(specializations=["coding", "devops", "enterprise_docs"]))
    cat = builder.catalog
    chosen = {
        R.REASONING: cat.evaluate(GENERAL, builder.budget, S.SLOT_PROFILES[R.REASONING], quant="Q4_K_M"),
        R.CODING: cat.evaluate(CODER, builder.budget, S.SLOT_PROFILES[R.CODING], quant="Q5_K_M"),
        R.WRITING: cat.evaluate(SMALL, builder.budget, S.SLOT_PROFILES[R.WRITING], quant="Q8_0"),
    }
    return workflow, chosen, builder.plan_resources(workflow, chosen)


def test_optimizer_keeps_the_busiest_model_and_loads_the_rest_on_demand(make_builder):
    builder = make_builder()
    _, chosen, (plan, policies) = three_model_plan(builder)
    total = sum(c.fit.vram_required_gb for c in chosen.values())
    assert total > builder.budget.vram_usable_gb  # they cannot all stay loaded
    assert policies[R.CODING] == LoadingPolicy.KEEP_LOADED  # five of nine agents use it
    assert policies[R.REASONING] == LoadingPolicy.LOAD_ON_DEMAND
    assert plan.peak_vram_gb <= builder.budget.vram_usable_gb and plan.status != "OVER"
    opt = plan.optimization
    assert opt.needed and opt.before_models_loaded == 3 and opt.before_peak_gb == pytest.approx(total, abs=0.2)
    assert opt.after_persistent == 1 and opt.after_on_demand == 2 and opt.after_peak_gb == plan.peak_vram_gb
    assert "recommended maximum" in opt.message
    assert plan.model_switches_per_run >= 2


def test_optimizer_timeline_marks_switches_and_sharing(make_builder):
    builder = make_builder()
    workflow, _, (plan, _) = three_model_plan(builder)
    by_agent = {seg.agent: seg for seg in plan.timeline}
    assert by_agent["developer"].shared and by_agent["test_runner"].shared
    assert by_agent["developer"].switch and not by_agent["test_runner"].switch  # same instance, no reload
    assert not by_agent["documentation_specialist"].shared
    assert [seg.start for seg in plan.timeline] == list(range(len(workflow.agents)))


def test_everything_stays_loaded_on_a_large_gpu(make_builder):
    builder = make_builder(fixture="rtx5090_32gb_64gb")
    _, chosen, (plan, policies) = three_model_plan(builder)
    assert set(policies.values()) == {LoadingPolicy.KEEP_LOADED}
    assert not plan.optimization.needed
    assert plan.peak_vram_gb == pytest.approx(sum(c.fit.vram_required_gb for c in chosen.values()), abs=0.2)


def test_run_time_manager_agrees_with_the_plan(make_builder, db, tmp_path):
    """The planner's predicted switch count is what the LangGraph run actually does."""
    builder = make_builder()
    workflow, chosen, (plan, policies) = three_model_plan(builder)
    for slot_id, card in chosen.items():
        slot = workflow.models[slot_id]
        slot.model_key, slot.vram_gb, slot.loading_policy = card.entry.name, card.fit.vram_required_gb, policies[slot_id]
    runtime = FakeRuntime({"*": [auto_reply]})
    manager = RunManager(db, runtime, bus=EventBus(), vram_budget_gb=lambda: builder.budget.vram_usable_gb, checkpoint_path=":memory:")
    run = manager.start(workflow, "Ship it", workspace=str(tmp_path))
    import time

    deadline = time.time() + 30
    while time.time() < deadline:
        record = manager.get(run.id)
        for approval in record.pending_approvals:
            manager.decide(run.id, approval.id, True)
        if record.status in TERMINAL:
            break
        time.sleep(0.02)
    record = manager.get(run.id)
    assert record.status == "completed", record.error
    # Planned switches exclude the first load; the run counts every load.
    assert record.result["model_switches"] == plan.model_switches_per_run + 1


# -- preferences, overrides, installed models --------------------------------


def test_uncensored_request_uses_only_uncensored_models(make_builder):
    plan = make_builder().plan(TeamRequest(specializations=["coding"], alignment=Alignment.UNCENSORED))
    assert plan.models and all(m.card.entry.alignment == Alignment.UNCENSORED for m in plan.models)
    assert plan.model_preference == "UNCENSORED"


def test_uncensored_in_the_description_is_honoured(make_builder):
    plan = make_builder().plan(TeamRequest(description="I want an uncensored AI team excellent at coding, Linux, Kubernetes, Terraform and technical documentation."))
    assert plan.request.alignment == Alignment.UNCENSORED
    assert all(m.card.entry.alignment == Alignment.UNCENSORED for m in plan.models)
    assert plan.model_preference == "UNCENSORED" and plan.can_build
    assert {"coding", "linux", "kubernetes", "terraform", "technical_docs"} <= set(plan.specializations)
    assert plan_to_summary(plan)["peak_vram_gb"] <= 14.2


def test_uncensored_preference_ranks_without_excluding(make_builder):
    preferred = make_builder().plan(TeamRequest(specializations=["coding"], preference=AlignmentPreference.UNCENSORED))
    assert preferred.models[0].card.entry.alignment == Alignment.UNCENSORED
    assert preferred.models[0].card.score.preference_bonus > 0
    standard_pool = {c.entry.alignment for m in preferred.models for c in m.alternatives}
    assert Alignment.STANDARD in standard_pool  # standard models remain available as alternatives


def test_no_matching_model_is_explained(make_builder):
    with pytest.raises(TeamError, match="No uncensored model"):
        make_builder([GENERAL, SMALL]).plan(TeamRequest(specializations=["coding"], alignment=Alignment.UNCENSORED))


def test_installed_model_is_preferred_when_close(make_builder):
    installed = [InstalledModel(model_key="qwen3-14b", display_name="Qwen3 14B", repo=GENERAL.repo, filename="Qwen3-14B-GGUF-Q4_K_M.gguf", quant="Q4_K_M", size_bytes=int(8.4 * GIB), tool_use=True)]
    plan = make_builder(installed=installed).plan(TeamRequest(specializations=["coding"]))
    assert all(m.card.installed for m in plan.models)
    assert plan.resource_plan.download_gb == 0 and plan.resource_plan.disk is None
    assert all(slot.model_key == "qwen3-14b" for slot in plan.workflow.models.values())
    assert resolve_model_keys(plan.workflow, installed) == []
    assert validate(plan.workflow, require_models=True).ok


def test_manual_override_in_guided_mode(make_builder):
    plan = make_builder().plan(TeamRequest(specializations=["coding"], overrides={R.CODING: ModelChoice(repo=SMALL.repo, quant="Q8_0")}))
    coding = next(m for m in plan.models if R.CODING in m.slots)
    assert coding.card.entry.repo == SMALL.repo and coding.card.quant == "Q8_0"
    assert any("chosen manually" in e for e in plan.explanations)


def test_plan_says_which_model_serves_each_role(make_builder):
    installed = [InstalledModel(model_key="qwen3-4b", display_name="Qwen3 4B", repo=SMALL.repo, filename="Qwen3-4B-GGUF-Q4_K_M.gguf", quant="Q4_K_M", size_bytes=int(2.5 * GIB), tool_use=True)]
    plan = make_builder(installed=installed).plan(TeamRequest(specializations=["coding"]))
    # One row per agent, in running order, each naming a model and the kind of work it is scored for.
    assert [r.agent for r in plan.roles] == [a.id for a in plan.workflow.agents]
    by_agent = {r.agent: r for r in plan.roles}
    assert by_agent["developer"].kind == "coding" and by_agent["developer"].kind_label == "Coding"
    assert by_agent["planner"].kind == "reasoning"
    for row in plan.roles:
        slot = plan.workflow.models[row.slot]
        assert (row.repo, row.quant) == (slot.repo, slot.quant) and 0 <= row.score <= 10 and not row.manual
    assert plan.roles[0].loads_model  # the first step always loads a model
    # Every installed model can be given to a role, with a score for that kind of work, next to the recommended ones.
    for kind in ("reasoning", "coding"):
        options = plan.choices[kind]
        assert any(o.installed and o.installed_key == "qwen3-4b" for o in options)
        assert any(not o.installed for o in options) and all(0 <= o.score <= 10 for o in options)
    coding_small = next(o for o in plan.choices["coding"] if o.installed_key == "qwen3-4b")
    reasoning_small = next(o for o in plan.choices["reasoning"] if o.installed_key == "qwen3-4b")
    assert coding_small.download_gb == 0 and coding_small.repo == reasoning_small.repo


def test_one_role_can_be_given_its_own_model(make_builder):
    builder = make_builder()
    base = builder.plan(TeamRequest(specializations=["coding"]))
    request = TeamRequest(specializations=["coding"], agent_overrides={"code_reviewer": ModelChoice(repo=SMALL.repo, quant="Q8_0")})
    plan = builder.plan(request)
    by_agent = {r.agent: r for r in plan.roles}
    assert (by_agent["code_reviewer"].repo, by_agent["code_reviewer"].quant) == (SMALL.repo, "Q8_0")
    assert by_agent["code_reviewer"].manual and by_agent["code_reviewer"].kind == "coding"
    # Nobody else moved: the developer shared a slot with the reviewer and keeps its model.
    for agent in ("tech_lead", "planner", "developer", "test_runner"):
        assert (by_agent[agent].repo, by_agent[agent].quant) == next((r.repo, r.quant) for r in base.roles if r.agent == agent)
        assert not by_agent[agent].manual
    slot = plan.workflow.models[by_agent["code_reviewer"].slot]
    assert slot.label == "Code Reviewer" and slot.kind == "coding" and slot.repo == SMALL.repo
    assert [a.id for a in plan.workflow.agents if a.model == by_agent["code_reviewer"].slot] == ["code_reviewer"]
    assert validate(plan.workflow).ok and any("Code Reviewer" in m.label for m in plan.models)


def test_models_chosen_by_hand_are_not_capped(make_builder):
    entries = [GENERAL, CODER, UNCENSORED, SMALL]
    picks = {
        "tech_lead": ModelChoice(repo=GENERAL.repo, quant="Q4_K_M"),
        "planner": ModelChoice(repo=UNCENSORED.repo, quant="Q4_K_M"),
        "developer": ModelChoice(repo=CODER.repo, quant="Q4_K_M"),
        "test_runner": ModelChoice(repo=SMALL.repo, quant="Q8_0"),
        "code_reviewer": ModelChoice(repo=SMALL.repo, quant="Q4_K_M"),
    }
    plan = make_builder(entries).plan(TeamRequest(specializations=["coding"], agent_overrides=picks))
    assert len(plan.models) == 5  # five roles, five different files: more than the automatic limit of three
    assert {(r.repo, r.quant) for r in plan.roles} == {(c.repo, c.quant) for c in picks.values()}
    assert set(plan.workflow.models) == {f"{agent}_model" for agent in picks}  # the shared slots nobody uses are gone
    assert plan.resource_plan.model_switches_per_run == 4 and validate(plan.workflow).ok


def test_resolve_model_keys_reports_missing_models(make_builder):
    plan = make_builder().plan(TeamRequest(specializations=["coding"]))
    assert set(resolve_model_keys(plan.workflow, [])) == set(plan.workflow.models)
    assert not validate(plan.workflow, require_models=True).ok


# -- low memory ---------------------------------------------------------------


def test_low_memory_pc_still_gets_a_viable_team(make_builder):
    plan = make_builder(fixture="cpu_only_16gb").plan(TeamRequest(specializations=["coding", "devops", "enterprise_docs"]))
    assert plan.models and plan.can_build
    assert all(m.card.fit.runnable for m in plan.models)
    assert plan.resource_plan.peak_vram_gb == 0  # CPU inference
    assert plan.resource_plan.ram_gb <= 13.6
    assert len(plan.models) == 1  # one small shared model


def test_twelve_gb_card_gets_a_smaller_quant_or_model(make_builder):
    plan = make_builder(fixture="rtx3060_12gb_32gb").plan(TeamRequest(specializations=["coding"]))
    assert plan.can_build and plan.resource_plan.peak_vram_gb <= plan.resource_plan.usable_vram_gb
    assert all(m.card.fit.vram_required_gb <= 10.5 for m in plan.models)


def test_big_model_is_not_chosen_when_it_needs_offload(make_builder):
    plan = make_builder([GENERAL, BIG, SMALL]).plan(TeamRequest(specializations=["coding"]))
    assert BIG.repo not in {m.card.entry.repo for m in plan.models}


# -- disk full ----------------------------------------------------------------


def test_disk_full_blocks_the_team_and_offers_options(make_builder):
    # Only ~3 GB safely usable: nothing can be downloaded.
    plan = make_builder([GENERAL, CODER], free_gb=33).plan(TeamRequest(specializations=["coding"]))
    assert not plan.can_build
    assert plan.blockers and plan.blockers[0].startswith("NOT SAFE")
    assert plan.resource_plan.disk is not None and not plan.resource_plan.disk.safe
    options = " | ".join(plan.notes)
    assert "Choose a smaller model" in options and "Delete unused models" in options


def test_disk_full_falls_back_to_installed_models(make_builder):
    installed = [InstalledModel(model_key="small", display_name="Qwen3 4B", repo=SMALL.repo, filename="Qwen3-4B-GGUF-Q8_0.gguf", quant="Q8_0", size_bytes=int(4.3 * GIB), tool_use=True)]
    plan = make_builder(installed=installed, free_gb=33).plan(TeamRequest(specializations=["coding"], prefer_installed=False))
    assert plan.can_build
    assert all(m.card.installed for m in plan.models) and plan.resource_plan.download_gb == 0
    assert any("not enough safe disk space" in e for e in plan.explanations)


def test_generated_pipelines_are_always_bounded_and_valid():
    combos = [["coding"], ["devops", "kubernetes"], ["scientific_research"], ["image"], ["creative_writing", "coding"], ["coding", "devops", "enterprise_docs", "linux", "terraform"]]
    for combo in combos:
        roles, focus, _ = __import__("agentfit.teams.builder", fromlist=["select_roles"]).select_roles(combo, "")
        workflow = T.generate_pipeline("x", "X", "d", roles, focus=focus)
        result = validate(workflow)
        assert result.ok, (combo, [i.message for i in result.errors])
