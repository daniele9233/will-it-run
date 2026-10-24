"""Verdicts, reasons and the scores built from them: what counts, what does not, and how sure the number is."""

from __future__ import annotations

import pytest

from agentfit import i18n
from agentfit.arena import scoring
from agentfit.arena.model import Reason, Verdict, why

# -- reasons are translated when they are read, not when they are written -----------------------


def test_a_reason_keeps_its_template_and_renders_in_the_active_language():
    reason = why("Expected {expected}, found {found}.", expected="274,5", found="277,8")
    assert reason.render() == "Expected 274,5, found 277,8."
    assert Reason.from_json(reason.to_json()) == reason
    i18n.set_default_language("it")
    try:
        # A template with no Italian entry still reads: the English text is the key.
        assert why("A sentence nobody translated {x}", x=1).render() == "A sentence nobody translated 1"
    finally:
        i18n.set_default_language("en")


def test_verdict_constructors_set_status_score_and_kind():
    ok = Verdict.passed(method="match")
    assert (ok.status, ok.score, ok.kind) == ("passed", 1.0, None)
    bad = Verdict.failed("wrong", why("No."), expected="1", extracted="2")
    assert (bad.status, bad.score, bad.kind) == ("failed", 0.0, "wrong")
    half = Verdict.partial(0.4, why("Some."))
    assert (half.status, half.score) == ("partial", 0.4)
    skipped = Verdict.unscored("manual", why("Read it yourself."))
    assert (skipped.status, skipped.score, skipped.kind) == ("unscored", None, "manual")
    broken = Verdict.error(why("LM Studio fell over."))
    assert (broken.status, broken.score, broken.kind) == ("error", None, "tech")


def test_a_partial_score_must_be_strictly_between_zero_and_one():
    with pytest.raises(ValueError):
        Verdict.partial(1.0, why("x"))
    with pytest.raises(ValueError):
        Verdict.partial(0.0, why("x"))


# -- scoring -----------------------------------------------------------------------------------


def row(question, status, score=None, repeat=0, kind=None, tps=50.0, tokens=100):
    return {"question_id": question, "repeat": repeat, "status": status, "score": score, "kind": kind, "tps": tps, "output_tokens": tokens, "duration_s": 2.0}


CATS = {"m1": "math", "m2": "math", "m3": "math", "m4": "math", "l1": "logic", "l2": "logic", "w1": "writing", "v1": "vision"}


def test_a_category_score_is_the_mean_of_its_scored_questions_as_a_percentage():
    rows = [row("m1", "passed", 1.0), row("m2", "passed", 1.0), row("m3", "failed", 0.0, kind="wrong"), row("m4", "partial", 0.5)]
    summary = scoring.summarize(rows, CATS, seed=1)
    math = summary["categories"]["math"]
    assert math["score"] == pytest.approx(62.5)
    assert (math["scored"], math["passed"], math["partial"], math["failed"]) == (4, 2, 1, 1)
    assert math["ci"][0] <= math["score"] <= math["ci"][1]


def test_unscored_skipped_and_technical_errors_are_not_errors_and_not_in_the_score():
    rows = [row("m1", "passed", 1.0), row("m2", "unscored", None, kind="manual"), row("m3", "skipped", None), row("m4", "error", None, kind="tech")]
    math = scoring.summarize(rows, CATS, seed=1)["categories"]["math"]
    assert math["score"] == pytest.approx(100.0)
    assert (math["scored"], math["unscored"], math["skipped"], math["error"], math["failed"]) == (1, 1, 1, 1, 0)


def test_a_category_with_nothing_scored_has_no_score_at_all():
    rows = [row("v1", "skipped", None)]
    vision = scoring.summarize(rows, CATS, seed=1)["categories"]["vision"]
    assert vision["score"] is None and vision["ci"] is None and vision["skipped"] == 1
    writing = scoring.summarize([row("w1", "unscored", None, kind="manual")], CATS, seed=1)["categories"]["writing"]
    assert writing["score"] is None and writing["unscored"] == 1


def test_repeats_of_one_question_are_averaged_before_the_category_is():
    rows = [row("m1", "passed", 1.0, repeat=0), row("m1", "failed", 0.0, repeat=1, kind="wrong"), row("m2", "passed", 1.0)]
    summary = scoring.summarize(rows, CATS, seed=1)
    assert summary["categories"]["math"]["score"] == pytest.approx(75.0)
    assert summary["consistency"] == pytest.approx(0.5)  # one question of two gave the same verdict every time


