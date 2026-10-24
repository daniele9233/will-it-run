"""The quantizations of one model, side by side: which models are one model, how they are ordered, how much graphics memory each
took, and whether the difference between two of them is real or noise."""

from __future__ import annotations

import pytest

from agentfit.arena import paired, quants
from agentfit.arena.report import build_report
from agentfit.arena.runner import ArenaService, RunRequest
from agentfit.arena.store import ArenaStore
from agentfit.events import EventBus
from agentfit.workflows.model_manager import ModelManager, VramReading
from arena_fakes import NO_TOOLS, ArenaRuntime, installed

CATEGORIES = ["general", "math", "json", "tools", "impossible"]

# -- the arithmetic ------------------------------------------------------------------------------


def test_the_sign_test_is_the_exact_binomial_one():
    assert paired.sign_test(9, 1) == pytest.approx(22 / 1024)
    assert paired.sign_test(1, 9) == pytest.approx(22 / 1024)
    assert paired.sign_test(10, 0) == pytest.approx(2 / 1024)
    assert paired.sign_test(5, 5) == 1.0
    assert paired.sign_test(0, 0) == 1.0
    # A long list of questions does not overflow.
    assert 0.0 < paired.sign_test(120, 60) < 1e-4


def test_holm_corrects_several_comparisons_and_never_lowers_a_p_value():
    assert paired.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert paired.holm([0.5]) == [0.5]
    assert paired.holm([0.4, 0.9]) == pytest.approx([0.8, 0.9])
    assert all(a >= p for a, p in zip(paired.holm([0.2, 0.001, 0.04]), [0.2, 0.001, 0.04], strict=True))


def test_a_difference_is_real_only_when_the_interval_and_the_corrected_test_agree():
    assert paired.verdict(-5.0, [-8.0, -2.0], 0.01) == "worse"
    assert paired.verdict(5.0, [2.0, 8.0], 0.01) == "better"
    assert paired.verdict(-5.0, [-8.0, 1.0], 0.01) == "noise"  # the interval holds zero
    assert paired.verdict(-5.0, [-8.0, -2.0], 0.2) == "noise"  # the corrected test does not agree
    assert paired.verdict(0.0, None, 0.0) == "noise"


def values(*scores: float) -> dict[str, float]:
    return {f"q{i}": s for i, s in enumerate(scores)}


def test_the_gap_counts_the_questions_on_which_the_two_parted_and_who_was_ahead():
    reference = values(1, 1, 1, 1, 1, 0, 0, 0.5)
    other = values(1, 1, 0, 0, 0, 0, 1, 0.5)
    questions = list(reference)
    category_of = {q: "a" if i < 4 else "b" for i, q in enumerate(questions)}
    gap = paired.difference(reference, other, questions, category_of, seed=42, key="t")
    assert (gap["better"], gap["worse"], gap["same"]) == (1, 3, 4)
    # Category a goes from 100 to 50, category b stays at 37.5: each category weighs the same, so the gap is -25.
    assert gap["delta"] == pytest.approx(-25.0, abs=0.01)
    assert gap["p"] == pytest.approx(paired.sign_test(1, 3)) and gap["identical"] is False


def test_the_same_results_always_give_the_same_interval():
    reference = values(*([1.0] * 20 + [0.0] * 5))
    other = values(*([1.0] * 12 + [0.0] * 13))
    questions = list(reference)
    category_of = {q: "a" for q in questions}
    first = paired.difference(reference, other, questions, category_of, seed=7, key="x")
    again = paired.difference(reference, other, questions, category_of, seed=7, key="x")
    assert first == again and first["ci"][1] < 0 and first["detectable"] > 0
    assert paired.difference(reference, reference, questions, category_of, seed=7, key="x")["identical"] is True


# -- which models are one model ------------------------------------------------------------------


def model_row(key, label, quant, *, position=0, params="30B", architecture="qwen3", size=10_000_000_000, **info):
    return {"model_key": key, "label": label, "cloud": False, "position": position,
            "info": {"quant": quant, "params": params, "architecture": architecture, "size_bytes": size, **info}}


