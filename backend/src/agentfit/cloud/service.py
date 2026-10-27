"""Cloud models: which providers have a key, which models they offer, and the calls themselves.

``CloudService`` is what the rest of the app talks to. ``HybridRuntime`` puts it behind
the same ``complete`` the engine already calls for LM Studio, so a workflow, a chat or
an investigation can use a cloud model in any slot without knowing it is one.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any

from agentfit.cloud import secrets
from agentfit.cloud.anthropic_wire import AnthropicClient, BlockMemory
from agentfit.cloud.openai_wire import CloudError, OpenAICompatClient, TurnMemory
from agentfit.cloud.providers import (
    PROVIDERS,
    Provider,
    chat_model,
    context_of,
    is_cloud,
    make_key,
    parse_key,
)
from agentfit.logging_setup import get_logger, log_event

log = get_logger("cloud")

# A provider's model list is asked for again after this long.
MODELS_TTL_SECONDS = 3600.0
# A provider whose list is missing is asked for it by ``load_missing`` at most this often.
RETRY_AFTER_SECONDS = 60.0
# The most context a cloud model is given, whatever it supports: each token of it is paid for.
DEFAULT_MAX_CONTEXT = 200_000


def _secret_name(provider_id: str) -> str:
    return f"cloud_{provider_id}"


class CloudService:
    def __init__(self, db: Any = None, *, max_context: Callable[[], int] | None = None,
                 client_factory: Callable[[Provider, str | None], Any] | None = None) -> None:
        self.db = db
        self._max_context = max_context or (lambda: DEFAULT_MAX_CONTEXT)
        self._factory = client_factory
        self._lock = threading.Lock()
        self._clients: dict[str, tuple[str | None, Any]] = {}
        self._models: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        # Why a provider's model list could not be loaded, and when it was last asked for by ``load_missing``.
        self._failures: dict[str, str] = {}
        self._tried: dict[str, float] = {}
        # Shared by every client of a provider, so a turn written before a key change is still recognised.
        self._turns = TurnMemory()
        self._blocks = BlockMemory()

    # -- keys ---------------------------------------------------------------
    def key_source(self, provider: Provider) -> tuple[str | None, str | None]:
        """(the key, where it comes from: "saved" or the name of an environment variable)."""
        try:
            saved = secrets.load(_secret_name(provider.id))
        except secrets.SecretError:
            saved = None
        if saved:
            return saved, "saved"
        for name in provider.env_keys:
            value = os.environ.get(name, "").strip()
            if value:
                return value, name
        return None, None

    def set_key(self, provider_id: str, key: str) -> dict[str, Any]:
        provider = self._provider(provider_id)
        secrets.save(_secret_name(provider.id), key)
        self._forget(provider.id)
        log_event(log, "cloud key saved", provider=provider.id)
        return self.status(provider.id)

    def delete_key(self, provider_id: str) -> dict[str, Any]:
        provider = self._provider(provider_id)
        secrets.delete(_secret_name(provider.id))
        self._forget(provider.id)
        log_event(log, "cloud key removed", provider=provider.id)
        return self.status(provider.id)

    def _forget(self, provider_id: str) -> None:
        with self._lock:
            self._clients.pop(provider_id, None)
            self._models.pop(provider_id, None)
            self._failures.pop(provider_id, None)
            self._tried.pop(provider_id, None)
        if self.db is not None:
            self.db.kv_delete("cloud_models", provider_id)

    @staticmethod
    def _provider(provider_id: str) -> Provider:
        provider = PROVIDERS.get(provider_id)
        if provider is None:
            raise CloudError(f"Unknown provider: {provider_id}")
        return provider

    # -- status -------------------------------------------------------------
    def status(self, provider_id: str) -> dict[str, Any]:
        provider = self._provider(provider_id)
        key, source = self.key_source(provider)
        cached = self._cached_models(provider.id)
        with self._lock:
            failure = self._failures.get(provider.id)
        return {
            "id": provider.id,
            "label": provider.label,
            "configured": bool(key),
            "source": source,
            "hint": f"...{key[-4:]}" if key and len(key) > 8 else None,
            "console_url": provider.console_url,
            "env_keys": list(provider.env_keys),
            "models": cached,
            # Why the list is empty, when the last attempt to load it failed.
            "error": failure,
        }

    def providers(self) -> list[dict[str, Any]]:
        return [self.status(p) for p in PROVIDERS]

    # -- clients ------------------------------------------------------------
    def client(self, provider: Provider) -> Any:
        key, source = self.key_source(provider)
        if source == "ANTHROPIC_AUTH_TOKEN":
            key = None  # a bearer token, not an API key: the SDK reads it from the environment itself
        if not key and provider.wire != "anthropic":
            raise CloudError(f"No API key for {provider.label}. Add one in Settings > Cloud models.")
        with self._lock:
            known = self._clients.get(provider.id)
            if known is not None and known[0] == key:
                return known[1]
            if self._factory is not None:
                made = self._factory(provider, key)
            elif provider.wire == "anthropic":
                made = AnthropicClient(provider, key, memory=self._blocks)
            else:
                made = OpenAICompatClient(provider, key or "", memory=self._turns)
            self._clients[provider.id] = (key, made)
            return made

    # -- models -------------------------------------------------------------
    def _cached_models(self, provider_id: str) -> list[dict[str, Any]]:
        with self._lock:
            entry = self._models.get(provider_id)
        if entry is not None:
            return entry[1]
        if self.db is not None:
            stored = self.db.kv_get("cloud_models", provider_id)
            if isinstance(stored, dict) and isinstance(stored.get("models"), list):
                return stored["models"]
        return []

    def models(self, provider_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        """The models a provider offers this key, best known first. Asked for live, then cached for an hour."""
        provider = self._provider(provider_id)
        with self._lock:
            entry = self._models.get(provider.id)
        if entry is not None and not refresh and time.time() - entry[0] < MODELS_TTL_SECONDS:
            return entry[1]
        client = self.client(provider)
        try:
            listed = client.list_models()
        except CloudError as exc:
            self._remember_failure(provider, str(exc), exc.status)
            raise
        with self._lock:
            self._failures.pop(provider.id, None)
        rows: dict[str, dict[str, Any]] = {}
        for row in listed:
            model_id = row["id"]
            if not chat_model(provider, model_id):
                continue
            rows[model_id] = {
                "id": model_id,
                "key": make_key(provider.id, model_id),
                "label": row.get("label") or model_id,
                "context": context_of(provider, model_id, row.get("context")),
                "recommended": model_id in provider.preferred,
            }
        order = {m: i for i, m in enumerate(provider.preferred)}
        models = sorted(rows.values(), key=lambda m: (order.get(m["id"], len(order)), m["id"]))
        with self._lock:
            self._models[provider.id] = (time.time(), models)
        if self.db is not None:
            self.db.kv_set("cloud_models", provider.id, {"at": time.time(), "models": models})
        return models

    def _remember_failure(self, provider: Provider, message: str, status: int | None = None) -> None:
        with self._lock:
            self._failures[provider.id] = message
        log_event(log, "cloud models not loaded", provider=provider.id, status=status, error=message)

    def load_missing(self) -> None:
        """Ask for the models of every provider that has a key and no list yet.

        A key saved while the provider could not be reached, or whose first test failed, would
        otherwise leave that provider out of every picker until someone pressed refresh. A
        provider is asked at most once per ``RETRY_AFTER_SECONDS``, so a page that asks often
        does not hammer one that refuses; the reason it failed is in its ``status``.
        """
        for provider in PROVIDERS.values():
            if not self.key_source(provider)[0] or self._cached_models(provider.id):
                continue
            with self._lock:
                last = self._tried.get(provider.id)
                if last is not None and time.time() - last < RETRY_AFTER_SECONDS:
                    continue
                self._tried[provider.id] = time.time()
            try:
                self.models(provider.id)
            except CloudError:
                pass  # models() recorded and logged it
            except Exception as exc:  # a status page must not fail because one provider answered oddly
                self._remember_failure(provider, f"{provider.label}: {type(exc).__name__}: {exc}")

    def available(self) -> list[dict[str, Any]]:
        """Every model of every provider that has a key, from the last list each provider gave."""
        out = []
        for provider in PROVIDERS.values():
            if not self.key_source(provider)[0] and provider.wire != "anthropic":
                continue
            for model in self._cached_models(provider.id):
                out.append({**model, "provider": provider.id, "provider_label": provider.label, "vision": provider.vision})
        return out

    def test(self, provider_id: str) -> dict[str, Any]:
        """Ask the provider for its models: it proves the key and fills the list in one call."""
        models = self.models(provider_id, refresh=True)
        return {**self.status(provider_id), "ok": True, "count": len(models)}

    # -- inference ----------------------------------------------------------
    def context_length(self, model_key: str) -> int:
        provider, model = parse_key(model_key)
        listed = next((m.get("context") for m in self._cached_models(provider.id) if m["id"] == model), None)
        return max(8192, min(context_of(provider, model, listed), int(self._max_context())))

    def complete(self, model_key: str, messages: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        provider, model = parse_key(model_key)
        options.pop("temperature", None)
        # A provider draws its answers its own way: the seed, top-p and the rest are for local models.
        options.pop("sampling", None)
        return self.client(provider).complete(model, messages, **options)


class HybridRuntime:
    """LM Studio for local models, the cloud for ``cloud:`` keys. Everything else is LM Studio's."""

    supports_streaming = True

    def __init__(self, local: Any, cloud: CloudService) -> None:
        self.local = local
        self.cloud = cloud

    @property
    def modalities(self) -> Any:
        return self.local.modalities

    def supports(self, modality: str) -> bool:
        return self.local.supports(modality)

    def complete(self, model: str, messages: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        if is_cloud(model):
            return self.cloud.complete(model, messages, **options)
        return self.local.complete(model, messages, **options)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.local, name)
