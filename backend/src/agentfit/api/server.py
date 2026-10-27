"""Local HTTP API for the desktop UI.

Binds to 127.0.0.1 only. When ``AGENTFIT_TOKEN`` is set (the desktop shell always sets
it), every ``/api`` call must carry it as a bearer token, or as ``?token=`` for the
event stream, which browsers cannot send headers with.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agentfit import branding, doctor, github, i18n
from agentfit.api import arena_routes, clone_routes, cloud_routes, image_routes
from agentfit.app import AppServices, get_services
from agentfit.benchmark import BenchmarkError
from agentfit.brain import BrainError
from agentfit.catalog import Alignment, SearchFilters, TaskProfile
from agentfit.catalog import classify as classify_mod
from agentfit.catalog.scoring import score_model
from agentfit.catalog.service import ModelCard, entry_from_installed
from agentfit.chat import CHAT_WORKFLOW, ChatError, answering_model, session_background
from agentfit.cloud import is_cloud
from agentfit.config import AlignmentPreference
from agentfit.downloads import DownloadError, DownloadRequest, UnsafeDownload
from agentfit.fit import LoadOptions, ModelSpec, VariantSpec, analyze, choose_variant
from agentfit.fit import accuracy as accuracy_mod
from agentfit.hardware import live_memory
from agentfit.i18n import tr
from agentfit.lmstudio import DOWNLOAD_URL, LMStudioError
from agentfit.logging_setup import get_logger, read_log_tail, redact
from agentfit.runtime import RuntimeUnavailable
from agentfit.teams import specializations as spec_mod
from agentfit.teams.builder import TeamError, TeamRequest, plan_to_summary, resolve_model_keys
from agentfit.workflows import checks as checks_mod
from agentfit.workflows import schemas as schema_mod
from agentfit.workflows import sources
from agentfit.workflows import tools as tools_mod
from agentfit.workflows.dsl import WorkflowDef, dump_yaml, load_yaml
from agentfit.workflows.runner import TERMINAL, RunError, RunRecord
from agentfit.workflows.validator import validate

log = get_logger("api")

# Runs of the expert console of a tool that is gone (REA): they stay in the database and are never listed as sessions.
REA_CONSOLE_WORKFLOW = "rea"

ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


def _token_guard(request: Request, token: str | None = Query(default=None)) -> None:
    expected = os.environ.get("AGENTFIT_TOKEN")
    if not expected:
        return
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else token
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing Will It Run token")


class SearchBody(BaseModel):
    query: str = ""
    alignment: Alignment | None = None
    categories: list[str] = Field(default_factory=list)
    installed_only: bool = False
    recommended_only: bool = False
    tool_use: bool = False
    vision: bool = False
    params_exact_b: float | None = None
    params_min_b: float | None = None
    params_max_b: float | None = None
    limit: int = 40
    online: bool = True


class RecommendBody(BaseModel):
    specialization: str | None = None
    slot: str | None = None
    alignment: Alignment | None = None
    preference: AlignmentPreference | None = None
    limit: int = 12
    context_length: int | None = None


class LoadBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_key: str
    context_length: int | None = None
    gpu: str | None = None


class UnloadBody(BaseModel):
    instance_id: str


class BenchmarkBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_key: str
    context_length: int | None = 8192
    max_output_tokens: int = 256
    unload_others: bool = True
    keep_loaded: bool = False


class RunBody(BaseModel):
    workflow_id: str
    task: str
    title: str | None = None
    project: str = "Default"
    # A folder of the user's to work in, instead of an empty workspace for this run.
    workspace: str | None = None
    # A GitHub repository to clone and work on a copy of. Wins over ``workspace``.
    repo_url: str | None = None
    repo_branch: str | None = None
    # In the user's own folder, ask before overwriting a file that was already there.
    protect_existing: bool = True
    # Read-only folder of text documents for workflows that build an evidence dossier.
    evidence_dir: str | None = None
    # Id of the previous run of the session: its request and result are given to the agents.
    continue_from: str | None = None
    # "ask": plan gates and high-risk actions wait for the user. "bypass": nothing waits.
    approval: Literal["ask", "bypass"] = "ask"
    # With ``continue_from`` naming a run that is still working: start when that run has ended.
    queue: bool = False


class AdviceBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_keys: list[str] = Field(default_factory=list)


class ChatBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    message: str
    # LM Studio key of the one model that answers.
    model_key: str
    workspace: str | None = None
    repo_url: str | None = None
    repo_branch: str | None = None
    protect_existing: bool = True
    approval: Literal["ask", "bypass"] = "ask"
    # Written while the chat is answering: wait for that answer, then send.
    queue: bool = False


class FolderBody(BaseModel):
    path: str


def open_folder(folder: Path) -> None:
    """Open a folder in the system's file manager. Only ever a folder: a file given here is never started."""
    if not folder.is_dir():
        raise OSError(f"{folder} is not a folder")
    resolved = str(folder.resolve())
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", resolved], close_fds=True)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", resolved], close_fds=True)


class AskBody(BaseModel):
    """A question about the work of a session that has ended. One model answers; the team does not run again."""

    message: str
    approval: Literal["ask", "bypass"] = "ask"
    # Written while the previous answer is still being written: wait for it, then send.
    queue: bool = False


class DecisionBody(BaseModel):
    approved: bool
    comment: str = ""


class MemoryBody(BaseModel):
    """A memory the user writes by hand."""

    kind: str = "note"
    title: str
    body: str = ""
    files: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # For a solution: the id of the error memory it fixes.
    fixes: int | None = None


class MemoryPatch(BaseModel):
    kind: str | None = None
    title: str | None = None
    body: str | None = None
    files: list[str] | None = None
    tags: list[str] | None = None


class YamlBody(BaseModel):
    yaml: str


class FitBody(BaseModel):
    repo: str
    quant: str
    context_length: int = 8192
    parallel: int = 1
    kv_bytes_per_element: float = 2.0


