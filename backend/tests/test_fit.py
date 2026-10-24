from __future__ import annotations

import pytest

from agentfit.fit import (
    FitCategory,
    LoadOptions,
    ModelSpec,
    Placement,
    VariantSpec,
    analyze,
    choose_variant,
    default_variants,
    estimate_memory,
    kv_cache_gb,
)
from agentfit.fit import quant as q
from agentfit.hardware.models import Confidence
from conftest import budget_for

GIB = 1024**3

# -- quantization -------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("Qwen3.8-27B-UD-Q3_K_XL.gguf", "Q3_K_XL"),
        ("Huihui-Qwen3.8-27B-abliterated-GSQ-RCO-IQ3_S-mtp.gguf", "IQ3_S"),
        ("gpt-oss-20b-Q4_K_S.gguf", "Q4_K_S"),
        ("gemma-4-12b-it-qat-Q4_0.gguf", "Q4_0"),
        ("model.Q5_K_M.gguf", "Q5_K_M"),
        ("Llama-3.2-1B-Instruct-Q8_0.gguf", "Q8_0"),
        ("x-IQ4_XS.gguf", "IQ4_XS"),
        ("x-IQ4_NL.gguf", "IQ4_NL"),
        ("model-f16.gguf", "F16"),
        ("model-BF16.gguf", "BF16"),
        ("folder/sub/model-q6_k.gguf", "Q6_K"),
        ("no-quant-here.gguf", None),
    ],
)
def test_quant_parsed_from_filename(filename, expected):
    assert q.parse_quant_from_filename(filename) == expected


def test_model_size_token_is_not_mistaken_for_a_quant():
    # "8B" and "Q8" look alike to a careless parser.
    assert q.parse_quant_from_filename("Llama-3.1-8B-Instruct-Q4_K_M.gguf") == "Q4_K_M"


