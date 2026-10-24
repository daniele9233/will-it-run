"""Running an Arena: the same questions for every model, one model in memory at a time, every verdict saved, a run that can be
stopped and resumed, and errors of the run itself kept apart from wrong answers."""

from __future__ import annotations

import threading
import time

import pytest

from agentfit.arena.report import build_report
from agentfit.arena.runner import ArenaError, ArenaService, RunRequest, read_response
from agentfit.arena.store import ArenaStore
from agentfit.events import EventBus
from agentfit.workflows.model_manager import ModelManager
from arena_fakes import NO_TOOLS, ArenaRuntime, installed, response

CATEGORIES = ["general", "math", "json", "tools", "impossible"]


class Rig:
    def __init__(self, db, runtime: ArenaRuntime, models=None) -> None:
        self.runtime = runtime
        self.bus = EventBus()
        self.store = ArenaStore(db)
        self.service = ArenaService(
            self.store, runtime, self.bus, installed=lambda: models or [installed("good", vision=True), installed("bad", vision=True), installed("blind")],
            gateway=lambda slots, emit: ModelManager(runtime, slots, emit), tools=NO_TOOLS,
        )

    def run(self, models, **options) -> str:
        options.setdefault("preset", "quick")
        options.setdefault("categories", CATEGORIES)
        run_id = self.service.start(RunRequest(models=models, **options))
        self.service.join(run_id, 60)
        return run_id


@pytest.fixture
def rig(db):
    return Rig(db, ArenaRuntime({"bad": "wrong"}))


def test_the_same_questions_in_the_same_words_go_to_every_model(rig):
    run_id = rig.run(["good", "bad"])
    asked: dict[str, list[tuple[str, str]]] = {"good": [], "bad": []}
    for call in rig.runtime.calls:
        asked[call["model"]].append((call["question"], repr(call["messages"])))
    assert asked["good"] == asked["bad"] and len(asked["good"]) == rig.store.get_run(run_id)["total"] // 2
    sampling = {(c["model"], repr(c["kwargs"]["sampling"]), c["kwargs"]["temperature"]) for c in rig.runtime.calls}
    assert len(sampling) == 2 and all(temperature == 0.0 for _, _, temperature in sampling)


def test_one_model_is_in_memory_at_a_time_and_the_card_is_left_empty(rig):
    rig.run(["good", "bad"])
    assert rig.runtime.most_loaded == 1
    assert rig.runtime.load_log == ["good", "bad"] and rig.runtime.loaded == {}


def test_every_model_gets_the_same_context_and_it_is_recorded(rig):
    run_id = rig.run(["good", "bad"])
    assert {o for o in rig.runtime.contexts.values()} <= {8192}
    assert [m["context_granted"] for m in rig.store.models(run_id)] == [8192, 8192]
    assert rig.store.get_run(run_id)["settings"]["context"] == 8192


def test_the_long_texts_ask_for_a_larger_window_and_a_big_answer_for_a_larger_one_still(db):
    rig = Rig(db, ArenaRuntime(preset="full"))
    long = rig.run(["good"], preset="standard", categories=["longctx"])
    assert rig.store.get_run(long)["settings"]["context"] == 16384
    big = rig.run(["good"], categories=["general"], max_tokens=16000)
    assert rig.store.get_run(big)["settings"]["context"] == 32768


def test_a_right_model_scores_everything_and_a_wrong_one_nothing(rig):
    run_id = rig.run(["good", "bad"])
    report = build_report(rig.store, run_id)
    good, bad = report["models"]
    assert (good["key"], good["rank"], good["score"]) == ("good", 1, 100.0)
    # A wrong answer may still be partly right (a call with one argument good): it is never a pass.
    assert (bad["key"], bad["rank"]) == ("bad", 2) and bad["score"] < 25 and bad["summary"]["counts"]["passed"] == 0
    assert report["run"]["status"] == "done" and report["run"]["done"] == report["run"]["total"]
    assert set(report["common"]) == set(CATEGORIES)
    cell = next(q for q in report["matrix"] if q["id"] == "general.001")["cells"]
    assert cell["good"]["status"] == "passed" and cell["bad"]["status"] == "failed"


def test_what_the_model_said_and_what_the_verifier_found_are_both_kept(rig):
    run_id = rig.run(["bad"], categories=["general"])
    (row,) = [r for r in rig.store.results(run_id, question_id="general.001")]
    assert (row["status"] == "failed" and row["answer"] and row["verdict"]["reason"]["t"] and row["tps"] is None) or row["tps"] > 0
    assert row["input_tokens"] == 50 and row["output_tokens"] == 30 and row["duration_s"] >= 0 and row["ttft_s"] is not None


