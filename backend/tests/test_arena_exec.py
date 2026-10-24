"""The verifiers that run something: the model's code with hidden tests, in a separate process."""

from __future__ import annotations

import shutil

import pytest

from agentfit.arena import sandbox
from agentfit.arena.model import Answer, Question
from agentfit.arena.toolprobe import Tools
from agentfit.arena.verifiers import Context, verify


def q(check: dict) -> Question:
    return Question(id="c.001", category="coding", title="t", lang="en", prompt="p", check=check, expected="", reference="")


def run(check: dict, text: str, tools: Tools | None = None, finish: str = "stop"):
    return verify(q(check), Answer(text=text, raw=text, finish_reason=finish), Context(tools=tools or Tools()))


PY = {
    "type": "code", "language": "python", "entry": "merge_intervals",
    "cases": [
        {"call": "merge_intervals", "args": [[[1, 3], [2, 6], [8, 10]]], "expected": [[1, 6], [8, 10]]},
        {"call": "merge_intervals", "args": [[[1, 4], [4, 5]]], "expected": [[1, 5]]},
        {"call": "merge_intervals", "args": [[]], "expected": []},
    ],
}
GOOD = '''Here you go:
```python
def merge_intervals(intervals):
    out = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out
```
'''


def test_the_probe_reports_python_and_the_overrides_win():
    tools = Tools()
    python = tools.python()
    assert python is not None and python.version.startswith("3.")
    off = Tools(overrides={"terraform": None, "node": None})
    assert off.terraform() is None and off.node() is None
    report = off.report()
    assert report["terraform"]["available"] is False and report["python"]["available"] is True


def test_the_sandbox_runs_a_process_and_stops_one_that_does_not_end(tmp_path):
    ok = sandbox.run_process([sandbox.python_exe(), "-c", "print('hi')"], tmp_path, timeout=10)
    assert ok.returncode == 0 and ok.stdout.strip() == "hi" and not ok.timed_out
    slow = sandbox.run_process([sandbox.python_exe(), "-c", "while True: pass"], tmp_path, timeout=1.0)
    assert slow.timed_out and slow.seconds < 5


def test_python_code_that_passes_every_hidden_case_is_right():
    v = run(PY, GOOD)
    assert v.status == "passed" and v.method == "execution"
    assert "def merge_intervals" in str(v.extracted)


def test_a_wrong_result_names_the_case_the_call_and_what_came_back():
    wrong = GOOD.replace("start <= out[-1][1]", "start < out[-1][1]")
    v = run(PY, wrong)
    assert (v.status, v.kind) == ("failed", "wrong")
    failing = [c.reason.render() for c in v.checks if not c.ok]
    assert any("merge_intervals" in f and "[[1, 4], [4, 5]]" in f and "[[1, 5]]" in f for f in failing)
    assert "1 of 3" in v.reason.render() or "2 of 3" in v.reason.render()


def test_a_missing_function_a_syntax_error_and_a_crash_each_say_what_happened():
    nothing = run(PY, "```python\ndef other():\n    pass\n```")
    assert nothing.status == "failed" and "merge_intervals" in nothing.reason.render()
    broken = run(PY, "```python\ndef merge_intervals(x:\n    return x\n```")
    assert broken.status == "failed" and "syntax" in broken.reason.render().lower()
    crash = run(PY, "```python\ndef merge_intervals(x):\n    return x[99]\n```")
    assert crash.status == "failed" and any("IndexError" in c.reason.render() for c in crash.checks)


def test_endless_code_times_out_and_says_so():
    spec = {**PY, "timeout": 2}
    v = run(spec, "```python\ndef merge_intervals(x):\n    while True:\n        pass\n```")
    assert v.status == "failed" and "did not finish" in v.reason.render().lower()


def test_code_with_no_fence_is_taken_whole_and_a_reply_with_no_code_is_a_format_failure():
    bare = "def merge_intervals(iv):\n    return sorted(iv) if not iv else [[min(a for a, b in iv), max(b for a, b in iv)]]\n"
    assert run(PY, bare).status == "failed"  # runs, but is wrong: the first case has two groups
    v = run(PY, "Non so come si fa.")
    assert (v.status, v.kind) == ("failed", "format")


def test_the_last_block_that_defines_the_function_is_the_one_that_counts():
    first_wrong = "```python\ndef merge_intervals(x):\n    return []\n```\nOops, better:\n" + GOOD
    assert run(PY, first_wrong).status == "passed"


def test_scripts_can_test_classes_and_state():
    spec = {
        "type": "code", "language": "python", "entry": "Counter",
        "cases": [{"script": "c = Counter(); c.add(2); c.add(3); assert c.total() == 5, c.total()"}, {"script": "assert Counter().total() == 0"}],
    }
    code = "```python\nclass Counter:\n    def __init__(self):\n        self.n = 0\n    def add(self, k):\n        self.n += k\n    def total(self):\n        return self.n\n```"
    assert run(spec, code).status == "passed"
    bad = code.replace("self.n += k", "self.n = k")
    v = run(spec, bad)
    assert v.status == "failed" and any("5" in c.reason.render() or "AssertionError" in c.reason.render() for c in v.checks)


def test_floats_compare_with_a_tolerance_and_unordered_results_by_content():
    spec = {"type": "code", "language": "python", "entry": "f", "cases": [
        {"call": "f", "args": [2], "expected": 1.4142135, "tolerance": 1e-6},
        {"call": "g", "args": [], "expected": [3, 1, 2], "unordered": True},
    ]}
    code = "```python\nimport math\ndef f(x):\n    return math.sqrt(x)\ndef g():\n    return [1, 2, 3]\n```"
    assert run(spec, code).status == "passed"


def test_the_sandbox_blocks_the_network_and_deleting_files(tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("x")
    spec = {"type": "code", "language": "python", "entry": "f", "cases": [{"call": "f", "args": [], "expected": "blocked"}]}
    code = f'''```python
import os, socket
def f():
    try:
        os.remove(r"{victim}")
    except PermissionError:
        pass
    try:
        socket.create_connection(("127.0.0.1", 9))
    except PermissionError:
        return "blocked"
    return "open"
```'''
    v = run(spec, code)
    assert v.status == "passed"
    assert victim.exists()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_javascript_runs_through_node_in_a_context_without_require():
    spec = {"type": "code", "language": "javascript", "entry": "sumEven", "cases": [
        {"call": "sumEven", "args": [[1, 2, 3, 4]], "expected": 6},
        {"call": "sumEven", "args": [[]], "expected": 0},
        {"script": "assert.strictEqual(sumEven([2, 2]), 4)"},
    ]}
    good = "```js\nfunction sumEven(xs) { return xs.filter(x => x % 2 === 0).reduce((a, b) => a + b, 0); }\n```"
    assert run(spec, good).status == "passed"
    bad = good.replace("% 2 === 0", "% 2 === 1")
    v = run(spec, bad)
    assert v.status == "failed" and any("sumEven" in c.reason.render() for c in v.checks)
    arrow = "```javascript\nconst sumEven = (xs) => xs.filter(x => x % 2 === 0).reduce((a, b) => a + b, 0);\n```"
    assert run(spec, arrow).status == "passed"


def test_without_an_interpreter_the_answer_is_not_scored_instead_of_failed():
    class NoPython(Tools):
        def python(self):
            return None

    v = run(PY, GOOD, tools=NoPython())
    assert v.status == "unscored" and v.kind == "tool"
    assert "Python" in v.reason.render()