def test_bytes_per_param_grows_with_bits():
    ladder = [q.bytes_per_param(x) for x in ("Q2_K", "Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "F16")]
    assert ladder == sorted(ladder)
    assert q.bytes_per_param("Q4_K_M") == pytest.approx(0.58)
    assert q.bytes_per_param("UD-Q4_K_M") == q.bytes_per_param("Q4_K_M")


def test_suffix_and_imatrix_adjust_size():
    assert q.bytes_per_param("Q4_K_S") < q.bytes_per_param("Q4_K_M") < q.bytes_per_param("Q4_K_L")
    assert q.bytes_per_param("IQ4_XS") < q.bytes_per_param("Q4_K_S")


def test_unknown_quant_is_sized_as_four_bit():
    assert q.bytes_per_param("WEIRD") == pytest.approx(0.58)
    assert not q.is_recognized("WEIRD") and q.is_recognized("Q4_K_M")


def test_quality_penalty_ordering():
    assert q.quality_penalty("Q8_0") == 0
    assert q.quality_penalty("Q8_0") > q.quality_penalty("Q4_K_M") > q.quality_penalty("Q3_K_M") > q.quality_penalty("Q2_K")


def test_model_size_estimation_from_params():
    assert q.estimate_file_gb(14.0, "Q4_K_M") == pytest.approx(8.12)
    assert q.estimate_file_gb(27.0, "Q8_0") == pytest.approx(28.35)


# -- memory -------------------------------------------------------------------


def test_real_file_size_wins_over_estimate():
    model = ModelSpec(name="m", params_b=14.0)
    real = estimate_memory(model, VariantSpec(quant="Q4_K_M", file_size_bytes=9 * GIB))
    estimated = estimate_memory(model, VariantSpec(quant="Q4_K_M"))
    assert real.weights_gb == 9.0 and real.weights_source == "file"
    assert estimated.weights_gb == pytest.approx(8.12) and estimated.weights_source == "estimate"


def test_kv_cache_exact_formula_with_architecture():
    # 2 x layers x kv_heads x head_dim x ctx x 2 bytes
    model = ModelSpec(name="m", params_b=8.0, num_layers=32, num_kv_heads=8, head_dim=128)
    gb, source = kv_cache_gb(model, 8192)
    assert source == "architecture"
    assert gb == pytest.approx(2 * 32 * 8 * 128 * 8192 * 2 / GIB)


def test_kv_cache_heuristic_without_architecture():
    gb, source = kv_cache_gb(ModelSpec(name="m", params_b=27.0), 8192)
    assert source == "heuristic" and gb == pytest.approx(0.000008 * 27 * 8192)


def test_kv_cache_scales_with_context_parallelism_and_precision():
    model = ModelSpec(name="m", params_b=14.0, num_layers=40, num_kv_heads=8, head_dim=128)
    base = estimate_memory(model, VariantSpec(quant="Q4_K_M"), LoadOptions(context_length=8192))
    double_ctx = estimate_memory(model, VariantSpec(quant="Q4_K_M"), LoadOptions(context_length=16384))
    two_slots = estimate_memory(model, VariantSpec(quant="Q4_K_M"), LoadOptions(context_length=8192, parallel=2))
    q8_kv = estimate_memory(model, VariantSpec(quant="Q4_K_M"), LoadOptions(context_length=8192, kv_bytes_per_element=1.0))
    assert double_ctx.kv_cache_gb == pytest.approx(base.kv_cache_gb * 2, abs=0.02)
    assert two_slots.kv_cache_gb == pytest.approx(base.kv_cache_gb * 2, abs=0.02)
    assert q8_kv.kv_cache_gb == pytest.approx(base.kv_cache_gb / 2, abs=0.02)


def test_memory_total_includes_every_component():
    model = ModelSpec(name="m", params_b=8.0, vision=True)
    estimate = estimate_memory(
        model, VariantSpec(quant="Q4_K_M", file_size_bytes=5 * GIB, mmproj_bytes=GIB), LoadOptions(context_length=4096, draft_model_gb=0.7)
    )
    parts = estimate.weights_gb + estimate.kv_cache_gb + estimate.runtime_overhead_gb + estimate.vision_projector_gb + estimate.draft_model_gb
    assert estimate.total_gb == pytest.approx(parts, abs=0.02)
    assert estimate.vision_projector_gb == 1.0 and estimate.vision_source == "file"


def test_vision_projector_is_estimated_when_file_unknown():
    estimate = estimate_memory(ModelSpec(name="m", params_b=8.0, vision=True), VariantSpec(quant="Q4_K_M"))
    assert estimate.vision_projector_gb > 0 and estimate.vision_source == "estimate"


def test_memory_confidence_levels():
    full = ModelSpec(name="m", params_b=8.0, num_layers=32, num_kv_heads=8, head_dim=128)
    assert estimate_memory(full, VariantSpec(quant="Q4_K_M", file_size_bytes=5 * GIB)).confidence == Confidence.HIGH
    assert estimate_memory(ModelSpec(name="m", params_b=8.0), VariantSpec(quant="Q4_K_M")).confidence == Confidence.MEDIUM
    assert estimate_memory(ModelSpec(name="m"), VariantSpec(quant="Q4_K_M")).confidence == Confidence.LOW


# -- fit categories -----------------------------------------------------------


def test_small_model_is_perfect_on_16gb(budget_4080s):
    fit = analyze(ModelSpec(name="14B", params_b=14.0), VariantSpec(quant="Q4_K_M"), budget_4080s)
    assert fit.category == FitCategory.PERFECT and fit.placement == Placement.GPU
    assert fit.gpu_offload_ratio == 1.0 and fit.ram_required_gb == 0
    assert fit.vram_headroom_gb > 1.0
    assert fit.speed.confidence == Confidence.MEDIUM and not fit.speed.measured


def test_fit_never_plans_with_full_physical_vram(budget_4080s):
    # 15.5 GB would "fit" in 16 GB of physical VRAM, but not in the 14.2 GB budget.
    fit = analyze(ModelSpec(name="m", params_b=20.0), VariantSpec(quant="Q5_K_M", file_size_bytes=int(14.5 * GIB)), budget_4080s)
    assert fit.placement == Placement.PARTIAL_OFFLOAD
    assert fit.vram_required_gb <= budget_4080s.vram_usable_gb + 0.01


def test_nearly_full_budget_is_memory_tight(budget_4080s):
    usable = budget_4080s.vram_usable_gb
    weights = usable - 0.5 - 0.000008 * 10 * 8192 - 0.1  # leave ~100 MB
    fit = analyze(ModelSpec(name="m", params_b=10.0), VariantSpec(quant="Q8_0", file_size_bytes=int(weights * GIB)), budget_4080s)
    assert fit.placement == Placement.GPU and fit.category == FitCategory.MEMORY_TIGHT


def test_partial_offload_is_capped_at_good_and_slower(budget_4080s):
    model = ModelSpec(name="27B", params_b=27.0)
    full = analyze(model, VariantSpec(quant="Q2_K"), budget_4080s)
    partial = analyze(model, VariantSpec(quant="Q4_K_M"), budget_4080s)
    assert partial.placement == Placement.PARTIAL_OFFLOAD
    assert partial.category in (FitCategory.GOOD, FitCategory.ACCEPTABLE, FitCategory.SLOW)
    assert 0 < partial.gpu_offload_ratio < 1 and partial.ram_required_gb > 0
    assert partial.speed.tokens_per_second < full.speed.tokens_per_second


def test_70b_is_limited_not_impossible_on_16gb(budget_4080s):
    choice = choose_variant(ModelSpec(name="70B", params_b=70.0), [], budget_4080s)
    assert choice is not None and choice.fit.runnable
    assert choice.fit.category in (FitCategory.SLOW, FitCategory.ACCEPTABLE, FitCategory.MEMORY_TIGHT)


def test_far_too_large_model_is_impossible(budget_4080s):
    choice = choose_variant(ModelSpec(name="400B", params_b=400.0), [], budget_4080s)
    assert choice is not None and choice.fit.category == FitCategory.IMPOSSIBLE
    assert not choice.fit.runnable and choice.fit.speed.tokens_per_second == 0


def test_cpu_only_machine_runs_small_models_slowly():
    budget = budget_for("cpu_only_16gb")
    fit = analyze(ModelSpec(name="4B", params_b=4.0), VariantSpec(quant="Q4_K_M"), budget)
    assert fit.placement == Placement.CPU and fit.vram_required_gb == 0
    assert fit.category in (FitCategory.ACCEPTABLE, FitCategory.SLOW)
    assert fit.speed.confidence == Confidence.LOW


def test_dual_gpu_split_placement():
    budget = budget_for("dual_nvidia_4090_3090_64gb")
    # 36 GB of weights + KV cache + overhead: too big for either card, fits across both.
    fit = analyze(ModelSpec(name="70B", params_b=70.0), VariantSpec(quant="Q3_K_M", file_size_bytes=int(36 * GIB)), budget)
    assert fit.memory.total_gb > budget.single_gpu_usable_gb
    assert fit.placement == Placement.GPU_SPLIT
    assert any("split" in r.text.lower() for r in fit.reasons)


def test_context_is_clamped_to_model_maximum(budget_4080s):
    fit = analyze(ModelSpec(name="m", params_b=7.0, max_context=4096), VariantSpec(quant="Q4_K_M"), budget_4080s, LoadOptions(context_length=32768))
    assert fit.context_length == 4096


def test_measured_speed_replaces_estimate(budget_4080s):
    fit = analyze(ModelSpec(name="m", params_b=14.0), VariantSpec(quant="Q4_K_M"), budget_4080s, measured_tps=47.8)
    assert fit.speed.measured and fit.speed.tokens_per_second == 47.8
    assert fit.speed.confidence == Confidence.HIGH and fit.speed.method == "local_benchmark"


def test_speed_estimate_close_to_real_benchmark(budget_4080s):
    # Measured on an RTX 4080 SUPER: Spark-X2.5-4B Q8_0 at 108.7 tok/s.
    fit = analyze(ModelSpec(name="4B", params_b=4.1), VariantSpec(quant="Q8_0", file_size_bytes=int(4.07 * GIB)), budget_4080s)
    assert fit.speed.tokens_per_second == pytest.approx(108.7, rel=0.2)


def test_moe_speed_uses_active_parameters(budget_4080s):
    dense = analyze(ModelSpec(name="d", params_b=30.0), VariantSpec(quant="Q2_K"), budget_4080s)
    moe = analyze(ModelSpec(name="m", params_b=30.0, active_params_b=3.0, is_moe=True, num_experts=128), VariantSpec(quant="Q2_K"), budget_4080s)
    assert moe.speed.tokens_per_second > dense.speed.tokens_per_second
    assert moe.speed.method.endswith("moe")
    assert moe.vram_required_gb == dense.vram_required_gb  # memory still counts every expert


# -- quantization choice ------------------------------------------------------


def test_chooses_best_quant_that_fits_comfortably(budget_4080s):
    model = ModelSpec(name="14B", params_b=14.0)
    choice = choose_variant(model, default_variants(model), budget_4080s, LoadOptions(context_length=8192))
    assert choice is not None
    assert choice.fit.category == FitCategory.PERFECT
    assert choice.fit.quant in ("Q5_K_M", "Q6_K")
    assert any("Q8_0" in line for line in choice.why())


def test_larger_context_pushes_toward_smaller_quant(budget_4080s):
    model = ModelSpec(name="14B", params_b=14.0)
    small_ctx = choose_variant(model, default_variants(model), budget_4080s, LoadOptions(context_length=8192))
    large_ctx = choose_variant(model, default_variants(model), budget_4080s, LoadOptions(context_length=32768))
    assert small_ctx and large_ctx
    assert q.quality_rank(large_ctx.fit.quant) < q.quality_rank(small_ctx.fit.quant)
    assert large_ctx.fit.placement == Placement.GPU


def test_two_bit_quants_are_a_last_resort(budget_4080s):
    model = ModelSpec(name="27B", params_b=27.0)
    choice = choose_variant(model, default_variants(model), budget_4080s)
    assert choice is not None and q.quant_bits(choice.fit.quant) >= 3


def test_context_shrinks_until_something_runs():
    budget = budget_for("cpu_only_16gb")
    model = ModelSpec(name="13B", params_b=13.0)
    choice = choose_variant(model, [VariantSpec(quant="Q8_0")], budget, LoadOptions(context_length=65536))
    assert choice is not None
    assert choice.fit.context_length < 65536
    if choice.fit.runnable:
        assert any("Context reduced" in r.text for r in choice.fit.reasons)


def test_every_result_explains_itself(budget_4080s):
    for quant in ("Q2_K", "Q4_K_M", "Q8_0"):
        fit = analyze(ModelSpec(name="27B", params_b=27.0), VariantSpec(quant=quant), budget_4080s)
        assert fit.reasons and all(r.kind in "+-i" and r.text for r in fit.reasons)
