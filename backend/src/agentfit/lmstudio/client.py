"""HTTP client for LM Studio's local server.

Native REST API v1 (https://lmstudio.ai/docs/developer/rest):
    GET  /api/v1/models
    POST /api/v1/models/load            {model, context_length?, flash_attention?, ...}
    POST /api/v1/models/unload          {instance_id}
    POST /api/v1/models/download        {model, quantization?}
    GET  /api/v1/models/download/status/:job_id
    POST /api/v1/chat                   {model, input, system_prompt?, stream?, ...}
OpenAI-compatible API, used for tool calling, JSON-schema output and embeddings:
    POST /v1/chat/completions
    POST /v1/embeddings                 {model, input: [text, ...]}
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from agentfit.branding import USER_AGENT
from agentfit.lmstudio.models import ChatResult, ChatStats, DownloadJob


class LMStudioError(Exception):
    """LM Studio answered with an error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LMStudioOffline(LMStudioError):
    """The LM Studio server cannot be reached."""


class ModelNotFound(LMStudioError):
    pass


_HTML_TAG = re.compile(r"<[^>]+>")


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = response.text or ""
        if "<html" in text.lower():
            # The server's own crash page ("Internal Server Error"): keep its words, not its markup.
            body = re.search(r"<pre>(.*?)</pre>", text, re.DOTALL | re.IGNORECASE)
            text = " ".join(_HTML_TAG.sub(" ", body.group(1) if body else text).split())
            return f"HTTP {response.status_code} {text}"[:300]
        return text[:300] or f"HTTP {response.status_code}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err or data)[:300]


def _fill(body: dict[str, Any], tools: list[dict[str, Any]] | None, response_format: dict[str, Any] | None, temperature: float | None,
          max_tokens: int | None, repeat_penalty: float | None, top_p: float | None, top_k: int | None, seed: int | None) -> None:
    """What a completion asks for, left out when it is not asked for: the server's own defaults apply to the rest."""
    if tools:
        body["tools"] = tools
    if response_format:
        body["response_format"] = response_format
    for name, value in (("temperature", temperature), ("repeat_penalty", repeat_penalty), ("top_p", top_p), ("top_k", top_k), ("seed", seed)):
        if value is not None:
            body[name] = value
    if max_tokens:
        body["max_tokens"] = max_tokens


class LMStudioClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234",
        api_token: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"User-Agent": USER_AGENT}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        self._client = client or httpx.Client(headers=headers, timeout=httpx.Timeout(30.0, connect=3.0))
        if client is not None and api_token:
            self._client.headers["Authorization"] = f"Bearer {api_token}"

    # -- plumbing -----------------------------------------------------------
    def _request(self, method: str, path: str, *, timeout: float | None = None, **kwargs: Any) -> Any:
        try:
            response = self._client.request(
                method, f"{self.base_url}{path}", timeout=timeout or httpx.USE_CLIENT_DEFAULT, **kwargs
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LMStudioOffline(f"LM Studio server is not reachable at {self.base_url}") from exc
        except httpx.HTTPError as exc:
            raise LMStudioError(f"LM Studio request failed: {exc}") from exc
        if response.status_code >= 400:
            message = _error_message(response)
            lowered = message.lower()
            if response.status_code == 404 or "not found" in lowered or "no model" in lowered:
                raise ModelNotFound(message, response.status_code)
            raise LMStudioError(message, response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise LMStudioError("LM Studio returned a non-JSON response") from exc

    def is_reachable(self) -> bool:
        try:
            self._request("GET", "/api/v1/models", timeout=3.0)
        except LMStudioError:
            return False
        return True

    # -- models -------------------------------------------------------------
    def list_models(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/v1/models")
        return list(data.get("models") or []) if isinstance(data, dict) else []

    def load(self, model: str, *, timeout: float = 900.0, **options: Any) -> dict[str, Any]:
        body = {"model": model, "echo_load_config": True, **{k: v for k, v in options.items() if v is not None}}
        return self._request("POST", "/api/v1/models/load", json=body, timeout=timeout)

    def unload(self, instance_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/v1/models/unload", json={"instance_id": instance_id}, timeout=120.0)

    # -- downloads ----------------------------------------------------------
    def download(self, model: str, quantization: str | None = None) -> DownloadJob:
        body: dict[str, Any] = {"model": model}
        if quantization:
            body["quantization"] = quantization
        return DownloadJob.model_validate(self._request("POST", "/api/v1/models/download", json=body, timeout=60.0))

    def download_status(self, job_id: str) -> DownloadJob:
        return DownloadJob.model_validate(self._request("GET", f"/api/v1/models/download/status/{job_id}"))

    # -- inference ----------------------------------------------------------
    def chat(
        self,
        model: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        context_length: int | None = None,
        reasoning: str | None = None,
        timeout: float = 900.0,
    ) -> ChatResult:
        """Native chat call. Its ``stats`` block is the source for benchmark numbers."""
        body: dict[str, Any] = {"model": model, "input": prompt, "stream": False, "store": False}
        for key, value in (
            ("system_prompt", system_prompt),
            ("max_output_tokens", max_output_tokens),
            ("temperature", temperature),
            ("context_length", context_length),
            ("reasoning", reasoning),
        ):
            if value is not None:
                body[key] = value
        data = self._request("POST", "/api/v1/chat", json=body, timeout=timeout)
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for item in data.get("output") or []:
            if item.get("type") == "message":
                text_parts.append(str(item.get("content") or ""))
            elif item.get("type") == "reasoning":
                reasoning_parts.append(str(item.get("content") or ""))
        stats = data.get("stats") or {}
        return ChatResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            model_instance_id=data.get("model_instance_id"),
            stats=ChatStats(
                input_tokens=stats.get("input_tokens"),
                output_tokens=stats.get("total_output_tokens"),
                reasoning_tokens=stats.get("reasoning_output_tokens"),
                tokens_per_second=stats.get("tokens_per_second"),
                time_to_first_token_seconds=stats.get("time_to_first_token_seconds"),
                model_load_time_seconds=stats.get("model_load_time_seconds"),
            ),
        )

    def embeddings(self, model: str, inputs: list[str], *, timeout: float = 60.0) -> list[list[float]]:
        """One vector per input text, in the order given."""
        data = self._request("POST", "/v1/embeddings", json={"model": model, "input": inputs}, timeout=timeout)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise LMStudioError("LM Studio returned no embeddings")
        rows = sorted(rows, key=lambda row: int(row.get("index") or 0))
        return [[float(value) for value in row.get("embedding") or []] for row in rows]

    def chat_completions(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
        repeat_penalty: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        seed: int | None = None,
        timeout: float = 900.0,
    ) -> dict[str, Any]:
        """OpenAI-compatible chat completion with optional tools / JSON-schema output."""
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        _fill(body, tools, response_format, temperature, max_tokens, repeat_penalty, top_p, top_k, seed)
        return self._request("POST", "/v1/chat/completions", json=body, timeout=timeout)

    def chat_completions_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        on_delta: Callable[[str, str], None],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
        repeat_penalty: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        seed: int | None = None,
        timeout: float = 900.0,
    ) -> dict[str, Any]:
        """The same completion, streamed. Returns the response a non-streamed call would give.

        ``on_delta(kind, text)`` is called as text arrives, with kind ``reasoning`` or
        ``content``. Reasoning is what the model thinks before it answers: LM Studio
        sends it apart (``reasoning_content``) or, for some models, inline between
        ``<think>`` tags; both are reported as reasoning.
        """
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
        _fill(body, tools, response_format, temperature, max_tokens, repeat_penalty, top_p, top_k, seed)
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] | None = None
        finish: str | None = None
        counted = {"reasoning": 0, "content": 0}
        thinking = False  # inside an inline <think> block
        try:
            with self._client.stream("POST", f"{self.base_url}/v1/chat/completions", json=body, timeout=timeout) as response:
                if response.status_code >= 400:
                    response.read()
                    raise LMStudioError(_error_message(response), response.status_code)
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
                        raise LMStudioError(str(chunk["error"].get("message") or chunk["error"]))
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choice = (chunk.get("choices") or [{}])[0]
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    piece = delta.get("reasoning_content") or delta.get("reasoning")
                    if piece:
                        reasoning.append(piece)
                        counted["reasoning"] += 1
                        on_delta("reasoning", piece)
                    piece = delta.get("content")
                    if piece:
                        content.append(piece)
                        if "<think>" in piece:
                            thinking = True
                        kind = "reasoning" if thinking else "content"
                        counted[kind] += 1
                        on_delta(kind, piece)
                        if "</think>" in piece:
                            thinking = False
                    for call in delta.get("tool_calls") or []:
                        slot = calls.setdefault(int(call.get("index") or 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        slot["id"] = call.get("id") or slot["id"]
                        function = call.get("function") or {}
                        slot["function"]["name"] += function.get("name") or ""
                        slot["function"]["arguments"] += function.get("arguments") or ""
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LMStudioOffline(f"LM Studio server is not reachable at {self.base_url}") from exc
        except httpx.HTTPError as exc:
            raise LMStudioError(f"LM Studio request failed: {exc}") from exc
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        if usage is None:
            # A server that streams no usage: one chunk is close to one token.
            usage = {
                "completion_tokens": counted["reasoning"] + counted["content"],
                "completion_tokens_details": {"reasoning_tokens": counted["reasoning"]},
            }
        return {"choices": [{"message": message, "finish_reason": finish or ("tool_calls" if calls else "stop")}], "usage": usage}

    def stream_chat_completions(
        self, model: str, messages: list[dict[str, Any]], *, temperature: float | None = 0.2, timeout: float = 900.0
    ) -> Iterator[str]:
        """Yield content deltas from a streaming OpenAI-compatible completion."""
        body = {"model": model, "messages": messages, "stream": True, "temperature": temperature}
        try:
            with self._client.stream(
                "POST", f"{self.base_url}/v1/chat/completions", json=body, timeout=timeout
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise LMStudioError(_error_message(response), response.status_code)
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
                    delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                    if delta.get("content"):
                        yield delta["content"]
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LMStudioOffline(f"LM Studio server is not reachable at {self.base_url}") from exc

    def close(self) -> None:
        self._client.close()