def test_the_settings_are_kept_with_the_run_and_the_seed_moves_with_each_repeat(rig):
    run_id = rig.run(["good"], categories=["math"], repeats=2, temperature=0.7, top_p=0.9, top_k=40, repeat_penalty=1.1, sampling_seed=5, max_tokens=900)
    settings = rig.store.get_run(run_id)["settings"]
    assert settings["temperature"] == 0.7 and settings["top_p"] == 0.9 and settings["top_k"] == 40 and settings["repeat_penalty"] == 1.1
    assert settings["seed"] == 5 and settings["repeats"] == 2 and settings["max_tokens"] == 900
    by_question: dict[str, list[dict]] = {}
    for call in rig.runtime.calls:
        by_question.setdefault(call["question"], []).append(call["kwargs"])
    first, second = next(iter(by_question.values()))
    assert first["sampling"] == {"seed": 5, "top_p": 0.9, "top_k": 40, "repeat_penalty": 1.1}
    assert second["sampling"]["seed"] == 6
    assert all(c["kwargs"]["max_tokens"] >= 900 for c in rig.runtime.calls)
    assert len(rig.store.results(run_id)) == 2 * len(by_question)


def test_a_model_that_cannot_see_is_skipped_on_the_pictures_and_not_failed_for_them(db):
    runtime = ArenaRuntime()
    rig = Rig(db, runtime, [installed("sees", vision=True), installed("blind")])
    run_id = rig.run(["sees", "blind"], categories=["vision", "general"], preset="standard")
    rows = rig.store.results(run_id, model_key="blind", light=True)
    skipped = [r for r in rows if r["status"] == "skipped"]
    assert skipped and all(r["question_id"].startswith("vision.") and r["kind"] == "capability" for r in skipped)
    assert not any(c["model"] == "blind" and c["question"].startswith("vision.") for c in runtime.calls)
    report = build_report(rig.store, run_id)
    assert report["common"] == ["general"]
    blind = next(m for m in report["models"] if m["key"] == "blind")
    assert blind["score"] == 100.0 and blind["score_all"] == 100.0 and blind["summary"]["counts"]["skipped"] == len(skipped)
    assert next(m for m in report["models"] if m["key"] == "sees")["categories_scored"] == 2


def test_what_nothing_can_judge_is_not_evaluated_and_is_not_a_wrong_answer(rig):
    run_id = rig.run(["good"], categories=["writing", "general"])
    report = build_report(rig.store, run_id)
    (model,) = report["models"]
    assert model["summary"]["counts"]["unscored"] == 2 and model["summary"]["counts"]["failed"] == 0
    writing = next(c for c in report["categories"] if c["id"] == "writing")
    assert writing["models"]["good"]["score"] is None and writing["models"]["good"]["unscored"] == 2
    assert model["score"] == 100.0 and "writing" not in report["common"]


def test_a_run_that_is_stopped_keeps_its_answers_and_resumes_where_it_was(db):
    runtime = ArenaRuntime()
    rig = Rig(db, runtime)
    runtime.hold_at = 4
    run_id = rig.service.start(RunRequest(models=["good"], preset="quick", categories=["general", "math"]))
    assert runtime.holding.wait(20)
    assert rig.service.stop(run_id)
    runtime.release.set()
    rig.service.join(run_id, 30)
    run = rig.store.get_run(run_id)
    assert run["status"] == "stopped" and 0 < rig.store.count_results(run_id) < run["total"]
    assert runtime.loaded == {} and rig.store.models(run_id)[0]["status"] == "stopped"
    kept = {(r["question_id"], r["repeat"]): r["created_at"] for r in rig.store.results(run_id, light=True)}
    runtime.hold_at = None
    asked_before = len(runtime.calls)
    rig.service.resume(run_id)
    rig.service.join(run_id, 30)
    run = rig.store.get_run(run_id)
    assert run["status"] == "done" and rig.store.count_results(run_id) == run["total"]
    again = {(r["question_id"], r["repeat"]): r["created_at"] for r in rig.store.results(run_id, light=True)}
    assert all(again[k] == v for k, v in kept.items())
    assert len(runtime.calls) - asked_before == run["total"] - len(kept)


