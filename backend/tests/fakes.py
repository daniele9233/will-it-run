"""Test doubles shared by the workflow and team tests."""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentfit.lmstudio.models import ChatResult, ChatStats, InstalledModel, LoadedInstance, LoadResult
from agentfit.runtime import ModelRuntime

Responder = Callable[[list[dict[str, Any]], dict[str, Any]], Any]


def write_gguf(path: Path, *, mtp_layers: int | None = None, version: int = 3) -> Path:
    """A GGUF file with only a header and metadata (what Will It Run reads), with or without an MTP head.

    The metadata has what a real Qwen3.8 file has around the key that matters: strings, numbers and the long arrays of the
    tokenizer, so the reader has to skip them.
    """

    def text(value: str) -> bytes:
        raw = value.encode("utf-8")
        return struct.pack("<Q", len(raw)) + raw

    def entry(key: str, kind: int, payload: bytes) -> bytes:
        return text(key) + struct.pack("<I", kind) + payload

    items = [
        entry("general.architecture", 8, text("qwen35")),
        entry("qwen35.block_count", 4, struct.pack("<I", 65 if mtp_layers else 64)),
        entry("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, 3) + text("a") + text("bb") + text("ccc")),
        entry("tokenizer.ggml.token_type", 9, struct.pack("<IQ", 5, 4) + struct.pack("<4i", 1, 1, 3, 1)),
        entry("general.quantization_version", 4, struct.pack("<I", 2)),
    ]
    if mtp_layers is not None:
        items.insert(2, entry("qwen35.nextn_predict_layers", 4, struct.pack("<I", mtp_layers)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", version, 0, len(items)) + b"".join(items))
    return path


def tool_call(name: str, **arguments: Any) -> dict[str, Any]:
    """A scripted assistant turn that calls one tool."""
    return {"tool_calls": [{"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class Truncated:
    """A scripted reply where the model used its whole output budget on reasoning."""


class ContextFull:
    """A scripted reply cut short by the context filling up, a few dozen tokens in.

    The prompt is counted at ``chars_per_token`` characters per token, as code is by a real
    tokenizer, so it is larger than the runtime estimated.
    """

    def __init__(self, chars_per_token: float = 2.0) -> None:
        self.chars_per_token = chars_per_token


class CutOffCall:
    """A scripted turn that ran out of output tokens in the middle of a tool call.

    ``arguments`` is what had been written when the limit was reached: JSON without its end.
    With ``chars_per_token`` the limit was the context, not the budget: the prompt is counted
    at that many characters per token and only a few dozen tokens were written.
    """

    def __init__(self, name: str, arguments: str, call_id: str = "", chars_per_token: float | None = None) -> None:
        self.name = name
        self.arguments = arguments
        self.call_id = call_id
        self.chars_per_token = chars_per_token


def _prompt_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) + (len(json.dumps(m["tool_calls"])) if m.get("tool_calls") else 0) for m in messages)


class BrokenConversation(Exception):
    """What LM Studio does when an earlier tool call in the conversation has arguments that are not JSON."""

    def __init__(self) -> None:
        super().__init__("HTTP 500 Internal Server Error")


def check_conversation(messages: list[dict[str, Any]]) -> None:
    """Refuse what the real server refuses: it parses the arguments of every earlier tool call again."""
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    for message in messages:
        for call in message.get("tool_calls") or []:
            try:
                arguments = json.loads(call["function"]["arguments"])
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                raise BrokenConversation() from exc
            if not isinstance(arguments, dict) or not call.get("id") or call["id"] not in answered:
                raise BrokenConversation()


def auto_reply(messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build the smallest object that satisfies the requested JSON schema."""
    schema = ((kwargs.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
    fill = {"string": "ok", "boolean": True, "integer": 0, "number": 0, "array": [], "object": {}}
    properties = schema.get("properties") or {}
    return {name: fill.get(properties.get(name, {}).get("type", "string"), "ok") for name in schema.get("required") or ["summary"]}


class FakeRuntime(ModelRuntime):
    """Scripted runtime. ``script`` maps a role name to a list of replies, consumed in order.

    A reply is a dict (returned as JSON content), a ``tool_call(...)`` turn, a string
    (raw content), an Exception (raised), or a callable receiving (messages, kwargs).
    The last reply repeats once the list is exhausted.
    """

    name = "fake"
    modalities = frozenset({"text"})

    def __init__(self, script: dict[str, list[Any]] | None = None, *, fail_load: set[str] | None = None) -> None:
        self.script = {role: list(replies) for role, replies in (script or {}).items()}
        self.fail_load = fail_load or set()
        self.loaded: dict[str, str] = {}
        self.contexts: dict[str, int | None] = {}
        self.load_calls: list[str] = []
        self.load_options: list[dict[str, Any]] = []
        self.unload_calls: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.installed: list[InstalledModel] = []

    def is_available(self) -> bool:
        return True

    def list_installed(self) -> list[InstalledModel]:
        return self.installed

    def list_loaded(self) -> list[LoadedInstance]:
        return [LoadedInstance(instance_id=i, model_key=k, context_length=self.contexts.get(k)) for k, i in self.loaded.items()]

    def load(
        self,
        model_key: str,
        *,
        context_length: int | None = None,
        gpu: str | None = None,
        ttl_seconds: int | None = None,
    ) -> LoadResult:
        self.load_calls.append(model_key)
        self.load_options.append({"model": model_key, "context_length": context_length, "ttl_seconds": ttl_seconds})
        if model_key in self.fail_load:
            raise RuntimeError(f"out of memory loading {model_key}")
        if model_key in self.loaded:
            return LoadResult(instance_id=self.loaded[model_key], model_key=model_key, already_loaded=True)
        self.loaded[model_key] = model_key
        self.contexts[model_key] = context_length
        return LoadResult(
            instance_id=model_key, model_key=model_key, load_time_seconds=0.01,
            load_config={"context_length": context_length} if context_length else None,
        )

    def unload(self, instance_id: str) -> None:
        self.unload_calls.append(instance_id)
        self.loaded = {k: v for k, v in self.loaded.items() if v != instance_id}
        self.contexts = {k: v for k, v in self.contexts.items() if k in self.loaded}

    @staticmethod
    def _role(messages: list[dict[str, Any]]) -> str:
        first = messages[0]["content"].splitlines()[0]
        return first.removeprefix("# Role:").strip()

    def complete(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        role = self._role(messages)
        self.calls.append({"model": model, "role": role, "messages": [dict(m) for m in messages], "kwargs": kwargs})
        check_conversation(messages)
        replies = self.script.get(role) or self.script.get("*") or [{"summary": f"{role} done"}]
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if callable(reply) and not isinstance(reply, Exception):
            reply = reply(messages, kwargs)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, CutOffCall):
            call = {"id": reply.call_id, "type": "function", "function": {"name": reply.name, "arguments": reply.arguments}}
            usage = {"prompt_tokens": 50, "completion_tokens": int(kwargs.get("max_tokens") or 1024)}
            if reply.chars_per_token:
                usage = {"prompt_tokens": int(_prompt_chars(messages) / reply.chars_per_token), "completion_tokens": 40}
            return {
                "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "", "tool_calls": [call]}}],
                "usage": usage,
            }
        if isinstance(reply, Truncated):
            return {
                "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "", "reasoning_content": "thinking..."}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": int(kwargs.get("max_tokens") or 4096), "completion_tokens_details": {"reasoning_tokens": 4096}},
            }
        if isinstance(reply, ContextFull):
            return {
                "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "", "reasoning_content": "The user wants"}}],
                "usage": {"prompt_tokens": int(_prompt_chars(messages) / reply.chars_per_token), "completion_tokens": 40},
            }
        if isinstance(reply, dict) and "tool_calls" in reply:
            # Tool turns are only meaningful while tools are offered; otherwise fall through.
            if kwargs.get("tools"):
                message: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": reply["tool_calls"]}
            else:
                message = {"role": "assistant", "content": json.dumps({"summary": f"{role} done"})}
        elif isinstance(reply, str):
            message = {"role": "assistant", "content": reply}
        else:
            message = {"role": "assistant", "content": json.dumps(reply)}
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 50, "completion_tokens": 20}}

    def measure(self, model: str, prompt: str, *, max_output_tokens: int = 256) -> ChatResult:
        return ChatResult(
            text="ok",
            stats=ChatStats(input_tokens=100, output_tokens=max_output_tokens, tokens_per_second=42.0, time_to_first_token_seconds=0.2),
        )
