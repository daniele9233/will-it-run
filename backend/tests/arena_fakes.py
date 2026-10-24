"""A runtime for the Arena's tests: it answers every question of the bank the way a model that always right, or always wrong,
or somewhere in between would, writes its answer a few characters at a time like a real server, and keeps a log of what it was asked."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any

from agentfit.arena.bank import kit
from agentfit.arena.model import Question
from agentfit.arena.suite import build_suite
from agentfit.arena.toolprobe import Tools
from agentfit.lmstudio.models import InstalledModel, LMStudioInstall, LoadedInstance, LoadResult
from agentfit.runtime import ModelRuntime

# Nothing on the PC running the tests may decide an answer.
NO_TOOLS = Tools(overrides={name: None for name in ("python", "node", "terraform", "ansible_lint", "psql")})


def installed(key: str, *, vision: bool = False, tool_use: bool = True, reasoning: bool = False, name: str | None = None, quant: str = "Q4_K_M",
              size_bytes: int = 4_000_000_000) -> InstalledModel:
    return InstalledModel(model_key=key, display_name=name or key, vision=vision, tool_use=tool_use, reasoning=reasoning, params_string="7B", quant=quant,
                          size_bytes=size_bytes, max_context_length=32768)


def response(entry: str, finish: str = "stop", *, reasoning: str = "", tokens: int = 30) -> dict[str, Any]:
    """What a server returns for an entry of the bank (a reference or a wrong answer, text or tool calls)."""
    answer = kit.scripted(entry)
    message: dict[str, Any] = {"role": "assistant", "content": answer.text}
    if reasoning:
        message["reasoning_content"] = reasoning
    if answer.tool_calls:
        message["tool_calls"] = [{"id": f"call_{i}", "type": "function", "function": {"name": c.name, "arguments": c.raw}} for i, c in enumerate(answer.tool_calls)]
    return {"choices": [{"message": message, "finish_reason": finish}], "usage": {"prompt_tokens": 50, "completion_tokens": tokens}}


class ArenaRuntime(ModelRuntime):
    """``behaviour`` maps a model key to what it does with a question: ``right`` (the reference), ``wrong`` (a wrong answer), or a
    function ``(question, call number) -> response | Exception``. The default is ``right``."""

    name = "arena-fake"
    modalities = frozenset({"text", "vision"})
    supports_streaming = True

    def __init__(self, behaviour: dict[str, Any] | None = None, *, seed: int = 42, preset: str = "full") -> None:
        self.behaviour = behaviour or {}
        self.questions = {json.dumps(q.chat_messages(), sort_keys=True, ensure_ascii=False): q for q in build_suite(seed, preset).questions}
        self.loaded: dict[str, str] = {}
        self.contexts: dict[str, int | None] = {}
        self.calls: list[dict[str, Any]] = []
        self.load_log: list[str] = []
        self.unload_log: list[str] = []
        self.most_loaded = 0
        self.fail_load: set[str] = set()
        self.models: list[InstalledModel] = []
        self.foreign: dict[str, str] = {}
        # A model that has to wait here before answering its Nth question: a test stops the run at that point.
        self.hold_at: int | None = None
        self.release = threading.Event()
        self.holding = threading.Event()
        self.cloud = SimpleNamespace(context_length=lambda key: 100_000)
        self._lock = threading.Lock()

    # -- the part of the runtime the gateway uses ---------------------------------------
    def is_available(self) -> bool:
        return True

    def ensure_server(self) -> bool:
        return True

    def status(self, deep: bool = False) -> LMStudioInstall:
        return LMStudioInstall(installed=True, server_running=True, api_reachable=True, server_port=1234, cli_version="0.4.0")

    def list_installed(self) -> list[InstalledModel]:
        return self.models

    def list_loaded(self) -> list[LoadedInstance]:
        return [LoadedInstance(instance_id=i, model_key=k, context_length=self.contexts.get(k)) for k, i in {**self.foreign, **self.loaded}.items()]

    def load(self, model_key: str, *, context_length: int | None = None, gpu: str | None = None, ttl_seconds: int | None = None) -> LoadResult:
        if model_key in self.fail_load:
            raise RuntimeError(f"out of memory loading {model_key}")
        self.load_log.append(model_key)
        self.loaded[model_key] = model_key
        self.contexts[model_key] = context_length
        self.most_loaded = max(self.most_loaded, len(self.loaded) + len(self.foreign))
        return LoadResult(instance_id=model_key, model_key=model_key, load_time_seconds=0.01, load_config={"context_length": context_length} if context_length else None)

    def unload(self, instance_id: str) -> None:
        self.unload_log.append(instance_id)
        self.loaded = {k: v for k, v in self.loaded.items() if v != instance_id}
        self.foreign = {k: v for k, v in self.foreign.items() if v != instance_id}

    def measure(self, model: str, prompt: str, *, max_output_tokens: int = 256) -> Any:
        raise NotImplementedError

    def estimate(self, model_key: str, context_length: int | None = None) -> None:
        return None

    # -- the answers ----------------------------------------------------------------
    def question_of(self, messages: list[dict[str, Any]]) -> Question:
        return self.questions[json.dumps(messages, sort_keys=True, ensure_ascii=False)]

    def complete(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        question = self.question_of(messages)
        with self._lock:
            self.calls.append({"model": model, "question": question.id, "messages": messages, "kwargs": kwargs})
            number = sum(1 for c in self.calls if c["model"] == model)
        if self.hold_at is not None and len(self.calls) == self.hold_at:
            self.holding.set()
            self.release.wait(20)
        how = self.behaviour.get(model, "right")
        if callable(how):
            how = how(question, number)
        if isinstance(how, Exception):
            raise how
        if isinstance(how, dict):
            reply = how
        elif how == "wrong":
            reply = response(question.wrong[0] if question.wrong else "No lo so.")
        else:
            reply = response(question.reference)
        text = reply["choices"][0]["message"].get("content") or ""
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            # Like a server: the answer arrives a piece at a time, and the caller can stop it in between.
            for start in range(0, max(len(text), 1), 40):
                on_delta("content", text[start:start + 40])
        return reply