def test_quantizations_of_one_model_are_found_and_put_in_order_of_precision():
    rows = [
        model_row("org/qwen3-30b@iq3_xxs", "Qwen3 30B A3B", "IQ3_XXS", position=0, size=11_000_000_000),
        model_row("org/qwen3-30b@q4_k_m", "Qwen3 30B A3B", "Q4_K_M", position=1, size=18_000_000_000),
        model_row("org/qwen3-30b@q3_k_m", "Qwen3 30B A3B", "Q3_K_M", position=2, size=14_000_000_000),
        model_row("other/gemma", "Gemma 4 12B", "Q4_K_M", position=3, params="12B", architecture="gemma"),
    ]
    (family,) = quants.find_families(quants.candidates(rows))
    assert family["members"] == ["org/qwen3-30b@q4_k_m", "org/qwen3-30b@q3_k_m", "org/qwen3-30b@iq3_xxs"]
    assert family["reference"] == "org/qwen3-30b@q4_k_m" and family["label"] == "Qwen3 30B A3B"


def test_a_quantization_written_in_the_name_is_not_part_of_the_family_and_other_sizes_are_not_the_same_model():
    rows = [
        model_row("a", "Qwen3 30B A3B Q4_K_M", "Q4_K_M", position=0),
        model_row("b", "Qwen3-30B-A3B-GGUF IQ3_XXS", "IQ3_XXS", position=1),
        model_row("c", "Qwen3 30B A3B", "Q8_0", position=2, params="8B"),  # another size
    ]
    (family,) = quants.find_families(quants.candidates(rows))
    assert family["members"] == ["a", "b"]


def test_two_files_in_the_same_quantization_are_one_member_and_a_cloud_model_is_never_a_member():
    rows = [
        model_row("a", "Qwen3 30B", "Q4_K_M", position=0),
        model_row("b", "Qwen3 30B", "Q4_K_M", position=1),
        {"model_key": "cloud:x", "label": "Qwen3 30B", "cloud": True, "position": 2, "info": {"provider": "x"}},
    ]
    assert quants.find_families(quants.candidates(rows)) == []


def test_the_order_of_precision_follows_the_bits_and_knows_what_it_does_not_know():
    names = ["IQ3_XXS", "Q3_K_S", "Q3_K_M", "IQ4_XS", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "F16"]
    bits = [quants.bits_per_weight(n) for n in names]
    assert bits == sorted(bits) and None not in bits
    assert quants.bits_per_weight(None) is None and quants.bits_per_weight("not-a-quant") is None
    assert quants.bits_per_weight("UD-Q3_K_XL") is not None  # not in the table: placed by its bit width


# -- a run, end to end ---------------------------------------------------------------------------

Q4, Q3, IQ3 = "tiny@q4_k_m", "tiny@q3_k_m", "tiny@iq3_xxs"


class Rig:
    def __init__(self, db, behaviour, *, probe=None) -> None:
        self.runtime = ArenaRuntime(behaviour)
        self.store = ArenaStore(db)
        models = [
            installed(Q3, name="Tiny 7B", quant="Q3_K_M", size_bytes=3_400_000_000),
            installed(IQ3, name="Tiny 7B", quant="IQ3_XXS", size_bytes=2_900_000_000),
            installed(Q4, name="Tiny 7B", quant="Q4_K_M", size_bytes=4_300_000_000),
        ]
        self.service = ArenaService(
            self.store, self.runtime, EventBus(), installed=lambda: models,
            gateway=lambda slots, emit: ModelManager(self.runtime, slots, emit, vram_probe=probe, sleep=lambda _s: None), tools=NO_TOOLS,
        )

    def run(self, **options) -> str:
        options.setdefault("preset", "quick")
        options.setdefault("categories", CATEGORIES)
        run_id = self.service.start(RunRequest(models=[Q3, IQ3, Q4], **options))
        self.service.join(run_id, 60)
        return run_id


def failing(ids_by_model):
    """A model that gets wrong the questions listed for it and right all the others."""
    return lambda question, _n: "wrong" if question.id in ids_by_model else "right"


@pytest.fixture
def questions():
    from agentfit.arena.suite import build_suite

    return [q.id for q in build_suite(42, "quick", CATEGORIES).questions]


def test_the_run_tells_which_models_are_one_model_and_the_tab_has_what_it_needs(db, questions):
    rig = Rig(db, {})
    run_id = rig.run()
    report = build_report(rig.store, run_id)
    (family,) = report["families"]
    assert family["members"] == [Q4, Q3, IQ3] and family["reference"] == Q4


