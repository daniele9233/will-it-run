"""Service container: wires storage, hardware, runtime, catalog and engines together.

Both the HTTP API and the CLI build one ``AppServices`` and call into it, so behaviour
is identical whichever front end is used.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from agentfit import branding, i18n, storage
from agentfit.arena.runner import ArenaService
from agentfit.arena.store import ArenaStore
from agentfit.benchmark import BenchmarkEngine
from agentfit.brain import BrainService, RuntimeEmbedder
from agentfit.catalog import CatalogService
from agentfit.chat import ChatService
from agentfit.clone import studio as clone_studio
from agentfit.clone.install import BrowserInstall
from agentfit.clone.kits import KitStore
from agentfit.clone.session import SessionRegistry
from agentfit.cloud import CloudService, HybridRuntime
from agentfit.config import Settings, load_settings, update_settings
from agentfit.db import Database, get_db, utcnow
from agentfit.downloads import DownloadManager
from agentfit.events import EventBus, bus
from agentfit.hardware import HardwareProfile, ResourceBudget, compute_budget, detect_hardware
from agentfit.hardware.windows import model_spill_mib
from agentfit.imagegen import ImageStudio
from agentfit.imagegen.components import ComponentStore
from agentfit.imagegen.engine import ComfyEngine
from agentfit.lmstudio import LMStudioRuntime
from agentfit.lmstudio.models import InstalledModel, LMStudioInstall
from agentfit.logging_setup import configure_logging, get_logger, log_event
from agentfit.runtime import RuntimeUnavailable
from agentfit.teams.builder import TeamBuilder
from agentfit.workflows.dsl import ModelSlot
from agentfit.workflows.model_manager import GatewayOptions, ModelManager, nvidia_vram_probe
from agentfit.workflows.runner import RunManager
from agentfit.workflows.store import WorkflowStore

log = get_logger("app")


def _free_vram_mib() -> float | None:
    reading = nvidia_vram_probe()
    return reading.free_mib if reading is not None else None


class AppServices:
    def __init__(self, db: Database | None = None, settings: Settings | None = None, event_bus: EventBus | None = None) -> None:
        configure_logging()
        self.db = db or get_db()
        self.settings = settings or load_settings()
        i18n.set_default_language(self.settings.ui.language)
        self.bus = event_bus or bus
        self._lock = threading.RLock()
        self._profile: HardwareProfile | None = None
        self._runtime: LMStudioRuntime | None = None
        self._downloads: DownloadManager | None = None
        self._runs: RunManager | None = None
        self._chats: ChatService | None = None
        self._arena: ArenaService | None = None
        self._brain: BrainService | None = None
        self._browser_install: BrowserInstall | None = None
        self._kits: KitStore | None = None
        self._clone_sessions: SessionRegistry | None = None
        self._clone: clone_studio.CloneStudio | None = None
        self._images: ImageStudio | None = None
        self.cloud = CloudService(self.db, max_context=lambda: self.settings.cloud.max_context)
        self.catalog = CatalogService(self.db, self.settings)
        self.workflows = WorkflowStore(self.db)

    # -- settings -----------------------------------------------------------
    def apply_settings(self, patch: dict[str, Any]) -> Settings:
        with self._lock:
            self.settings = update_settings(patch)
            i18n.set_default_language(self.settings.ui.language)
            self.catalog.settings = self.settings
            # Runtime and download manager read connection details from settings.
            self._runtime = None
            if self._downloads is not None:
                self._downloads.settings = self.settings
                self._downloads.runtime = self.runtime
            if self._runs is not None:
                self._runs.runtime = self.models_runtime
            if self._images is not None:
                self._images.settings = self.settings
                self._images.engine.settings = self.settings
                self._images.engine.forget_install()
                self._images.components.settings = self.settings
            return self.settings

    # -- hardware -----------------------------------------------------------
    def hardware(self, *, refresh: bool = False, progress: Callable[[str, str], None] | None = None) -> HardwareProfile:
        with self._lock:
            if self._profile is not None and not refresh:
                return self._profile
            if not refresh:
                cached = self.db.kv_get("hardware", "profile")
                if cached:
                    self._profile = HardwareProfile.model_validate(cached)
                    return self._profile
        profile = detect_hardware(progress)
        with self._lock:
            self._profile = profile
            self.db.kv_set("hardware", "profile", profile.model_dump(mode="json"))
            self.db.execute(
                "INSERT INTO hardware_profile(detected_at, fingerprint, profile) VALUES (?, ?, ?)",
                (utcnow(), profile.fingerprint(), profile.model_dump_json()),
            )
        log_event(log, "hardware detected", gpus=[g.name for g in profile.gpus], ram_gb=round(profile.ram.total_gb, 1))
        return profile

    def budget(self) -> ResourceBudget:
        return compute_budget(self.hardware(), self.settings.hardware)

    # -- runtime ------------------------------------------------------------
    @property
    def runtime(self) -> LMStudioRuntime:
        with self._lock:
            if self._runtime is None:
                self._runtime = LMStudioRuntime(self.settings)
            return self._runtime

    @property
    def models_runtime(self) -> HybridRuntime:
        """What runs call: LM Studio for local models, the provider's API for ``cloud:`` ones."""
        return HybridRuntime(self.runtime, self.cloud)

    def lmstudio_status(self, *, deep: bool = False) -> LMStudioInstall:
        return self.runtime.status(deep=deep)

    def installed_models(self) -> list[InstalledModel]:
        """Installed models, falling back to the last known list when LM Studio is unreachable."""
        try:
            models = self.runtime.list_installed()
        except RuntimeUnavailable:
            cached = self.db.kv_get("lmstudio", "installed") or []
            return [InstalledModel.model_validate(m) for m in cached]
        self.db.kv_set("lmstudio", "installed", [m.model_dump(mode="json") for m in models])
        return models

    # -- engines ------------------------------------------------------------
    @property
    def downloads(self) -> DownloadManager:
        with self._lock:
            if self._downloads is None:
                self._downloads = DownloadManager(
                    self.db, self.settings, self.runtime, lambda: self.hardware(refresh=False).drives, bus=self.bus
                )
            return self._downloads

    @property
    def benchmarks(self) -> BenchmarkEngine:
        return BenchmarkEngine(self.db, self.runtime, bus=self.bus)

    @property
    def runs(self) -> RunManager:
        with self._lock:
            if self._runs is None:
                self._runs = RunManager(
                    self.db,
                    self.models_runtime,
                    bus=self.bus,
                    require_approval=lambda: self.settings.workflow.require_approval_for_high_risk_actions,
                    vram_budget_gb=lambda: self.budget().vram_usable_gb or None,
                    vram_probe=nvidia_vram_probe,
                    spill_probe=model_spill_mib,
                    gateway_options=self.gateway_options,
                    brain=self.brain,
                    clone_session=lambda run_id, workspace, cancelled, emit: self.clone_sessions.session(run_id, workspace, cancelled, emit),
                    clone_session_close=lambda run_id: self.clone_sessions.close(run_id),
                )
                _ = self.kits  # the cloner's steps find the kits and the browser engine through the module
            return self._runs

    @property
    def browser_install(self) -> BrowserInstall:
        """The browser engine the site cloner drives (Playwright's library, kept in the app's own folder)."""
        with self._lock:
            if self._browser_install is None:
                self._browser_install = BrowserInstall(bus=self.bus)
                clone_studio.install_source = lambda: self.browser_install
            return self._browser_install

    @property
    def kits(self) -> KitStore:
        """The cloning kit, fetched at its pinned commit the first time it is needed."""
        with self._lock:
            if self._kits is None:
                self._kits = KitStore()
                clone_studio.kit_store = self._kits
                _ = self.browser_install
            return self._kits

    @property
    def clone_sessions(self) -> SessionRegistry:
        with self._lock:
            if self._clone_sessions is None:
                self._clone_sessions = SessionRegistry(self.browser_install, self.bus)
            return self._clone_sessions

    @property
    def clone(self) -> clone_studio.CloneStudio:
        """Site clones: an address in, a project on this PC out."""
        with self._lock:
            if self._clone is None:
                self._clone = clone_studio.CloneStudio(self.db, self.runs, self.kits, self.browser_install)
            return self._clone

    @property
    def images(self) -> ImageStudio:
        """The image studio: ComfyUI draws on this PC, Will It Run asks and keeps the result."""
        with self._lock:
            if self._images is None:
                engine = ComfyEngine(self.settings, self.bus)
                components = ComponentStore(
                    self.db, self.settings, self.bus, engine, drives=self.refreshed_drives
                )
                self._images = ImageStudio(
                    self.db,
                    self.settings,
                    self.bus,
                    engine=engine,
                    components=components,
                    vram_budget_gb=lambda: self.budget().vram_usable_gb,
                    lmstudio_loaded=self._model_loaded,
                )
            return self._images

    def _model_loaded(self) -> bool:
        """Whether LM Studio holds a model right now: it competes for the same VRAM."""
        try:
            return bool(self.runtime.list_loaded())
        except Exception:
            return False

    @property
    def brain(self) -> BrainService:
        """The permanent memory of each project. Its vectors come from whatever runtime is current."""
        with self._lock:
            if self._brain is None:
                self._brain = BrainService(
                    self.db,
                    RuntimeEmbedder(lambda: self.runtime, lambda: self.settings.memory.embedding_model, _free_vram_mib),
                    enabled=lambda: self.settings.memory.enabled,
                )
            return self._brain

    @property
    def chats(self) -> ChatService:
        with self._lock:
            if self._chats is None:
                self._chats = ChatService(self.db, self.runs)
            return self._chats

    @property
    def arena(self) -> ArenaService:
        """The Arena: the same questions put to each model in turn, every answer checked by code."""
        with self._lock:
            if self._arena is None:
                self._arena = ArenaService(
                    ArenaStore(self.db), lambda: self.models_runtime, self.bus,
                    installed=self.installed_models, gateway=self._arena_gateway, environment=self._arena_environment,
                )
            return self._arena

    def _arena_gateway(self, slots: dict[str, ModelSlot], emit: Callable[[str, dict[str, Any]], None]) -> ModelManager:
        """A gateway of its own for a run: it loads one model at a time and unloads it when its turn is over."""
        return ModelManager(
            self.models_runtime, slots, emit, vram_budget_gb=self.budget().vram_usable_gb or None, vram_probe=nvidia_vram_probe,
            profiles=self.runs.profiles, options=self.gateway_options(), spill_probe=model_spill_mib,
        )

    def _arena_environment(self) -> dict[str, Any]:
        """The PC and the programs a run was made with: two runs are comparable only if this says they were alike."""
        import platform

        profile = self.hardware()
        lm = self.lmstudio_status()
        gpus = [g.name for g in profile.gpus if g.usable_for_inference] or [g.name for g in profile.gpus]
        return {
            "app": {"name": branding.APP_NAME, "version": branding.APP_VERSION}, "os": profile.os.caption or platform.platform(), "python": platform.python_version(),
            "cpu": profile.cpu.model, "gpu": gpus, "vram_gb": round(max((g.vram_total_mb for g in profile.gpus), default=0) / 1024, 1),
            "ram_gb": round(profile.ram.total_gb, 1),
            "lmstudio": {"installed": lm.installed, "version": lm.cli_version, "port": lm.server_port},
        }

    def gateway_options(self) -> GatewayOptions:
        workflow = self.settings.workflow
        return GatewayOptions(
            margin_mib=workflow.vram_margin_mib,
            saturated_mib=workflow.vram_saturated_mib,
            idle_ttl_seconds=workflow.model_idle_ttl_seconds,
        )

    def context_advice(self, model_keys: list[str]) -> list[dict[str, Any]]:
        """For each model: the largest context that fits this PC's usable VRAM, and how that is known.

        It starts from LM Studio's own estimate and uses real measurements once the model has
        been loaded by a run. ``safe_context`` is None when neither exists.
        """
        from agentfit.workflows.model_manager import AUTO_MAX_CONTEXT, estimate_profile, safe_context

        options = self.gateway_options()
        usable_mib = (self.budget().vram_usable_gb or 0.0) * 1024
        installed = {m.model_key: m for m in self.installed_models()}
        store = self.runs.profiles
        advice: list[dict[str, Any]] = []
        for key in dict.fromkeys(model_keys):
            model = installed.get(key)
            supported = model.max_context_length if model else None
            profile = store.get(key)
            if profile is None and model is not None:
                profile = estimate_profile(self.runtime, key, options.min_context, options.min_context + 8192)
                if profile is not None:
                    store.put(key, profile)
            entry: dict[str, Any] = {"model_key": key, "max_context": supported, "safe_context": None, "basis": None, "fits_gpu": None}
            if profile is not None and usable_mib > 0:
                fits = safe_context(profile, usable_mib, int(supported or AUTO_MAX_CONTEXT))
                entry.update({
                    "safe_context": fits if fits >= options.min_context else 0,
                    "basis": profile.source,
                    "fits_gpu": profile.need_mib(options.min_context) <= usable_mib,
                })
            advice.append(entry)
        return advice

    def team_builder(self) -> TeamBuilder:
        return TeamBuilder(
            self.catalog,
            self.settings,
            self.budget(),
            self.installed_models(),
            self.refreshed_drives(),
            self.lmstudio_status().models_dir,
        )

    def refreshed_drives(self) -> list[Any]:
        """Drive list with live free space (the cached profile's figures go stale)."""
        drives = []
        for drive in self.hardware().drives:
            free = storage.free_gb_at(drive.letter + "\\")
            drives.append(drive.model_copy(update={"free_bytes": int(free * 1024**3)}) if free is not None else drive)
        return drives

    def storage_locations(self) -> list[storage.StorageLocation]:
        return storage.storage_locations(
            self.refreshed_drives(), self.settings.storage, self.lmstudio_status().models_dir
        )


_services: AppServices | None = None
_services_lock = threading.Lock()


def get_services() -> AppServices:
    global _services
    with _services_lock:
        if _services is None:
            _services = AppServices()
        return _services
