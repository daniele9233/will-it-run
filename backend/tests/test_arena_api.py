"""The Arena over HTTP: what a run could ask, the models, a run from start to report, the answers, the files, and the questions
the person has to answer before a run may begin."""

from __future__ import annotations

import csv
import html
import io
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.arena.runner import ArenaService
from agentfit.arena.store import ArenaStore
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.workflows.runner import RunManager
from arena_fakes import NO_TOOLS, ArenaRuntime, installed
from conftest import load_profile

CATEGORIES = ["general", "math", "vision", "writing", "impossible"]


@pytest.fixture
def arena(monkeypatch):
    monkeypatch.setattr("agentfit.app.nvidia_vram_probe", lambda: None)
    monkeypatch.setattr("agentfit.app.model_spill_mib", lambda: None)
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    db = Database(":memory:")
    services = AppServices(db=db, settings=Settings(), event_bus=EventBus())
    services._profile = load_profile("rtx4080super_16gb_32gb")
    runtime = ArenaRuntime({"bad": "wrong"})
    runtime.models = [installed("good", vision=True, name="Good model"), installed("bad", name="Bad model")]
    services._runtime = runtime  # type: ignore[assignment]
    services._runs = RunManager(db, runtime, bus=services.bus, vram_budget_gb=lambda: 14.2, checkpoint_path=":memory:")
    services._arena = ArenaService(
        ArenaStore(db), lambda: services.models_runtime, services.bus, installed=services.installed_models, gateway=services._arena_gateway,
        tools=NO_TOOLS, environment=services._arena_environment,
    )
    client = TestClient(create_app(services))
    client.services = services  # type: ignore[attr-defined]
    client.runtime = runtime  # type: ignore[attr-defined]
    return client