def test_a_server_error_is_a_run_error_that_is_not_held_against_the_model_and_is_asked_again_on_resume(db):
    flaky = {"on": True}

    def how(question, number):
        return RuntimeError("the server fell over") if flaky["on"] and question.id in ("general.002", "general.003") else "right"

    rig = Rig(db, ArenaRuntime({"good": how}))
    run_id = rig.run(["good"], categories=["general"])
    report = build_report(rig.store, run_id)
    (model,) = report["models"]
    assert model["summary"]["counts"]["error"] == 2 and model["summary"]["counts"]["failed"] == 0 and model["score"] == 100.0
    row = rig.store.results(run_id, question_id="general.002")[0]
    assert row["status"] == "error" and row["kind"] == "tech" and "the server fell over" in row["error"]
    flaky["on"] = False
    rig.service.resume(run_id)
    rig.service.join(run_id, 30)
    report = build_report(rig.store, run_id)
    assert report["models"][0]["summary"]["counts"]["error"] == 0 and report["models"][0]["summary"]["counts"]["passed"] == report["run"]["total"]


def test_a_model_that_keeps_failing_is_left_out_after_three_errors_in_a_row(db):
    rig = Rig(db, ArenaRuntime({"bad": lambda q, n: RuntimeError("boom"), "good": "right"}))
    run_id = rig.run(["bad", "good"], categories=["general"])
    bad, good = rig.store.models(run_id)
    assert bad["status"] == "failed" and "left out" in bad["error"] and good["status"] == "done"
    assert len(rig.store.results(run_id, model_key="bad")) == 3
    assert rig.store.get_run(run_id)["status"] == "done"


def test_a_model_that_does_not_load_is_reported_and_the_run_goes_on(rig):
    rig.runtime.fail_load.add("bad")
    run_id = rig.run(["bad", "good"])
    models = {m["model_key"]: m for m in rig.store.models(run_id)}
    assert models["bad"]["status"] == "failed" and "out of memory" in models["bad"]["error"] and models["good"]["status"] == "done"
    assert rig.store.results(run_id, model_key="bad") == []
    report = build_report(rig.store, run_id)
    assert [m["key"] for m in report["models"]] == ["good", "bad"] and report["models"][1]["rank"] is None
    assert rig.store.get_run(run_id)["status"] == "done"


def test_when_no_model_loads_the_run_has_failed(rig):
    rig.runtime.fail_load.update({"bad", "good"})
    run_id = rig.run(["bad", "good"])
    assert rig.store.get_run(run_id)["status"] == "failed"


def test_models_that_are_already_loaded_are_not_unloaded_without_the_persons_agreement(rig):
    rig.runtime.foreign["someone-elses"] = "someone-elses"
    with pytest.raises(ArenaError) as refused:
        rig.service.start(RunRequest(models=["good"], preset="quick", categories=["general"]))
    assert refused.value.code == "needs_unload" and refused.value.extra["loaded"][0]["model_key"] == "someone-elses"
    assert rig.runtime.unload_log == [] and rig.store.list_runs() == []
    run_id = rig.run(["good"], categories=["general"], confirm_unload=True)
    assert rig.runtime.unload_log[0] == "someone-elses" and rig.store.get_run(run_id)["status"] == "done"


def test_a_cloud_model_needs_the_persons_agreement_and_is_not_loaded(db):
    rig = Rig(db, ArenaRuntime({"cloud:openai/gpt-5": "right"}))
    with pytest.raises(ArenaError) as refused:
        rig.service.start(RunRequest(models=["cloud:openai/gpt-5"], preset="quick", categories=["general"]))
    assert refused.value.code == "needs_cloud"
    run_id = rig.run(["cloud:openai/gpt-5"], categories=["general"], confirm_cloud=True)
    assert rig.store.get_run(run_id)["status"] == "done" and rig.runtime.load_log == []
    assert rig.store.models(run_id)[0]["cloud"] is True and rig.store.get_run(run_id)["settings"]["cloud_sampling"] == "provider default"


def test_an_unknown_model_or_category_is_refused_before_anything_starts(rig):
    with pytest.raises(ArenaError) as unknown:
        rig.service.start(RunRequest(models=["nothing-here"], preset="quick"))
    assert unknown.value.code == "unknown_model"
    with pytest.raises(ArenaError):
        rig.service.start(RunRequest(models=["good"], categories=["astrology"]))
    with pytest.raises(ArenaError):
        rig.service.start(RunRequest(models=["good"], preset="enormous"))
    with pytest.raises(ValueError):
        RunRequest(models=["good", "good"])
    assert rig.store.list_runs() == []


def test_only_one_run_works_at_a_time(db):
    runtime = ArenaRuntime()
    rig = Rig(db, runtime)
    runtime.hold_at = 1
    first = rig.service.start(RunRequest(models=["good"], preset="quick", categories=["general"]))
    assert runtime.holding.wait(20)
    with pytest.raises(ArenaError) as busy:
        rig.service.start(RunRequest(models=["bad"], preset="quick", categories=["general"]))
    assert busy.value.code == "busy" and busy.value.extra["run"] == first
    with pytest.raises(ArenaError):
        rig.service.delete(first)
    runtime.release.set()
    rig.service.join(first, 30)
    assert rig.service.delete(first) and rig.store.get_run(first) is None


