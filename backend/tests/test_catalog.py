from __future__ import annotations

from typing import Any

import pytest

from agentfit.catalog import (
    Alignment,
    CatalogService,
    ModelEntry,
    ModelFile,
    SearchFilters,
    TaskProfile,
    classify,
)
from agentfit.catalog.huggingface import (
    HubUnavailable,
    drop_auxiliary_files,
    entry_from_hub,
    files_from_siblings,
)
from agentfit.catalog.query import parse_query
from agentfit.catalog.scoring import PREFERENCE_BONUS, score_model
from agentfit.catalog.seed import entry_from_seed, load_seed
from agentfit.catalog.service import explain_versus
from agentfit.config import AlignmentPreference, ScoringWeights, Settings
from agentfit.fit import ModelSpec, VariantSpec, analyze
from agentfit.hardware.models import Confidence
from agentfit.lmstudio.models import InstalledModel

GIB = 1024**3


def make_entry(repo: str, params_b: float, *, files: dict[str, float] | None = None, downloads: int = 50_000, **kwargs: Any) -> ModelEntry:
    alignment, evidence = classify.detect_alignment(repo)
    file_list = [
        ModelFile(repo=repo, filename=f"{repo.split('/')[-1]}-{quant}.gguf", quant=quant, size_bytes=int(gb * GIB), sha256="ab" * 32, revision="rev1")
        for quant, gb in (files or {}).items()
    ]
    capabilities = kwargs.pop("capabilities", ["tool_use"])
    kwargs.setdefault("nominal_params_b", classify.parse_params_from_name(repo)[0])
    return ModelEntry(
        repo=repo, name=repo.split("/")[-1].removesuffix("-GGUF"), publisher=repo.split("/")[0], params_b=params_b,
        context_length=131072, license="apache-2.0", capabilities=capabilities,
        categories=classify.detect_categories(repo, None, None, capabilities, params_b),
        alignment=alignment, alignment_evidence=evidence, downloads=downloads, files=file_list,
        files_complete=bool(file_list), source="huggingface", metadata_confidence=Confidence.HIGH,
        quality=classify.estimate_task_quality(repo, params_b), **kwargs,
    )


class FakeHub:
    def __init__(self, entries: list[ModelEntry] | None = None, *, offline: bool = False) -> None:
        self.entries = {e.repo.lower(): e for e in entries or []}
        self.offline = offline
        self.searches: list[str] = []

    def search(self, text: str, limit: int = 30, sort: str = "downloads") -> list[ModelEntry]:
        self.searches.append(text)
        if self.offline:
            raise HubUnavailable("no network")
        terms = text.lower().split()
        return [e for e in self.entries.values() if all(t in e.repo.lower() for t in terms)][:limit]

    def details(self, repo: str) -> ModelEntry | None:
        if self.offline:
            raise HubUnavailable("no network")
        return self.entries.get(repo.lower())


STD_14B = make_entry("unsloth/Qwen3-14B-GGUF", 14.0, files={"Q4_K_M": 8.4, "Q5_K_M": 9.8, "Q8_0": 14.6})
UNC_14B = make_entry("mradermacher/Huihui-Qwen3-14B-abliterated-GGUF", 14.0, files={"Q4_K_M": 8.4, "Q5_K_M": 9.8, "Q8_0": 14.6})
UNC_27B = make_entry("huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF", 27.0, files={"IQ3_XXS": 10.1, "Q3_K_M": 13.2, "Q4_K_M": 16.5})
CODER = make_entry("bartowski/Qwen2.5-Coder-14B-Instruct-GGUF", 14.0, files={"Q4_K_M": 8.4, "Q6_K": 11.3})
TINY = make_entry("unsloth/Llama-3.2-1B-Instruct-GGUF", 1.2, files={"Q8_0": 1.3}, capabilities=[])


@pytest.fixture
def catalog(db, monkeypatch) -> CatalogService:
    # Isolate from the bundled seed so each test controls exactly what exists.
    monkeypatch.setattr("agentfit.catalog.service.load_seed", lambda: ())
    return CatalogService(db, Settings(), hub=FakeHub([STD_14B, UNC_14B, UNC_27B, CODER, TINY]))  # type: ignore[arg-type]


# -- alignment detection ------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF",
        "HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF",
        "DavidAU/Qwen3-0.6B-heretic-abliterated-uncensored",
        "someone/Model-Derestricted-GGUF",
        "someone/Model-de-restricted",
        "someone/Llama-Unfiltered-8B",
    ],
)
def test_uncensored_models_are_recognised(name):
    alignment, evidence = classify.detect_alignment(name)
    assert alignment == Alignment.UNCENSORED and evidence