def finish(client: TestClient, run_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        report = client.get(f"/api/arena/runs/{run_id}").json()
        if report["run"]["status"] not in ("queued", "running", "stopping"):
            return report
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


def start(client: TestClient, **body: Any) -> str:
    body.setdefault("preset", "standard")
    body.setdefault("categories", CATEGORIES)
    reply = client.post("/api/arena/runs", json={"models": ["good", "bad"], **body})
    assert reply.status_code == 200, reply.text
    return reply.json()["id"]


def test_the_overview_says_what_the_packs_hold(arena):
    data = arena.get("/api/arena/overview").json()
    assert data["presets"] == {"quick": 48, "standard": 115, "full": 192}
    assert len(data["categories"]) == 18 and data["suite"]["version"] == "1.0.0" and len(data["suite"]["bank_hash"]) == 64
    vision = next(c for c in data["categories"] if c["id"] == "vision")
    assert vision["needs"] == ["vision"] and vision["counts"] == [2, 6, 10] and vision["area"] == "vision"
    assert next(c for c in data["categories"] if c["id"] == "writing")["scored"] is False
    assert data["tools"]["terraform"]["available"] is False and data["areas"][0] == "knowledge" and data["busy"] is None


def test_the_models_that_can_be_chosen_and_what_each_can_do(arena):
    arena.runtime.foreign["good"] = "good"
    data = arena.get("/api/arena/models").json()
    by_key = {m["key"]: m for m in data["local"]}
    assert by_key["good"]["vision"] is True and by_key["good"]["loaded"] is True and by_key["bad"]["vision"] is False and by_key["bad"]["loaded"] is False
    assert data["loaded"][0]["model_key"] == "good" and data["cloud"] == []


def test_a_plan_says_how_much_will_be_asked_without_asking_it(arena):
    plan = arena.post("/api/arena/plan", json={"models": ["good", "bad"], "preset": "quick", "repeats": 2}).json()
    assert plan["questions"] == 48 and plan["answers"] == 48 * 2 * 2 and plan["blind"] == ["bad"] and plan["context"] == 8192
    assert arena.runtime.calls == []
    refused = arena.post("/api/arena/plan", json={"models": ["good"], "categories": ["astrology"]})
    assert refused.status_code == 400 and "astrology" in refused.json()["detail"]
    assert arena.post("/api/arena/plan", json={"models": []}).status_code == 422


def test_a_run_from_the_start_to_its_report(arena):
    run_id = start(arena)
    report = finish(arena, run_id)
    assert report["run"]["status"] == "done" and report["run"]["done"] == report["run"]["total"]
    good, bad = report["models"]
    assert good["key"] == "good" and good["rank"] == 1 and good["score"] > bad["score"]
    assert report["run"]["suite_version"] == "1.0.0" and report["run"]["seed"] == 42 and report["run"]["settings"]["temperature"] == 0.0
    assert report["run"]["environment"]["tools"]["terraform"]["available"] is False and report["run"]["environment"]["lmstudio"]["version"] == "0.4.0"
    blind = {c["id"]: c for c in report["categories"]}["vision"]["models"]["bad"]
    assert blind["skipped"] == 6 and blind["score"] is None
    runs = arena.get("/api/arena/runs").json()["runs"]
    assert [r["id"] for r in runs] == [run_id] and runs[0]["done"] == runs[0]["total"] and runs[0]["models"][0]["status"] == "done"


def test_the_detail_of_a_question_has_the_picture_the_expected_answer_and_what_each_model_said(arena):
    run_id = start(arena)
    finish(arena, run_id)
    data = arena.get(f"/api/arena/runs/{run_id}/questions/vision.001").json()
    question = data["question"]
    assert question["images"][0]["data_url"].startswith("data:image/png;base64,") and question["expected"] and question["check_type"] == "number"
    assert question["reference"].startswith("Risposta:") and "check" not in question
    assert data["answers"]["good"][0]["status"] == "passed" and data["answers"]["bad"][0]["status"] == "skipped"
    assert data["answers"]["bad"][0]["reason"] == "This model cannot see pictures, so the question was not asked."
    wrong = arena.get(f"/api/arena/runs/{run_id}/questions/general.001").json()["answers"]["bad"][0]
    assert wrong["status"] == "failed" and wrong["reason"] and wrong["expected"] and wrong["answer"] and wrong["output_tokens"] == 30
    assert arena.get(f"/api/arena/runs/{run_id}/questions/nope.001").status_code == 404


def test_a_person_can_mark_an_answer_nothing_could_judge_and_the_mark_never_enters_a_score(arena):
    run_id = start(arena)
    before = finish(arena, run_id)
    row = arena.get(f"/api/arena/runs/{run_id}/questions/writing.001").json()["answers"]["good"][0]
    assert row["status"] == "unscored" and row["manual"] is None
    marked = arena.post(f"/api/arena/runs/{run_id}/manual", json={"model": "good", "question": "writing.001", "mark": "failed", "note": "off topic"})
    assert marked.status_code == 200
    row = arena.get(f"/api/arena/runs/{run_id}/questions/writing.001").json()["answers"]["good"][0]
    assert row["status"] == "unscored" and row["manual"] == "failed" and row["manual_note"] == "off topic"
    after = arena.get(f"/api/arena/runs/{run_id}").json()
    assert [m["score"] for m in after["models"]] == [m["score"] for m in before["models"]]
    assert arena.post(f"/api/arena/runs/{run_id}/manual", json={"model": "good", "question": "writing.001", "mark": "maybe"}).status_code == 400
    assert arena.post(f"/api/arena/runs/{run_id}/manual", json={"model": "good", "question": "nope.001", "mark": "passed"}).status_code == 404


def test_the_report_comes_as_json_csv_markdown_and_a_page(arena):
    run_id = start(arena, title="Prova di prova")
    finish(arena, run_id)
    exported = {fmt: arena.get(f"/api/arena/runs/{run_id}/export", params={"format": fmt}) for fmt in ("json", "csv", "md", "html")}
    assert all(r.status_code == 200 for r in exported.values())
    assert all("attachment" in r.headers["content-disposition"] and "prova-di-prova" in r.headers["content-disposition"] for r in exported.values())

    data = json.loads(exported["json"].text)
    assert data["format"] == "willitrun-arena-report" and data["suite"]["hash"] and data["suite"]["seed"] == 42 and data["settings"]["repeats"] == 1
    assert data["results"] and {r["status"] for r in data["results"]} >= {"passed", "failed", "unscored", "skipped"}
    assert data["ranking"][0]["key"] == "good" and data["environment"]["tools"]
    assert all("data_b64" not in str(q.get("images")) for q in data["questions"])

    rows = list(csv.DictReader(io.StringIO(exported["csv"].content.decode("utf-8-sig"))))
    assert len(rows) == len(data["results"]) and {"model", "question", "status", "score", "kind", "reason", "tokens_per_second"} <= set(rows[0])
    assert any(r["status"] == "unscored" and r["score"] == "" for r in rows)

    text = exported["md"].text
    assert "# Prova di prova" in text and "Ranking" in text and "Good model" in text and "Not evaluated" in text and data["suite"]["hash"][:12] in text

    page = exported["html"].text
    assert page.startswith("<!doctype html>") and "<svg" in page and "<script" not in page and "https://" not in page.replace("http://www.w3.org", "")
    assert "Prova di prova" in page and "st-unscored" in page


def test_an_answer_with_markup_in_it_cannot_break_the_exported_page(arena):
    arena.runtime.behaviour["good"] = lambda q, n: {"choices": [{"message": {"content": "<script>alert(1)</script> Risposta: x"}, "finish_reason": "stop"}], "usage": {}}
    run_id = start(arena, categories=["general"], preset="quick")
    finish(arena, run_id)
    page = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "html"}).text
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page


