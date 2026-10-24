"""User settings, stored as YAML in %APPDATA%\\Will It Run\\settings.yaml."""

from __future__ import annotations

from enum import Enum
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agentfit import paths


class AlignmentPreference(str, Enum):
    ANY = "any"
    STANDARD = "standard"
    UNCENSORED = "uncensored"


class ExperienceMode(str, Enum):
    AUTOMATIC = "automatic"
    GUIDED = "guided"
    EXPERT = "expert"


class RuntimeSettings(BaseModel):
    primary: str = "lmstudio"
    lmstudio_host: str = "127.0.0.1"
    lmstudio_port: int = 1234
    # Only needed when LM Studio has API authentication enabled.
    lmstudio_api_token: str = ""
    # A model that carries its own MTP head (the "-mtp" GGUFs of Qwen3.8) is loaded with speculative decoding: the same
    # answers, about 1.4 to 1.6 times faster. Measured on an RTX 4080 SUPER: 44 -> 62-68 tok/s on prose.
    speculative_mtp: bool = True
    # Tokens guessed per step. 2 is the best for prose and explanations; code gains from 3 or 4.
    speculative_draft_tokens: int = Field(default=2, ge=1, le=8)


class HardwareSettings(BaseModel):
    # VRAM that Windows, the compositor and desktop apps keep for themselves.
    vram_os_reserve_gb: float = 1.0
    # Extra safety margin, as a percentage of each GPU's total VRAM.
    vram_reserve_percent: float = 5.0
    ram_reserve_percent: float = 15.0
    # Multiplier applied to raw memory bandwidth in the tok/s roofline.
    tps_efficiency: float = 0.55
    ddr_bandwidth_gbps: float | None = None


class StorageSettings(BaseModel):
    reserve_percent: float = 15.0
    # Never let a download leave less than this free, whatever the percentage says.
    min_free_gb: float = 10.0
    # Extra space a download may need while in flight (partial file, verification).
    temp_overhead_percent: float = 10.0
    custom_model_dir: str = ""


class ModelSettings(BaseModel):
    alignment_preference: AlignmentPreference = AlignmentPreference.ANY
    default_context_length: int = 8192
    huggingface_enabled: bool = True
    catalog_cache_hours: int = 24


class PerformanceSettings(BaseModel):
    target_tokens_per_second: float = 20.0


class WorkflowSettings(BaseModel):
    max_retries: int = 3
    max_steps: int = 40
    require_approval_for_high_risk_actions: bool = True
    # Model gateway (see workflows.model_manager).
    # A loaded model that stays idle this long is unloaded by LM Studio itself. 0 = never.
    model_idle_ttl_seconds: int = 600
    # VRAM kept free on paper when the context of a model is chosen.
    vram_margin_mib: int = 600
    # Less than this free right after a load counts as saturated: reload with less context.
    vram_saturated_mib: int = 480


class MemorySettings(BaseModel):
    # The Project Brain: what each project remembers from one session to the next.
    enabled: bool = True
    # LM Studio key of the embedding model for semantic search. Empty: the best one installed.
    embedding_model: str = ""


class CloudSettings(BaseModel):
    # Cloud models (Claude, ChatGPT, DeepSeek, Gemini). Their keys are not here: see agentfit.cloud.secrets.
    # The most context a cloud model is given, whatever it supports: every token of it is paid for.
    max_context: int = 200_000


class DownloadSettings(BaseModel):
    # auto: Will It Run's own downloader (pause, stop, resume); LM Studio's only as a fallback.
    backend: str = "auto"
    max_concurrent: int = 1
    verify_hash: bool = True


class ScoringWeights(BaseModel):
    hardware_fit: float = 0.25
    task_quality: float = 0.25
    specialization: float = 0.15
    speed: float = 0.10
    context: float = 0.08
    tool_use: float = 0.07
    reliability: float = 0.05
    local_benchmark: float = 0.05

    def normalized(self) -> dict[str, float]:
        raw = self.model_dump()
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}


class UiSettings(BaseModel):
    theme: str = "dark"
    accent: str = "#4aa8e0"
    mode: ExperienceMode = ExperienceMode.GUIDED
    notifications: bool = True
    first_run_complete: bool = False
    # "system" follows the Windows display language; otherwise "en" or "it".
    language: str = "system"
    # Whole-interface zoom factor, 0.8 to 1.6.
    zoom: float = 1.0
    # The "getting started" strip on Home, once the user has closed it.
    guide_dismissed: bool = False


class Settings(BaseModel):
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    hardware: HardwareSettings = Field(default_factory=HardwareSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    performance: PerformanceSettings = Field(default_factory=PerformanceSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    cloud: CloudSettings = Field(default_factory=CloudSettings)
    downloads: DownloadSettings = Field(default_factory=DownloadSettings)
    scoring: ScoringWeights = Field(default_factory=ScoringWeights)
    ui: UiSettings = Field(default_factory=UiSettings)

    @property
    def lmstudio_base_url(self) -> str:
        return f"http://{self.runtime.lmstudio_host}:{self.runtime.lmstudio_port}"


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings() -> Settings:
    path = paths.settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return Settings()
    return Settings.model_validate(raw)


def save_settings(settings: Settings) -> None:
    path = paths.settings_path()
    data = settings.model_dump(mode="json")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def update_settings(patch: dict[str, Any]) -> Settings:
    current = load_settings().model_dump(mode="json")
    merged = Settings.model_validate(_deep_merge(current, patch))
    save_settings(merged)
    return merged