@pytest.mark.parametrize("name", ["unsloth/Qwen3-14B-GGUF", "google/gemma-4-12b-it", "bartowski/Qwen2.5-Coder-14B-GGUF"])
def test_standard_models_are_not_flagged(name):
    alignment, evidence = classify.detect_alignment(name)
    assert alignment == Alignment.STANDARD and evidence == []


def test_alignment_can_come_from_tags_or_base_model():
    assert classify.detect_alignment("x/y", ["gguf", "abliterated"])[0] == Alignment.UNCENSORED
    entry = entry_from_hub({"id": "quantizer/Nice-Name-GGUF", "tags": ["gguf"], "cardData": {"base_model": "huihui-ai/Qwen3-8B-abliterated"}, "siblings": []})
    assert entry.alignment == Alignment.UNCENSORED
    assert any("base model" in e for e in entry.alignment_evidence)


# -- uncensored filters -------------------------------------------------------


def test_uncensored_filter_returns_only_uncensored(catalog, budget_4080s):
    result = catalog.search("", budget_4080s, SearchFilters(alignment=Alignment.UNCENSORED))
    assert result.cards and all(c.entry.alignment == Alignment.UNCENSORED for c in result.cards)
    assert {c.entry.repo for c in result.cards} == {UNC_14B.repo, UNC_27B.repo}


def test_standard_filter_excludes_uncensored(catalog, budget_4080s):
    catalog.search("qwen", budget_4080s)  # populate the cache from the hub
    result = catalog.search("qwen", budget_4080s, SearchFilters(alignment=Alignment.STANDARD))
    assert result.cards and all(c.entry.alignment == Alignment.STANDARD for c in result.cards)


def test_any_filter_shows_both(catalog, budget_4080s):
    result = catalog.search("qwen", budget_4080s)
    alignments = {c.entry.alignment for c in result.cards}
    assert alignments == {Alignment.STANDARD, Alignment.UNCENSORED}


def test_uncensored_models_are_never_hidden_by_name(catalog, budget_4080s):
    """A model is not dropped merely because its name says uncensored or abliterated."""
    result = catalog.search("abliterated", budget_4080s)
    assert UNC_14B.repo in {c.entry.repo for c in result.cards}


def test_uncensored_search_tries_each_community_term(catalog, budget_4080s):
    catalog.search("coding uncensored", budget_4080s)
    assert {"abliterated", "uncensored", "heretic"} <= {s.split()[-1] for s in catalog.hub.searches}


def test_search_filters_by_the_size_a_model_name_states(catalog, budget_4080s):
    """27B finds a model whose metadata says 27.8, because its name says 27B."""
    qwen = make_entry("x/Qwen3.8-27B-GGUF", 27.8, files={"Q4_K_M": 16.5}, nominal_params_b=27.0)
    catalog.hub.entries[qwen.repo.lower()] = qwen
    result = catalog.search("qwen", budget_4080s, SearchFilters(params_exact_b=27))
    assert {c.entry.repo for c in result.cards} == {UNC_27B.repo, qwen.repo}
    assert result.total_candidates == 2


def test_search_can_filter_a_parameter_range(catalog, budget_4080s):
    result = catalog.search("qwen", budget_4080s, SearchFilters(params_min_b=8, params_max_b=20))
    assert {c.entry.repo for c in result.cards} == {STD_14B.repo, UNC_14B.repo, CODER.repo}


def test_size_facets_list_the_sizes_with_several_models(catalog, budget_4080s):
    result = catalog.search("qwen", budget_4080s)
    facets = {facet.params_b: facet.models for facet in result.sizes}
    assert facets[14.0] == 3
    assert 27.0 not in facets  # one model: not a useful menu entry


def test_selected_uncensored_model_shows_full_trust_information(catalog, budget_4080s):
    card = next(c for c in catalog.search("27B abliterated", budget_4080s).cards if c.entry.repo == UNC_27B.repo)
    entry = card.entry
    assert entry.repo == UNC_27B.repo and entry.publisher == "huihui-ai"
    assert entry.license == "apache-2.0" and entry.params_b == 27.0
    assert card.file is not None and card.file.sha256 and card.file.revision == "rev1"
    assert card.download_size_bytes == card.file.size_bytes and not card.size_is_estimate
    assert card.fit.runnable and card.fit.speed.tokens_per_second > 0


# -- no ranking penalty -------------------------------------------------------


