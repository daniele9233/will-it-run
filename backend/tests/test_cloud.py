"""Cloud models: naming, keys, the two wire formats, the gateway, and the API. Nothing leaves this machine."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from anthropic.types.beta import BetaMessage

from agentfit.cloud import secrets
from agentfit.cloud.anthropic_wire import AnthropicClient, BlockMemory, echo_blocks, to_anthropic
from agentfit.cloud.openai_wire import CloudError, OpenAICompatClient, TurnMemory
from agentfit.cloud.providers import PROVIDERS, context_of, is_cloud, label_of, make_key, parse_key
from agentfit.cloud.service import CloudService, HybridRuntime
from agentfit.workflows.dsl import ModelSlot
from agentfit.workflows.model_manager import ModelManager
from fakes import FakeRuntime


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch):
    for provider in PROVIDERS.values():
        for name in provider.env_keys:
            monkeypatch.delenv(name, raising=False)


def test_a_cloud_model_is_named_by_provider_and_model():
    key = make_key("deepseek", "deepseek-v4-pro")
    assert key == "cloud:deepseek/deepseek-v4-pro" and is_cloud(key) and not is_cloud("qwen3-14b")
    provider, model = parse_key(key)
    assert provider.id == "deepseek" and model == "deepseek-v4-pro"
    assert label_of(key) == "deepseek-v4-pro · DeepSeek"
    assert context_of(PROVIDERS["openai"], "gpt-5.5") == 1_050_000
    assert context_of(PROVIDERS["anthropic"], "claude-opus-5-5", listed=1_000_000) == 1_000_000
    with pytest.raises(ValueError):
        parse_key("cloud:nobody/x")


def test_keys_are_encrypted_on_disk_and_never_shown(tmp_path):
    secrets.save("cloud_test", "sk-test-1234567890abcdef")
    raw = secrets._path("cloud_test").read_bytes()
    assert b"sk-test" not in raw
    assert secrets.load("cloud_test") == "sk-test-1234567890abcdef"
    service = CloudService()
    service.set_key("deepseek", "sk-deep-1234567890abcd")
    status = service.status("deepseek")
    assert status["configured"] and status["source"] == "saved" and status["hint"] == "...abcd"
    assert "sk-deep" not in json.dumps(service.providers())
    service.delete_key("deepseek")
    assert service.status("deepseek")["configured"] is False
    secrets.delete("cloud_test")


def test_a_key_in_the_environment_is_used_when_none_is_saved(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE")
    status = CloudService().status("gemini")
    assert status["configured"] and status["source"] == "GEMINI_API_KEY"


# -- OpenAI-compatible --------------------------------------------------------------


def sse(*chunks: dict[str, Any]) -> bytes:
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"url": str(request.url), "body": json.loads(request.content or b"{}"), "auth": request.headers.get("authorization")})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def openai_client(provider: str, recorder: Recorder, memory: TurnMemory | None = None) -> OpenAICompatClient:
    return OpenAICompatClient(PROVIDERS[provider], "sk-key", client=httpx.Client(transport=httpx.MockTransport(recorder)),
                              memory=memory, sleep=lambda s: None)


TOOL_TURN = sse(
    {"choices": [{"delta": {"reasoning_content": "I should look."}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "browser_snapshot", "arguments": '{"pa'}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th": "C:/a"}'}}]}, "finish_reason": "tool_calls"}]},
    {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 12}},
)


def test_openai_gets_its_own_token_limit_and_no_temperature():
    recorder = Recorder(httpx.Response(200, content=TOOL_TURN))
    seen: list[tuple[str, str]] = []
    answer = openai_client("openai", recorder).complete(
        "gpt-5.5", [{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "browser_snapshot"}}],
        max_tokens=4000, on_delta=lambda kind, text: seen.append((kind, text)),
    )
    body = recorder.requests[0]["body"]
    assert recorder.requests[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert recorder.requests[0]["auth"] == "Bearer sk-key"
    assert body["max_completion_tokens"] == 4000 and "max_tokens" not in body and "temperature" not in body
    message = answer["choices"][0]["message"]
    assert message["tool_calls"][0]["function"] == {"name": "browser_snapshot", "arguments": '{"path": "C:/a"}'}
    assert message["reasoning_content"] == "I should look." and ("reasoning", "I should look.") in seen
    assert answer["usage"]["prompt_tokens"] == 100 and answer["choices"][0]["finish_reason"] == "tool_calls"


def test_deepseek_gets_its_reasoning_back_on_every_assistant_turn():
    memory = TurnMemory()
    recorder = Recorder(httpx.Response(200, content=TOOL_TURN))
    client = openai_client("deepseek", recorder, memory)
    first = client.complete("deepseek-v4-pro", [{"role": "user", "content": "go"}], tools=[{"type": "function", "function": {"name": "x"}}])
    call = first["choices"][0]["message"]["tool_calls"][0]
    # The engine keeps only text and calls; the reasoning has to come back from memory.
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call["id"], "type": "function", "function": call["function"]}]},
        {"role": "tool", "tool_call_id": call["id"], "content": "ok"},
        {"role": "assistant", "content": "an answer from before"},
    ]
    client.complete("deepseek-v4-pro", history, tools=[{"type": "function", "function": {"name": "x"}}],
                    response_format={"type": "json_schema", "json_schema": {}})
    body = recorder.requests[1]["body"]
    assistant = [m for m in body["messages"] if m["role"] == "assistant"]
    assert assistant[0]["reasoning_content"] == "I should look."
    assert assistant[1]["reasoning_content"] == ""  # unknown, but present: DeepSeek refuses a turn without it
    assert body["response_format"] == {"type": "json_object"} and "max_completion_tokens" not in body
    assert recorder.requests[1]["url"] == "https://api.deepseek.com/chat/completions"


def test_gemini_gets_its_thought_signature_back():
    turn = sse(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "g1", "function": {"name": "x", "arguments": "{}"},
                                                 "extra_content": {"google": {"thought_signature": "SIG"}}}]}}]},
    )
    recorder = Recorder(httpx.Response(200, content=turn))
    client = openai_client("gemini", recorder)
    out = client.complete("gemini-3.8-flash", [{"role": "user", "content": "go"}])
    call = out["choices"][0]["message"]["tool_calls"][0]
    client.complete("gemini-3.8-flash", [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call["id"], "type": "function", "function": call["function"]}]},
        {"role": "tool", "tool_call_id": call["id"], "content": "ok"},
    ])
    sent = recorder.requests[1]["body"]["messages"][1]["tool_calls"][0]
    assert sent["extra_content"] == {"google": {"thought_signature": "SIG"}}
    assert recorder.requests[0]["url"].startswith("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
    assert "stream_options" not in recorder.requests[0]["body"]


def test_errors_say_what_to_do_and_busy_servers_are_tried_again():
    refused = Recorder(httpx.Response(401, json={"error": {"message": "Incorrect API key"}}))
    with pytest.raises(CloudError, match=r"refused the API key .* Settings > Cloud models"):
        openai_client("openai", refused).complete("gpt-5.5", [{"role": "user", "content": "x"}])
    busy = Recorder(httpx.Response(429, json={"error": {"message": "slow down"}}),
                    httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})))
    out = openai_client("deepseek", busy).complete("deepseek-v4-pro", [{"role": "user", "content": "x"}])
    assert out["choices"][0]["message"]["content"] == "ok" and len(busy.requests) == 2


# -- Anthropic ----------------------------------------------------------------------


def test_the_conversation_is_translated_for_claude():
    memory = BlockMemory()
    system, messages = to_anthropic([
        {"role": "system", "content": "You are a reverse engineer."},
        {"role": "user", "content": "How does search work?"},
        {"role": "assistant", "content": "Looking.", "tool_calls": [
            {"id": "toolu_1", "type": "function", "function": {"name": "browser_snapshot", "arguments": '{"path": "C:/a"}'}}]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "ERROR: nothing there"},
        {"role": "user", "content": "[2 rounds left]"},
    ], memory)
    assert system == "You are a reverse engineer."
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"][1] == {"type": "tool_use", "id": "toolu_1", "name": "browser_snapshot", "input": {"path": "C:/a"}}
    last = messages[2]["content"]
    assert last[0]["type"] == "tool_result" and last[0]["is_error"] is True and last[1]["text"] == "[2 rounds left]"


def test_after_a_fallback_only_the_finishing_models_work_is_echoed():
    blocks = [{"type": "thinking", "thinking": "a", "signature": "s1"}, {"type": "text", "text": "partial"},
              {"type": "tool_use", "id": "t0", "name": "x", "input": {}},
              {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
              {"type": "thinking", "thinking": "b", "signature": "s2"}, {"type": "tool_use", "id": "t1", "name": "y", "input": {}}]
    assert [b.get("id") or b["type"] for b in echo_blocks(blocks)] == ["text", "thinking", "t1"]


class FakeStream:
    def __init__(self, message: BetaMessage, events: list[Any]) -> None:
        self.message, self.events = message, events

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self) -> BetaMessage:
        return self.message


class FakeAnthropic:
    def __init__(self, *messages: dict[str, Any]) -> None:
        self.messages = [BetaMessage.model_validate({"id": "msg", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                                     "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}, **m})
                         for m in messages]
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params: Any) -> FakeStream:
        self.calls.append(json.loads(json.dumps(params)))
        message = self.messages.pop(0) if len(self.messages) > 1 else self.messages[0]
        delta = SimpleNamespace(type="thinking_delta", thinking="thinking...")
        return FakeStream(message, [SimpleNamespace(type="content_block_delta", delta=delta)])


def test_claude_thinks_and_its_thinking_goes_back_unchanged():
    fake = FakeAnthropic(
        {"content": [{"type": "thinking", "thinking": "Plan: inspect.", "signature": "SIG1"},
                     {"type": "tool_use", "id": "toolu_9", "name": "browser_snapshot", "input": {"path": "C:/a"}}],
         "stop_reason": "tool_use"},
        {"content": [{"type": "text", "text": "It uses SQLite."}]},
    )
    client = AnthropicClient(PROVIDERS["anthropic"], "sk-ant-x", client=fake)  # type: ignore[arg-type]
    seen: list[tuple[str, str]] = []
    first = client.complete("claude-opus-5-5", [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}],
                            tools=[{"type": "function", "function": {"name": "browser_snapshot", "parameters": {"type": "object"}}}],
                            max_tokens=8000, on_delta=lambda k, t: seen.append((k, t)))
    params = fake.calls[0]
    assert params["thinking"]["type"] == "adaptive" and params["thinking"]["display"] == "summarized"
    assert params["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert params["fallbacks"] == "default" and set(params["betas"]) == {"thinking-binding-controls-2026-08-01", "server-side-fallback-2026-07-01"}
    assert params["max_tokens"] == 32_000 and "temperature" not in params
    assert params["tools"][0]["eager_input_streaming"] is True and params["system"] == "S"
    message = first["choices"][0]["message"]
    assert first["choices"][0]["finish_reason"] == "tool_calls" and message["reasoning_content"] == "Plan: inspect."
    assert ("reasoning", "thinking...") in seen
    call = message["tool_calls"][0]
    client.complete("claude-opus-5-5", [
        {"role": "system", "content": "S"}, {"role": "user", "content": "Q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call["id"], "type": "function", "function": call["function"]}]},
        {"role": "tool", "tool_call_id": call["id"], "content": "found it"},
    ])
    replayed = fake.calls[1]["messages"][1]["content"]
    assert replayed[0] == {"type": "thinking", "thinking": "Plan: inspect.", "signature": "SIG1"}


def test_a_refusal_becomes_an_answer_with_no_calls_to_run():
    fake = FakeAnthropic({"content": [{"type": "tool_use", "id": "t", "name": "x", "input": {}}], "stop_reason": "refusal",
                          "stop_details": {"type": "refusal", "category": "cyber", "explanation": None}})
    out = AnthropicClient(PROVIDERS["anthropic"], "k", client=fake).complete("claude-haiku-4-5", [{"role": "user", "content": "x"}])  # type: ignore[arg-type]
    message = out["choices"][0]["message"]
    assert "tool_calls" not in message and "declined this request (cyber)" in message["content"]
    assert "thinking" not in fake.calls[0] and "fallbacks" not in fake.calls[0]  # an older model gets neither


# -- the engine ---------------------------------------------------------------------


class StubCloudClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []

    def complete(self, model: str, messages: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        self.calls.append((model, messages, options))
        return {"choices": [{"message": {"role": "assistant", "content": "from the cloud"}, "finish_reason": "stop"}], "usage": {}}

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro", "context": None},
                {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash", "context": None}]


def test_the_gateway_hands_out_a_cloud_model_without_loading_anything():
    stub = StubCloudClient()
    cloud = CloudService(client_factory=lambda provider, key: stub, max_context=lambda: 64_000)
    local = FakeRuntime()
    runtime = HybridRuntime(local, cloud)
    events: list[tuple[str, dict[str, Any]]] = []
    key = make_key("deepseek", "deepseek-v4-pro")
    manager = ModelManager(runtime, {"m": ModelSlot(model_key=key, auto_tune=True)}, lambda t, p: events.append((t, p)))
    lease = manager.acquire("m", 9000)
    assert lease.instance_id == key and lease.context_length == 64_000  # capped: every token of it is paid for
    assert local.load_calls == []
    assert events[0][1]["cloud"] is True and events[0][1]["to"] == key
    manager.acquire("m", 9000)
    assert len(events) == 1  # announced once
    secrets.save("cloud_deepseek", "sk-x-1234567890")
    try:
        out = runtime.complete(key, [{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=100)
    finally:
        secrets.delete("cloud_deepseek")
    assert out["choices"][0]["message"]["content"] == "from the cloud"
    assert stub.calls[0][0] == "deepseek-v4-pro" and "temperature" not in stub.calls[0][2]
    manager.close()
    assert local.unload_calls == []


def test_models_are_listed_recommended_first_and_cached(monkeypatch):
    stub = StubCloudClient()
    cloud = CloudService(client_factory=lambda provider, key: stub)
    secrets.save("cloud_deepseek", "sk-x-1234567890")
    try:
        models = cloud.models("deepseek")
        assert [m["id"] for m in models] == ["deepseek-v4-pro", "deepseek-v4-flash"]
        assert models[0]["key"] == "cloud:deepseek/deepseek-v4-pro" and models[0]["context"] == 1_000_000
        assert [m["key"] for m in cloud.available()] == [m["key"] for m in models]
    finally:
        secrets.delete("cloud_deepseek")
    assert cloud.available() == []  # no key, no models offered


class FlakyLister:
    """Lists no models until ``works`` is set, as a provider that refused the first call."""

    def __init__(self) -> None:
        self.works = False
        self.asked = 0

    def list_models(self) -> list[dict[str, Any]]:
        self.asked += 1
        if not self.works:
            raise CloudError("DeepSeek is not reachable (timed out)")
        return [{"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro", "context": None}]


def test_a_key_with_no_model_list_is_asked_for_it_and_says_why_when_that_fails(monkeypatch):
    from agentfit.cloud import service as service_module

    lister = FlakyLister()
    cloud = CloudService(client_factory=lambda provider, key: lister)
    secrets.save("cloud_deepseek", "sk-x-1234567890")
    try:
        cloud.load_missing()
        status = cloud.status("deepseek")
        assert lister.asked == 1 and cloud.available() == []
        assert status["configured"] and status["models"] == [] and "not reachable" in status["error"]

        cloud.load_missing()
        assert lister.asked == 1  # asked a moment ago: not again, a page that asks often must not hammer it

        monkeypatch.setattr(service_module, "RETRY_AFTER_SECONDS", 0.0)
        lister.works = True
        cloud.load_missing()
        assert [m["key"] for m in cloud.available()] == ["cloud:deepseek/deepseek-v4-pro"]
        assert cloud.status("deepseek")["error"] is None

        cloud.load_missing()
        assert lister.asked == 2  # a provider that has its list is left alone
    finally:
        secrets.delete("cloud_deepseek")


def test_a_provider_without_a_key_is_never_asked_for_models():
    lister = FlakyLister()
    CloudService(client_factory=lambda provider, key: lister).load_missing()
    assert lister.asked == 0


def test_a_model_list_that_is_not_json_is_an_error_a_person_can_read():
    recorder = Recorder(httpx.Response(200, content=b"<html>Sign in to the network</html>"))
    with pytest.raises(CloudError, match="DeepSeek answered HTTP 200: the answer is not a model list"):
        openai_client("deepseek", recorder).list_models()


def test_without_a_key_the_call_says_where_to_add_one():
    with pytest.raises(CloudError, match="No API key for ChatGPT"):
        CloudService().complete(make_key("openai", "gpt-5.5"), [{"role": "user", "content": "x"}])
