"""Embeddings: the vectors semantic search compares.

They come from an embedding model in LM Studio, the same runtime that serves the chat
models. LM Studio ships with one (``text-embedding-nomic-embed-text-v1.5``, 84 MB), so
semantic search normally works with nothing to download.

The embedding model is loaded with ``lms load --gpu off``, so its weights stay in system
RAM. That does not keep it off the graphics card: measured on 2026-10-14 on an RTX 4080
SUPER, loading it that way took 457 MiB of VRAM (954 MiB used before, 1411 after),
which is what LM Studio's engine process holds on the card whatever the weights do. On
a card a 14 GB model nearly fills, that is enough to push the model into system RAM.

So the embedding model is loaded only when the card has that much to spare
(``free_vram_mib``), it unloads itself after a few idle minutes, and the model gateway
unloads it first when a run's model needs the room. When it cannot be loaded the brain
searches by words and through the graph, and says so.

Without an embedding model, or with LM Studio closed, there are no vectors: the brain
says so and searches by keywords and by the graph alone.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from agentfit.brain.models import EmbedStatus
from agentfit.logging_setup import get_logger

log = get_logger("brain")

# Characters of a text that are embedded. Embedding models read about 2000 tokens at most.
MAX_CHARS = 1500
BATCH = 16
# After a failure the embedder is left alone this long, so a run never waits on it twice.
RETRY_SECONDS = 60.0
# LM Studio unloads the embedding model after this long idle, so it does not hold VRAM a run will want.
IDLE_TTL_SECONDS = 300
# An 84 MB model loads in a few seconds. Past this, the load is given up on: an agent that is
# starting waits for its memory, and must not wait on a command that hangs.
LOAD_DEADLINE_SECONDS = 90.0
# What loading the embedding model takes on the graphics card (457 MiB measured), and what must
# stay free beside it, like the gateway's own margin. Less free VRAM than the sum: it is not loaded.
EMBEDDER_VRAM_MIB = 500
MIN_FREE_MIB_TO_LOAD = EMBEDDER_VRAM_MIB + 600
# Preferred when several embedding models are installed: the multilingual ones first.
_PREFERENCE = ("bge-m3", "multilingual", "embeddinggemma", "qwen3-embedding", "jina-embeddings-v3", "nomic", "bge", "e5", "gte")


def within(seconds: float, call: Callable[[], Any]) -> Any:
    """The result of ``call``, or TimeoutError when it has not come after ``seconds``.

    The call is left to finish by itself: a load that completes late is found loaded next time.
    """
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["result"] = call()
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True, name="embedding-load")
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"it did not load within {seconds:.0f} seconds")
    if "error" in box:
        raise box["error"]
    return box.get("result")


class Embedder(Protocol):
    def status(self) -> EmbedStatus: ...

    def embed(self, texts: list[str], *, query: bool = False) -> tuple[str, list[list[float]]] | None:
        """(model, one vector per text), or None when embedding is not possible right now."""


class NoEmbedder:
    """Semantic search switched off: keyword and graph search only."""

    def __init__(self, reason: str = "Semantic search is turned off.") -> None:
        self._reason = reason

    def status(self) -> EmbedStatus:
        return EmbedStatus(available=False, reason=self._reason, code="off")

    def embed(self, texts: list[str], *, query: bool = False) -> tuple[str, list[list[float]]] | None:
        return None


def prefixes(model: str) -> tuple[str, str]:
    """(prefix for a stored text, prefix for a question) that the model was trained with."""
    name = model.lower()
    if "nomic" in name:
        return "search_document: ", "search_query: "
    if "e5" in name:
        return "passage: ", "query: "
    return "", ""


def choose_model(keys: list[str], wanted: str = "") -> str | None:
    """The embedding model to use: the one asked for when it is installed, else the best known one."""
    if wanted and wanted in keys:
        return wanted
    for hint in _PREFERENCE:
        match = next((k for k in keys if hint in k.lower()), None)
        if match:
            return match
    return keys[0] if keys else None


class RuntimeEmbedder:
    """Embeds through the model runtime (LM Studio)."""

    def __init__(
        self, runtime: Callable[[], Any], wanted: Callable[[], str] = lambda: "", free_vram_mib: Callable[[], float | None] | None = None,
        load_deadline: float = LOAD_DEADLINE_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._wanted = wanted
        self._load_deadline = load_deadline
        # Why the last search went without vectors although a model is installed, or "".
        self.last_skip = ""
        # Free graphics memory right now, or None when it cannot be read. Only asked before a load that would use the card.
        self._free_vram_mib = free_vram_mib
        self._lock = threading.Lock()
        self._down_until = 0.0
        self._reason = ""
        self._installed: tuple[float, list[str]] = (0.0, [])
        # Model key -> what to name in a request: the instance LM Studio loaded.
        self._instance: dict[str, str] = {}

    # -- which model ----------------------------------------------------------
    def _embedding_models(self, runtime: Any) -> list[str]:
        checked, keys = self._installed
        if keys and time.monotonic() - checked < 120:
            return keys
        keys = [m.model_key for m in runtime.list_installed() if m.type == "embedding"]
        self._installed = (time.monotonic(), keys)
        return keys

    def _model(self, runtime: Any) -> str | None:
        return choose_model(self._embedding_models(runtime), self._wanted())

    def status(self) -> EmbedStatus:
        runtime = self._runtime()
        if getattr(runtime, "embed", None) is None:
            return EmbedStatus(reason="This model runtime has no embeddings.", code="no_runtime")
        try:
            model = self._model(runtime)
        except Exception:
            return EmbedStatus(reason="LM Studio is not reachable, so memories are searched by keywords only.", code="offline")
        if model is None:
            return EmbedStatus(reason="No embedding model is installed in LM Studio, so memories are searched by keywords only.", code="no_model")
        if time.monotonic() < self._down_until:
            return EmbedStatus(model=model, reason=self._reason, code="failed")
        return EmbedStatus(available=True, model=model)

    # -- loading --------------------------------------------------------------
    def _ensure_loaded(self, runtime: Any, model: str) -> str | None:
        """The instance to call, loading the model if the graphics card has room. None when it has not."""
        for instance in runtime.list_loaded():
            if instance.model_key == model:
                self._instance[model] = instance.instance_id
                return instance.instance_id
        free = self._free_vram_mib() if self._free_vram_mib is not None else None
        if free is not None and free < MIN_FREE_MIB_TO_LOAD:
            # Not a failure: the card belongs to the model that is working. Try again next time.
            self.last_skip = (
                f"The embedding model was not loaded: it takes about {EMBEDDER_VRAM_MIB} MiB of graphics memory and only "
                f"{free:.0f} MiB are free, which the working model needs."
            )
            return None
        try:
            # Weights in system RAM: see the module note.
            result = within(self._load_deadline, lambda: runtime.load(model, gpu="off", ttl_seconds=IDLE_TTL_SECONDS))
        except TimeoutError:
            raise  # the command hangs: trying another way of loading would only wait again
        except Exception as exc:
            log.info("embedding model %s did not load with its weights in RAM (%s); loading it with the runtime's defaults", model, exc)
            result = within(self._load_deadline, lambda: runtime.load(model))
        self._instance[model] = result.instance_id
        return result.instance_id

    # -- embedding ------------------------------------------------------------
    def embed(self, texts: list[str], *, query: bool = False) -> tuple[str, list[list[float]]] | None:
        if not texts or time.monotonic() < self._down_until:
            return None
        runtime = self._runtime()
        call = getattr(runtime, "embed", None)
        if call is None:
            return None
        with self._lock:
            try:
                model = self._model(runtime)
                if model is None:
                    return None
                self.last_skip = ""
                instance = self._ensure_loaded(runtime, model)
                if instance is None:
                    return None
                stored, asked = prefixes(model)
                prefix = asked if query else stored
                vectors: list[list[float]] = []
                for start in range(0, len(texts), BATCH):
                    batch = [prefix + text[:MAX_CHARS] for text in texts[start : start + BATCH]]
                    vectors.extend(call(instance, batch))
                if len(vectors) != len(texts):
                    raise ValueError(f"asked for {len(texts)} vectors, got {len(vectors)}")
            except Exception as exc:
                self._down_until = time.monotonic() + RETRY_SECONDS
                self._reason = f"The embedding model did not answer ({exc}), so memories are searched by keywords only for now."
                self._instance.clear()
                log.warning("embedding failed: %s", exc)
                return None
            self._reason = ""
            return model, vectors
