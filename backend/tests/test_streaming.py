"""The schema a model is held to, and the text it generates as it is generated."""

from __future__ import annotations

import json
import queue
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from agentfit.events import EventBus
from agentfit.lmstudio.client import LMStudioClient, LMStudioError
from agentfit.workflows import schemas
from agentfit.workflows.compiler import LiveText, parse_structured
from agentfit.workflows.dsl import AgentDef, load_yaml
from agentfit.workflows.runner import RunManager
from fakes import FakeRuntime

# -- the schema sent to the model ---------------------------------------------


def _objects(schema: dict[str, Any], path: str = "$"):
    """Every object schema inside ``schema``, with where it is."""
    if schema.get("type") == "object" and "properties" in schema:
        yield path, schema
        for name, child in schema["properties"].items():
            yield from _objects(child, f"{path}.{name}")
    if isinstance(schema.get("items"), dict):
        yield from _objects(schema["items"], f"{path}[]")
    for option in schema.get("anyOf", []):
        if isinstance(option, dict):
            yield from _objects(option, path)


def _fields(model: type[BaseModel], path: str = "$"):
    """Every field of ``model`` and of the models nested in it, as schema paths."""
    from typing import get_args

    for name, info in model.model_fields.items():
        yield f"{path}.{name}"
        for arg in (info.annotation, *get_args(info.annotation)):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                yield from _fields(arg, f"{path}.{name}[]" if arg is not info.annotation else f"{path}.{name}")


@pytest.mark.parametrize("name", sorted(schemas.SCHEMAS))
def test_every_field_a_schema_requires_can_be_produced(name):
    schema = schemas.response_format(name)["json_schema"]["schema"]
    found = dict(_objects(schema))
    for path, node in found.items():
        missing = set(node.get("required", [])) - set(node["properties"])
        assert not missing, f"{name} {path}: required but not in the schema: {sorted(missing)}"
        assert node["additionalProperties"] is False, f"{name} {path} lets the model add keys of its own"
    # Nothing the Python model has was lost on the way to the decoder.
    declared = {f"{p}.{field}" for p, node in found.items() for field in node["properties"]}
    assert set(_fields(schemas.SCHEMAS[name])) <= declared


def test_a_field_called_title_survives_but_the_title_annotation_does_not():
    plan = schemas.response_format("Plan")["json_schema"]["schema"]
    step = plan["properties"]["steps"]["items"]
    assert list(step["properties"]) == ["title", "detail", "owner"] and step["required"] == ["title", "detail"]
    assert "title" in schemas.response_format("Documentation")["json_schema"]["schema"]["properties"]
    assert "title" in schemas.response_format("Draft")["json_schema"]["schema"]["properties"]
    # The annotation pydantic adds ("title": "Plan", "title": "Summary") is still removed.
    assert "title" not in plan and "title" not in plan["properties"]["summary"] and "$defs" not in plan
    # What used to fail three times in a row now validates.
    answer = {"summary": "s", "steps": [{"title": "Scaffold", "detail": "Create index.html"}]}
    assert parse_structured(json.dumps(answer), "Plan")["steps"][0]["title"] == "Scaffold"
    with pytest.raises(ValueError, match=r"steps\.0\.title: Field required"):
        parse_structured(json.dumps({"summary": "s", "steps": [{"detail": "no title"}]}), "Plan")


# -- streaming client ---------------------------------------------------------