def test_a_run_is_stopped_and_resumed_over_http(arena):
    arena.runtime.hold_at = 3
    run_id = start(arena, categories=["general"], preset="quick")
    assert arena.runtime.holding.wait(20)
    assert arena.post(f"/api/arena/runs/{run_id}/stop").json() == {"stopping": True}
    arena.runtime.release.set()
    report = finish(arena, run_id)
    assert report["run"]["status"] == "stopped" and report["run"]["done"] < report["run"]["total"]
    arena.runtime.hold_at = None
    assert arena.post(f"/api/arena/runs/{run_id}/resume", json={}).status_code == 200
    report = finish(arena, run_id)
    assert report["run"]["status"] == "done" and report["run"]["done"] == report["run"]["total"]
    again = arena.post(f"/api/arena/runs/{run_id}/resume", json={})
    assert again.status_code == 409 and again.json()["code"] == "complete"


def test_the_person_is_asked_before_models_are_unloaded_or_a_cloud_model_is_used(arena):
    arena.runtime.foreign["other"] = "other"
    refused = arena.post("/api/arena/runs", json={"models": ["good"], "preset": "quick", "categories": ["general"]})
    assert refused.status_code == 409 and refused.json()["code"] == "needs_unload" and refused.json()["loaded"][0]["model_key"] == "other"
    cloud = arena.post("/api/arena/runs", json={"models": ["cloud:openai/gpt-5"], "preset": "quick", "categories": ["general"]})
    assert cloud.status_code == 409 and cloud.json()["code"] == "needs_cloud" and cloud.json()["cloud"] == ["cloud:openai/gpt-5"]
    assert arena.get("/api/arena/runs").json()["runs"] == []
    run_id = start(arena, categories=["general"], preset="quick", models=["good"], confirm_unload=True)
    assert finish(arena, run_id)["run"]["status"] == "done"


def test_a_run_can_be_renamed_and_deleted(arena):
    run_id = start(arena, categories=["general"], preset="quick")
    finish(arena, run_id)
    assert arena.patch(f"/api/arena/runs/{run_id}", json={"title": "Nuovo nome", "note": "una nota"}).json()["title"] == "Nuovo nome"
    run = arena.get(f"/api/arena/runs/{run_id}").json()["run"]
    assert run["title"] == "Nuovo nome" and run["note"] == "una nota"
    assert arena.delete(f"/api/arena/runs/{run_id}").status_code == 200
    assert arena.get(f"/api/arena/runs/{run_id}").status_code == 404
    assert arena.delete(f"/api/arena/runs/{run_id}").status_code == 404
    assert arena.get(f"/api/arena/runs/{run_id}/export").status_code == 404