def test_uncensored_models_are_not_penalized(budget_4080s):
    """Same base model, same files: the uncensored build must score exactly like the standard one."""
    weights = ScoringWeights()
    profile = TaskProfile(id="coding", coding=3, reasoning=1, writing=0.5, categories=["Coding"])
    twin = UNC_14B.model_copy(update={"quality": STD_14B.quality, "categories": STD_14B.categories})
    variant = VariantSpec(quant="Q4_K_M", file_size_bytes=int(8.4 * GIB))
    fit_std = analyze(STD_14B.to_spec(), variant, budget_4080s)
    fit_unc = analyze(twin.to_spec(), variant, budget_4080s)
    std = score_model(STD_14B, fit_std, profile, weights)
    unc = score_model(twin, fit_unc, profile, weights)
    assert unc.total == std.total and unc.preference_bonus == 0 == std.preference_bonus


def test_preference_adds_a_visible_bonus_and_never_a_penalty(budget_4080s):
    weights = ScoringWeights()
    profile = TaskProfile()
    variant = VariantSpec(quant="Q4_K_M", file_size_bytes=int(8.4 * GIB))
    fit = analyze(STD_14B.to_spec(), variant, budget_4080s)
    neutral_std = score_model(STD_14B, fit, profile, weights)
    neutral_unc = score_model(UNC_14B, fit, profile, weights)
    pref_unc = score_model(UNC_14B, fit, profile, weights, preference=AlignmentPreference.UNCENSORED)
    std_under_unc_pref = score_model(STD_14B, fit, profile, weights, preference=AlignmentPreference.UNCENSORED)
    pref_std = score_model(STD_14B, fit, profile, weights, preference=AlignmentPreference.STANDARD)
    assert pref_unc.preference_bonus == PREFERENCE_BONUS and pref_unc.total > neutral_unc.total
    assert std_under_unc_pref.total == neutral_std.total  # the other side loses nothing
    assert pref_std.preference_bonus == PREFERENCE_BONUS


def test_preference_does_not_rescue_a_weak_model(budget_4080s):
    weak = make_entry("x/Tiny-Uncensored-GGUF", 0.5, files={"Q8_0": 0.6})
    fit = analyze(weak.to_spec(), VariantSpec(quant="Q8_0", file_size_bytes=int(0.6 * GIB)), budget_4080s)
    score = score_model(weak, fit, TaskProfile(), ScoringWeights(), preference=AlignmentPreference.UNCENSORED)
    assert score.preference_bonus == 0  # quality superiority is never fabricated


# -- recommendation ranking ---------------------------------------------------


def test_recommend_ranks_by_score_and_only_runnable(catalog, budget_4080s):
    catalog.search("qwen", budget_4080s)
    catalog.search("llama", budget_4080s)
    cards = catalog.recommend(TaskProfile(id="coding", coding=3, reasoning=1, writing=0.5, categories=["Coding"], needs_tools=True), budget_4080s, enrich=False)
    assert cards and all(c.fit.runnable for c in cards)
    totals = [c.score.total for c in cards]
    assert totals == sorted(totals, reverse=True)
    assert TINY.repo not in {c.entry.repo for c in cards}  # no tool use
    assert cards[0].why and cards[0].score.confidence["local_benchmark"] == "NOT MEASURED"


def test_recommend_with_alignment_filter(catalog, budget_4080s):
    catalog.search("qwen", budget_4080s)
    cards = catalog.recommend(TaskProfile(), budget_4080s, alignment=Alignment.UNCENSORED, enrich=False)
    assert cards and all(c.entry.alignment == Alignment.UNCENSORED for c in cards)


def test_recommend_prefers_14b_over_27b_on_16gb_and_explains_why(catalog, budget_4080s):
    catalog.search("qwen", budget_4080s)
    profile = TaskProfile(id="coding", coding=3, reasoning=1, writing=0.5, categories=["Coding"], min_context=16384)
    cards = catalog.recommend(profile, budget_4080s, enrich=False)
    by_repo = {c.entry.repo: c for c in cards}
    small, big = by_repo[STD_14B.repo], by_repo[UNC_27B.repo]
    assert small.score.total > big.score.total
    lines = explain_versus(small, big)
    assert lines and any("tok/s" in line or "VRAM" in line or "quantization" in line for line in lines)


def test_measured_benchmark_feeds_the_score(catalog, budget_4080s, db):
    installed = InstalledModel(model_key="qwen3-14b", display_name="Qwen3 14B", repo=STD_14B.repo, filename="q.gguf", quant="Q4_K_M", size_bytes=int(8.4 * GIB), tool_use=True)
    db.execute(
        "INSERT INTO benchmark(model_key, generation_tps, details, created_at) VALUES (?, ?, '{}', '2026-01-01T00:00:00+00:00')",
        ("qwen3-14b", 61.0),
    )
    card = catalog.evaluate(STD_14B, budget_4080s, installed=installed, measured=catalog.measured_tps())
    assert card.measured_tps == 61.0 and card.fit.speed.measured
    assert card.score.local_benchmark == 100.0 and card.score.confidence["local_benchmark"] == "HIGH"


