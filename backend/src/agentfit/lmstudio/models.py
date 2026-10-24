"""LM Studio data types."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LMStudioInstall(BaseModel):
    installed: bool = False
    home: str | None = None
    app_path: str | None = None
    cli_path: str | None = None
    models_dir: str | None = None
    server_running: bool = False
    server_port: int | None = None
    api_reachable: bool = False
    cli_version: str | None = None
    engines: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LoadedInstance(BaseModel):
    instance_id: str
    model_key: str
    context_length: int | None = None
    size_bytes: int | None = None
    # "llm" or "embedding". An embedding model is small and is not one of the models a run swaps.
    type: str = "llm"


class InstalledModel(BaseModel):
    model_key: str
    display_name: str
    type: str = "llm"
    publisher: str | None = None
    # "<publisher>/<repo>/<file>.gguf" relative to the LM Studio models directory.
    relative_path: str | None = None
    repo: str | None = None
    filename: str | None = None
    format: str | None = None
    quant: str | None = None
    size_bytes: int | None = None
    params_string: str | None = None
    architecture: str | None = None
    vision: bool = False
    tool_use: bool = False
    reasoning: bool = False
    max_context_length: int | None = None
    loaded_instances: list[LoadedInstance] = Field(default_factory=list)

    @property
    def is_loaded(self) -> bool:
        return bool(self.loaded_instances)


class DownloadJob(BaseModel):
    job_id: str | None = None
    status: str
    total_size_bytes: int | None = None
    downloaded_bytes: int | None = None
    bytes_per_second: float | None = None
    estimated_completion: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class LoadResult(BaseModel):
    instance_id: str
    model_key: str
    load_time_seconds: float | None = None
    load_config: dict | None = None
    already_loaded: bool = False
    # "mtp" when the model was loaded with speculative decoding through its own MTP head.
    speculative: str | None = None


class ChatStats(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    tokens_per_second: float | None = None
    time_to_first_token_seconds: float | None = None
    model_load_time_seconds: float | None = None


class ChatResult(BaseModel):
    text: str = ""
    reasoning: str = ""
    stats: ChatStats = Field(default_factory=ChatStats)
    model_instance_id: str | None = None


class ResourceEstimate(BaseModel):
    """LM Studio's own load estimate (``lms load --estimate-only``)."""

    gpu_memory_gb: float | None = None
    total_memory_gb: float | None = None
    context_length: int | None = None
    raw: str = ""
