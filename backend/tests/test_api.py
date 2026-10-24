"""HTTP API: wiring, auth, error mapping and the end-to-end team flow against fakes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentfit import storage
from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.lmstudio.models import InstalledModel, LMStudioInstall
from agentfit.workflows.runner import RunManager
from conftest import load_profile
from fakes import FakeRuntime, auto_reply, tool_call
from test_catalog import FakeHub, make_entry

GIB = 1024**3
MODEL = make_entry("unsloth/Qwen3-14B-GGUF", 14.0, files={"Q4_K_M": 8.4, "Q5_K_M": 9.8})
UNCENSORED = make_entry("mradermacher/Huihui-Qwen3-14B-abliterated-GGUF", 14.0, files={"Q4_K_M": 8.4})
INSTALLED = InstalledModel(model_key="qwen3-14b", display_name="Qwen3 14B", repo=MODEL.repo, filename="Qwen3-14B-GGUF-Q4_K_M.gguf", quant="Q4_K_M", size_bytes=int(8.4 * GIB), tool_use=True)


class ApiRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__({"*": [auto_reply]})
        self.installed = [INSTALLED]
        self.online = True

    def status(self, deep: bool = False) -> LMStudioInstall:
        return LMStudioInstall(installed=True, models_dir="D:\\Models", server_running=self.online, api_reachable=self.online, server_port=1234, cli_path="lms.exe")

    def is_available(self) -> bool:
        return self.online

    def ensure_server(self) -> bool:
        return self.online

    def estimate(self, model_key: str, context_length: int | None = None) -> None:
        return None


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr("agentfit.catalog.service.load_seed", lambda: ())
    monkeypatch.setattr(storage, "free_gb_at", lambda path: 400.0)
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    db = Database(":memory:")
    services = AppServices(db=db, settings=Settings(), event_bus=EventBus())
    services._profile = load_profile("rtx4080super_16gb_32gb")
    runtime = ApiRuntime()
    services._runtime = runtime  # type: ignore[assignment]
    services.catalog.hub = FakeHub([MODEL, UNCENSORED])  # type: ignore[assignment]
    for entry in (MODEL, UNCENSORED):
        services.catalog._cache_put(entry)
    services._runs = RunManager(db, runtime, bus=services.bus, vram_budget_gb=lambda: 14.2, checkpoint_path=":memory:")
    # Settings writes go to the isolated test home; keep the in-memory copy authoritative.
    client = TestClient(create_app(services))
    client.services = services  # type: ignore[attr-defined]
    client.runtime = runtime  # type: ignore[attr-defined]
    return client


def test_health_and_app_info(api):
    assert api.get("/api/health").json()["ok"] is True
    info = api.get("/api/app").json()
    assert info["name"] == "Will It Run" and info["lmstudio_download_url"].startswith("https://lmstudio.ai")


def test_token_is_enforced_when_configured(api, monkeypatch):
    monkeypatch.setenv("AGENTFIT_TOKEN", "s3cret")
    assert api.get("/api/health").status_code == 200  # health stays open for the shell's readiness probe
    assert api.get("/api/hardware").status_code == 401
    assert api.get("/api/hardware", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert api.get("/api/hardware", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert api.get("/api/hardware?token=s3cret").status_code == 200


def test_hardware_endpoint_returns_profile_and_budget(api):
    data = api.get("/api/hardware").json()
    assert data["profile"]["gpus"][0]["name"] == "NVIDIA GeForce RTX 4080 SUPER"
    assert data["budget"]["single_gpu_usable_gb"] == pytest.approx(14.2, abs=0.1)
    assert data["budget"]["explanation"] and data["fingerprint"]


def test_installed_models_with_fit(api):
    data = api.get("/api/models/installed").json()
    (card,) = data["cards"]
    assert card["installed"] and card["installed_key"] == "qwen3-14b" and card["fit"]["category"] == "PERFECT"
    assert data["total_bytes"] == INSTALLED.size_bytes


def test_search_with_uncensored_filter(api):
    data = api.post("/api/models/search", json={"query": "", "alignment": "uncensored"}).json()
    assert [c["entry"]["repo"] for c in data["cards"]] == [UNCENSORED.repo]
    card = data["cards"][0]
    assert card["entry"]["alignment"] == "uncensored" and card["entry"]["alignment_evidence"]
    assert card["entry"]["publisher"] == "mradermacher" and card["entry"]["license"] == "apache-2.0"
    assert card["download_size_bytes"] and card["fit"]["speed"]["tokens_per_second"] > 0


def test_model_details_lists_every_file_with_its_fit(api):
    data = api.get("/api/models/details", params={"repo": MODEL.repo}).json()
    assert [f["file"]["quant"] for f in data["files"]] == ["Q4_K_M", "Q5_K_M"]
    assert data["files"][0]["installed"] and not data["files"][1]["installed"]
    assert data["files"][0]["file"]["url"].startswith("https://huggingface.co/unsloth/Qwen3-14B-GGUF/resolve/")
    assert data["entry"]["model_card_url"] == "https://huggingface.co/unsloth/Qwen3-14B-GGUF"
    assert api.get("/api/models/details", params={"repo": "nobody/nothing"}).status_code == 404


def test_expert_fit_endpoint(api):
    small = api.post("/api/models/fit", json={"repo": MODEL.repo, "quant": "Q4_K_M", "context_length": 8192}).json()
    large = api.post("/api/models/fit", json={"repo": MODEL.repo, "quant": "Q4_K_M", "context_length": 65536, "parallel": 2}).json()
    assert small["category"] == "PERFECT" and large["memory"]["kv_cache_gb"] > small["memory"]["kv_cache_gb"] * 8


def test_recommend_endpoint(api):
    data = api.post("/api/models/recommend", json={"specialization": "coding"}).json()
    assert data["profile"]["id"] == "coding" and data["cards"]
    assert data["cards"][0]["why"]


def test_download_plan_and_unsafe_refusal(api, monkeypatch):
    body = {"repo": UNCENSORED.repo, "filename": "x", "quant": "Q4_K_M"}
    plan = api.post("/api/downloads/plan", json=body).json()
    assert plan["plan"]["safe"] and plan["request"]["size_bytes"] == int(8.4 * GIB) and plan["request"]["sha256"]
    monkeypatch.setattr(storage, "free_gb_at", lambda path: 12.0)
    refused = api.post("/api/downloads", json=body)
    assert refused.status_code == 409
    assert refused.json()["kind"] == "unsafe_download" and not refused.json()["plan"]["safe"]
    assert api.get("/api/downloads").json()["downloads"] == []


def test_download_of_missing_file_is_404(api):
    assert api.post("/api/downloads/plan", json={"repo": MODEL.repo, "filename": "nope.gguf", "quant": "Q9_Z"}).status_code == 404


def test_specializations_and_tools(api):
    groups = api.get("/api/specializations").json()["groups"]
    assert groups[0]["group"] == "Software"
    tools = api.get("/api/tools").json()
    assert {"powershell", "write_file"} <= {t["name"] for t in tools["tools"]} and "CodeReview" in tools["schemas"]


def test_workflow_listing_get_validate_and_yaml(api):
    listed = api.get("/api/workflows").json()["workflows"]
    assert {"template:coding", "template:devops", "template:documentation"} <= {w["id"] for w in listed}
    got = api.get("/api/workflows/template:coding").json()
    assert got["source"] == "template" and got["validation"]["ok"]
    assert got["workflow"]["edges"][0]["from"] == "START"
    yaml_text = api.get("/api/workflows/template:coding/yaml").text
    assert yaml_text.startswith("workflow:") and "human_approval:" in yaml_text
    broken = got["workflow"] | {"edges": []}
    assert not api.post("/api/workflows/validate", json=broken).json()["ok"]
    assert api.get("/api/workflows/nope").status_code == 404


def test_workflow_save_import_and_delete(api):
    workflow = api.get("/api/workflows/template:documentation").json()["workflow"]
    workflow["workflow"]["id"] = "my-docs"
    workflow["agents"][0]["tools"] = ["read_file"]
    saved = api.put("/api/workflows", json=workflow).json()
    assert saved["saved"] and saved["id"] == "my-docs"
    assert api.get("/api/workflows/my-docs").json()["workflow"]["agents"][0]["tools"] == ["read_file"]
    yaml_text = api.get("/api/workflows/my-docs/yaml").text.replace("my-docs", "imported-docs")
    assert api.post("/api/workflows/import", json={"yaml": yaml_text}).status_code == 200
    assert api.post("/api/workflows/import", json={"yaml": "workflow: [not, valid"}).status_code == 400
    api.delete("/api/workflows/my-docs")
    assert api.get("/api/workflows/my-docs").status_code == 404


def test_invalid_workflow_is_not_saved(api):
    workflow = api.get("/api/workflows/template:coding").json()["workflow"]
    workflow["workflow"]["id"] = "broken"
    workflow["agents"][0]["model"] = "missing-slot"
    result = api.put("/api/workflows", json=workflow).json()
    assert not result["saved"] and not result["validation"]["ok"]
    assert api.get("/api/workflows/broken").status_code == 404


def wait_for_run(api, run_id: str, approve: bool = True, timeout: float = 30) -> dict[str, Any]:
    deadline = time.time() + timeout
    decided: set[str] = set()
    while time.time() < deadline:
        data = api.get(f"/api/runs/{run_id}").json()
        for approval in data["run"]["pending_approvals"]:
            if approval["id"] not in decided:
                decided.add(approval["id"])
                api.post(f"/api/runs/{run_id}/approvals/{approval['id']}", json={"approved": approve, "comment": ""})
        if data["run"]["status"] in ("completed", "failed", "rejected", "cancelled", "retry_limit", "no_route", "blocked"):
            return data
        time.sleep(0.03)
    raise AssertionError("run did not finish")


def test_acceptance_flow_plan_create_and_run_a_team(api):
    """Specializations -> plan -> create -> visual workflow -> run locally."""
    request = {"specializations": ["coding", "devops", "enterprise_docs"], "mode": "automatic"}
    planned = api.post("/api/teams/plan", json=request).json()
    summary = planned["summary"]
    assert summary["agents"] == 9 and summary["models"] == 1 and summary["download_gb"] == 0  # installed model reused
    assert summary["peak_vram_gb"] <= 14.2 and summary["can_build"]
    assert planned["plan"]["resource_plan"]["timeline"] and planned["plan"]["explanations"]

    created = api.post("/api/teams", json=request).json()
    workflow_id = created["workflow_id"]
    stored = api.get(f"/api/workflows/{workflow_id}").json()
    assert stored["validation"]["ok"] and stored["workflow"]["layout"]  # positions for the visual editor
    assert api.get("/api/teams").json()["teams"][0]["workflow_id"] == workflow_id

    started = api.post("/api/runs", json={"workflow_id": workflow_id, "task": "Create a health-check service with deployment docs"})
    assert started.status_code == 200, started.text
    finished = wait_for_run(api, started.json()["id"])
    assert finished["run"]["status"] == "completed", finished["run"]["error"]
    kinds = [e["type"] for e in finished["events"]]
    assert kinds.count("node.completed") == 9 and "approval.requested" in kinds and "model.switch" in kinds
    assert api.runtime.load_calls == ["qwen3-14b"]  # nine agents, one model instance
    assert api.get("/api/runs").json()["runs"][0]["id"] == started.json()["id"]


def test_run_refused_without_models_or_server(api):
    # A bare template has no model assigned to its slots, so it cannot run until one is chosen.
    response = api.post("/api/runs", json={"workflow_id": "template:coding", "task": "x"})
    assert response.status_code == 409 and "No installed model is assigned" in response.json()["detail"]
    assert api.post("/api/runs", json={"workflow_id": "ghost", "task": "x"}).status_code == 404


def test_run_requires_lmstudio_server(api):
    created = api.post("/api/teams", json={"specializations": ["enterprise_docs"]}).json()
    api.runtime.online = False
    response = api.post("/api/runs", json={"workflow_id": created["workflow_id"], "task": "write docs"})
    assert response.status_code == 503 and "not running" in response.json()["detail"]


def test_team_errors_are_reported(api):
    assert api.post("/api/teams/plan", json={}).status_code == 400
    assert api.post("/api/teams/plan", json={"specializations": ["nope"]}).status_code == 400


def test_settings_round_trip_hides_the_lmstudio_token(api):
    updated = api.put("/api/settings", json={"runtime": {"lmstudio_api_token": "super-secret"}, "models": {"alignment_preference": "uncensored"}}).json()
    assert "lmstudio_api_token" not in updated["runtime"] and updated["runtime"]["lmstudio_api_token_set"] is True
    assert updated["models"]["alignment_preference"] == "uncensored"
    assert "super-secret" not in api.get("/api/settings").text
    assert api.put("/api/settings", json={"models": {"alignment_preference": "bogus"}}).status_code == 400
    api.put("/api/settings", json={"runtime": {"lmstudio_api_token": ""}, "models": {"alignment_preference": "any"}})


def test_benchmarks_and_storage_endpoints(api):
    assert api.get("/api/benchmarks").json() == {"benchmarks": []}
    locations = api.get("/api/storage").json()["locations"]
    assert any(loc["recommended"] for loc in locations)
    assert api.get("/api/logs").json()["entries"] is not None


def test_live_endpoint_reports_loaded_models(api):
    api.runtime.loaded = {"qwen3-14b": "qwen3-14b"}
    data = api.get("/api/live").json()
    assert data["lmstudio_connected"] and data["loaded"][0]["model_key"] == "qwen3-14b" and data["ram_total_bytes"] > 0


# -- language, per-file score, accuracy and download folder ---------------------


def test_api_answers_in_the_language_of_the_request(api):
    english = api.get("/api/quantization").json()
    italian = api.get("/api/quantization", headers={"X-WillItRun-Lang": "it"}).json()
    assert english["table"][0]["label"] == "Near lossless"
    assert italian["table"][0]["label"] == "Quasi senza perdita"
    assert italian["reference"]["model"] == "Llama-3-8B"
    # The language of one request does not leak into the next.
    assert api.get("/api/quantization").json()["table"][0]["label"] == "Near lossless"
    why = api.get("/api/models/details", params={"repo": MODEL.repo}, headers={"X-WillItRun-Lang": "it"}).json()["card"]["why"]
    assert any("Entra completamente nella tua GPU" in line for line in why)


def test_every_file_has_its_own_score_and_accuracy(api):
    files = api.get("/api/models/details", params={"repo": MODEL.repo}).json()["files"]
    by_quant = {f["file"]["quant"]: f for f in files}
    assert set(by_quant) == {"Q4_K_M", "Q5_K_M"}
    for row in files:
        assert 0 < row["score"]["agentfit_score"] <= 10
        assert row["accuracy"]["basis"] == "measured"
    # The same model at a lighter quantization keeps more quality, and the score reflects it.
    assert by_quant["Q5_K_M"]["accuracy"]["level"] > by_quant["Q4_K_M"]["accuracy"]["level"]
    assert by_quant["Q5_K_M"]["score"]["task_quality"] > by_quant["Q4_K_M"]["score"]["task_quality"]
    assert by_quant["Q4_K_M"]["installed"] is True and by_quant["Q5_K_M"]["installed"] is False


def test_recommended_card_carries_the_accuracy_of_its_quantization(api):
    card = api.get("/api/models/details", params={"repo": UNCENSORED.repo}).json()["card"]
    assert card["accuracy"]["quant"] == card["quant"] and card["accuracy"]["ppl_delta"] is not None


def test_download_plan_follows_the_chosen_folder(api, tmp_path):
    body = {"repo": MODEL.repo, "filename": "Qwen3-14B-GGUF-Q5_K_M.gguf", "quant": "Q5_K_M"}
    default = api.post("/api/downloads/plan", json=body).json()
    assert default["backend"] == "direct"  # stoppable by default, even with LM Studio online
    assert default["plan"]["target_dir"].startswith("D:\\Models") and default["outside_lmstudio"] is False
    assert default["default_folder"] == "D:\\Models" and default["plan"]["reason"]

    custom = api.post("/api/downloads/plan", json=body | {"base_dir": str(tmp_path / "Models")}).json()
    assert custom["plan"]["target_dir"] == str(tmp_path / "Models" / "unsloth" / "Qwen3-14B-GGUF")
    assert custom["outside_lmstudio"] is True and custom["backend"] == "direct"

    bad = api.post("/api/downloads/plan", json=body | {"base_dir": "not-a-path"})
    assert bad.status_code == 400 and "folder" in bad.json()["detail"]


def test_readiness_includes_the_size_ladder_and_a_featured_model(api):
    data = api.get("/api/readiness").json()
    labels = [step["label"] for step in data["ladder"]]
    assert labels[0] == "4B" and labels[-1] == "200B"
    assert data["ladder"][0]["runnable"] is True and data["ladder"][-1]["category"] == "IMPOSSIBLE"
    assert data["target_tps"] > 0
    assert data["featured"] is None or data["featured"]["vram_required_gb"] >= 0


def test_recommended_file_is_agentfits_pick_not_the_installed_one(api):
    # Q4_K_M is installed; with room to spare on a 16 GB card Will It Run would take Q5_K_M.
    data = api.get("/api/models/details", params={"repo": MODEL.repo}).json()
    assert data["card"]["quant"] == "Q4_K_M" and data["card"]["installed"] is True
    assert data["recommended_file"] == "Qwen3-14B-GGUF-Q5_K_M.gguf"
    best = max(data["files"], key=lambda f: f["score"]["total"])
    assert best["file"]["filename"] == data["recommended_file"]


def test_verification_templates_and_checks_are_served_in_the_users_language(api):
    listed = {w["id"]: w for w in api.get("/api/workflows").json()["workflows"]}
    board = listed["template:adversarial_blackboard"]
    assert board["category"] == "verification" and board["checks"] == 3 and board["evidence"]
    assert listed["template:coding"]["category"] == "team" and not listed["template:coding"]["evidence"]
    italian = {w["id"]: w for w in api.get("/api/workflows", headers={"X-WillItRun-Lang": "it"}).json()["workflows"]}
    assert italian["template:adversarial_blackboard"]["name"] == "Lavagna avversariale"
    assert "scettico" in italian["template:adversarial_blackboard"]["description"]
    # The definition itself stays as authored: it is what the models read.
    got = api.get("/api/workflows/template:adversarial_blackboard", headers={"X-WillItRun-Lang": "it"}).json()
    assert got["validation"]["ok"] and got["workflow"]["workflow"]["name"] == "Adversarial Blackboard"
    assert [c["kind"] for c in got["workflow"]["checks"]] == ["evidence_dossier", "verify_claims", "audit_trail"]
    assert got["workflow"]["agents"][1]["reads"] == ["dossier", "researcher.claims"]

    checks = {c["kind"]: c for c in api.get("/api/tools").json()["checks"]}
    assert set(checks) == {"evidence_dossier", "verify_claims", "tally_votes", "verify_commits", "audit_trail", "refuse"}
    assert checks["tally_votes"]["defaults"]["threshold"] == 0.66 and "not a calibrated value" in checks["tally_votes"]["description"]
    translated = {c["kind"]: c for c in api.get("/api/tools", headers={"X-WillItRun-Lang": "it"}).json()["checks"]}
    assert translated["verify_claims"]["label"] == "Filtro delle affermazioni"


def test_a_verification_workflow_runs_through_the_api_and_refuses_without_evidence(api, tmp_path):
    definition = api.get("/api/workflows/template:adversarial_blackboard").json()["workflow"]
    definition["workflow"]["id"] = "board-copy"
    for slot in definition["models"].values():
        slot["model_key"] = "qwen3-14b"  # one installed model for every role
    assert api.put("/api/workflows", json=definition).json()["saved"]

    # No evidence: stopped by the first check, before any model is loaded.
    started = api.post("/api/runs", json={"workflow_id": "board-copy", "task": "What does the contract say?"})
    assert started.status_code == 200, started.text
    finished = wait_for_run(api, started.json()["id"])
    assert finished["run"]["status"] == "blocked" and "No model was called" in finished["run"]["error"]
    assert api.runtime.load_calls == []
    first = next(e for e in finished["events"] if e["type"] == "node.completed")
    assert first["payload"]["kind"] == "check" and first["payload"]["output"]["verdict"] == "NO_EVIDENCE"

    # A documents folder that does not exist is refused up front.
    missing = api.post("/api/runs", json={"workflow_id": "board-copy", "task": "x" * 30, "evidence_dir": str(tmp_path / "nope")})
    assert missing.status_code == 400 and "documents folder does not exist" in missing.json()["detail"]

    # With a folder of documents the run starts, and its start event names the folder.
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "contract.txt").write_text("The supplier shall deliver the goods within thirty days of the order. " * 6, encoding="utf-8")
    started = api.post("/api/runs", json={"workflow_id": "board-copy", "task": "When must the goods be delivered?", "evidence_dir": str(folder)})
    finished = wait_for_run(api, started.json()["id"])
    assert finished["events"][0]["payload"]["evidence_dir"] == str(folder)
    dossier = next(e for e in finished["events"] if e["type"] == "node.completed")["payload"]
    assert dossier["output"]["verdict"] == "EVIDENCE_READY" and [s["name"] for s in dossier["output"]["sources"]] == ["Request", "contract.txt"]
    # The scripted model proves nothing, so the run ends refused rather than with an invented answer.
    assert finished["run"]["status"] == "blocked" and finished["run"]["result"]["ledger"]


def test_team_plan_lists_every_role_with_its_model_and_accepts_a_model_per_role(api):
    request = {"specializations": ["coding"], "mode": "expert"}
    plan = api.post("/api/teams/plan", json=request).json()["plan"]
    assert [r["agent"] for r in plan["roles"]] == [a["id"] for a in plan["workflow"]["agents"]]
    assert {r["kind"] for r in plan["roles"]} == {"reasoning", "coding"}
    assert all(r["model"] and 0 <= r["score"] <= 10 for r in plan["roles"])
    option = next(o for o in plan["choices"]["coding"] if o["repo"] != plan["roles"][-1]["repo"])
    picked = api.post("/api/teams/plan", json={**request, "agent_overrides": {"code_reviewer": {"repo": option["repo"], "quant": option["quant"], "installed_key": option["installed_key"]}}}).json()["plan"]
    reviewer = next(r for r in picked["roles"] if r["agent"] == "code_reviewer")
    assert reviewer["manual"] and reviewer["repo"] == option["repo"]
    assert sum(r["manual"] for r in picked["roles"]) == 1 and len(picked["models"]) == 2


def test_a_run_can_work_in_a_folder_and_continue_from_the_previous_one(api, tmp_path):
    request = {"specializations": ["enterprise_docs"], "mode": "automatic"}
    workflow_id = api.post("/api/teams", json=request).json()["workflow_id"]
    project = tmp_path / "project"
    project.mkdir()

    first = api.post("/api/runs", json={"workflow_id": workflow_id, "task": "Write the runbook", "workspace": str(project)})
    assert first.status_code == 200, first.text
    done = wait_for_run(api, first.json()["id"])
    started = done["events"][0]["payload"]
    assert started["workspace"] == str(project) and started["source"] == {"kind": "folder", "path": str(project)}
    assert started["protect_existing"] is True and started["continued"] is False

    # The next request of the session is told what the previous one did, without it becoming the stored task.
    second = api.post("/api/runs", json={"workflow_id": workflow_id, "task": "Now shorten it", "workspace": str(project), "continue_from": first.json()["id"]})
    again = wait_for_run(api, second.json()["id"])
    assert again["run"]["task"] == "Now shorten it" and again["events"][0]["payload"]["continued"] is True
    prompts = [m["content"] for call in api.runtime.calls for m in call["messages"] if m["role"] == "user"]
    assert any("## Earlier in this session" in text and "The previous request was: Write the runbook" in text for text in prompts)

    # Bad places are refused before anything starts.
    for body, why in (
        ({"workspace": str(tmp_path / "missing")}, "does not exist"),
        ({"workspace": project.anchor}, "not a whole drive"),
        ({"repo_url": "https://example.com/a/b"}, "not a GitHub repository address"),
    ):
        refused = api.post("/api/runs", json={"workflow_id": workflow_id, "task": "x", **body})
        assert refused.status_code == 400 and why in refused.json()["detail"], refused.text


def test_a_request_written_while_a_run_works_starts_when_it_ends(api, tmp_path):
    request = {"specializations": ["coding"], "mode": "automatic"}
    workflow_id = api.post("/api/teams", json=request).json()["workflow_id"]
    first = api.post("/api/runs", json={"workflow_id": workflow_id, "task": "Build the page"}).json()
    # The coding team waits at its plan gate: the run is still working.
    deadline = time.time() + 20
    while time.time() < deadline and not api.get(f"/api/runs/{first['id']}").json()["run"]["pending_approvals"]:
        time.sleep(0.03)

    queued = api.post("/api/runs", json={"workflow_id": workflow_id, "task": "Now add a footer", "continue_from": first["id"], "queue": True, "approval": "bypass"})
    assert queued.status_code == 200 and queued.json() == {"queued": True, "after": first["id"]}
    assert api.get(f"/api/runs/{first['id']}").json()["run"]["queued"] == "Now add a footer"
    assert len(api.get("/api/runs").json()["runs"]) == 1  # nothing has started yet
    # It can be taken back, and written again.
    assert api.delete(f"/api/runs/{first['id']}/queued").json() == {"removed": True}
    assert api.get(f"/api/runs/{first['id']}").json()["run"]["queued"] is None
    api.post("/api/runs", json={"workflow_id": workflow_id, "task": "Now add a footer", "continue_from": first["id"], "queue": True, "approval": "bypass"})

    wait_for_run(api, first["id"])
    deadline = time.time() + 20
    while time.time() < deadline and len(api.get("/api/runs").json()["runs"]) < 2:
        time.sleep(0.03)
    second = next(r for r in api.get("/api/runs").json()["runs"] if r["id"] != first["id"])
    done = wait_for_run(api, second["id"], approve=False)  # it ignores permissions: there is nothing to answer
    started = done["events"][0]["payload"]
    assert done["run"]["task"] == "Now add a footer" and done["run"]["status"] == "completed"
    assert started["continued"] is True and started["approval"] == "bypass"
    assert api.get(f"/api/runs/{first['id']}").json()["run"]["queued"] is None
    # Queued after a run that has already ended, a request simply starts.
    late = api.post("/api/runs", json={"workflow_id": workflow_id, "task": "And a header", "continue_from": second["id"], "queue": True, "approval": "bypass"})
    assert "id" in late.json() and wait_for_run(api, late.json()["id"])["run"]["status"] == "completed"


def test_the_list_of_workflows_says_which_one_is_working(api):
    workflow_id = api.post("/api/teams", json={"specializations": ["coding"], "mode": "automatic"}).json()["workflow_id"]

    def running() -> str | None:
        return next(w for w in api.get("/api/workflows").json()["workflows"] if w["id"] == workflow_id)["running_run"]

    assert running() is None
    run = api.post("/api/runs", json={"workflow_id": workflow_id, "task": "Build the page"}).json()
    assert running() == run["id"]  # working, or waiting for the plan to be approved: it is the run to open
    wait_for_run(api, run["id"])
    assert running() is None


def test_a_chat_is_one_model_answering_in_its_own_words_turn_after_turn(api, tmp_path):
    api.runtime.script["Assistant"] = ["<think>greet</think>Hello! I am ready.", tool_call("write_file", path="notes.txt", content="tides"), "I wrote notes.txt."]
    first = api.post("/api/chats", json={"message": "Hi  there", "model_key": "qwen3-14b"})
    assert first.status_code == 200, first.text
    chat_id = first.json()["chat"]
    wait_for_run(api, first.json()["run"])
    chat = api.get(f"/api/chats/{chat_id}").json()
    assert (chat["title"], chat["model_key"], chat["active"]) == ("Hi there", "qwen3-14b", False)
    assert [(t["message"], t["answer"], t["status"]) for t in chat["turns"]] == [("Hi  there", "Hello! I am ready.", "completed")]
    # The model was asked for an answer, not for a JSON object, and was offered its tools.
    call = api.runtime.calls[-1]
    assert call["role"] == "Assistant" and not call["kwargs"].get("response_format") and "JSON" not in call["messages"][0]["content"]
    assert {t["function"]["name"] for t in call["kwargs"]["tools"]} >= {"read_file", "write_file", "run_python", "git"}

    # The next message works in a folder of the user's, and is told what was said before.
    project = tmp_path / "project"
    project.mkdir()
    second = api.post(f"/api/chats/{chat_id}/messages", json={"message": "Write notes.txt", "model_key": "qwen3-14b", "workspace": str(project)})
    assert second.status_code == 200, second.text
    wait_for_run(api, second.json()["run"])
    chat = api.get(f"/api/chats/{chat_id}").json()
    turn = chat["turns"][1]
    assert turn["answer"] == "I wrote notes.txt." and (project / "notes.txt").read_text(encoding="utf-8") == "tides"
    assert [e["payload"]["tool"] for e in turn["events"] if e["type"] == "tool.call" and e["payload"]["status"] == "success"] == ["write_file"]
    assert chat["source"] == {"kind": "folder", "path": str(project)}
    prompts = [m["content"] for c in api.runtime.calls for m in c["messages"] if m["role"] == "user"]
    assert any("## The conversation so far" in p and "The person: Hi  there" in p and "You: Hello! I am ready." in p for p in prompts)

    # Its turns are not sessions; the chat is listed with how many it has.
    assert api.get("/api/runs").json()["runs"] == []
    assert [(c["id"], c["turns"], c["active"]) for c in api.get("/api/chats").json()["chats"]] == [(chat_id, 2, False)]
    # A model that is not installed and an empty message are refused, and leave no chat behind.
    assert api.post("/api/chats", json={"message": "x", "model_key": "ghost"}).status_code == 409
    assert api.post("/api/chats", json={"message": "  ", "model_key": "qwen3-14b"}).status_code == 400
    assert api.post("/api/chats", json={"message": "x", "model_key": "qwen3-14b", "workspace": str(tmp_path / "missing")}).status_code == 400
    assert len(api.get("/api/chats").json()["chats"]) == 1
    # Deleting the chat removes its turns and never the user's folder.
    assert api.delete(f"/api/chats/{chat_id}").json() == {"deleted": chat_id}
    assert api.get("/api/chats").json()["chats"] == [] and api.get(f"/api/chats/{chat_id}").status_code == 404
    assert (project / "notes.txt").exists()


def test_a_chat_without_a_folder_keeps_its_files_from_one_message_to_the_next(api):
    api.runtime.script["Assistant"] = [tool_call("write_file", path="a.txt", content="one"), "Written.", tool_call("read_file", path="a.txt"), "It says one."]
    first = api.post("/api/chats", json={"message": "Write a.txt", "model_key": "qwen3-14b"}).json()
    wait_for_run(api, first["run"])
    second = api.post(f"/api/chats/{first['chat']}/messages", json={"message": "Read it back", "model_key": "qwen3-14b"}).json()
    wait_for_run(api, second["run"])
    turn = api.get(f"/api/chats/{first['chat']}").json()["turns"][1]
    read = next(e["payload"] for e in turn["events"] if e["type"] == "tool.call" and e["payload"]["tool"] == "read_file" and e["payload"]["status"] != "running")
    assert (read["status"], read["output"]) == ("success", "one") and turn["answer"] == "It says one."
    # The model was loaded for the first message and was still there for the second: a chat does not reload it each time.
    assert api.runtime.load_calls == ["qwen3-14b"] and api.runtime.unload_calls == [] and "qwen3-14b" in api.runtime.loaded
    api.delete(f"/api/chats/{first['chat']}")


def test_context_advice_says_what_fits_and_how_it_is_known(api):
    from agentfit.workflows.model_manager import MemoryProfile

    unknown = api.post("/api/models/context-advice", json={"model_keys": ["qwen3-14b", "ghost"]}).json()
    assert unknown["usable_vram_gb"] > 10
    by_key = {a["model_key"]: a for a in unknown["advice"]}
    # No estimator in this runtime and no load yet: nothing is known, and nothing is made up.
    assert by_key["qwen3-14b"]["safe_context"] is None and by_key["qwen3-14b"]["basis"] is None
    assert by_key["ghost"]["safe_context"] is None

    # After a run has measured the model, the advice comes from the measurement.
    measured = MemoryProfile(fixed_mib=9000, per_1k_mib=40, source="measured", points=[(8192, 9328.0), (16384, 9655.0)])
    api.services.runs.profiles.put("qwen3-14b", measured)
    known = api.post("/api/models/context-advice", json={"model_keys": ["qwen3-14b"]}).json()["advice"][0]
    usable_mib = api.services.budget().vram_usable_gb * 1024
    assert known["basis"] == "measured" and known["fits_gpu"] is True
    assert known["safe_context"] == min(known["max_context"] or 131072, int((usable_mib - 9000) / 40 * 1000) // 1024 * 1024)

    # A model whose weights alone exceed the usable VRAM: zero, not a small positive number.
    api.services.runs.profiles.put("qwen3-14b", MemoryProfile(fixed_mib=40000, per_1k_mib=60))
    too_big = api.post("/api/models/context-advice", json={"model_keys": ["qwen3-14b"]}).json()["advice"][0]
    assert too_big["safe_context"] == 0 and too_big["fits_gpu"] is False and too_big["basis"] == "estimate"



def test_a_folder_is_opened_and_a_file_never_started(api, tmp_path, monkeypatch):
    from agentfit.api import server

    opened: list[Path] = []
    monkeypatch.setattr(server, "open_folder", opened.append)
    folder = tmp_path / "Il mio sito"
    folder.mkdir()
    assert api.post("/api/folders/open", json={"path": str(folder)}).status_code == 200
    assert opened == [folder]
    program = tmp_path / "tool.exe"
    program.write_bytes(b"MZ")
    refused = api.post("/api/folders/open", json={"path": str(program)})
    assert refused.status_code == 404 and opened == [folder]
