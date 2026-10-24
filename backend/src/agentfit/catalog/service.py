"""Catalog service: discovery, search, fit evaluation and recommendations.

Sources, merged by repository id:
  * the offline seed catalog (always available),
  * Hugging Face live search and file metadata (cached in SQLite),
  * models installed in LM Studio.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel, Field

from agentfit.catalog import classify
from agentfit.catalog.huggingface import HubUnavailable, HuggingFaceClient
from agentfit.catalog.models import Alignment, ModelEntry, ModelFile
from agentfit.catalog.query import ParsedQuery, parse_query
from agentfit.catalog.scoring import ScoreBreakdown, TaskProfile, score_model
from agentfit.catalog.seed import load_seed
from agentfit.config import AlignmentPreference, Settings
from agentfit.db import Database, utcnow
from agentfit.fit import FitResult, LoadOptions, VariantSpec, analyze, choose_variant, default_variants
from agentfit.fit import quant as q
from agentfit.fit.accuracy import Accuracy, accuracy_for
from agentfit.fit.models import FitCategory, Placement
from agentfit.hardware.budget import GpuBudget, ResourceBudget
from agentfit.hardware.models import Confidence, GpuBackend
from agentfit.i18n import tr
from agentfit.lmstudio.models import InstalledModel
from agentfit.logging_setup import get_logger

log = get_logger("catalog")

ENRICH_TOP_N = 10
RECOMMEND_POOL = 600


class ModelCard(BaseModel):
    """A model evaluated against this PC: what the explorer and recommender show."""

    entry: ModelEntry
    quant: str
    file: ModelFile | None = None
    download_size_bytes: int | None = None
    size_is_estimate: bool = True
    fit: FitResult
    score: ScoreBreakdown
    # What the chosen quantization costs in quality (reference measurement, see fit.accuracy).
    accuracy: Accuracy | None = None
    stars: dict[str, int] = Field(default_factory=dict)
    why: list[str] = Field(default_factory=list)
    quant_reasoning: list[str] = Field(default_factory=list)
    installed: bool = False
    installed_key: str | None = None
    measured_tps: float | None = None


class SearchFilters(BaseModel):
    alignment: Alignment | None = None
    categories: list[str] = Field(default_factory=list)
    installed_only: bool = False
    recommended_only: bool = False
    tool_use: bool = False
    vision: bool = False
    max_params_b: float | None = None


class SearchResponse(BaseModel):
    cards: list[ModelCard]
    parsed: ParsedQuery
    online: bool
    notes: list[str] = Field(default_factory=list)
    hub_unreachable: bool = False
    total_candidates: int = 0


def hypothetical_budget(base: ResourceBudget, vram_gb: float, settings: Settings) -> ResourceBudget:
    """What-if budget for a query such as "... for 16GB VRAM"."""
    hw = settings.hardware
    safety = vram_gb * hw.vram_reserve_percent / 100
    usable = max(0.0, vram_gb - hw.vram_os_reserve_gb - safety)
    gpu = GpuBudget(
        index=0,
        name=f"Hypothetical {vram_gb:g} GB GPU",
        backend=GpuBackend.CUDA,
        total_gb=vram_gb,
        os_reserve_gb=hw.vram_os_reserve_gb,
        safety_reserve_gb=round(safety, 2),
        usable_gb=round(usable, 2),
        bandwidth_gbps=base.gpu_bandwidth_gbps,
    )
    return base.model_copy(
        update={
            "gpus": [gpu],
            "single_gpu_usable_gb": gpu.usable_gb,
            "split_usable_gb": gpu.usable_gb,
            "split_supported": False,
        }
    )


def entry_from_installed(model: InstalledModel) -> ModelEntry:
    """Catalog entry for an installed model that no other source knows about."""
    total, active = classify.parse_params_from_name(model.params_string or model.display_name)
    repo = model.repo or f"lmstudio/{model.model_key}"
    capabilities = [c for c, on in (("vision", model.vision), ("tool_use", model.tool_use), ("reasoning", model.reasoning)) if on]
    alignment, evidence = classify.detect_alignment(f"{model.model_key} {model.repo or ''}")
    unknown = ["license"] + ([] if model.repo else ["repository"])
    return ModelEntry(
        repo=repo,
        name=model.display_name,
        publisher=model.publisher or repo.split("/", 1)[0],
        family=classify.detect_family(model.model_key),
        architecture=model.architecture,
        params_b=total,
        active_params_b=active,
        is_moe=active is not None,
        context_length=model.max_context_length,
        capabilities=capabilities,
        categories=classify.detect_categories(model.model_key, None, None, capabilities, total),
        alignment=alignment,
        alignment_evidence=evidence,
        source="lmstudio",
        metadata_confidence=Confidence.MEDIUM,
        unknown_fields=unknown,
        quality=classify.estimate_task_quality(model.model_key, total, active),
    )


def installed_file(model: InstalledModel, repo: str) -> ModelFile | None:
    if not model.filename:
        return None
    return ModelFile(repo=repo, filename=model.filename, quant=model.quant, size_bytes=model.size_bytes)


class CatalogService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        hub: HuggingFaceClient | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.hub = hub or HuggingFaceClient()

    # -- cache --------------------------------------------------------------
    def _cache_put(self, entry: ModelEntry) -> None:
        self.db.execute(
            "INSERT INTO model_metadata(repo, name, publisher, alignment, params_b, source, data, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(repo) DO UPDATE SET name = excluded.name, "
            "publisher = excluded.publisher, alignment = excluded.alignment, params_b = excluded.params_b, "
            "source = excluded.source, data = excluded.data, fetched_at = excluded.fetched_at",
            (
                entry.repo.lower(),
                entry.name,
                entry.publisher,
                entry.alignment.value,
                entry.params_b,
                entry.source,
                entry.model_dump_json(),
                utcnow(),
            ),
        )

    def _cache_get(self, repo: str) -> ModelEntry | None:
        row = self.db.query_one("SELECT data FROM model_metadata WHERE repo = ?", (repo.lower(),))
        return ModelEntry.model_validate_json(row["data"]) if row else None

    def cached_entries(self) -> list[ModelEntry]:
        rows = self.db.query("SELECT data FROM model_metadata")
        return [ModelEntry.model_validate_json(r["data"]) for r in rows]

    # -- knowledge ----------------------------------------------------------
    def known_entries(self) -> dict[str, ModelEntry]:
        """Seed plus everything cached from the Hub; cached data wins (it has real files)."""
        known = {e.repo.lower(): e for e in load_seed()}
        for entry in self.cached_entries():
            seed = known.get(entry.repo.lower())
            known[entry.repo.lower()] = _merge(seed, entry) if seed else entry
        return known

    def details(self, repo: str, *, refresh: bool = False) -> tuple[ModelEntry | None, bool]:
        """Return (entry, online). Fetches exact files from the Hub unless cached."""
        cached = self._cache_get(repo)
        if cached and cached.files_complete and not refresh:
            return cached, True
        seed = next((e for e in load_seed() if e.repo.lower() == repo.lower()), None)
        if not self.settings.models.huggingface_enabled:
            return cached or seed, False
        try:
            fresh = self.hub.details(repo)
        except HubUnavailable as exc:
            log.warning("hub details unavailable for %s: %s", repo, exc)
            return cached or seed, False
        if fresh is None:
            return cached or seed, True
        merged = _merge(seed, fresh) if seed else fresh
        self._cache_put(merged)
        return merged, True

    def _enrich(self, entries: list[ModelEntry]) -> list[ModelEntry]:
        """Fetch exact file lists for entries that lack them, in parallel."""
        todo = [e for e in entries if not e.files_complete and not e.repo.startswith("lmstudio/")]
        if not todo or not self.settings.models.huggingface_enabled:
            return entries
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda e: self.details(e.repo)[0], todo))
        fresh = {e.repo.lower(): e for e in results if e is not None}
        return [fresh.get(e.repo.lower(), e) for e in entries]

    # -- benchmarks ---------------------------------------------------------
    def measured_tps(self) -> dict[str, float]:
        rows = self.db.query(
            "SELECT model_key, generation_tps FROM benchmark WHERE generation_tps IS NOT NULL "
            "ORDER BY created_at ASC"
        )
        return {r["model_key"]: float(r["generation_tps"]) for r in rows}

    # -- evaluation ---------------------------------------------------------
    def evaluate(
        self,
        entry: ModelEntry,
        budget: ResourceBudget,
        profile: TaskProfile | None = None,
        *,
        installed: InstalledModel | None = None,
        context_length: int | None = None,
        quant: str | None = None,
        preference: AlignmentPreference | None = None,
        measured: dict[str, float] | None = None,
    ) -> ModelCard:
        profile = profile or TaskProfile()
        target = profile.target_tps or self.settings.performance.target_tokens_per_second
        context = context_length or max(profile.min_context, self.settings.models.default_context_length)
        options = LoadOptions(context_length=context)
        spec = entry.to_spec()
        measured_value = (measured or {}).get(installed.model_key) if installed else None
        efficiency = self.settings.hardware.tps_efficiency

        if installed is not None:
            mmproj = min((f.size_bytes for f in entry.mmproj_files if f.size_bytes), default=None)
            variant = VariantSpec(
                quant=installed.quant or "Q4_K_M",
                file_size_bytes=installed.size_bytes,
                filename=installed.filename,
                mmproj_bytes=mmproj,
            )
            fit = analyze(spec, variant, budget, options, target_tps=target, efficiency=efficiency, measured_tps=measured_value)
            reasoning = [tr("Installed file: {file} ({quant}).", file=installed.filename or installed.model_key, quant=variant.quant)]
            file = installed_file(installed, entry.repo)
            size, estimate = installed.size_bytes, False
        else:
            variants = entry.variants() or default_variants(spec)
            if quant:
                wanted = [v for v in variants if v.quant.upper() == quant.upper()] or [VariantSpec(quant=quant)]
                fit = analyze(spec, wanted[0], budget, options, target_tps=target, efficiency=efficiency)
                variant, reasoning = wanted[0], []
            else:
                choice = choose_variant(spec, variants, budget, options, target_tps=target, efficiency=efficiency)
                assert choice is not None
                variant, fit, reasoning = choice.variant, choice.fit, choice.why()
            file = entry.file_for(variant.quant) if entry.files else None
            if file and file.size_bytes:
                size, estimate = file.size_bytes, False
            else:
                size = int(fit.memory.weights_gb * 1024**3) if entry.params_b else None
                estimate = True

        score = score_model(
            entry,
            fit,
            profile,
            self.settings.scoring,
            target_tps=target,
            preference=preference or self.settings.models.alignment_preference,
            measured_tps=measured_value,
        )
        return ModelCard(
            entry=entry,
            quant=fit.quant,
            file=file,
            download_size_bytes=size,
            size_is_estimate=estimate,
            fit=fit,
            score=score,
            accuracy=accuracy_for(fit.quant),
            stars={
                "coding": classify.stars(entry.quality.coding),
                "reasoning": classify.stars(entry.quality.reasoning),
                "writing": classify.stars(entry.quality.writing),
            },
            why=explain(entry, fit, profile),
            quant_reasoning=reasoning,
            installed=installed is not None,
            installed_key=installed.model_key if installed else None,
            measured_tps=measured_value,
        )

    # -- search -------------------------------------------------------------
    def search(
        self,
        query: str,
        budget: ResourceBudget,
        filters: SearchFilters | None = None,
        installed: list[InstalledModel] | None = None,
        *,
        limit: int = 40,
        online: bool = True,
    ) -> SearchResponse:
        filters = filters or SearchFilters()
        parsed = parse_query(query)
        notes: list[str] = []
        alignment = filters.alignment or parsed.alignment
        categories = list(dict.fromkeys([*filters.categories, *parsed.categories]))
        installed = installed or []
        installed_by_repo = {m.repo.lower(): m for m in installed if m.repo}

        if parsed.vram_gb:
            budget = hypothetical_budget(budget, parsed.vram_gb, self.settings)
            notes.append(tr("Showing fit for a hypothetical {gb:g} GB GPU, not this PC.", gb=parsed.vram_gb))

        known = self.known_entries()
        is_online = False
        hub_unreachable = False
        hub_queries = parsed.hub_queries()
        if not hub_queries and alignment == Alignment.UNCENSORED:
            hub_queries = ["abliterated", "uncensored", "heretic"]
        if online and self.settings.models.huggingface_enabled and hub_queries and not filters.installed_only:
            try:
                for text in hub_queries[:3]:
                    for entry in self._hub_search(text):
                        key = entry.repo.lower()
                        known[key] = _merge(known.get(key), entry) if key in known else entry
                is_online = True
            except HubUnavailable as exc:
                hub_unreachable = True
                notes.append(tr("Hugging Face is unreachable ({error}); showing cached and built-in models only.", error=exc))
        elif not self.settings.models.huggingface_enabled:
            notes.append(tr("Hugging Face search is disabled in Settings; showing cached and built-in models only."))

        candidates: list[tuple[ModelEntry, InstalledModel | None]] = []
        seen: set[str] = set()
        for model in installed:
            if model.type != "llm":
                continue
            entry = known.get((model.repo or "").lower()) or entry_from_installed(model)
            candidates.append((entry, model))
            seen.add(entry.repo.lower())
        if not (filters.installed_only or parsed.installed_only):
            for key, entry in known.items():
                if key not in seen:
                    candidates.append((entry, installed_by_repo.get(key)))

        def keep(entry: ModelEntry) -> bool:
            haystack = f"{entry.repo} {entry.name} {' '.join(entry.base_models)}".lower()
            if any(term not in haystack for term in parsed.terms):
                return False
            if alignment and entry.alignment != alignment:
                return False
            if categories and not set(categories) & set(entry.categories):
                return False
            if (filters.tool_use or parsed.tool_use) and not entry.tool_use:
                return False
            if filters.vision and not entry.vision:
                return False
            if parsed.params_b and entry.params_b and abs(entry.params_b - parsed.params_b) / parsed.params_b > 0.15:
                return False
            if parsed.params_b and not entry.params_b:
                return False
            return not (filters.max_params_b and (entry.params_b or 0) > filters.max_params_b)

        filtered = [(e, m) for e, m in candidates if keep(e)]
        profile = _profile_for(categories, parsed.tool_use or filters.tool_use)
        measured = self.measured_tps()
        preference = AlignmentPreference.ANY if alignment else None

        # Rank cheaply first, then fetch exact files for the visible top and re-rank.
        prelim = [
            self.evaluate(e, budget, profile, installed=m, preference=preference, measured=measured)
            for e, m in _prefilter(filtered, RECOMMEND_POOL)
        ]
        prelim.sort(key=_rank_key(parsed.wants_best or not parsed.terms))
        top = prelim[: max(limit, ENRICH_TOP_N)]
        if is_online:
            enriched = self._enrich([c.entry for c in top[:ENRICH_TOP_N]])
            by_repo = {e.repo.lower(): e for e in enriched}
            top = [
                self.evaluate(
                    by_repo.get(c.entry.repo.lower(), c.entry),
                    budget,
                    profile,
                    installed=installed_by_repo.get(c.entry.repo.lower()) if c.installed else None,
                    preference=preference,
                    measured=measured,
                )
                if c.entry.repo.lower() in by_repo and not c.installed
                else c
                for c in top
            ]
            top.sort(key=_rank_key(parsed.wants_best or not parsed.terms))
        if filters.recommended_only:
            top = [c for c in top if c.fit.category in (FitCategory.PERFECT, FitCategory.EXCELLENT, FitCategory.GOOD)]
        if not top and alignment == Alignment.UNCENSORED and not is_online:
            notes.append(tr("No uncensored models are cached yet. Connect to the Internet to search Hugging Face."))
        return SearchResponse(
            cards=top[:limit], parsed=parsed, online=is_online, notes=notes, hub_unreachable=hub_unreachable,
            total_candidates=len(filtered),
        )

    def _hub_search(self, text: str) -> list[ModelEntry]:
        """Hub search with a short-lived cache so repeated typing does not hammer the API."""
        key = text.lower().strip()
        cached = self.db.kv_get_with_age("hub_search", key)
        if cached and cached[1] < self.settings.models.catalog_cache_hours:
            return [ModelEntry.model_validate(item) for item in cached[0]]
        entries = self.hub.search(text, limit=30)
        self.db.kv_set("hub_search", key, [json.loads(e.model_dump_json()) for e in entries])
        for entry in entries:
            if self._cache_get(entry.repo) is None:
                self._cache_put(entry)
        return entries

    # -- recommend ----------------------------------------------------------
    def recommend(
        self,
        profile: TaskProfile,
        budget: ResourceBudget,
        installed: list[InstalledModel] | None = None,
        *,
        limit: int = 12,
        alignment: Alignment | None = None,
        preference: AlignmentPreference | None = None,
        context_length: int | None = None,
        enrich: bool = True,
        max_vram_gb: float | None = None,
    ) -> list[ModelCard]:
        """Best models for a task on this PC, one per base model."""
        installed = installed or []
        installed_by_repo = {m.repo.lower(): m for m in installed if m.repo and m.type == "llm"}
        known = self.known_entries()
        for model in installed:
            if model.type == "llm" and (model.repo or "").lower() not in known:
                entry = entry_from_installed(model)
                known[entry.repo.lower()] = entry
                installed_by_repo[entry.repo.lower()] = model
        measured = self.measured_tps()

        def eligible(entry: ModelEntry) -> bool:
            if alignment and entry.alignment != alignment:
                return False
            if profile.needs_vision and not entry.vision:
                return False
            if profile.needs_tools and not entry.tool_use:
                return False
            if "Embedding" in entry.categories or "Reranking" in entry.categories:
                return "Embedding" in profile.categories or "Reranking" in profile.categories
            return bool(entry.params_b)

        pool = [(e, installed_by_repo.get(k)) for k, e in known.items() if eligible(e)]
        cards = [
            self.evaluate(
                e, budget, profile, installed=m, preference=preference, measured=measured, context_length=context_length
            )
            for e, m in _prefilter(pool, RECOMMEND_POOL)
        ]
        cards = [c for c in cards if c.fit.runnable]
        if max_vram_gb is not None:
            cards = [c for c in cards if c.fit.vram_required_gb <= max_vram_gb or c.fit.placement == Placement.CPU]
        cards.sort(key=lambda c: -c.score.total)
        cards = _dedupe(cards)[: max(limit * 2, ENRICH_TOP_N)]
        if enrich:
            fresh = {e.repo.lower(): e for e in self._enrich([c.entry for c in cards[:ENRICH_TOP_N] if not c.installed])}
            cards = [
                self.evaluate(
                    fresh[c.entry.repo.lower()], budget, profile, preference=preference, measured=measured,
                    context_length=context_length,
                )
                if c.entry.repo.lower() in fresh and not c.installed
                else c
                for c in cards
            ]
            cards = [c for c in cards if c.fit.runnable]
            if max_vram_gb is not None:
                cards = [c for c in cards if c.fit.vram_required_gb <= max_vram_gb or c.fit.placement == Placement.CPU]
            cards.sort(key=lambda c: -c.score.total)
        return cards[:limit]

    def why_not(self, chosen: ModelCard, alternative: ModelCard) -> list[str]:
        return explain_versus(chosen, alternative)


# -- helpers ------------------------------------------------------------------


def _merge(seed: ModelEntry | None, fresh: ModelEntry) -> ModelEntry:
    """Keep the seed's architecture geometry when the Hub document lacks it."""
    if seed is None:
        return fresh
    update: dict[str, Any] = {}
    for field in (
        "num_layers", "num_kv_heads", "head_dim", "num_experts", "active_experts", "active_params_b",
        "architecture", "context_length", "license", "params_b", "release_date",
    ):
        if getattr(fresh, field) in (None, "") and getattr(seed, field) not in (None, ""):
            update[field] = getattr(seed, field)
    if seed.is_moe and not fresh.is_moe:
        update["is_moe"] = True
    if not fresh.base_models and seed.base_models:
        update["base_models"] = seed.base_models
    for cap in seed.capabilities:
        if cap not in fresh.capabilities:
            update.setdefault("capabilities", list(fresh.capabilities)).append(cap)
    if not update:
        return fresh
    merged = fresh.model_copy(update=update)
    merged.unknown_fields = [
        f for f in merged.unknown_fields
        if not ((f == "license" and merged.license) or (f == "parameters" and merged.params_b)
                or (f == "architecture" and merged.architecture) or (f == "context_length" and merged.context_length))
    ]
    merged.categories = classify.detect_categories(
        merged.repo, merged.tags, merged.pipeline_tag, merged.capabilities, merged.params_b
    )
    merged.quality = classify.estimate_task_quality(
        merged.base_models[0] if merged.base_models else merged.repo, merged.params_b, merged.active_params_b
    )
    return merged


