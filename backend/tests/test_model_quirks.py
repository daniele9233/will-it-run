"""What local models do that the engine has to survive: turns the runtime cannot read, tests in other languages."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from agentfit.events import EventBus
from agentfit.workflows import tools
from agentfit.workflows.compiler import unreadable_turn
from agentfit.workflows.dsl import load_yaml
from agentfit.workflows.runner import RunManager
from fakes import FakeRuntime, tool_call

UNREADABLE = RuntimeError(
    'Engine protocol predict stream returned an error: {"code":500,"message":"The model produced output that does not '
    'match the expected peg-native format","type":"server_error"}'
)

FLOW = """
workflow: {id: quirks, name: Quirks, max_steps: 10}
models:
  main: {model_key: gpt-oss-20b}
agents:
  - {id: tester, role: Tester, goal: run the tests, model: main, output_schema: TestReport, tools: [read_file, run_python],
     system_prompt: "You run the tests that exist and report only what you actually ran."}
edges:
  - {from: START, to: tester}
  - {from: tester, to: END}
"""


def run(db: Any, script: dict[str, list[Any]], tmp_path: Path) -> tuple[Any, RunManager, FakeRuntime]:
    runtime = FakeRuntime(script)
    manager = RunManager(db, runtime, bus=EventBus(), checkpoint_path=":memory:")
    record = manager.start(load_yaml(FLOW), "Run the tests", workspace=str(tmp_path), background=False)
    return record, manager, runtime


def test_the_parse_error_of_lm_studio_is_recognised():
    assert unreadable_turn(UNREADABLE)
    assert unreadable_turn(RuntimeError("Failed to parse tool call arguments"))
    assert not unreadable_turn(RuntimeError("HTTP 500 Internal Server Error"))
    assert not unreadable_turn(RuntimeError("LM Studio server is not reachable"))


def test_an_unreadable_turn_is_explained_to_the_model_instead_of_sent_again(db, tmp_path):
    script = {"Tester": [UNREADABLE, tool_call("run_python", code="print('ok')"), {"passed": True, "summary": "ran it"}]}
    record, manager, runtime = run(db, script, tmp_path)
    assert record.status == "completed"
    second = runtime.calls[1]["messages"]
    assert second[-1]["role"] == "user" and second[-1]["content"].startswith("Your last reply could not be read")
    assert "read_file, run_python" in second[-1]["content"] and "backslash" in second[-1]["content"]
    retry = next(e["payload"] for e in manager.events(record.id) if e["type"] == "node.retry")
    assert "peg-native" in retry["reason"] and "could not read" in retry["explanation"]


def test_after_several_unreadable_turns_the_agent_concludes_without_tools(db, tmp_path):
    # What failed the gpt-oss-20b run of 2026-10-14 three times in a row: now the agent answers with what it has.
    script = {"Tester": [UNREADABLE, UNREADABLE, UNREADABLE, {"passed": True, "summary": "could not run the JavaScript tests"}]}
    record, _, runtime = run(db, script, tmp_path)
    assert record.status == "completed", record.error
    final = runtime.calls[-1]
    assert not final["kwargs"].get("tools") and final["kwargs"].get("response_format")
    assert any("Stop using tools now" in str(m.get("content")) for m in final["messages"])
    # The feedback is replaced, not piled up.
    assert sum(1 for m in runtime.calls[2]["messages"] if str(m.get("content", "")).startswith("Your last reply could not be read")) == 1


def test_other_errors_still_count_against_the_retries(db, tmp_path):
    record, _, _ = run(db, {"Tester": [RuntimeError("HTTP 500 Internal Server Error")]}, tmp_path)
    assert record.status == "failed" and "after 3 attempts" in (record.error or "")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_run_tests_runs_javascript_tests_with_node(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "hero.test.js").write_text(
        "const test = require('node:test');\nconst assert = require('node:assert');\ntest('adds', () => assert.strictEqual(1 + 1, 2));\n",
        encoding="utf-8",
    )
    context = tools.ToolContext(workspace=tmp_path)
    assert tools.execute("run_tests", {}, context).startswith("exit code 0")
    (tests_dir / "broken.test.js").write_text("require('jsdom');\n", encoding="utf-8")
    output = tools.execute("run_tests", {}, context)
    assert output.startswith("exit code 1") and "npm install" in output


def test_run_tests_says_why_pytest_found_nothing(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Hi</h1>", encoding="utf-8")
    output = tools.execute("run_tests", {}, tools.ToolContext(workspace=tmp_path))
    assert output.startswith("exit code 5") and "pytest found no tests" in output