def sse(*chunks: dict[str, Any] | str) -> bytes:
    lines = [f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n" for c in chunks]
    return "".join(lines).encode("utf-8")


def delta(**fields: Any) -> dict[str, Any]:
    finish = fields.pop("finish_reason", None)
    return {"choices": [{"delta": fields, "finish_reason": finish}]}


def client_for(body: bytes, status: int = 200) -> tuple[LMStudioClient, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(status, content=body, headers={"content-type": "text/event-stream" if status == 200 else "application/json"})

    return LMStudioClient(client=httpx.Client(transport=httpx.MockTransport(handler))), sent


def test_stream_reports_reasoning_and_answer_and_returns_the_whole_response():
    body = sse(
        delta(reasoning_content="The user wants "), delta(reasoning_content="a plan."),
        delta(content='{"summary": '), delta(content='"ok"}'),
        delta(finish_reason="stop"),
        {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 9, "completion_tokens_details": {"reasoning_tokens": 5}}},
        "[DONE]",
    )
    client, sent = client_for(body)
    seen: list[tuple[str, str]] = []
    response = client.chat_completions_stream("m", [{"role": "user", "content": "x"}], lambda kind, text: seen.append((kind, text)),
                                              response_format={"type": "json_schema"}, max_tokens=500)
    assert seen == [("reasoning", "The user wants "), ("reasoning", "a plan."), ("content", '{"summary": '), ("content", '"ok"}')]
    message = response["choices"][0]["message"]
    assert message == {"role": "assistant", "content": '{"summary": "ok"}', "reasoning_content": "The user wants a plan."}
    assert response["choices"][0]["finish_reason"] == "stop" and response["usage"]["completion_tokens"] == 9
    assert sent[0]["stream"] is True and sent[0]["stream_options"] == {"include_usage": True} and sent[0]["max_tokens"] == 500


def test_inline_think_blocks_count_as_reasoning_and_tool_calls_are_put_back_together():
    body = sse(
        delta(content="<think>"), delta(content="check the file"), delta(content="</think>"), delta(content="Reading."),
        delta(tool_calls=[{"index": 0, "id": "call_1", "function": {"name": "read_", "arguments": '{"pa'}}]),
        delta(tool_calls=[{"index": 0, "function": {"name": "file", "arguments": 'th": "a.py"}'}}]),
        delta(finish_reason="tool_calls"),
        "not json at all", "[DONE]",
    )
    client, _ = client_for(body)
    seen: list[tuple[str, str]] = []
    response = client.chat_completions_stream("m", [], lambda kind, text: seen.append((kind, text)), tools=[{"type": "function"}])
    assert [kind for kind, _ in seen] == ["reasoning", "reasoning", "reasoning", "content"]
    message = response["choices"][0]["message"]
    assert message["tool_calls"] == [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]
    assert response["choices"][0]["finish_reason"] == "tool_calls"
    # No usage in the stream: counted from the chunks, three of them reasoning.
    assert response["usage"] == {"completion_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 3}}


def test_a_refused_stream_raises_with_the_servers_message():
    client, _ = client_for(json.dumps({"error": {"message": "response_format is not supported by this model"}}).encode(), status=400)
    with pytest.raises(LMStudioError, match="response_format is not supported"):
        client.chat_completions_stream("m", [], lambda kind, text: None)
    client, _ = client_for(sse({"error": {"message": "context length exceeded"}}))
    with pytest.raises(LMStudioError, match="context length exceeded"):
        client.chat_completions_stream("m", [], lambda kind, text: None)


# -- live text in a run -------------------------------------------------------


class _Ctx:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.emit_live = lambda event_type, payload: self.sent.append({"type": event_type, **payload})


def test_live_text_is_sent_a_few_times_a_second_with_the_speed_so_far():
    now = [100.0]
    ctx = _Ctx()
    live = LiveText(ctx, AgentDef(id="planner", role="Planner", model="m"), clock=lambda: now[0])  # type: ignore[arg-type]
    for i in range(30):  # 30 tokens over 1.45 seconds
        live.feed("reasoning" if i < 20 else "content", "<think>w " if i == 0 else "w ")
        now[0] += 0.05
    live.flush()
    assert 5 <= len(ctx.sent) <= 8  # not one event per token
    assert all(e["type"] == "node.stream" and e["node"] == "planner" for e in ctx.sent)
    assert ctx.sent[0]["tokens_per_second"] is None  # too early to mean anything
    assert ctx.sent[-1]["tokens"] == 30 and 19 <= ctx.sent[-1]["tokens_per_second"] <= 22
    assert "".join(e["reasoning"] for e in ctx.sent) == "w " * 20  # the tag is not shown
    assert "".join(e["content"] for e in ctx.sent) == "w " * 10


class StreamingRuntime(FakeRuntime):
    """A fake that thinks out loud before it answers, like a reasoning model behind a streaming server."""

    supports_streaming = True

    def complete(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        on_delta = kwargs.pop("on_delta", None)
        response = super().complete(model, messages, **kwargs)
        message = response["choices"][0]["message"]
        thought = f"Thinking about {self._role(messages)}."
        if on_delta is not None:
            for word in thought.split(" "):
                on_delta("reasoning", word + " ")
            on_delta("content", str(message.get("content") or ""))
        message["reasoning_content"] = thought
        return response


def test_a_run_streams_reasoning_to_watchers_and_keeps_it_with_the_step(db, tmp_path):
    definition = load_yaml("""
workflow: {id: two, name: Two}
models: {work: {model_key: m}}
agents:
  - {id: lead, role: Lead, goal: brief, model: work, output_schema: TaskBrief, system_prompt: "Turn the request into a short brief with an objective and acceptance criteria."}
  - {id: planner, role: Planner, goal: plan, model: work, output_schema: Plan, system_prompt: "Write the ordered plan a developer can follow, one small step at a time please."}
edges:
  - {from: START, to: lead}
  - {from: lead, to: planner}
  - {from: planner, to: END}
""")
    script = {"Lead": [{"objective": "a hero page"}], "Planner": [{"summary": "two steps", "steps": [{"title": "Scaffold", "detail": "index.html"}]}]}
    bus = EventBus()
    watcher = bus.subscribe()
    manager = RunManager(db, StreamingRuntime(script), bus=bus, checkpoint_path=":memory:")
    record = manager.start(definition, "Make a hero page", workspace=str(tmp_path / "ws"), background=False)
    assert record.status == "completed", record.error

    seen: list[dict[str, Any]] = []
    while True:
        try:
            seen.append(watcher.get_nowait()["data"])
        except queue.Empty:
            break
    live = [e for e in seen if e.get("type") == "node.stream"]
    assert {e["payload"]["node"] for e in live} == {"lead", "planner"}
    assert "Thinking about Planner." in "".join(e["payload"]["reasoning"] for e in live if e["payload"]["node"] == "planner")
    assert all(e["live"] is True and e["seq"] is None for e in live)
    # Live text is for whoever is watching: none of it is stored, and it does not crowd the backlog.
    stored = manager.events(record.id)
    assert not [e for e in stored if e["type"] == "node.stream"]
    assert not [e for e in bus.recent(f"run.{record.id}") if e["data"]["type"] == "node.stream"]
    # The finished step keeps what the model thought, and the context it ran with.
    done = {e["payload"]["node"]: e["payload"] for e in stored if e["type"] == "node.completed"}
    assert done["planner"]["reasoning"] == "Thinking about Planner." and done["planner"]["context_length"] > 0
    assert done["planner"]["output"]["steps"][0]["title"] == "Scaffold"


def test_stop_ends_the_answer_being_written_not_just_the_step_after_it(db, tmp_path):
    """Before, Stop was looked at between model calls: a run kept writing its current answer, minutes at times."""
    definition = load_yaml("""
workflow: {id: one, name: One}
models: {work: {model_key: m}}
agents:
  - {id: lead, role: Lead, goal: brief, model: work, output_schema: TaskBrief, system_prompt: "Turn the request into a short brief with an objective and acceptance criteria."}
  - {id: planner, role: Planner, goal: plan, model: work, output_schema: Plan, system_prompt: "Write the ordered plan a developer can follow, one small step at a time please."}
edges:
  - {from: START, to: lead}
  - {from: lead, to: planner}
  - {from: planner, to: END}
""")
    manager: RunManager

    class StoppedMidAnswer(StreamingRuntime):
        words = 0

        def complete(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
            on_delta = kwargs["on_delta"]

            def counting(kind: str, text: str) -> None:
                type(self).words += 1
                if type(self).words == 2:
                    manager.cancel(run_id())  # the user presses Stop while the second word arrives
                on_delta(kind, text)

            return super().complete(model, messages, **{**kwargs, "on_delta": counting})

    def run_id() -> str:
        return manager.list()[0].id

    manager = RunManager(db, StoppedMidAnswer({"Lead": [{"objective": "x"}]}), bus=EventBus(), checkpoint_path=":memory:")
    record = manager.start(definition, "x", workspace=str(tmp_path / "ws"), background=False)
    assert record.status == "cancelled"
    # The stream was cut at the word during which Stop arrived: nothing after it was read, no step completed.
    assert StoppedMidAnswer.words == 2
    assert not [e for e in manager.events(record.id) if e["type"] == "node.completed"]


def test_a_runtime_that_does_not_stream_is_called_exactly_as_before(db, tmp_path):
    definition = load_yaml("""
workflow: {id: one, name: One}
models: {work: {model_key: m}}
agents:
  - {id: lead, role: Lead, goal: brief, model: work, output_schema: TaskBrief, system_prompt: "Turn the request into a short brief with an objective and acceptance criteria."}
edges:
  - {from: START, to: lead}
  - {from: lead, to: END}
""")
    runtime = FakeRuntime({"Lead": [{"objective": "x"}]})
    manager = RunManager(db, runtime, bus=EventBus(), checkpoint_path=":memory:")
    record = manager.start(definition, "x", workspace=str(tmp_path / "ws"), background=False)
    assert record.status == "completed"
    assert all("on_delta" not in call["kwargs"] for call in runtime.calls)