def _prefilter(
    pool: list[tuple[ModelEntry, InstalledModel | None]], cap: int
) -> list[tuple[ModelEntry, InstalledModel | None]]:
    """Bound evaluation cost: all installed models, then the most adopted of the rest."""
    if len(pool) <= cap:
        return pool
    installed = [p for p in pool if p[1] is not None]
    rest = sorted((p for p in pool if p[1] is None), key=lambda p: -(p[0].downloads or 0))
    return installed + rest[: max(0, cap - len(installed))]


def _rank_key(by_score: bool):
    if by_score:
        return lambda c: (-c.score.total, -(c.entry.downloads or 0))
    # A named search ranks runnable matches by adoption, so the canonical repo comes first.
    return lambda c: (not c.fit.runnable, -(c.entry.downloads or 0), -c.score.total)


def _dedupe(cards: list[ModelCard]) -> list[ModelCard]:
    """Keep the best-scoring build of each base model; installed builds always stay."""
    seen: set[str] = set()
    out: list[ModelCard] = []
    for card in cards:
        key = (card.entry.base_models[0] if card.entry.base_models else card.entry.name).lower()
        if card.installed:
            out.append(card)
            seen.add(key)
        elif key not in seen:
            seen.add(key)
            out.append(card)
    return out


def _profile_for(categories: list[str], needs_tools: bool) -> TaskProfile:
    if "Coding" in categories:
        return TaskProfile(id="coding", label="Coding", coding=3, reasoning=1, writing=0.5, categories=["Coding"], needs_tools=needs_tools)
    if "Reasoning" in categories:
        return TaskProfile(id="reasoning", label="Reasoning", coding=0.5, reasoning=3, writing=1, categories=["Reasoning"], needs_tools=needs_tools)
    if "Writing" in categories:
        return TaskProfile(id="writing", label="Writing", coding=0.2, reasoning=1, writing=3, categories=["Writing"], needs_tools=needs_tools)
    if "Vision" in categories:
        return TaskProfile(id="vision", label="Vision", categories=["Vision"], needs_vision=True, needs_tools=needs_tools)
    return TaskProfile(categories=categories, needs_tools=needs_tools)


