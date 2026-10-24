"""Model runtime abstraction.

``LMStudioRuntime`` is the only implementation in version 1. The interface exists so
other runtimes (for example a ComfyUI runtime for image and video generation) can be
added without touching the team builder or the workflow engine. A runtime declares the
modalities it can actually serve; Will It Run never claims a capability a runtime lacks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Only for annotations: importing the package here would pull in its runtime, which
    # needs this module first.
    from agentfit.lmstudio.models import ChatResult, InstalledModel, LoadedInstance, LoadResult


class RuntimeUnavailable(Exception):
    """The runtime is not installed or not reachable."""


class ModelRuntime(ABC):
    #: Stable identifier, e.g. "lmstudio".
    name: str = "runtime"
    #: Modalities this runtime can serve: "text", "vision", "embedding", "image", "video".
    modalities: frozenset[str] = frozenset()

    #: True when ``complete`` accepts ``on_delta`` and reports text as it is generated.
    supports_streaming: bool = False

    def supports(self, modality: str) -> bool:
        return modality in self.modalities

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def list_installed(self) -> list[InstalledModel]: ...

    @abstractmethod
    def list_loaded(self) -> list[LoadedInstance]: ...

    @abstractmethod
    def load(
        self,
        model_key: str,
        *,
        context_length: int | None = None,
        gpu: str | None = None,
        ttl_seconds: int | None = None,
    ) -> LoadResult:
        """Load a model. ``ttl_seconds`` asks the runtime to unload it after that long idle, where supported."""

    @abstractmethod
    def unload(self, instance_id: str) -> None: ...

    @abstractmethod
    def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """OpenAI-style chat completion response."""

    @abstractmethod
    def measure(self, model: str, prompt: str, *, max_output_tokens: int) -> ChatResult:
        """Run a prompt and return the runtime's own timing statistics."""


UNSUPPORTED_MODALITY_MESSAGE = {
    "image": (
        "Image generation models (Stable Diffusion, FLUX and similar) do not run in LM Studio. "
        "Will It Run can build the prompt-writing and critique agents now; the image generator "
        "itself needs a separate runtime such as ComfyUI, which is not included in this version."
    ),
    "video": (
        "Video generation models do not run in LM Studio. Will It Run can build the prompt and "
        "review agents now; generation needs a separate runtime such as ComfyUI, which is not "
        "included in this version."
    ),
}
