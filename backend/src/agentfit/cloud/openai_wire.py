"""Chat completions against OpenAI, DeepSeek and Gemini (its OpenAI-compatible endpoint).

The engine speaks this dialect already (LM Studio's ``/v1/chat/completions``), so the
messages go out almost as they are. What differs per provider:

- OpenAI's reasoning models take ``max_completion_tokens`` and refuse any temperature
  but their own. No temperature is sent to any provider: their defaults are what they tune.
- DeepSeek, with tools in the request, wants the ``reasoning_content`` of every earlier
  assistant turn sent back, or it answers 400.
- Gemini 3 attaches a thought signature to its tool calls and wants it back unchanged.

The engine rebuilds assistant turns from their text and tool calls only (see
``compiler.checked_calls``), which drops both. ``TurnMemory`` remembers what each turn
carried besides those and puts it back on the way out, keyed by the turn's tool-call ids
or, for a turn without calls, by its text.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import httpx

from agentfit.branding import USER_AGENT
from agentfit.cloud.providers import Provider

# Fields of a message and of a tool call that the engine keeps itself.
_MESSAGE_FIELDS = {"role", "content", "tool_calls", "tool_call_id", "name"}
_CALL_FIELDS = {"id", "type", "function", "index"}
# Requests that failed this way are tried again, after the pause the server asks for or a short one.
_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
RETRIES = 2


class CloudError(Exception):
    """A provider answered with an error, or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _fingerprint(message: dict[str, Any]) -> str:
    calls = message.get("tool_calls") or []
    if calls:
        return "calls:" + "|".join(str(c.get("id") or "") + ":" + str((c.get("function") or {}).get("name") or "") for c in calls)
    return "text:" + hashlib.sha256(str(message.get("content") or "").encode("utf-8")).hexdigest()


class TurnMemory:
    """What the provider sent with each assistant turn that the engine does not keep."""

    def __init__(self, size: int = 4000) -> None:
        self._lock = threading.Lock()
        self._turns: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._size = size

    def remember(self, message: dict[str, Any]) -> None:
        extra = {k: v for k, v in message.items() if k not in _MESSAGE_FIELDS and v not in (None, "")}
        calls = {
            str(c.get("id")): {k: v for k, v in c.items() if k not in _CALL_FIELDS}
            for c in message.get("tool_calls") or [] if c.get("id")
        }
        calls = {k: v for k, v in calls.items() if v}
        if not extra and not calls:
            return
        with self._lock:
            self._turns[_fingerprint(message)] = {"message": extra, "calls": calls}
            self._turns.move_to_end(_fingerprint(message))
            while len(self._turns) > self._size:
                self._turns.popitem(last=False)

    def restore(self, message: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            kept = self._turns.get(_fingerprint(message))
        if not kept:
            return message
        out = {**kept["message"], **message}
        if message.get("tool_calls"):
            out["tool_calls"] = [{**kept["calls"].get(str(c.get("id")), {}), **c} for c in message["tool_calls"]]
        return out


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return (response.text or f"HTTP {response.status_code}")[:400]
    if isinstance(data, list) and data:
        data = data[0]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:400]
    return str(err or data)[:400]


def explain(provider: Provider, status: int | None, text: str) -> str:
    """An error as a person can act on it."""
    if status in (401, 403):
        return f"{provider.label} refused the API key (HTTP {status}): {text}. Check the key in Settings > Cloud models."
    if status == 402 or "insufficient" in text.lower() or "balance" in text.lower():
        return f"{provider.label} says the account has no credit left (HTTP {status}): {text}"
    if status == 404:
        return f"{provider.label} does not know this model (HTTP 404): {text}. Pick another one."
    if status == 429:
        return f"{provider.label} is rate-limiting this key (HTTP 429): {text}"
    return f"{provider.label} answered HTTP {status}: {text}" if status else f"{provider.label}: {text}"