def explain(entry: ModelEntry, fit: FitResult, profile: TaskProfile) -> list[str]:
    """Human-readable reasons behind a recommendation ("+ ..." / "- ...")."""
    lines: list[str] = []
    quality = {"coding": entry.quality.coding, "reasoning": entry.quality.reasoning, "writing": entry.quality.writing}
    focus = max(("coding", "reasoning", "writing"), key=lambda k: getattr(profile, k))
    value = quality[focus]
    skill = {"coding": tr("coding"), "reasoning": tr("reasoning"), "writing": tr("writing")}[focus]
    confidence = tr(entry.quality.confidence.value)
    if value >= 85:
        lines.append("+ " + tr("Excellent {skill} capability (estimate, {confidence} confidence)", skill=skill, confidence=confidence))
    elif value >= 75:
        lines.append("+ " + tr("Strong {skill} capability (estimate, {confidence} confidence)", skill=skill, confidence=confidence))
    elif value > 0:
        lines.append("- " + tr("Moderate {skill} capability for this job (estimate)", skill=skill))
    else:
        lines.append("- " + tr("Quality unknown: parameter count not available"))
    for reason in fit.reasons:
        lines.append(f"{reason.kind if reason.kind in '+-' else '·'} {reason.text}")
    if entry.tool_use:
        lines.append("+ " + tr("Tool use supported"))
    elif profile.needs_tools:
        lines.append("- " + tr("Tool use not confirmed for this model"))
    if profile.needs_vision and entry.vision:
        lines.append("+ " + tr("Accepts images"))
    if entry.alignment == Alignment.UNCENSORED:
        lines.append("· " + tr("Uncensored / abliterated build ({evidence})", evidence="; ".join(entry.alignment_evidence[:2])))
    if entry.metadata_confidence in (Confidence.LOW, Confidence.UNKNOWN):
        lines.append(
            "· " + tr("Limited metadata: {fields} unknown", fields=", ".join(entry.unknown_fields or [tr("several fields")]))
        )
    return lines