def test_scoring_weights_are_configurable(budget_4080s):
    variant = VariantSpec(quant="Q4_K_M", file_size_bytes=int(8.4 * GIB))
    fit = analyze(STD_14B.to_spec(), variant, budget_4080s)
    default = score_model(STD_14B, fit, TaskProfile(), ScoringWeights())
    speed_only = score_model(STD_14B, fit, TaskProfile(), ScoringWeights(hardware_fit=0, task_quality=0, specialization=0, speed=1, context=0, tool_use=0, reliability=0, local_benchmark=0))
    assert speed_only.total == speed_only.speed != default.total
    assert sum(ScoringWeights().normalized().values()) == pytest.approx(1.0)


# -- offline behaviour --------------------------------------------------------


def test_search_works_offline_from_cache(db, budget_4080s, monkeypatch):
    monkeypatch.setattr("agentfit.catalog.service.load_seed", lambda: ())
    online = CatalogService(db, Settings(), hub=FakeHub([STD_14B, UNC_14B]))  # type: ignore[arg-type]
    online.search("qwen3", budget_4080s)
    offline = CatalogService(db, Settings(), hub=FakeHub(offline=True))  # type: ignore[arg-type]
    result = offline.search("huihui", budget_4080s)
    assert not result.online
    assert UNC_14B.repo in {c.entry.repo for c in result.cards}


def test_offline_search_reports_that_the_hub_is_unreachable(db, budget_4080s, monkeypatch):
    monkeypatch.setattr("agentfit.catalog.service.load_seed", lambda: ())
    result = CatalogService(db, Settings(), hub=FakeHub(offline=True)).search("something", budget_4080s)  # type: ignore[arg-type]
    assert not result.online and any("unreachable" in n for n in result.notes)


def test_installed_models_always_listed(catalog, budget_4080s):
    installed = [InstalledModel(model_key="local-model", display_name="Local Model 9B", params_string="9B", quant="Q4_K_M", size_bytes=int(5.5 * GIB))]
    result = catalog.search("", budget_4080s, SearchFilters(installed_only=True), installed)
    assert [c.installed_key for c in result.cards] == ["local-model"]
    assert result.cards[0].entry.source == "lmstudio" and "license" in result.cards[0].entry.unknown_fields


# -- query parsing ------------------------------------------------------------


def test_query_qwen_uncensored_27b():
    parsed = parse_query("Qwen uncensored 27B")
    assert parsed.terms == ["qwen"] and parsed.alignment == Alignment.UNCENSORED and parsed.params_b == 27.0


def test_query_coding_abliterated():
    parsed = parse_query("coding abliterated")
    assert parsed.categories == ["Coding"] and parsed.alignment == Alignment.UNCENSORED and parsed.terms == []


def test_query_best_for_vram():
    parsed = parse_query("best uncensored model for 16GB VRAM")
    assert parsed.vram_gb == 16.0 and parsed.alignment == Alignment.UNCENSORED and parsed.wants_best and parsed.terms == []


def test_query_vram_uses_a_hypothetical_budget(catalog, budget_4080s):
    result = catalog.search("best uncensored model for 8GB VRAM", budget_4080s)
    assert any("hypothetical" in n for n in result.notes)
    assert all(c.fit.vram_usable_gb < 8 for c in result.cards)


# -- metadata honesty ---------------------------------------------------------


def test_missing_metadata_is_reported_not_invented():
    entry = entry_from_hub({"id": "someone/Mystery-Model-GGUF", "tags": ["gguf"], "siblings": [{"rfilename": "mystery.gguf"}]})
    assert entry.license is None and entry.params_b is None and entry.context_length is None
    assert {"license", "parameters", "architecture", "context_length"} <= set(entry.unknown_fields)
    assert entry.metadata_confidence == Confidence.LOW
    assert entry.quality.confidence == Confidence.UNKNOWN and entry.quality.coding == 0