def test_the_quantizations_of_one_model_are_compared_over_http(arena):
    arena.runtime.models.append(installed("good@q3_k_m", name="Good model", quant="Q3_K_M", size_bytes=3_000_000_000))
    listed = {m["key"]: m for m in arena.get("/api/arena/models").json()["local"]}
    assert listed["good"]["family"] == listed["good@q3_k_m"]["family"] != listed["bad"]["family"]
    arena.runtime.behaviour["good@q3_k_m"] = lambda question, _n: "wrong" if question.id.startswith("general.") else "right"
    run_id = start(arena, models=["good", "good@q3_k_m", "bad"], categories=["general", "math", "impossible"])
    report = finish(arena, run_id)
    (family,) = report["families"]
    assert family["members"] == ["good", "good@q3_k_m"] and family["reference"] == "good"
    answer = arena.get(f"/api/arena/runs/{run_id}/quants").json()
    assert answer["problem"] is None and answer["comparison"]["order"] == ["good", "good@q3_k_m"]
    other = answer["comparison"]["models"]["good@q3_k_m"]
    assert other["delta"] < 0 and other["size_bytes"] == 3_000_000_000 and other["quant"] == "Q3_K_M"
    # Chosen by hand: any models of the run, any of them as the reference.
    by_hand = arena.get(f"/api/arena/runs/{run_id}/quants", params={"models": "good,bad", "reference": "bad"}).json()
    assert by_hand["comparison"]["reference"] == "bad" and by_hand["comparison"]["models"]["good"]["delta"] > 0
    assert arena.get(f"/api/arena/runs/{run_id}/quants", params={"models": "good"}).json()["problem"] == "needs_two"
    assert arena.get("/api/arena/runs/arena-nothing/quants").status_code == 404
    # The files carry the comparison too, in the language of whoever asks.
    markdown = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "md"}).text
    assert "## Quantizations: Good model" in markdown and "Q3_K_M" in markdown and "real loss" in markdown
    italian = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "md"}, headers={"x-willitrun-lang": "it"}).text
    assert "## Quantizzazioni: Good model" in italian and "perdita reale" in italian
    page = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "html"}).text
    assert "Quantizations: Good model" in page and "Graphics memory" in page
    exported = json.loads(arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "json"}).text)
    assert exported["quantizations"][0]["reference"] == "good" and exported["quantizations"][0]["models"]["good@q3_k_m"]["verdict"] in ("worse", "noise")


def test_every_arena_route_is_behind_the_token(arena, monkeypatch):
    monkeypatch.setenv("AGENTFIT_TOKEN", "s3cret")
    for path in ("/api/arena/overview", "/api/arena/models", "/api/arena/runs"):
        assert arena.get(path).status_code == 401
        assert arena.get(path, headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert arena.get("/api/arena/runs/arena-x/quants").status_code == 401
    assert arena.post("/api/arena/runs", json={"models": ["good"]}).status_code == 401


def test_the_reasons_the_report_and_the_messages_are_written_in_the_language_of_the_reader(arena):
    run_id = start(arena, categories=["general", "instructions"], preset="standard")
    finish(arena, run_id)
    path = f"/api/arena/runs/{run_id}/questions/general.001"
    english = arena.get(path).json()["answers"]["bad"][0]
    italian = arena.get(path, headers={"x-willitrun-lang": "it"}).json()["answers"]["bad"][0]
    assert english["reason"].startswith("Expected") and italian["reason"].startswith("Atteso")
    rules = arena.get(f"/api/arena/runs/{run_id}/questions/instructions.001", headers={"x-willitrun-lang": "it"}).json()["answers"]["bad"][0]
    assert rules["checks"] and all("expected" not in c["text"] for c in rules["checks"]) and any("Parole" in c["text"] or "Frasi" in c["text"] for c in rules["checks"])
    report = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "md"}, headers={"x-willitrun-lang": "it"}).text
    assert "## Classifica" in report and "Cultura generale" in report and "## Ranking" not in report
    refused = arena.get("/api/arena/runs/nope/export", headers={"x-willitrun-lang": "it"})
    assert refused.status_code == 404 and refused.json()["detail"] == "Questa sfida non esiste."
    no_models = arena.post("/api/arena/runs", json={"models": ["nothing"], "preset": "quick"}, headers={"x-willitrun-lang": "it"})
    assert no_models.status_code == 409 and "non è installato" in no_models.json()["detail"]


