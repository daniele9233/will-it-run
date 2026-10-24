"""Claude through Anthropic's SDK, spoken to the engine in the chat-completions dialect.

The engine builds OpenAI-shaped conversations (``system``/``user``/``assistant`` with
``tool_calls``/``tool``) and reads OpenAI-shaped answers. This module translates both ways
and keeps what the translation would otherwise lose:

- **Thinking.** Current Claude models think before they answer (adaptive thinking, always
  on for Claude Opus 5.5) and the thinking blocks of a tool loop have to be sent back
  unchanged. The engine keeps only text and tool calls, so the blocks of each assistant
  turn are remembered here and replayed verbatim, keyed by the turn's tool-use ids.
- **Edited history.** The engine shortens old tool results when a conversation outgrows
  its context. That edit invalidates the thinking that came after it; the request asks
  the API to drop such blocks instead of refusing it (``prefix_mismatch_behavior``).
- **Refusals.** A refused turn becomes an answer that says so, with no tool calls to run.
  The server-side fallback is on for the models that accept it: a refused request is
  answered by the model Anthropic routes it to, inside the same call.

Thinking is streamed as a readable summary (``display: "summarized"``), which is what the
session view shows as the model's reasoning.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import anthropic

from agentfit.cloud.openai_wire import CloudError, explain
from agentfit.cloud.providers import Provider

# Models that take adaptive thinking, an effort level and summarized thinking.
_ADAPTIVE = ("claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-5",
             "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")
# Models that accept the server-side refusal fallback in its "default" form.
_FALLBACK = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
_BINDING_BETA = "thinking-binding-controls-2026-08-01"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# A turn of an agent that thinks first needs room for the thinking as well as the answer.
MIN_MAX_TOKENS = 32_000
# Model-internal blocks that are echoed back as they came.
_ECHOED = {"thinking", "redacted_thinking", "text", "tool_use", "server_tool_use", "web_search_tool_result", "fallback"}


def adaptive(model: str) -> bool:
    return model.startswith(_ADAPTIVE)


def _fingerprint_openai(message: dict[str, Any]) -> str:
    calls = message.get("tool_calls") or []
    if calls:
        return "calls:" + "|".join(str(c.get("id") or "") for c in calls)
    return "text:" + hashlib.sha256(str(message.get("content") or "").encode("utf-8")).hexdigest()


class BlockMemory:
    """The content blocks of each assistant turn, as Claude wrote them."""

    def __init__(self, size: int = 2000) -> None:
        self._lock = threading.Lock()
        self._turns: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._size = size

    def remember(self, openai_message: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
        key = _fingerprint_openai(openai_message)
        with self._lock:
            self._turns[key] = blocks
            self._turns.move_to_end(key)
            while len(self._turns) > self._size:
                self._turns.popitem(last=False)

    def blocks(self, openai_message: dict[str, Any]) -> list[dict[str, Any]] | None:
        with self._lock:
            return self._turns.get(_fingerprint_openai(openai_message))


def echo_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The blocks of a turn as they go back. After a fallback mid-answer, what the declining
    model started (thinking, tool calls) is left out; its text and what follows stay."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    out = []
    for index, block in enumerate(blocks):
        kind = block.get("type")
        if kind not in _ECHOED or kind == "fallback":
            continue
        if index < last and kind in ("thinking", "redacted_thinking", "tool_use", "server_tool_use"):
            continue
        out.append(block)
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


_DATA_URL = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)


def _user_parts(content: Any) -> list[dict[str, Any]]:
    """The blocks of a user message: its text, and its pictures (``image_url`` data addresses) as Anthropic images."""
    if not isinstance(content, list):
        text = _text_of(content)
        return [{"type": "text", "text": text}] if text.strip() else []
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image_url":
            url = str((part.get("image_url") or {}).get("url") or "")
            match = _DATA_URL.match(url)
            if match:
                parts.append({"type": "image", "source": {"type": "base64", "media_type": match.group(1).lower(), "data": match.group(2)}})
        elif str(part.get("text") or "").strip():
            parts.append({"type": "text", "text": str(part["text"])})
    return parts


def to_anthropic(messages: list[dict[str, Any]], memory: BlockMemory) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI-shaped messages -> (system prompt, Anthropic messages)."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    def user_blocks() -> list[dict[str, Any]]:
        if not out or out[-1]["role"] != "user":
            out.append({"role": "user", "content": []})
        return out[-1]["content"]

    for message in messages:
        role = message.get("role")
        if role == "system":
            text = _text_of(message.get("content")).strip()
            if not out:
                if text:
                    system_parts.append(text)
            elif text:
                user_blocks().append({"type": "text", "text": text})
        elif role == "user":
            user_blocks().extend(_user_parts(message.get("content")))
        elif role == "tool":
            result = {"type": "tool_result", "tool_use_id": str(message.get("tool_call_id") or ""),
                      "content": _text_of(message.get("content")) or "(no output)"}
            if result["content"].startswith(("ERROR", "DENIED")):
                result["is_error"] = True
            blocks = user_blocks()
            # Tool results come first in their user turn.
            position = sum(1 for b in blocks if b.get("type") == "tool_result")
            blocks.insert(position, result)
        elif role == "assistant":
            kept = memory.blocks(message)
            if kept is not None:
                blocks = echo_blocks(kept)
            else:
                blocks = []
                text = _text_of(message.get("content")).strip()
                if text:
                    blocks.append({"type": "text", "text": text})
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    blocks.append({"type": "tool_use", "id": str(call.get("id") or ""), "name": str(function.get("name") or ""),
                                   "input": arguments if isinstance(arguments, dict) else {}})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "Begin."}]})
    return "\n\n".join(system_parts), out


def to_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    converted = []
    for tool in tools or []:
        function = tool.get("function") or {}
        converted.append({
            "name": function.get("name"),
            "description": function.get("description") or "",
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            # Inputs arrive as they are written; the engine checks them before running anything.
            "eager_input_streaming": True,
        })
    return converted


def _strip_thinking(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**m, "content": [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")]}
        if m["role"] == "assistant" else m
        for m in messages
    ]


class AnthropicClient:
    def __init__(self, provider: Provider, api_key: str | None, client: anthropic.Anthropic | None = None,
                 memory: BlockMemory | None = None) -> None:
        self.provider = provider
        # No key: the SDK finds one itself (ANTHROPIC_API_KEY, or an `ant auth login` profile).
        self._client = client or (anthropic.Anthropic(api_key=api_key, timeout=900.0) if api_key else anthropic.Anthropic(timeout=900.0))
        self.memory = memory or BlockMemory()

    def _error(self, exc: Exception) -> CloudError:
        if isinstance(exc, anthropic.APIStatusError):
            message = getattr(exc, "message", None) or str(exc)
            return CloudError(explain(self.provider, exc.status_code, str(message)[:400]), exc.status_code)
        if isinstance(exc, anthropic.APIConnectionError):
            return CloudError(explain(self.provider, None, f"the connection failed ({exc})"))
        return CloudError(explain(self.provider, None, str(exc)))

    def list_models(self) -> list[dict[str, Any]]:
        try:
            page = self._client.models.list(limit=100)
            return [
                {"id": m.id, "label": getattr(m, "display_name", None) or m.id, "context": getattr(m, "max_input_tokens", None)}
                for m in page
            ]
        except anthropic.AnthropicError as exc:
            raise self._error(exc) from exc

    def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        # ``response_format``: the JSON shape is described in the prompt and checked by the engine.
        system, converted = to_anthropic(messages, self.memory)
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max(int(max_tokens or 0), MIN_MAX_TOKENS),
            "messages": converted,
            # The longest stable prefix (tools, system, earlier turns) is cached between the turns of a loop.
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = to_tools(tools)
        betas: list[str] = []
        if adaptive(model):
            params["thinking"] = {"type": "adaptive", "display": "summarized",
                                  "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
            params["output_config"] = {"effort": "high"}
            betas.append(_BINDING_BETA)
        if model.startswith(_FALLBACK) and model in _FALLBACK:
            params["fallbacks"] = "default"
            betas.append(_FALLBACK_BETA)
        if betas:
            params["betas"] = betas
        tries = 0
        while True:
            try:
                return self._stream(params, on_delta)
            except anthropic.BadRequestError as exc:
                text = str(getattr(exc, "message", "") or exc).lower()
                tries += 1
                if tries > 2:
                    raise self._error(exc) from exc
                if "fallback" in text and "fallbacks" in params:
                    params.pop("fallbacks")
                    params["betas"] = [b for b in params.get("betas", []) if b != _FALLBACK_BETA]
                elif ("thinking" in text or "signature" in text or "block_binding" in text) and "thinking" in params:
                    # The one-time recovery the API documents: replay the turns without their thinking.
                    params["messages"] = _strip_thinking(params["messages"])
                    params["thinking"] = {k: v for k, v in params["thinking"].items() if k != "block_binding"}
                    params["betas"] = [b for b in params.get("betas", []) if b != _BINDING_BETA]
                else:
                    raise self._error(exc) from exc
                if not params.get("betas"):
                    params.pop("betas", None)
            except ValueError as exc:
                # Tool input the SDK could not parse at all: the turn is asked for again.
                tries += 1
                if tries > 2:
                    raise CloudError(explain(self.provider, None, f"unreadable tool call ({exc})")) from exc
            except anthropic.AnthropicError as exc:
                raise self._error(exc) from exc

    def _stream(self, params: dict[str, Any], on_delta: Callable[[str, str], None] | None) -> dict[str, Any]:
        with self._client.beta.messages.stream(**params) as stream:
            for event in stream:
                if event.type == "content_block_delta" and on_delta is not None:
                    delta = event.delta
                    if delta.type == "thinking_delta" and delta.thinking:
                        on_delta("reasoning", delta.thinking)
                    elif delta.type == "text_delta" and delta.text:
                        on_delta("content", delta.text)
            final = stream.get_final_message()
        blocks = [block.model_dump(mode="json", exclude_none=True) for block in final.content]
        return self._to_openai(final, blocks)

    def _to_openai(self, final: Any, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        text = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
        thought = "\n\n".join(b.get("thinking") or "" for b in blocks if b.get("type") == "thinking" and b.get("thinking"))
        stop = getattr(final, "stop_reason", None)
        calls = []
        if stop != "refusal":
            # After a fallback mid-answer, only the calls of the model that finished the turn count.
            for block in echo_blocks(blocks):
                if block.get("type") == "tool_use":
                    calls.append({"id": block["id"], "type": "function",
                                  "function": {"name": block["name"], "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)}})
        else:
            details = getattr(final, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            text = (text + "\n\n" if text else "") + (
                f"[Claude declined this request{f' ({category})' if category else ''}. Rephrase it, or use another model.]"
            )
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if thought:
            message["reasoning_content"] = thought
        if calls:
            message["tool_calls"] = calls
        self.memory.remember(message, blocks)
        finish = {"tool_use": "tool_calls", "max_tokens": "length", "end_turn": "stop", "refusal": "stop"}.get(str(stop), "stop")
        if calls and finish == "stop":
            finish = "tool_calls"
        usage = getattr(final, "usage", None)
        prompt = 0
        output = 0
        if usage is not None:
            prompt = sum(int(getattr(usage, name, 0) or 0) for name in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            output = int(getattr(usage, "output_tokens", 0) or 0)
        return {"choices": [{"message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": output}}