def _profile_for(spec_id: str | None, slot: str | None) -> TaskProfile:
    if slot and slot in spec_mod.SLOT_PROFILES:
        return spec_mod.SLOT_PROFILES[slot]
    spec = spec_mod.SPECIALIZATIONS.get(spec_id or "")
    if spec is None:
        return TaskProfile()
    group_slot = {"Software": "coding", "Infrastructure": "coding", "Content": "writing", "Research": "reasoning"}.get(spec.group, "reasoning")
    base = spec_mod.SLOT_PROFILES[group_slot]
    return base.model_copy(update={"id": spec.id, "label": spec.label})


def create_app(services: AppServices | None = None) -> FastAPI:
    svc = services or get_services()
    app = FastAPI(title=f"{branding.APP_NAME} Core", version=branding.APP_VERSION, docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"], allow_credentials=False
    )
    app.add_middleware(i18n.LanguageMiddleware)
    guard = [Depends(_token_guard)]

    @app.exception_handler(LMStudioError)
    async def _lmstudio_error(_: Request, exc: LMStudioError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc), "kind": "lmstudio"})

    @app.exception_handler(RuntimeUnavailable)
    async def _runtime_unavailable(_: Request, exc: RuntimeUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "kind": "lmstudio_offline"})

    # -- app ----------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "app": branding.APP_NAME, "version": branding.APP_VERSION}

    @app.get("/api/app", dependencies=guard)
    def app_info() -> dict[str, Any]:
        return {
            "name": branding.APP_NAME,
            "version": branding.APP_VERSION,
            "tagline": branding.APP_TAGLINE,
            "first_run_complete": svc.settings.ui.first_run_complete,
            "lmstudio_download_url": DOWNLOAD_URL,
        }

    # -- hardware -----------------------------------------------------------
    def _hardware_payload(refresh: bool = False) -> dict[str, Any]:
        profile = svc.hardware(refresh=refresh)
        budget = svc.budget()
        return {"profile": profile.model_dump(mode="json"), "budget": budget.model_dump(mode="json"), "fingerprint": profile.fingerprint()}

    @app.get("/api/hardware", dependencies=guard)
    def hardware(refresh: bool = False) -> dict[str, Any]:
        return _hardware_payload(refresh)

    @app.post("/api/scan", dependencies=guard)
    def scan() -> dict[str, Any]:
        """First-run scan. Emits ``scan.step`` events so the wizard can animate progress."""

        def step(name: str, state: str, detail: str = "") -> None:
            svc.bus.publish("scan.step", {"step": name, "state": state, "detail": detail})

        svc.hardware(refresh=True, progress=lambda s, st: step(s, st))
        step("lmstudio", "running")
        lm = svc.lmstudio_status(deep=True)
        step("lmstudio", "done" if lm.installed else "missing", lm.app_path or "")
        step("models", "running")
        try:
            models = svc.installed_models()
        except Exception:
            models = []
        step("models", "done", f"{len(models)} models")
        return {**_hardware_payload(), "lmstudio": lm.model_dump(), "installed_count": len(models)}

    @app.get("/api/live", dependencies=guard)
    def live() -> dict[str, Any]:
        data = live_memory()
        try:
            data["loaded"] = [i.model_dump() for i in svc.runtime.list_loaded()]
            data["lmstudio_connected"] = svc.runtime.is_available()
        except Exception:
            data["loaded"], data["lmstudio_connected"] = [], False
        return data

    # Model sizes shown on the dashboard's "what runs on this PC" chart: (label, total B, active B).
    SIZE_LADDER = (
        ("4B", 4.0, None), ("8B", 8.0, None), ("14B", 14.0, None), ("27B", 27.0, None),
        ("35B MoE", 35.0, 3.0), ("70B", 70.0, None), ("200B", 200.0, None),
    )

    @app.get("/api/readiness", dependencies=guard)
    def readiness() -> dict[str, Any]:
        """Dashboard summary: how well each kind of job runs on this PC, in plain words."""
        budget = svc.budget()
        installed = svc.installed_models()
        target = svc.settings.performance.target_tokens_per_second
        efficiency = svc.settings.hardware.tps_efficiency
        rows: list[dict[str, Any]] = []
        tops: list[ModelCard] = []
        slots = (
            (tr("Coding"), "coding"), (tr("Reasoning"), "reasoning"), (tr("Vision"), "vision"), (tr("Writing"), "writing"),
        )
        for label, slot in slots:
            cards = svc.catalog.recommend(spec_mod.SLOT_PROFILES[slot], budget, installed, limit=1, enrich=False)
            top = cards[0] if cards else None
            if top:
                tops.append(top)
            rows.append({
                "label": label,
                "category": top.fit.category.value if top else None,
                "model": top.entry.name if top else None,
                "quant": top.quant if top else None,
                "tokens_per_second": top.fit.speed.tokens_per_second if top else None,
                "params_b": top.entry.params_b if top else None,
            })
        options = LoadOptions(context_length=8192)
        ladder: list[dict[str, Any]] = []
        for label, params_b, active_b in SIZE_LADDER:
            spec = ModelSpec(name=label, params_b=params_b, active_params_b=active_b, is_moe=active_b is not None)
            choice = choose_variant(spec, [], budget, options, target_tps=target, efficiency=efficiency)
            if choice is None:
                continue
            fit = choice.fit
            ladder.append({
                "label": label,
                "params_b": params_b,
                "quant": fit.quant,
                "category": fit.category.value,
                "runnable": fit.runnable,
                "placement": fit.placement.value,
                "tokens_per_second": fit.speed.tokens_per_second,
                "memory_gb": fit.memory.total_gb,
            })
        big = next((step for step in ladder if step["params_b"] == 70.0), None)
        rows.append({
            "label": tr("Large 70B models"),
            "category": big["category"] if big else None,
            "model": None,
            "quant": big["quant"] if big else None,
            "tokens_per_second": big["tokens_per_second"] if big else None,
            "params_b": 70.0,
        })
        order = ["PERFECT", "EXCELLENT", "GOOD", "ACCEPTABLE", "MEMORY TIGHT", "SLOW", "NOT RECOMMENDED", "IMPOSSIBLE"]
        core = sorted(order.index(r["category"]) for r in rows[:4] if r["category"])
        # The overall verdict follows the typical everyday job, not the best or the worst case.
        overall = order[core[len(core) // 2]] if core else None
        # The model most jobs agree on is the one drawn inside the memory map.
        featured = max(tops, key=lambda c: sum(1 for t in tops if t.entry.repo == c.entry.repo), default=None)
        return {
            "rows": rows,
            "overall": overall,
            "target_tps": target,
            "ladder": ladder,
            "featured": {
                "repo": featured.entry.repo,
                "name": featured.entry.name,
                "quant": featured.quant,
                "category": featured.fit.category.value,
                "placement": featured.fit.placement.value,
                "vram_required_gb": featured.fit.vram_required_gb,
                "ram_required_gb": featured.fit.ram_required_gb,
                "tokens_per_second": featured.fit.speed.tokens_per_second,
                "installed": featured.installed,
            } if featured else None,
        }

    @app.get("/api/storage", dependencies=guard)
    def storage_info() -> dict[str, Any]:
        return {"locations": [loc.model_dump() for loc in svc.storage_locations()]}

    # -- LM Studio ----------------------------------------------------------
    @app.get("/api/lmstudio", dependencies=guard)
    def lmstudio(deep: bool = True) -> dict[str, Any]:
        return {**svc.lmstudio_status(deep=deep).model_dump(), "download_url": DOWNLOAD_URL}

    @app.post("/api/lmstudio/server/start", dependencies=guard)
    def lmstudio_start() -> dict[str, Any]:
        return {"running": svc.runtime.ensure_server()}

    # -- models -------------------------------------------------------------
    def _installed_cards() -> list[ModelCard]:
        budget = svc.budget()
        known = svc.catalog.known_entries()
        measured = svc.catalog.measured_tps()
        cards: list[ModelCard] = []
        for model in svc.installed_models():
            if model.type != "llm":
                continue
            entry = known.get((model.repo or "").lower()) or entry_from_installed(model)
            cards.append(svc.catalog.evaluate(entry, budget, installed=model, measured=measured))
        return cards

    @app.get("/api/models/installed", dependencies=guard)
    def models_installed() -> dict[str, Any]:
        models = svc.installed_models()
        return {
            "cards": [c.model_dump(mode="json") for c in _installed_cards()],
            "models": [m.model_dump() for m in models],
            "total_bytes": sum(m.size_bytes or 0 for m in models),
        }

    @app.post("/api/models/context-advice", dependencies=guard)
    def models_context_advice(body: AdviceBody) -> dict[str, Any]:
        return {"advice": svc.context_advice(body.model_keys[:16]), "usable_vram_gb": round(svc.budget().vram_usable_gb or 0.0, 1)}

    @app.post("/api/models/search", dependencies=guard)
    def models_search(body: SearchBody) -> dict[str, Any]:
        filters = SearchFilters(
            alignment=body.alignment, categories=body.categories, installed_only=body.installed_only,
            recommended_only=body.recommended_only, tool_use=body.tool_use, vision=body.vision,
            params_exact_b=body.params_exact_b, params_min_b=body.params_min_b, params_max_b=body.params_max_b,
        )
        result = svc.catalog.search(
            body.query, svc.budget(), filters, svc.installed_models(), limit=body.limit, online=body.online
        )
        return result.model_dump(mode="json")

    @app.post("/api/models/recommend", dependencies=guard)
    def models_recommend(body: RecommendBody) -> dict[str, Any]:
        profile = _profile_for(body.specialization, body.slot)
        cards = svc.catalog.recommend(
            profile, svc.budget(), svc.installed_models(), limit=body.limit, alignment=body.alignment,
            preference=body.preference, context_length=body.context_length,
        )
        why_not: dict[str, Any] | None = None
        if cards:
            top = cards[0]
            bigger = next(
                (c for c in cards[1:] if (c.entry.params_b or 0) > (top.entry.params_b or 0) * 1.3), None
            )
            if bigger:
                lines = svc.catalog.why_not(top, bigger)
                if lines:
                    why_not = {"model": bigger.entry.name, "lines": lines}
        return {"profile": profile.model_dump(), "cards": [c.model_dump(mode="json") for c in cards], "why_not": why_not}

    @app.get("/api/models/details", dependencies=guard)
    def models_details(repo: str, refresh: bool = False) -> dict[str, Any]:
        entry, online = svc.catalog.details(repo, refresh=refresh)
        installed = [m for m in svc.installed_models() if (m.repo or "").lower() == repo.lower()]
        if entry is None and installed:
            entry = entry_from_installed(installed[0])
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Model '{repo}' was not found")
        budget = svc.budget()
        target = svc.settings.performance.target_tokens_per_second
        options = LoadOptions(context_length=svc.settings.models.default_context_length)
        spec = entry.to_spec()
        mmproj = min((f.size_bytes for f in entry.mmproj_files if f.size_bytes), default=None)
        measured = svc.catalog.measured_tps()
        profile = TaskProfile()
        files = []
        for file in entry.files:
            if not file.quant:
                continue
            local = next((m for m in installed if m.filename == file.filename), None)
            measured_tps = measured.get(local.model_key) if local else None
            fit = analyze(
                spec, VariantSpec(quant=file.quant, file_size_bytes=file.size_bytes, mmproj_bytes=mmproj, filename=file.filename),
                budget, options, target_tps=target, efficiency=svc.settings.hardware.tps_efficiency, measured_tps=measured_tps,
            )
            # The same score the recommender uses, for this exact file.
            score = score_model(
                entry, fit, profile, svc.settings.scoring, target_tps=target,
                preference=svc.settings.models.alignment_preference, measured_tps=measured_tps,
            )
            files.append({
                "file": file.model_dump() | {"url": file.url},
                "fit": fit.model_dump(mode="json"),
                "score": score.model_dump(),
                "accuracy": accuracy_mod.accuracy_for(file.quant).model_dump(),
                "installed": local is not None,
            })
        card = svc.catalog.evaluate(entry, budget, installed=installed[0] if installed else None, measured=measured)
        best = choose_variant(
            spec, entry.variants(), budget, options, target_tps=target, efficiency=svc.settings.hardware.tps_efficiency
        ) if entry.files else None
        benchmarks = [b.model_dump() for m in installed for b in svc.benchmarks.history(m.model_key, limit=5)]
        return {
            "entry": entry.model_dump(mode="json") | {"model_card_url": entry.model_card_url},
            "card": card.model_dump(mode="json"),
            "files": files,
            # The file Will It Run would pick among all of them, whatever is installed.
            "recommended_file": best.variant.filename if best and best.fit.runnable else None,
            "online": online,
            "installed": [m.model_dump() for m in installed],
            "benchmarks": benchmarks,
            "stars": {k: classify_mod.stars(v) for k, v in (("coding", entry.quality.coding), ("reasoning", entry.quality.reasoning), ("writing", entry.quality.writing))},
        }

    @app.get("/api/quantization", dependencies=guard)
    def quantization() -> dict[str, Any]:
        """Reference table: what each quantization costs in quality (see ``fit.accuracy``)."""
        return {
            "table": [row.model_dump() for row in accuracy_mod.reference_table()],
            "reference": accuracy_mod.reference_info(),
        }

    @app.post("/api/models/fit", dependencies=guard)
    def models_fit(body: FitBody) -> dict[str, Any]:
        """Expert mode: fit for an exact quant, context length, parallelism and KV precision."""
        entry, _ = svc.catalog.details(body.repo)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Model '{body.repo}' was not found")
        file = entry.file_for(body.quant)
        mmproj = min((f.size_bytes for f in entry.mmproj_files if f.size_bytes), default=None)
        fit = analyze(
            entry.to_spec(),
            VariantSpec(quant=body.quant, file_size_bytes=file.size_bytes if file else None, mmproj_bytes=mmproj),
            svc.budget(),
            LoadOptions(context_length=body.context_length, parallel=body.parallel, kv_bytes_per_element=body.kv_bytes_per_element),
            target_tps=svc.settings.performance.target_tokens_per_second,
            efficiency=svc.settings.hardware.tps_efficiency,
        )
        return fit.model_dump(mode="json")

    @app.get("/api/models/estimate", dependencies=guard)
    def models_estimate(model_key: str, context_length: int | None = None) -> dict[str, Any]:
        estimate = svc.runtime.estimate(model_key, context_length)
        return {"estimate": estimate.model_dump() if estimate else None}

    @app.post("/api/models/load", dependencies=guard)
    def models_load(body: LoadBody) -> dict[str, Any]:
        result = svc.runtime.load(body.model_key, context_length=body.context_length, gpu=body.gpu)
        svc.bus.publish("models.changed", {"model_key": body.model_key, "action": "loaded"})
        return result.model_dump()

    @app.post("/api/models/unload", dependencies=guard)
    def models_unload(body: UnloadBody) -> dict[str, Any]:
        svc.runtime.unload(body.instance_id)
        svc.bus.publish("models.changed", {"instance_id": body.instance_id, "action": "unloaded"})
        return {"unloaded": body.instance_id}

    # -- downloads ----------------------------------------------------------
    def _smaller_options(request: DownloadRequest) -> list[str]:
        entry, _ = svc.catalog.details(request.repo)
        if entry is None or not request.size_bytes:
            return []
        smaller = [f for f in entry.files if f.size_bytes and f.size_bytes < request.size_bytes and f.quant]
        smaller.sort(key=lambda f: -(f.size_bytes or 0))
        return [f"{f.quant} ({(f.size_bytes or 0) / 1024**3:.1f} GB)" for f in smaller[:3]]

    def _complete_request(request: DownloadRequest) -> DownloadRequest:
        """Fill size, hash and revision from the real file list; never trust a guessed size."""
        entry, _ = svc.catalog.details(request.repo)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Model '{request.repo}' was not found")
        file = next((f for f in entry.files if f.filename == request.filename), None) or (
            entry.file_for(request.quant) if request.quant else None
        )
        if file is None:
            raise HTTPException(status_code=404, detail="That file does not exist in the repository.")
        companions = request.companions
        if entry.mmproj_files and not companions:
            companions = [min(entry.mmproj_files, key=lambda f: f.size_bytes or 0)]
        return request.model_copy(
            update={
                "filename": file.filename, "quant": file.quant, "size_bytes": file.size_bytes,
                "sha256": file.sha256, "revision": file.revision, "companions": companions,
            }
        )

    @app.post("/api/downloads/plan", dependencies=guard)
    def downloads_plan(request: DownloadRequest) -> dict[str, Any]:
        request = _complete_request(request)
        try:
            plan = svc.downloads.plan(request, _smaller_options(request))
        except DownloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        custom = bool(request.base_dir or request.target_dir)
        return {
            "plan": plan.model_dump(),
            "request": request.model_dump(mode="json"),
            "backend": svc.downloads.resolve_backend(request.backend, custom_folder=custom),
            "default_folder": svc.downloads.default_base_dir(),
            "lmstudio_folder": svc.lmstudio_status().models_dir,
            # LM Studio only scans its own folder; elsewhere the finished file is linked in.
            "outside_lmstudio": svc.downloads.outside_lmstudio(plan.target_dir),
        }

    @app.post("/api/downloads", dependencies=guard)
    def downloads_create(request: DownloadRequest) -> JSONResponse:
        request = _complete_request(request)
        try:
            record = svc.downloads.enqueue(request, _smaller_options(request))
        except UnsafeDownload as exc:
            return JSONResponse(status_code=409, content={"detail": exc.plan.verdict, "kind": "unsafe_download", "plan": exc.plan.model_dump()})
        except DownloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(content=record.model_dump() | {"progress": record.progress})

    @app.get("/api/downloads", dependencies=guard)
    def downloads_list() -> dict[str, Any]:
        return {"downloads": [d.model_dump() | {"progress": d.progress} for d in svc.downloads.list()]}

    @app.post("/api/downloads/{record_id}/{action}", dependencies=guard)
    def downloads_action(record_id: str, action: str) -> dict[str, Any]:
        handlers = {"pause": svc.downloads.pause, "resume": svc.downloads.resume, "retry": svc.downloads.retry, "cancel": svc.downloads.cancel}
        if action not in handlers:
            raise HTTPException(status_code=404, detail="Unknown action")
        try:
            record = handlers[action](record_id)
        except DownloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return record.model_dump() | {"progress": record.progress}

    @app.delete("/api/downloads/{record_id}", dependencies=guard)
    def downloads_remove(record_id: str) -> dict[str, Any]:
        try:
            svc.downloads.remove(record_id)
        except DownloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"removed": record_id}

    # -- benchmarks ---------------------------------------------------------
    @app.get("/api/benchmarks", dependencies=guard)
    def benchmarks_list(model_key: str | None = None) -> dict[str, Any]:
        history = svc.benchmarks.history(model_key)
        cards = {c.installed_key: c for c in _installed_cards()} if history else {}
        return {
            "benchmarks": [
                b.model_dump() | {
                    "agentfit_score": cards[b.model_key].score.agentfit_score if b.model_key in cards else None,
                    "fit": cards[b.model_key].fit.category.value if b.model_key in cards else None,
                    "display_name": cards[b.model_key].entry.name if b.model_key in cards else b.model_key,
                }
                for b in history
            ]
        }

    @app.post("/api/benchmarks", dependencies=guard)
    def benchmarks_run(body: BenchmarkBody) -> dict[str, Any]:
        fingerprint = svc.hardware().fingerprint()

        def work() -> None:
            try:
                svc.benchmarks.run(
                    body.model_key, context_length=body.context_length, max_output_tokens=body.max_output_tokens,
                    unload_others=body.unload_others, keep_loaded=body.keep_loaded, hardware_fingerprint=fingerprint,
                )
            except (BenchmarkError, LMStudioError, RuntimeUnavailable) as exc:
                svc.bus.publish("benchmark.progress", {"model_key": body.model_key, "stage": "failed", "error": str(exc)})
            except Exception as exc:
                log.exception("benchmark crashed")
                svc.bus.publish("benchmark.progress", {"model_key": body.model_key, "stage": "failed", "error": str(exc)})

        threading.Thread(target=work, daemon=True, name="benchmark").start()
        return {"started": True, "model_key": body.model_key}

    # -- teams --------------------------------------------------------------
    @app.get("/api/specializations", dependencies=guard)
    def specializations() -> dict[str, Any]:
        return {"groups": spec_mod.catalog()}

    @app.post("/api/teams/plan", dependencies=guard)
    def teams_plan(request: TeamRequest) -> dict[str, Any]:
        try:
            plan = svc.team_builder().plan(request)
        except TeamError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"plan": plan.model_dump(mode="json", by_alias=True), "summary": plan_to_summary(plan)}

    @app.post("/api/teams", dependencies=guard)
    def teams_create(request: TeamRequest) -> dict[str, Any]:
        try:
            plan = svc.team_builder().plan(request)
        except TeamError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not plan.can_build:
            raise HTTPException(status_code=409, detail="; ".join(plan.blockers))
        svc.workflows.save(plan.workflow)
        summary = plan_to_summary(plan)
        svc.workflows.save_team(plan.id, plan.name, plan.workflow.workflow.id, {
            "summary": summary,
            "models": [
                {"label": m.label, "repo": m.card.entry.repo, "name": m.card.entry.name, "quant": m.card.quant,
                 "filename": m.card.file.filename if m.card.file else None, "needs_download": m.needs_download,
                 "download_gb": m.download_gb, "policy": m.loading_policy.value, "vram_gb": m.vram_gb, "agents": m.agents,
                 "alignment": m.card.entry.alignment.value}
                for m in plan.models
            ],
            "agents": [a.model_dump() for a in plan.agents],
            "explanations": plan.explanations,
            "notes": plan.notes,
        })
        return {"plan": plan.model_dump(mode="json", by_alias=True), "summary": summary, "workflow_id": plan.workflow.workflow.id}

    @app.get("/api/teams", dependencies=guard)
    def teams_list() -> dict[str, Any]:
        return {"teams": svc.workflows.teams()}

    @app.delete("/api/teams/{team_id}", dependencies=guard)
    def teams_delete(team_id: str) -> dict[str, Any]:
        svc.workflows.delete_team(team_id)
        return {"deleted": team_id}

    # -- GitHub account (through the GitHub CLI) ------------------------------
    github_login = github.Login()

    @app.get("/api/github", dependencies=guard)
    def github_status() -> dict[str, Any]:
        who = github.account()
        return {"cli": who.cli, "signed_in": who.signed_in, "login": who.login, "sign_in": github_login.snapshot()}

    @app.post("/api/github/login", dependencies=guard)
    def github_sign_in() -> dict[str, Any]:
        """Start the browser sign-in. The answer carries the one-time code to enter on github.com."""
        try:
            return github_login.start()
        except github.GitHubError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/github/login", dependencies=guard)
    def github_sign_in_cancel() -> dict[str, Any]:
        return github_login.cancel()

    @app.get("/api/github/repos", dependencies=guard)
    def github_repos() -> dict[str, Any]:
        try:
            return {"repos": github.repositories()}
        except github.GitHubError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -- workflows ----------------------------------------------------------
    @app.get("/api/workflows", dependencies=guard)
    def workflows_list() -> dict[str, Any]:
        # A workflow that is working says so in the list, with the run to open.
        active = svc.runs.active()
        return {"workflows": [{**w.model_dump(), "running_run": active.get(w.id)} for w in svc.workflows.list()]}

    @app.get("/api/workflows/{workflow_id:path}/yaml", dependencies=guard)
    def workflows_yaml(workflow_id: str) -> PlainTextResponse:
        definition = svc.workflows.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return PlainTextResponse(dump_yaml(definition))

    @app.post("/api/workflows/validate", dependencies=guard)
    def workflows_validate(definition: WorkflowDef, require_models: bool = False) -> dict[str, Any]:
        return validate(definition, require_models=require_models).model_dump()

    @app.post("/api/workflows/import", dependencies=guard)
    def workflows_import(body: YamlBody) -> dict[str, Any]:
        try:
            definition = load_yaml(body.yaml)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid workflow YAML: {exc}") from exc
        result = validate(definition)
        if not result.ok:
            raise HTTPException(status_code=400, detail="; ".join(i.message for i in result.errors))
        svc.workflows.save(definition)
        return {"workflow": definition.model_dump(mode="json", by_alias=True), "validation": result.model_dump()}

    @app.get("/api/workflows/{workflow_id:path}", dependencies=guard)
    def workflows_get(workflow_id: str) -> dict[str, Any]:
        definition = svc.workflows.get(workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return {
            "workflow": definition.model_dump(mode="json", by_alias=True),
            "validation": validate(definition).model_dump(),
            "source": "template" if workflow_id.startswith("template:") else "user",
        }

    @app.put("/api/workflows", dependencies=guard)
    def workflows_save(definition: WorkflowDef) -> dict[str, Any]:
        result = validate(definition)
        if not result.ok:
            return {"saved": False, "validation": result.model_dump()}
        svc.workflows.save(definition)
        return {"saved": True, "validation": result.model_dump(), "id": definition.workflow.id}

    @app.delete("/api/workflows/{workflow_id:path}", dependencies=guard)
    def workflows_delete(workflow_id: str) -> dict[str, Any]:
        svc.workflows.delete(workflow_id)
        return {"deleted": workflow_id}

    @app.get("/api/tools", dependencies=guard)
    def tools_list() -> dict[str, Any]:
        checks = [{**c, "label": tr(c["label"]), "description": tr(c["description"])} for c in checks_mod.catalog()]
        return {"tools": tools_mod.tool_catalog(), "schemas": sorted(schema_mod.SCHEMAS), "checks": checks}

    # -- runs ---------------------------------------------------------------
    @app.post("/api/runs", dependencies=guard)
    def runs_start(body: RunBody) -> dict[str, Any]:
        definition = svc.workflows.get(body.workflow_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        missing = resolve_model_keys(definition, svc.installed_models())
        if missing:
            labels = ", ".join(definition.models[m].label or m for m in missing)
            raise HTTPException(
                status_code=409,
                detail=f"No installed model is assigned for: {labels}. Download the team's models or pick installed ones in the workflow editor.",
            )
        local = any(not is_cloud(slot.model_key) for slot in definition.models.values())
        if local and not svc.runtime.ensure_server():
            raise HTTPException(status_code=503, detail="LM Studio's local server is not running and could not be started.")
        takes_evidence = any(c.kind == "evidence_dossier" for c in definition.checks)
        if not body.task.strip():
            raise HTTPException(status_code=400, detail="Describe the task for the team.")

        def begin() -> RunRecord:
            place = sources.resolve(
                folder=body.workspace, repo_url=body.repo_url, repo_branch=body.repo_branch, evidence_dir=body.evidence_dir,
                protect_existing=body.protect_existing, takes_evidence=takes_evidence,
            )
            if body.continue_from and place.source.get("kind") == "none" and not takes_evidence:
                # The next request of a session that has no folder of the user's goes on in the workspace of the
                # one before: otherwise it would start from an empty folder, without the files it is asked about.
                before = svc.runs.started(body.continue_from)
                kept = str(before.get("workspace") or "")
                if (before.get("source") or {}).get("kind", "none") == "none" and kept and Path(kept).is_dir():
                    place = sources.Place(workspace=kept, evidence_dir=place.evidence_dir, protect_existing=False, source={"kind": "none"})
            context: str | None = None
            if body.continue_from and not takes_evidence:
                # Never for an evidence workflow: an earlier answer must not become evidence for the next.
                try:
                    previous = svc.runs.get(body.continue_from)
                    context = sources.earlier_work(previous.task, previous.result)
                except RunError:
                    context = None
            return svc.runs.start(
                definition, body.task, title=body.title, project=body.project, workspace=place.workspace,
                evidence_dir=place.evidence_dir, protect_existing=place.protect_existing, source=place.source, context=context,
                approval=body.approval,
            )

        if body.queue and body.continue_from:
            # Written while that run was working: it starts when the run ends, and is told what the run did.
            try:
                if svc.runs.queue_after(body.continue_from, body.task, begin):
                    return {"queued": True, "after": body.continue_from}
            except RunError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            run = begin()
        except sources.SourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RunError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return run.model_dump()

    @app.post("/api/runs/{run_id}/ask", dependencies=guard)
    def runs_ask(run_id: str, body: AskBody) -> dict[str, Any]:
        """A question about a session's work, answered by one model in the place the team worked."""
        if not body.message.strip():
            raise HTTPException(status_code=400, detail="Write a question.")
        try:
            record = svc.runs.get(run_id)
        except RunError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if record.status not in TERMINAL:
            raise HTTPException(status_code=409, detail="The team is still working. Ask when it has finished, or write the next request for the team.")
        started = svc.runs.started(run_id)
        installed = [m.model_key for m in svc.installed_models() if m.type == "llm"]
        model_key = answering_model(svc.workflows.get(record.workflow_id), installed)
        if model_key is None:
            raise HTTPException(status_code=409, detail="No model is installed in LM Studio to answer with.")
        if not svc.runtime.ensure_server():
            raise HTTPException(status_code=503, detail="LM Studio's local server is not running and could not be started.")
        source = started.get("source") or {"kind": "none"}
        workspace = str(started.get("workspace") or "") or None
        chat = svc.chats.for_session(run_id, record.title, model_key, source)
        background = session_background(record, svc.runs.files_written(run_id))

        def begin() -> RunRecord:
            return svc.chats.send(
                chat["id"], body.message, model_key=model_key, workspace=workspace,
                protect_existing=source.get("kind") == "folder" and started.get("protect_existing") is not False,
                source=source, approval=body.approval, background=background,
            )

        working = svc.chats.active_run(chat["id"])
        if working:
            if body.queue and svc.runs.queue_after(working, body.message, begin):
                return {"chat": chat["id"], "queued": True, "after": working}
            if svc.chats.active_run(chat["id"]):
                raise HTTPException(status_code=409, detail="The previous answer is still being written. Stop it, or send the question to wait for it.")
        try:
            answer = begin()
        except RunError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"chat": chat["id"], "run": answer.id, "model_key": model_key}

    @app.get("/api/runs/{run_id}/questions", dependencies=guard)
    def runs_questions(run_id: str) -> dict[str, Any]:
        """The questions asked about a session's work, with their answers. None when there are none yet."""
        return {"chat": svc.chats.of_session(run_id)}

    @app.get("/api/runs", dependencies=guard)
    def runs_list() -> dict[str, Any]:
        # The turns of a chat are runs too. They are listed with their chat, not among the sessions.
        return {"runs": [r.model_dump() for r in svc.runs.list() if r.workflow_id not in (CHAT_WORKFLOW, REA_CONSOLE_WORKFLOW)]}

    # -- chat: one model, one conversation ------------------------------------
    @app.get("/api/chats", dependencies=guard)
    def chats_list() -> dict[str, Any]:
        return {"chats": svc.chats.list()}

    @app.get("/api/chats/{chat_id}", dependencies=guard)
    def chats_get(chat_id: str) -> dict[str, Any]:
        try:
            return svc.chats.get(chat_id)
        except ChatError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def chat_send(chat_id: str | None, body: ChatBody) -> dict[str, Any]:
        if not body.message.strip():
            raise HTTPException(status_code=400, detail="Write a message.")
        if is_cloud(body.model_key):
            pass  # called over the Internet; a missing key is reported by the call itself
        elif not any(m.model_key == body.model_key for m in svc.installed_models()):
            raise HTTPException(status_code=409, detail="Choose a model that is installed in LM Studio, or a cloud model.")
        elif not svc.runtime.ensure_server():
            raise HTTPException(status_code=503, detail="LM Studio's local server is not running and could not be started.")
        try:
            chat = svc.chats.get(chat_id) if chat_id else svc.chats.create(body.message, body.model_key)
        except ChatError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def begin() -> RunRecord:
            place = sources.resolve(folder=body.workspace, repo_url=body.repo_url, repo_branch=body.repo_branch, protect_existing=body.protect_existing)
            return svc.chats.send(
                chat["id"], body.message, model_key=body.model_key, workspace=place.workspace,
                protect_existing=place.protect_existing, source=place.source, approval=body.approval,
            )

        working = svc.chats.active_run(chat["id"])
        if working:
            # One answer at a time: this message waits for the one being written, or is refused.
            if body.queue and svc.runs.queue_after(working, body.message, begin):
                return {"chat": chat["id"], "queued": True, "after": working}
            if svc.chats.active_run(chat["id"]):
                raise HTTPException(status_code=409, detail="This chat is still answering. Stop it, or send the message to wait for it.")
        try:
            run = begin()
        except (sources.SourceError, RunError) as exc:
            if not chat_id:
                # Nothing was said in it: a chat that could not start is not kept.
                with contextlib.suppress(ChatError):
                    svc.chats.delete(chat["id"])
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"chat": chat["id"], "run": run.id}

    @app.post("/api/chats", dependencies=guard)
    def chats_start(body: ChatBody) -> dict[str, Any]:
        return chat_send(None, body)

    @app.post("/api/chats/{chat_id}/messages", dependencies=guard)
    def chats_message(chat_id: str, body: ChatBody) -> dict[str, Any]:
        return chat_send(chat_id, body)

    @app.delete("/api/chats/{chat_id}", dependencies=guard)
    def chats_delete(chat_id: str) -> dict[str, Any]:
        try:
            svc.chats.delete(chat_id)
        except ChatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": chat_id}

    @app.post("/api/folders/open", dependencies=guard)
    def folders_open(body: FolderBody) -> dict[str, Any]:
        """Show a folder in File Explorer: where a chat or an investigation wrote."""
        target = Path(body.path.strip().strip('"'))
        if not target.is_dir():
            raise HTTPException(status_code=404, detail=f"There is no folder at {target}.")
        try:
            open_folder(target)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"opened": str(target)}

    @app.get("/api/runs/{run_id}", dependencies=guard)
    def runs_get(run_id: str, after: int = 0) -> dict[str, Any]:
        try:
            run = svc.runs.get(run_id)
        except RunError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"run": run.model_dump(), "events": svc.runs.events(run_id, after)}

    @app.get("/api/runs/{run_id}/definition", dependencies=guard)
    def runs_definition(run_id: str) -> dict[str, Any]:
        """The workflow a run was started with, when it is not a saved workflow (a website study)."""
        stored = svc.db.kv_get("run_definition", run_id)
        if not isinstance(stored, dict):
            raise HTTPException(status_code=404, detail="This run uses a saved workflow.")
        return {"workflow": stored}

    @app.post("/api/runs/{run_id}/approvals/{approval_id}", dependencies=guard)
    def runs_decide(run_id: str, approval_id: str, body: DecisionBody) -> dict[str, Any]:
        try:
            return svc.runs.decide(run_id, approval_id, body.approved, body.comment).model_dump()
        except RunError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/cancel", dependencies=guard)
    def runs_cancel(run_id: str) -> dict[str, Any]:
        try:
            return svc.runs.cancel(run_id).model_dump()
        except RunError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/runs/{run_id}/queued", dependencies=guard)
    def runs_unqueue(run_id: str) -> dict[str, Any]:
        return {"removed": svc.runs.unqueue(run_id)}

    @app.delete("/api/runs/{run_id}", dependencies=guard)
    def runs_delete(run_id: str) -> dict[str, Any]:
        try:
            questions = svc.chats.of_session(run_id)
            if questions is not None:
                # The questions about a session go with it. Its files stay where they are.
                svc.chats.delete(questions["id"])
            svc.runs.delete(run_id)
        except (ChatError, RunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": run_id}

    # -- project brain: the permanent memory of each project ------------------
    def _brain_error(exc: BrainError) -> HTTPException:
        return HTTPException(status_code=404 if "no memory" in str(exc).lower() else 400, detail=str(exc))

    @app.get("/api/brain", dependencies=guard)
    def brain_projects() -> dict[str, Any]:
        return {
            "enabled": svc.brain.enabled(),
            "semantic": svc.brain.embed_status().model_dump(),
            "projects": [p.model_dump() for p in svc.brain.projects()],
        }

    @app.get("/api/brain/lookup", dependencies=guard)
    def brain_lookup(workspace: str | None = None, repo_url: str | None = None) -> dict[str, Any]:
        """The memory of the place a run is about to work in, if that place has one."""
        source: dict[str, Any] | None = None
        place: str | None = None
        try:
            if repo_url and repo_url.strip():
                owner, repo = sources.parse_github(repo_url)
                source = {"kind": "github", "url": f"https://github.com/{owner}/{repo}"}
            elif workspace and workspace.strip():
                place = str(sources.user_folder(workspace))
                source = {"kind": "folder", "path": place}
        except sources.SourceError:
            source = None
        project = svc.brain.lookup(source, place)
        return {"enabled": svc.brain.enabled(), "project": project.model_dump() if project else None}

    @app.get("/api/brain/projects/{project_id}", dependencies=guard)
    def brain_project(project_id: str) -> dict[str, Any]:
        try:
            return svc.brain.overview(project_id)
        except BrainError as exc:
            raise _brain_error(exc) from exc

    @app.delete("/api/brain/projects/{project_id}", dependencies=guard)
    def brain_forget(project_id: str) -> dict[str, Any]:
        try:
            svc.brain.forget(project_id)
        except BrainError as exc:
            raise _brain_error(exc) from exc
        return {"deleted": project_id}

    def _memory_runs(run_ids: list[str | None]) -> dict[str, dict[str, Any]]:
        """Where each memory came from, when that run still exists: a session, or a turn of a chat."""
        ids = sorted({run_id for run_id in run_ids if run_id})
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        rows = svc.db.query(f"SELECT id, project FROM workflow_run WHERE id IN ({marks})", tuple(ids))
        return {r["id"]: {"id": r["id"], "chat": r["project"][5:] if str(r["project"]).startswith("chat:") else None} for r in rows}

    @app.get("/api/brain/projects/{project_id}/memories", dependencies=guard)
    def brain_memories(project_id: str, q: str = "", kind: str | None = None, replaced: bool = False) -> dict[str, Any]:
        """The memories of a project, newest first; with ``q``, the ones that answer it, best first, with why."""
        try:
            if q.strip():
                found = svc.brain.search(project_id, q, kinds=[kind] if kind else None, limit=20)
                runs = _memory_runs([hit.memory.run_id for hit in found.hits])
                rows = [{**hit.model_dump(), "links": svc.brain.links(hit.memory), "run": runs.get(hit.memory.run_id or "")} for hit in found.hits]
                return {"rows": rows, "searched": True, "semantic": found.semantic, "model": found.model, "total": found.total}
            memories = svc.brain.memories(project_id, kind=kind, include_superseded=replaced)
        except BrainError as exc:
            raise _brain_error(exc) from exc
        runs = _memory_runs([m.run_id for m in memories])
        rows = [
            {"memory": m.model_dump(), "score": None, "semantic": None, "keyword": False, "via": [], "links": svc.brain.links(m), "run": runs.get(m.run_id or "")}
            for m in memories
        ]
        return {"rows": rows, "searched": False, "semantic": False, "model": None, "total": len(rows)}

    @app.post("/api/brain/projects/{project_id}/memories", dependencies=guard)
    def brain_add(project_id: str, body: MemoryBody) -> dict[str, Any]:
        try:
            memory, state = svc.brain.add(
                project_id, kind=body.kind, title=body.title, body=body.body, files=body.files, tags=body.tags, source="user", agent="You",
                fixes=body.fixes,
            )
        except BrainError as exc:
            raise _brain_error(exc) from exc
        return {"memory": memory.model_dump(), "state": state}

    @app.put("/api/brain/memories/{memory_id}", dependencies=guard)
    def brain_edit(memory_id: int, body: MemoryPatch) -> dict[str, Any]:
        try:
            memory = svc.brain.edit(memory_id, kind=body.kind, title=body.title, body=body.body, files=body.files, tags=body.tags)
        except BrainError as exc:
            raise _brain_error(exc) from exc
        return {"memory": memory.model_dump()}

    @app.delete("/api/brain/memories/{memory_id}", dependencies=guard)
    def brain_delete(memory_id: int) -> dict[str, Any]:
        try:
            svc.brain.delete(memory_id)
        except BrainError as exc:
            raise _brain_error(exc) from exc
        return {"deleted": memory_id}

    @app.get("/api/brain/projects/{project_id}/graph", dependencies=guard)
    def brain_graph(project_id: str, limit: int = 160) -> dict[str, Any]:
        try:
            return svc.brain.graph(project_id, max(10, min(limit, 400))).model_dump()
        except BrainError as exc:
            raise _brain_error(exc) from exc

    @app.post("/api/brain/projects/{project_id}/reindex", dependencies=guard)
    def brain_reindex(project_id: str) -> dict[str, Any]:
        """Give a vector to every memory that has none for the embedding model in use."""
        try:
            done = svc.brain.reindex(project_id)
            return {**svc.brain.overview(project_id), "newly_embedded": done}
        except BrainError as exc:
            raise _brain_error(exc) from exc

    # -- settings, logs, doctor --------------------------------------------
    @app.get("/api/settings", dependencies=guard)
    def settings_get() -> dict[str, Any]:
        data = svc.settings.model_dump(mode="json")
        # Never send the stored LM Studio token back to the UI; only say whether one is set.
        data["runtime"]["lmstudio_api_token_set"] = bool(data["runtime"].pop("lmstudio_api_token", ""))
        return data

    @app.put("/api/settings", dependencies=guard)
    def settings_put(patch: dict[str, Any]) -> dict[str, Any]:
        runtime_patch = patch.get("runtime")
        if isinstance(runtime_patch, dict):
            runtime_patch.pop("lmstudio_api_token_set", None)
        try:
            svc.apply_settings(patch)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid settings: {exc}") from exc
        return settings_get()

    @app.get("/api/logs", dependencies=guard)
    def logs(lines: int = 300) -> dict[str, Any]:
        return {"entries": redact(read_log_tail(min(lines, 2000)))}

    @app.get("/api/doctor", dependencies=guard)
    def doctor_run() -> dict[str, Any]:
        checks = doctor.run_doctor(svc)
        return {"overall": doctor.overall(checks), "checks": [c.model_dump() for c in checks]}

    # -- live events --------------------------------------------------------
    @app.get("/api/events", dependencies=guard)
    async def events() -> StreamingResponse:
        subscription = svc.bus.subscribe()

        async def stream() -> Any:
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = await asyncio.to_thread(subscription.get, True, 15.0)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            finally:
                svc.bus.unsubscribe(subscription)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # Cloud models, the site cloner, the Arena, and the image studio (ComfyUI as the engine).
    cloud_routes.install_routes(app, svc, guard)
    clone_routes.install_routes(app, svc, guard)
    arena_routes.install_routes(app, svc, guard)
    image_routes.install_routes(app, svc, guard)
    return app
