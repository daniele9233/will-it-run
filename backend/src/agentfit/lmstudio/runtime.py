"""LM Studio as Will It Run's primary model runtime."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentfit.config import Settings, load_settings
from agentfit.fit.quant import normalize_quant, parse_quant_from_filename
from agentfit.hardware import nvidia
from agentfit.lmstudio import detect
from agentfit.lmstudio.cli import LmsCli, LmsCliError
from agentfit.lmstudio.client import LMStudioClient, LMStudioError, LMStudioOffline
from agentfit.lmstudio.gguf import has_mtp_head
from agentfit.lmstudio.models import (
    ChatResult,
    InstalledModel,
    LMStudioInstall,
    LoadedInstance,
    LoadResult,
    ResourceEstimate,
)
from agentfit.logging_setup import get_logger, log_event
from agentfit.runtime import ModelRuntime, RuntimeUnavailable

log = get_logger("lmstudio")

# LM Studio's own default is 1.1. The MTP head knows nothing of a repeat penalty, so every penalised token is a guess it
# gets wrong: 1.1 cost 10 % of the speed (acceptance 42 % against 52 %). Qwen's and Gemma's cards recommend 1.0 anyway.
MTP_REPEAT_PENALTY = 1.0
# What LM Studio says when it cannot do speculative decoding: the model has no MTP head, or the CLI does not know the flags.
_MTP_REFUSED = re.compile(r"mtp|speculative", re.IGNORECASE)
# What a model needs on top of its file in VRAM (KV cache of a 16-32K context, compute buffers, the MTP head's own state).
FULL_OFFLOAD_ALLOWANCE_MIB = 2560


def _first_gpu_total_mib() -> float | None:
    try:
        rows = nvidia.live_usage()
    except Exception:
        return None
    return float(rows[0]["vram_total_mb"] or 0) or None if rows else None


def _split_path(relative_path: str | None) -> tuple[str | None, str | None]:
    """"publisher/repo/file.gguf" -> ("publisher/repo", "file.gguf")."""
    if not relative_path:
        return None, None
    parts = relative_path.replace("\\", "/").split("/")
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}", "/".join(parts[2:])
    return None, parts[-1]


def installed_from_cli(row: dict[str, Any]) -> InstalledModel:
    path = row.get("path")
    repo, filename = _split_path(path)
    quant = (row.get("quantization") or {}).get("name") or (parse_quant_from_filename(filename) if filename else None)
    return InstalledModel(
        model_key=row.get("modelKey") or "",
        display_name=row.get("displayName") or row.get("modelKey") or "",
        type=row.get("type") or "llm",
        publisher=row.get("publisher"),
        relative_path=path,
        repo=repo,
        filename=filename,
        format=row.get("format"),
        quant=normalize_quant(quant) if quant else None,
        size_bytes=row.get("sizeBytes"),
        params_string=row.get("paramsString"),
        architecture=row.get("architecture"),
        vision=bool(row.get("vision")),
        tool_use=bool(row.get("trainedForToolUse")),
        max_context_length=row.get("maxContextLength"),
    )


def _instances(row: dict[str, Any]) -> list[LoadedInstance]:
    out: list[LoadedInstance] = []
    for inst in row.get("loaded_instances") or []:
        if not isinstance(inst, dict):
            continue
        instance_id = inst.get("id") or inst.get("instance_id") or inst.get("identifier")
        if not instance_id:
            continue
        config = inst.get("config") or {}
        out.append(
            LoadedInstance(
                instance_id=str(instance_id),
                model_key=row.get("key") or "",
                context_length=config.get("context_length"),
                size_bytes=row.get("size_bytes"),
                type=row.get("type") or "llm",
            )
        )
    return out


def installed_from_rest(row: dict[str, Any]) -> InstalledModel:
    caps = row.get("capabilities") or {}
    quant = (row.get("quantization") or {}).get("name")
    return InstalledModel(
        model_key=row.get("key") or "",
        display_name=row.get("display_name") or row.get("key") or "",
        type=row.get("type") or "llm",
        publisher=row.get("publisher"),
        format=row.get("format"),
        quant=normalize_quant(quant) if quant else None,
        size_bytes=row.get("size_bytes"),
        params_string=row.get("params_string"),
        architecture=row.get("architecture"),
        vision=bool(caps.get("vision")),
        tool_use=bool(caps.get("trained_for_tool_use")),
        reasoning=bool(caps.get("reasoning")),
        max_context_length=row.get("max_context_length"),
        loaded_instances=_instances(row),
    )


def merge_installed(cli_rows: list[InstalledModel], rest_rows: list[InstalledModel]) -> list[InstalledModel]:
    """The CLI knows file paths; the REST API knows capabilities and loaded instances."""
    by_key = {m.model_key: m for m in cli_rows}
    for rest in rest_rows:
        base = by_key.get(rest.model_key)
        if base is None:
            by_key[rest.model_key] = rest
            continue
        by_key[rest.model_key] = base.model_copy(
            update={
                "loaded_instances": rest.loaded_instances,
                "reasoning": rest.reasoning,
                "vision": base.vision or rest.vision,
                "tool_use": base.tool_use or rest.tool_use,
                "size_bytes": base.size_bytes or rest.size_bytes,
                "quant": base.quant or rest.quant,
            }
        )
    return sorted(by_key.values(), key=lambda m: m.display_name.lower())


class LMStudioRuntime(ModelRuntime):
    name = "lmstudio"
    supports_streaming = True
    # LM Studio serves text and vision-language models and embeddings. No image/video generation.
    modalities = frozenset({"text", "vision", "embedding"})

    def __init__(
        self,
        settings: Settings | None = None,
        client: LMStudioClient | None = None,
        cli: LmsCli | None = None,
        install: LMStudioInstall | None = None,
        total_vram_mib: Callable[[], float | None] | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._total_vram_mib = total_vram_mib or _first_gpu_total_mib
        self._install = install or detect.detect_install()
        self.client = client or LMStudioClient(self.settings.lmstudio_base_url, self.settings.runtime.lmstudio_api_token)
        self.cli = cli or LmsCli(self._install.cli_path)
        # Which model files carry an MTP head (by size and modified time), where they are, and the models loaded with it.
        self._mtp_files: dict[str, tuple[tuple[int, int], bool]] = {}
        self._model_paths: dict[str, Path] = {}
        self._mtp_models: set[str] = set()
        self._mtp_refused: set[str] = set()

    # -- status -------------------------------------------------------------
    def status(self, *, deep: bool = False) -> LMStudioInstall:
        """Installation plus live server state. ``deep`` also queries CLI version and engines."""
        info = self._install.model_copy(deep=True)
        info.api_reachable = self.client.is_reachable()
        info.server_running = info.api_reachable
        if info.api_reachable:
            info.server_port = self.settings.runtime.lmstudio_port
        elif self.cli.available:
            state = self.cli.server_status()
            info.server_running = bool(state.get("running"))
            if state.get("port"):
                info.server_port = int(state["port"])
                if info.server_running and info.server_port != self.settings.runtime.lmstudio_port:
                    info.notes.append(
                        f"LM Studio server runs on port {info.server_port}, but Will It Run is configured "
                        f"for port {self.settings.runtime.lmstudio_port}. Update Settings > Runtime."
                    )
        if deep and self.cli.available:
            info.cli_version = self.cli.version()
            info.engines = self.cli.runtime_engines()
        if info.installed and not info.server_running:
            info.notes.append("LM Studio's local server is not running. Start it to load and run models.")
        return info

    def is_available(self) -> bool:
        return self.client.is_reachable()

    def ensure_server(self) -> bool:
        """Start the local server through the CLI when it is not running."""
        if self.client.is_reachable():
            return True
        if not self.cli.available:
            return False
        try:
            self.cli.server_start()
        except LmsCliError:
            return False
        for _ in range(20):
            if self.client.is_reachable():
                return True
            time.sleep(0.5)
        return False

    # -- models -------------------------------------------------------------
    def list_installed(self) -> list[InstalledModel]:
        cli_rows: list[InstalledModel] = []
        rest_rows: list[InstalledModel] = []
        if self.cli.available:
            try:
                cli_rows = [installed_from_cli(r) for r in self.cli.ls() if r.get("modelKey")]
            except (LmsCliError, ValueError) as exc:
                log.warning("lms ls failed: %s", exc)
        try:
            rest_rows = [installed_from_rest(r) for r in self.client.list_models() if r.get("key")]
        except LMStudioError as exc:
            if not cli_rows:
                raise RuntimeUnavailable(str(exc)) from exc
        return merge_installed(cli_rows, rest_rows)

    def list_loaded(self) -> list[LoadedInstance]:
        try:
            return [inst for row in self.client.list_models() for inst in _instances(row)]
        except LMStudioOffline:
            pass
        if not self.cli.available:
            return []
        try:
            return [
                LoadedInstance(
                    instance_id=row.get("identifier") or row.get("modelKey") or "",
                    model_key=row.get("modelKey") or row.get("identifier") or "",
                    context_length=row.get("contextLength"),
                    size_bytes=row.get("sizeBytes"),
                    type=row.get("type") or "llm",
                )
                for row in self.cli.ps()
            ]
        except (LmsCliError, ValueError):
            return []

    def load(
        self,
        model_key: str,
        *,
        context_length: int | None = None,
        gpu: str | None = None,
        ttl_seconds: int | None = None,
    ) -> LoadResult:
        """Load a model, reusing an instance that is already in memory.

        The REST load endpoint has no GPU-offload and no idle-timeout parameter, so an
        explicit ``gpu`` ratio or a ``ttl_seconds`` goes through ``lms load --gpu`` /
        ``--ttl`` when the CLI is installed. Without the CLI the idle timeout is skipped.
        """
        for inst in self.list_loaded():
            if inst.model_key == model_key or inst.instance_id == model_key:
                if not {inst.instance_id, model_key} & self._mtp_models and self._mtp_path(model_key):
                    # Loaded outside this run (LM Studio's own window, an earlier session): it is probably running its MTP head.
                    self._mtp_models.update({inst.instance_id, model_key})
                return LoadResult(
                    instance_id=inst.instance_id, model_key=model_key, already_loaded=True,
                    load_config={"context_length": inst.context_length} if inst.context_length else None,
                    speculative="mtp" if {inst.instance_id, model_key} & self._mtp_models else None,
                )
        started = time.perf_counter()
        self._mtp_models.discard(model_key)
        plan = self._mtp_plan(model_key)
        if gpu or plan or (ttl_seconds and self.cli.available):
            result = self._load_with_cli(model_key, context_length, gpu, ttl_seconds, plan, started)
            if result is not None:
                return result
        data = self.client.load(model_key, context_length=context_length)
        elapsed = time.perf_counter() - started
        log_event(log, "model loaded", model=model_key, seconds=round(elapsed, 2))
        return LoadResult(
            instance_id=str(data.get("instance_id") or model_key),
            model_key=model_key,
            load_time_seconds=data.get("load_time_seconds") or round(elapsed, 3),
            load_config=data.get("load_config"),
        )

    def _load_with_cli(
        self, model_key: str, context_length: int | None, gpu: str | None, ttl_seconds: int | None,
        plan: tuple[int, str | None] | None, started: float,
    ) -> LoadResult | None:
        """The load through ``lms``. None when it failed and the REST API should be tried instead (no GPU ratio was asked)."""
        for attempt in [plan, None] if plan else [None]:
            tokens, offload = attempt if attempt else (None, None)
            try:
                self.cli.load(
                    model_key, context_length=context_length, gpu=gpu or offload, ttl_seconds=ttl_seconds,
                    # One slot: LM Studio's default of four cost 1.4 GiB more on a 27B, and a run asks for one answer at a time.
                    parallel=1 if tokens else None, mtp_draft_tokens=tokens,
                )
            except LmsCliError as exc:
                if tokens and _MTP_REFUSED.search(str(exc)):
                    log.warning("LM Studio would not load %s with MTP (%s); loading it without", model_key, exc)
                    self._mtp_refused.add(model_key)
                    continue
                if gpu:
                    raise LMStudioError(str(exc)) from exc
                log.warning("lms load failed for %s (%s); loading through the REST API without an idle timeout", model_key, exc)
                return None
            elapsed = time.perf_counter() - started
            loaded = next((i for i in self.list_loaded() if i.model_key == model_key), None)
            instance_id = loaded.instance_id if loaded else model_key
            if tokens:
                self._mtp_models.update({instance_id, model_key})
            log_event(log, "model loaded via CLI", model=model_key, seconds=round(elapsed, 2), gpu=gpu, ttl=ttl_seconds, mtp_draft_tokens=tokens)
            return LoadResult(
                instance_id=instance_id,
                model_key=model_key,
                load_time_seconds=round(elapsed, 3),
                load_config={"context_length": loaded.context_length} if loaded and loaded.context_length else None,
                speculative="mtp" if tokens else None,
            )
        return None

    def _mtp_path(self, model_key: str) -> Path | None:
        """The model's file when it carries an MTP head, the setting is on and LM Studio has not refused it."""
        if not self.settings.runtime.speculative_mtp or not self.cli.available or model_key in self._mtp_refused:
            return None
        path = self._model_file(model_key)
        return path if path is not None and self._carries_mtp(path) else None

    def _mtp_plan(self, model_key: str) -> tuple[int, str | None] | None:
        """(tokens to guess per step, GPU offload to ask for) for a model that carries an MTP head. None: load it plainly.

        LM Studio's own choice of offload, left on automatic, cost a fifth of the speed on a 27B (55 against 66-68 tok/s) with
        the VRAM in use the same to within 100 MiB, so a model that fits the card with room to spare is loaded with ``--gpu max``.
        """
        path = self._mtp_path(model_key)
        if path is None:
            return None
        total = self._total_vram_mib()
        fits = total is not None and path.stat().st_size / 1_048_576 + FULL_OFFLOAD_ALLOWANCE_MIB <= total
        return self.settings.runtime.speculative_draft_tokens, "max" if fits else None

    def _model_file(self, model_key: str) -> Path | None:
        known = self._model_paths.get(model_key)
        if known is not None and known.is_file():
            return known
        root = self._install.models_dir
        if not root:
            return None
        try:
            models = self.list_installed()
        except (RuntimeUnavailable, LMStudioError):
            return None
        row = next((m for m in models if m.model_key == model_key and m.relative_path), None)
        if row is None or not row.relative_path:
            return None
        path = Path(root) / row.relative_path
        if path.is_file():
            self._model_paths[model_key] = path
            return path
        return None

    def _carries_mtp(self, path: Path) -> bool:
        try:
            info = path.stat()
        except OSError:
            return False
        stamp = (info.st_size, int(info.st_mtime))
        cached = self._mtp_files.get(str(path))
        if cached is not None and cached[0] == stamp:
            return cached[1]
        found = has_mtp_head(path)
        self._mtp_files[str(path)] = (stamp, found)
        return found

    def unload(self, instance_id: str) -> None:
        self._mtp_models.discard(instance_id)
        try:
            self.client.unload(instance_id)
        except LMStudioOffline:
            if not self.cli.available:
                raise
            self.cli.unload(instance_id)
        log_event(log, "model unloaded", instance=instance_id)

    def unload_all(self) -> list[str]:
        unloaded: list[str] = []
        for inst in self.list_loaded():
            self.unload(inst.instance_id)
            unloaded.append(inst.instance_id)
        return unloaded

    def estimate(self, model_key: str, context_length: int | None = None) -> ResourceEstimate | None:
        """LM Studio's own memory estimate for an installed model, when the CLI is present."""
        if not self.cli.available:
            return None
        try:
            return self.cli.estimate(model_key, context_length)
        except LmsCliError:
            return None

    # -- inference ----------------------------------------------------------
    def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
        on_delta: Callable[[str, str], None] | None = None,
        sampling: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``sampling`` (seed, top_p, top_k, repeat_penalty) is for a caller that has to say how the answer is drawn, the Arena for one."""
        options: dict[str, Any] = {"tools": tools, "response_format": response_format, "temperature": temperature, "max_tokens": max_tokens}
        if model in self._mtp_models:
            options["repeat_penalty"] = MTP_REPEAT_PENALTY
        for name in ("seed", "top_p", "top_k", "repeat_penalty"):
            if (sampling or {}).get(name) is not None:
                options[name] = sampling[name]  # type: ignore[index]
        if on_delta is not None:
            return self.client.chat_completions_stream(model, messages, on_delta, **options)
        return self.client.chat_completions(model, messages, **options)

    def measure(self, model: str, prompt: str, *, max_output_tokens: int = 256) -> ChatResult:
        return self.client.chat(model, prompt, max_output_tokens=max_output_tokens, temperature=0.0)

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """One vector per text, from an embedding model that is loaded."""
        return self.client.embeddings(model, texts)