def explain_versus(chosen: ModelCard, alt: ModelCard) -> list[str]:
    """Why ``chosen`` was preferred over ``alt`` ("Why not the 27B?")."""
    lines: list[str] = []
    q_c, q_a = chosen.score.task_quality, alt.score.task_quality
    if q_a > q_c + 1:
        lines.append("+ " + tr("{name} has slightly stronger expected quality ({a:.0f} vs {b:.0f})", name=alt.entry.name, a=q_a, b=q_c))
    lines.append(tr("but"))
    if alt.fit.placement == Placement.PARTIAL_OFFLOAD and chosen.fit.placement in (Placement.GPU, Placement.GPU_SPLIT):
        lines.append("- " + tr("requires partial CPU offload"))
    if alt.fit.speed.tokens_per_second < chosen.fit.speed.tokens_per_second * 0.9:
        lines.append(
            "- " + tr("expected ~{a:.0f} tok/s versus ~{b:.0f} tok/s", a=alt.fit.speed.tokens_per_second, b=chosen.fit.speed.tokens_per_second)
        )
    if alt.fit.vram_headroom_gb < min(1.0, chosen.fit.vram_headroom_gb):
        lines.append("- " + tr("leaves only {gb:.1f} GB of VRAM reserve", gb=max(alt.fit.vram_headroom_gb, 0)))
        lines.append("- " + tr("poor choice for concurrent agents"))
    if q.quant_bits(alt.quant) and q.quant_bits(chosen.quant) and q.quant_bits(alt.quant) < q.quant_bits(chosen.quant):
        lines.append("- " + tr("only fits at {a}, a heavier quantization than {b}", a=alt.quant, b=chosen.quant))
    if alt.score.total < chosen.score.total:
        lines.append("- " + tr("lower overall Will It Run score ({a:.1f} vs {b:.1f})", a=alt.score.agentfit_score, b=chosen.score.agentfit_score))
    return lines if len(lines) > 1 else []