def test_the_report_can_be_saved_where_the_person_chose(arena, tmp_path):
    run_id = start(arena, categories=["general"], preset="quick")
    finish(arena, run_id)
    target = tmp_path / "rapporto.html"
    saved = arena.post(f"/api/arena/runs/{run_id}/export", json={"format": "html", "path": str(target)})
    assert saved.status_code == 200 and saved.json()["path"] == str(target)
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>") and saved.json()["bytes"] == target.stat().st_size
    assert arena.post(f"/api/arena/runs/{run_id}/export", json={"format": "html", "path": str(tmp_path / "nope" / "x.html")}).status_code == 400
    assert arena.post(f"/api/arena/runs/{run_id}/export", json={"format": "html", "path": "relativo.html"}).status_code == 400
    assert arena.post(f"/api/arena/runs/{run_id}/export", json={"format": "pdf", "path": str(target)}).status_code == 400
    assert arena.post("/api/arena/runs/nope/export", json={"format": "html", "path": str(target)}).status_code == 404


# -- a report to read, and models tested in separate challenges --------------------------------------------------


def test_the_answers_report_shows_every_question_and_what_every_model_said(arena):
    run_id = start(arena, title="Domande e risposte", categories=["general", "math"], preset="quick")
    finish(arena, run_id)
    asked = arena.get(f"/api/arena/runs/{run_id}/questions/general.001").json()["question"]
    page = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "answers"})
    markdown = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "answers-md"})
    assert page.status_code == 200 and markdown.status_code == 200
    assert "domande-e-risposte-answers.html" in page.headers["content-disposition"]
    assert "domande-e-risposte-answers.md" in markdown.headers["content-disposition"]

    html_page = page.text
    assert html_page.startswith("<!doctype html>") and "<script" not in html_page
    assert html.escape(asked["prompt"].split("\n")[0]) in html_page and html.escape(asked["expected"]) in html_page
    # Both models are there, and the right answers as well as the wrong ones: this is not the list of failures.
    assert html_page.count("Good model") >= 2 and html_page.count("Bad model") >= 2
    assert 'class="ans passed"' in html_page and 'class="ans failed"' in html_page
    assert "Only where the models differ" in html_page and 'class="q differs' in html_page

    text = markdown.text
    assert "### `general.001`" in text and "#### Good model" in text and "#### Bad model" in text and "```text" in text
    assert asked["prompt"].split("\n")[0] in text and f"**Expected**: {asked['expected']}" in text


def test_the_answers_report_is_in_the_language_of_the_reader(arena):
    run_id = start(arena, categories=["general"], preset="quick")
    finish(arena, run_id)
    italian = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "answers-md"}, headers={"x-willitrun-lang": "it"}).text
    assert "Domande e risposte" in italian and "**Atteso**" in italian and "Classifica" in italian


def test_an_answer_with_markup_cannot_break_the_answers_report(arena):
    arena.runtime.behaviour["good"] = lambda q, n: {"choices": [{"message": {"content": "<script>alert(1)</script> Risposta: x"}, "finish_reason": "stop"}], "usage": {}}
    run_id = start(arena, categories=["general"], preset="quick")
    finish(arena, run_id)
    page = arena.get(f"/api/arena/runs/{run_id}/export", params={"format": "answers"}).text
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page