class OpenAICompatClient:
    def __init__(self, provider: Provider, api_key: str, client: httpx.Client | None = None,
                 memory: TurnMemory | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.provider = provider
        self.base_url = provider.base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT}
        self._client = client or httpx.Client(timeout=httpx.Timeout(900.0, connect=10.0))
        self.memory = memory or TurnMemory()
        self._sleep = sleep

    # -- models -------------------------------------------------------------
    def list_models(self) -> list[dict[str, Any]]:
        try:
            response = self._client.get(f"{self.base_url}/models", headers=self._headers, timeout=20.0)
        except httpx.HTTPError as exc:
            raise CloudError(explain(self.provider, None, f"not reachable ({exc})")) from exc
        if response.status_code >= 400:
            raise CloudError(explain(self.provider, response.status_code, _error_text(response)), response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            # A proxy or a captive portal answers 200 with a page, not a list.
            raise CloudError(explain(self.provider, response.status_code, "the answer is not a model list")) from exc
        rows = data.get("data") if isinstance(data, dict) else data
        models = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id") or "").removeprefix("models/")
            if model_id:
                models.append({"id": model_id, "label": row.get("display_name") or model_id, "context": None})
        return models

    # -- completion ---------------------------------------------------------
    def _body(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
              response_format: dict[str, Any] | None, max_tokens: int | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [self.memory.restore(m) if m.get("role") == "assistant" else m for m in messages],
            "stream": True,
        }
        if self.provider.id == "deepseek" and tools:
            # DeepSeek answers 400 when an earlier assistant turn lacks it, whatever the turn did.
            body["messages"] = [
                {**m, "reasoning_content": m.get("reasoning_content") or ""} if m.get("role") == "assistant" else m
                for m in body["messages"]
            ]
        if self.provider.id != "gemini":
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
        if response_format:
            if self.provider.id == "deepseek":
                # DeepSeek has JSON mode but no schemas: the schema is in the prompt already.
                body["response_format"] = {"type": "json_object"}
            else:
                body["response_format"] = response_format
        if max_tokens:
            body["max_completion_tokens" if self.provider.id == "openai" else "max_tokens"] = max_tokens
        return body

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
        body = self._body(model, messages, tools, response_format, max_tokens)
        attempt = 0
        while True:
            try:
                return self._stream(body, on_delta)
            except CloudError as exc:
                attempt += 1
                if exc.status not in _RETRY_STATUS or attempt > RETRIES:
                    raise
                self._sleep(min(20.0, 2.0 * attempt * attempt))

    def _stream(self, body: dict[str, Any], on_delta: Callable[[str, str], None] | None) -> dict[str, Any]:
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        extra: dict[str, Any] = {}
        usage: dict[str, Any] | None = None
        finish: str | None = None
        thinking = False
        try:
            with self._client.stream("POST", f"{self.base_url}/chat/completions", json=body, headers=self._headers) as response:
                if response.status_code >= 400:
                    response.read()
                    raise CloudError(explain(self.provider, response.status_code, _error_text(response)), response.status_code)
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(chunk.get("error"), dict):
                        raise CloudError(explain(self.provider, None, str(chunk["error"].get("message") or chunk["error"])))
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choice = (chunk.get("choices") or [{}])[0]
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    for key, value in delta.items():
                        if key not in ("role", "content", "tool_calls", "reasoning_content", "reasoning", "refusal") and value:
                            extra[key] = value
                    piece = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(piece, str) and piece:
                        reasoning.append(piece)
                        if on_delta:
                            on_delta("reasoning", piece)
                    piece = delta.get("content")
                    if piece:
                        content.append(piece)
                        if "<think>" in piece or "<thought>" in piece:
                            thinking = True
                        if on_delta:
                            on_delta("reasoning" if thinking else "content", piece)
                        if "</think>" in piece or "</thought>" in piece:
                            thinking = False
                    for call in delta.get("tool_calls") or []:
                        slot = calls.setdefault(int(call.get("index") or 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        slot["id"] = call.get("id") or slot["id"]
                        function = call.get("function") or {}
                        slot["function"]["name"] += function.get("name") or ""
                        slot["function"]["arguments"] += function.get("arguments") or ""
                        for key, value in call.items():
                            # Gemini's thought signature travels here (``extra_content``).
                            if key not in _CALL_FIELDS and value:
                                slot[key] = value
                    if delta.get("refusal"):
                        content.append(str(delta["refusal"]))
        except httpx.HTTPError as exc:
            raise CloudError(explain(self.provider, None, f"the connection failed ({exc})")) from exc
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content), **extra}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        if calls:
            for call in calls.values():
                # A call without an id cannot be matched to its result, nor to what TurnMemory keeps.
                call["id"] = call["id"] or f"call_{uuid.uuid4().hex[:16]}"
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        self.memory.remember(message)
        usage = usage or {}
        return {"choices": [{"message": message, "finish_reason": finish or ("tool_calls" if calls else "stop")}], "usage": usage}