def test_a_real_loss_is_called_real_and_a_one_question_wobble_is_called_noise(db, questions):
    rig = Rig(db, {IQ3: failing(set(questions[:9])), Q3: failing({questions[0]})})
    run_id = rig.run()
    result = quants.compare(rig.store, run_id)
    assert result["problem"] is None and result["reference"] == Q4 and result["members"] == [Q4, Q3, IQ3]
    comparison = result["comparison"]
    assert comparison["order"] == [Q4, Q3, IQ3] and comparison["questions"] == len(questions) and comparison["dropped"] == 0
    models = comparison["models"]
    assert models[Q4]["score"] == 100.0 and models[Q4]["is_reference"] is True and "verdict" not in models[Q4]
    assert models[IQ3]["verdict"] == "worse" and models[IQ3]["delta"] < -15 and models[IQ3]["better"] == 0 and models[IQ3]["worse"] >= 9
    assert models[IQ3]["delta_ci"][1] < 0 and models[IQ3]["p_adjusted"] < 0.05
    # The interval of the model's own score is not the interval of its difference.
    assert models[IQ3]["ci"][0] < models[IQ3]["score"] < models[IQ3]["ci"][1] + 1e-9 and models[IQ3]["ci"] != models[IQ3]["delta_ci"]
    assert models[Q3]["verdict"] == "noise" and models[Q3]["worse"] == 1 and models[Q3]["p"] == 1.0
    # The file's size and the speed come with it; the gap a run could not have seen is given for the noise.
    assert models[IQ3]["size_bytes"] == 2_900_000_000 and models[Q3]["detectable"] > 0
    assert sum(1 for c in comparison["categories"] for m in c["models"].values() if m["delta"] is not None) > 0


def test_a_question_one_of_them_could_not_answer_is_left_out_for_all_of_them(db, questions):
    rig = Rig(db, {})
    run_id = rig.run()
    rig.store.db.execute("UPDATE arena_result SET status = 'error', score = NULL WHERE run_id = ? AND model_key = ? AND question_id = ?", (run_id, Q3, questions[0]))
    comparison = quants.compare(rig.store, run_id)["comparison"]
    assert comparison["questions"] == len(questions) - 1 and comparison["dropped"] == 1


def test_models_can_be_chosen_by_hand_and_the_reference_can_be_changed(db, questions):
    rig = Rig(db, {IQ3: failing(set(questions[:9]))})
    run_id = rig.run()
    result = quants.compare(rig.store, run_id, members=[IQ3, Q3], reference=IQ3)
    assert result["members"] == [IQ3, Q3] and result["comparison"]["reference"] == IQ3
    models = result["comparison"]["models"]
    assert models[Q3]["delta"] > 15 and models[Q3]["verdict"] == "better"
    assert quants.compare(rig.store, run_id, members=[Q3])["problem"] == "needs_two"
    assert quants.compare(rig.store, "arena-nothing") is None


def test_repeats_are_averaged_before_the_questions_are_compared(db, questions):
    rig = Rig(db, {IQ3: lambda question, n: "wrong" if question.id in questions[:6] and n % 2 == 0 else "right"})
    run_id = rig.run(repeats=2, temperature=0.5)
    comparison = quants.compare(rig.store, run_id)["comparison"]
    assert comparison["settings"]["repeats"] == 2 and comparison["settings"]["temperature"] == 0.5
    # Half the repeats were right: the question is worth about one half, not a wrong answer.
    assert 0 < comparison["models"][IQ3]["worse"] <= 6 and comparison["models"][IQ3]["delta"] > -25


# -- the graphics memory a model took --------------------------------------------------------------


def test_the_graphics_memory_a_model_takes_is_kept_with_it_and_compares_across_the_run(db):
    holder: dict[str, ArenaRuntime] = {}
    sizes = {Q4: 5200, Q3: 4300, IQ3: 3800}

    def probe():
        runtime = holder["runtime"]
        return VramReading(total_mib=16000, used_mib=1200 + sum(sizes[k] for k in runtime.loaded))

    rig = Rig(db, {}, probe=probe)
    holder["runtime"] = rig.runtime
    run_id = rig.run()
    info = {m["model_key"]: m["info"] for m in rig.store.models(run_id)}
    assert {k: v["vram_mib"] for k, v in info.items()} == sizes
    assert all(v["vram_total_mib"] == 16000 and v["partial_offload"] is False and v["saturated"] is False for v in info.values())
    comparison = quants.compare(rig.store, run_id)["comparison"]
    assert [comparison["models"][k]["vram_mib"] for k in comparison["order"]] == [5200, 4300, 3800]
    assert comparison["speed_comparable"] is True


def test_without_a_memory_probe_nothing_is_invented(db):
    rig = Rig(db, {})
    run_id = rig.run()
    assert all("vram_mib" not in m["info"] for m in rig.store.models(run_id))
    assert quants.compare(rig.store, run_id)["comparison"]["models"][Q4]["vram_mib"] is None