def test_models_tested_in_separate_challenges_can_be_put_side_by_side(arena):
    first = start(arena, models=["good"], title="Il buono", categories=["general", "math"], preset="quick")
    finish(arena, first)
    second = start(arena, models=["bad"], title="Il cattivo", categories=["general", "math"], preset="quick")
    finish(arena, second)

    made = arena.post("/api/arena/compare", json={"run_ids": [first, second], "title": "Buono contro cattivo"})
    assert made.status_code == 200, made.text
    body = made.json()
    assert set(body["models"]) == {"Good model", "Bad model"} and body["replaced"] == []

    report = arena.get(f"/api/arena/runs/{body['id']}").json()
    assert report["run"]["status"] == "done" and report["run"]["title"] == "Buono contro cattivo"
    assert report["run"]["settings"]["merged"]["from"] == [first, second]
    assert {m["key"] for m in report["models"]} == {"good", "bad"}
    # The same questions, so the same ranking the two models would have had in one challenge.
    assert report["models"][0]["key"] == "good" and report["models"][0]["score"] > report["models"][1]["score"]
    together = start(arena, models=["good", "bad"], categories=["general", "math"], preset="quick")
    reference = finish(arena, together)
    assert {m["key"]: m["score"] for m in report["models"]} == {m["key"]: m["score"] for m in reference["models"]}

    # Every report works on it, the one with the answers included.
    page = arena.get(f"/api/arena/runs/{body['id']}/export", params={"format": "answers"}).text
    assert "Good model" in page and "Bad model" in page and "separate challenges" in page
    listed = {r["id"]: r for r in arena.get("/api/arena/runs").json()["runs"]}
    assert listed[body["id"]]["merged"] is True and listed[first]["merged"] is False
    # It holds copies: deleting an original leaves the comparison readable.
    assert arena.delete(f"/api/arena/runs/{first}").status_code == 200
    assert {m["key"] for m in arena.get(f"/api/arena/runs/{body['id']}").json()["models"]} == {"good", "bad"}


def test_a_comparison_has_nothing_to_resume_and_cannot_be_compared_again(arena):
    first = start(arena, models=["good"], categories=["general"], preset="quick")
    finish(arena, first)
    second = start(arena, models=["bad"], categories=["general"], preset="quick")
    finish(arena, second)
    merged = arena.post("/api/arena/compare", json={"run_ids": [first, second]}).json()["id"]
    resumed = arena.post(f"/api/arena/runs/{merged}/resume", json={})
    assert resumed.status_code == 409 and resumed.json()["code"] == "merged"
    again = arena.post("/api/arena/compare", json={"run_ids": [merged, first]})
    assert again.status_code == 409 and "already a comparison" in again.json()["detail"]


def test_challenges_that_asked_other_questions_cannot_be_compared(arena):
    first = start(arena, models=["good"], categories=["general"], preset="quick")
    finish(arena, first)
    other_seed = start(arena, models=["bad"], categories=["general"], preset="quick", seed=7)
    finish(arena, other_seed)
    refused = arena.post("/api/arena/compare", json={"run_ids": [first, other_seed]})
    assert refused.status_code == 409 and "did not ask the same questions" in refused.json()["detail"]
    assert arena.post("/api/arena/compare", json={"run_ids": [first]}).status_code == 400
    assert arena.post("/api/arena/compare", json={"run_ids": [first, "nope"]}).status_code == 404


def test_a_model_tested_twice_keeps_its_newest_answers_and_says_so(arena):
    first = start(arena, models=["good", "bad"], categories=["general"], preset="quick")
    finish(arena, first)
    second = start(arena, models=["good"], categories=["general"], preset="quick", temperature=0.4, title="Più tardi")
    finish(arena, second)
    body = arena.post("/api/arena/compare", json={"run_ids": [first, second]}).json()
    assert [(r["model"], r["kept"], r["dropped"]) for r in body["replaced"]] == [("Good model", second, first)]
    assert any("temperature" in line for line in body["differences"])
    report = arena.get(f"/api/arena/runs/{body['id']}").json()
    assert {m["key"] for m in report["models"]} == {"good", "bad"}