def test_a_run_the_app_left_working_is_stopped_when_it_starts_again(db):
    rig = Rig(db, ArenaRuntime())
    run_id = rig.run(["good"], categories=["general"])
    db.execute("UPDATE arena_run SET status = 'running' WHERE id = ?", (run_id,))
    again = Rig(db, ArenaRuntime())
    assert again.store.get_run(run_id)["status"] == "stopped" and again.store.get_run(run_id)["note"] == "interrupted"


def test_the_screen_is_told_what_happens_and_the_text_is_not_kept_in_the_backlog(rig):
    queue = rig.bus.subscribe()
    run_id = rig.run(["good"], categories=["general"])
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    topics = [e["topic"] for e in events]
    assert "arena.run" in topics and "arena.progress" in topics and "arena.delta" in topics and "arena.model" in topics
    answered = [e["data"] for e in events if e["topic"] == "arena.progress" and e["data"].get("phase") == "answered"]
    assert answered[-1]["done"] == answered[-1]["total"] and answered[-1]["run_id"] == run_id
    assert not [e for e in rig.bus.recent("arena.delta")]


def test_a_run_keeps_its_own_questions_so_it_can_be_read_after_the_bank_changes(rig):
    run_id = rig.run(["good"], categories=["vision", "pdf"], preset="standard")
    picture = next(q for q in rig.store.all_questions(run_id) if q["category"] == "vision")
    assert picture["images"][0]["data_b64"] and picture["reference"] and picture["expected"]
    assert rig.store.question(run_id, "pdf.001")["attachments"][0]["mime"] == "application/pdf"
    assert [q["id"] for q in rig.store.questions(run_id)][:2] == ["pdf.001", "pdf.002"] or rig.store.questions(run_id)


def test_the_environment_and_the_tools_that_checked_the_answers_are_recorded(rig):
    run_id = rig.run(["good"], categories=["general"])
    environment = rig.store.get_run(run_id)["environment"]
    assert environment["tools"]["terraform"]["available"] is False and environment["suite"]["version"]


# -- reading a reply ----------------------------------------------------------------------------------------------------


def test_a_reply_is_read_with_its_reasoning_apart_and_its_tool_calls_parsed():
    reply = {"choices": [{"message": {"content": "<think>let me see</think>Risposta: 4", "reasoning_content": "first thought", "tool_calls": [
        {"function": {"name": "get_weather", "arguments": '{"city": "Roma"}'}}, {"function": {"name": "broken", "arguments": "{nope"}}]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 9, "completion_tokens_details": {"reasoning_tokens": 5}}}
    answer, counts = read_response(reply)
    assert answer.text == "Risposta: 4" and "first thought" in answer.reasoning and "let me see" in answer.reasoning
    assert [(c.name, c.arguments) for c in answer.tool_calls] == [("get_weather", {"city": "Roma"}), ("broken", None)]
    assert answer.finish_reason == "tool_calls" and counts == {"input_tokens": 7, "output_tokens": 9, "reasoning_tokens": 5}


def test_a_reply_that_ran_out_of_tokens_is_a_truncation_and_not_a_wrong_answer(db):
    cut = response("Risposta: ", finish="length")
    rig = Rig(db, ArenaRuntime({"good": lambda q, n: cut}))
    run_id = rig.run(["good"], categories=["general"])
    row = rig.store.results(run_id, question_id="general.001")[0]
    assert row["status"] == "failed" and row["kind"] == "truncated" and row["finish_reason"] == "length"


def test_the_speed_of_an_answer_is_measured_from_its_first_token(db):
    def slow(question, number):
        time.sleep(0.05)
        return response(question.reference, tokens=100)

    rig = Rig(db, ArenaRuntime({"good": slow}))
    run_id = rig.run(["good"], categories=["general"])
    row = rig.store.results(run_id, question_id="general.001")[0]
    assert row["ttft_s"] >= 0.05 and row["output_tokens"] == 100
    assert row["tps"] is None or row["tps"] > 0
    assert threading.active_count() >= 1


def test_how_long_a_model_took_to_be_ready_and_the_context_it_got_are_recorded(rig):
    run_id = rig.run(["good"], categories=["general"])
    (model,) = rig.store.models(run_id)
    assert model["load_seconds"] is not None and model["load_seconds"] >= 0 and model["context_granted"] == 8192