def test_hub_entry_reads_real_files_sizes_and_hashes():
    info = {
        "id": "pub/Model-8B-GGUF", "author": "pub", "sha": "abc123", "tags": ["gguf", "license:mit"], "downloads": 10, "likes": 1,
        "gguf": {"total": 8_030_000_000, "architecture": "llama", "context_length": 8192, "chat_template": "{{ tools }}"},
        "siblings": [
            {"rfilename": "Model-8B-Q4_K_M.gguf", "size": 4_900_000_000, "lfs": {"sha256": "aa" * 32}},
            {"rfilename": "Model-8B-Q8_0.gguf", "size": 8_500_000_000, "lfs": {"sha256": "bb" * 32}},
            {"rfilename": "mmproj-Model-8B-f16.gguf", "size": 600_000_000},
            {"rfilename": "README.md", "size": 100},
        ],
    }
    entry = entry_from_hub(info)
    assert [f.quant for f in entry.files] == ["Q4_K_M", "Q8_0"]
    assert entry.files[0].sha256 == "aa" * 32 and entry.files[0].revision == "abc123"
    assert entry.files[0].url == "https://huggingface.co/pub/Model-8B-GGUF/resolve/abc123/Model-8B-Q4_K_M.gguf"
    assert len(entry.mmproj_files) == 1 and "vision" in entry.capabilities and "tool_use" in entry.capabilities
    assert entry.license == "mit" and entry.files_complete and entry.metadata_confidence == Confidence.HIGH


def test_auxiliary_gguf_files_are_not_offered_as_the_model():
    files = [
        ModelFile(repo="r", filename="Model-27B-mtp-Q4_0.gguf", quant="Q4_0", size_bytes=int(1.6 * GIB)),
        ModelFile(repo="r", filename="Model-27B-Q4_K_M.gguf", quant="Q4_K_M", size_bytes=int(16.5 * GIB)),
        ModelFile(repo="r", filename="Model-27B-draft-Q8_0.gguf", quant="Q8_0", size_bytes=int(28 * GIB)),
    ]
    assert [f.filename for f in drop_auxiliary_files(files, 27.0)] == ["Model-27B-Q4_K_M.gguf"]


def test_sharded_gguf_counts_as_one_file_with_summed_size():
    siblings = [
        {"rfilename": "big-Q4_K_M-00001-of-00002.gguf", "size": 30},
        {"rfilename": "big-Q4_K_M-00002-of-00002.gguf", "size": 12},
    ]
    models, _ = files_from_siblings("r", siblings, None)
    assert len(models) == 1 and models[0].size_bytes == 42


def test_name_size_wins_when_metadata_disagrees():
    entry = entry_from_hub({"id": "p/Model-27B-GGUF", "tags": ["gguf"], "gguf": {"total": 1_500_000_000}, "siblings": []})
    assert entry.params_b == 27.0 and entry.nominal_params_b == 27.0
    assert any("disagree" in f for f in entry.unknown_fields)
    seeded = entry_from_seed({"name": "openai/gpt-oss-20b", "gguf_repos": ["unsloth/gpt-oss-20b-GGUF"], "parameters": 1_774_000_000, "is_moe": True, "active_parameters": 299_000_000})
    assert seeded.params_b == 20.0 and seeded.nominal_params_b == 20.0
    assert seeded.active_params_b == pytest.approx(3.37, abs=0.05)


@pytest.mark.parametrize(
    "name, total, active",
    [("Qwen3.6-35B-A3B", 35.0, 3.0), ("Mixtral-8x7B-Instruct", 56.0, None), ("gemma-3-270m", 0.27, None), ("Qwen2.5-Coder-14B", 14.0, None), ("no-size", None, None)],
)
def test_params_parsed_from_names(name, total, active):
    assert classify.parse_params_from_name(name) == (total, active)


def test_categories_allow_multiple_and_avoid_false_positives():
    coder = classify.detect_categories("Qwen/Qwen2.5-Coder-14B", None, None, ["tool_use"], 14.0)
    assert {"Coding", "General"} <= set(coder)
    assert "Medical / Biomedical Research" in classify.detect_categories("google/medgemma-4b-it", None, None, [], 4.0)
    assert "Medical / Biomedical Research" not in classify.detect_categories("someone/Intermediate-7B", None, None, [], 7.0)
    assert classify.detect_categories("nomic-ai/nomic-embed-text-v1.5", None, "sentence-similarity", [], 0.1) == ["Embedding"]
    assert "Vision" in classify.detect_categories("x/y", None, "image-text-to-text", [], 8.0)


def test_bundled_seed_catalog_is_real_and_usable():
    seed = load_seed()
    assert len(seed) > 1000
    assert all("/" in e.repo for e in seed[:200])
    assert any(e.alignment == Alignment.UNCENSORED for e in seed)
    spec = seed[0].to_spec()
    assert isinstance(spec, ModelSpec)