def test_failures_are_counted_by_kind_and_hallucination_is_its_own_number():
    rows = [
        row("m1", "failed", 0.0, kind="wrong"), row("m2", "failed", 0.0, kind="format"), row("m3", "failed", 0.0, kind="fabricated"),
        row("m4", "failed", 0.0, kind="truncated"), row("l1", "passed", 1.0), row("l2", "failed", 0.0, kind="refused"),
    ]
    summary = scoring.summarize(rows, CATS, seed=1)
    assert summary["fail_kinds"] == {"wrong": 1, "format": 1, "fabricated": 1, "truncated": 1, "refused": 1}
    assert summary["counts"]["failed"] == 5 and summary["counts"]["passed"] == 1


def test_speed_ignores_tiny_answers_and_reports_the_median():
    rows = [row("m1", "passed", 1.0, tps=40.0, tokens=200), row("m2", "passed", 1.0, tps=60.0, tokens=200), row("m3", "passed", 1.0, tps=500.0, tokens=3), row("m4", "passed", 1.0, tps=80.0, tokens=200)]
    speed = scoring.summarize(rows, CATS, seed=1)["speed"]
    assert speed["tps_mean"] == pytest.approx(60.0)
    assert speed["tps_median"] == pytest.approx(60.0)
    assert speed["output_tokens"] == 603


def test_the_same_seed_gives_the_same_interval_and_another_seed_may_not():
    rows = [row(f"m{i}", "passed" if i % 3 else "failed", 1.0 if i % 3 else 0.0, kind=None if i % 3 else "wrong") for i in range(1, 5)]
    cats = {f"m{i}": "math" for i in range(1, 5)}
    a = scoring.summarize(rows, cats, seed=5)["categories"]["math"]["ci"]
    b = scoring.summarize(rows, cats, seed=5)["categories"]["math"]["ci"]
    assert a == b


def test_the_summary_is_json_friendly():
    import json

    summary = scoring.summarize([row("m1", "passed", 1.0), row("l1", "failed", 0.0, kind="wrong")], CATS, seed=1)
    assert json.loads(json.dumps(summary)) == summary


# -- ranking -------------------------------------------------------------------------------------


def make_summary(scores: dict[str, list[float] | None]) -> dict:
    cats = {}
    for cat, values in scores.items():
        if values is None:
            cats[cat] = {"score": None, "ci": None, "scored": 0, "values": [], "total": 3}
        else:
            cats[cat] = {"score": 100 * sum(values) / len(values), "ci": None, "scored": len(values), "values": values, "total": len(values)}
    return {"categories": cats, "speed": {"tps_mean": 50.0}}


def test_models_are_ranked_on_the_categories_every_model_could_be_scored_on():
    # B has no vision, so vision is left out for everybody: A cannot win just by answering one more category.
    a = make_summary({"math": [1.0, 1.0], "logic": [0.0, 0.0], "vision": [1.0, 1.0]})
    b = make_summary({"math": [1.0, 0.0], "logic": [1.0, 1.0], "vision": None})
    ranking = scoring.rank({"a": a, "b": b}, seed=1)
    assert ranking.common == ["math", "logic"]  # in the order the categories are listed everywhere
    by_model = {r["model"]: r for r in ranking.models}
    assert by_model["a"]["score"] == pytest.approx(50.0)
    assert by_model["b"]["score"] == pytest.approx(75.0)
    assert [r["model"] for r in ranking.models] == ["b", "a"] and ranking.models[0]["rank"] == 1
    assert by_model["a"]["score_all"] == pytest.approx(66.67, abs=0.01)
    assert by_model["b"]["score_all"] == pytest.approx(75.0)


def test_an_ordinary_model_with_nothing_in_common_still_gets_a_score_of_its_own():
    a = make_summary({"math": [1.0]})
    b = make_summary({"logic": [0.0]})
    ranking = scoring.rank({"a": a, "b": b}, seed=1)
    assert ranking.common == []
    assert {r["model"]: r["score_all"] for r in ranking.models} == {"a": 100.0, "b": 0.0}


def test_areas_are_the_average_of_their_scored_categories():
    summary = make_summary({"sql": [1.0, 1.0], "terraform": [0.0, 0.0], "ansible": None})
    areas = scoring.areas_of(summary["categories"])
    assert areas["infra"]["score"] == pytest.approx(50.0)
    assert sorted(areas["infra"]["categories"]) == ["sql", "terraform"]
    assert "vision" not in areas or areas["vision"]["score"] is None


def test_ties_share_the_better_rank_and_are_marked():
    a = make_summary({"math": [1.0]})
    b = make_summary({"math": [1.0]})
    ranking = scoring.rank({"a": a, "b": b}, seed=1)
    assert [r["rank"] for r in ranking.models] == [1, 1]
    assert all(r["tied"] for r in ranking.models)
