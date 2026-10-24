"""Code: the model's program is taken out of its reply and run against test cases the model never saw."""

from __future__ import annotations

import json
import re
from typing import Any

from agentfit.arena import sandbox
from agentfit.arena import text as tx
from agentfit.arena.model import FORMAT, TOOL, WRONG, Answer, Check, Question, Verdict, why
from agentfit.arena.toolprobe import Tools
from agentfit.arena.verifiers import Context, register
from agentfit.arena.verifiers.basic import shorten

# (the names a code block may carry, what unlabelled code in that language looks like)
_LANGS = {
    "python": ({"python", "py", "python3", "py3"}, r"^\s*(?:def|class|import|from)\s"),
    "javascript": ({"javascript", "js", "node", "mjs", "cjs", "jsx"}, r"\bfunction\b|=>|\bconst\b|\blet\b"),
}


def extract_code(reply: str, language: str) -> str | None:
    """The program in a reply: every block written in the language (or in none, when no block names one), in order, so a
    helper in one block and the function in another both count and a later definition replaces an earlier one."""
    names, looks_like = _LANGS[language]
    blocks = tx.code_blocks(reply)
    chosen = [b for b in blocks if b.lang in names]
    if not chosen:
        chosen = [b for b in blocks if b.lang == "" and re.search(looks_like, b.code, re.MULTILINE)]
    if chosen:
        return "\n\n".join(b.code for b in chosen)
    if not blocks and re.search(looks_like, reply, re.MULTILINE):
        return reply
    return None


def _shown_call(case: dict[str, Any]) -> str:
    if "call" not in case:
        return "script"
    args = ", ".join(json.dumps(a, ensure_ascii=False) for a in case.get("args", []))
    return shorten(f"{case['call']}({args})", 90)


@register("code")
def code(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    language = spec.get("language", "python")
    tools: Tools = ctx.tools or Tools()
    interpreter = tools.python() if language == "python" else tools.node()
    if interpreter is None:
        name = "Python" if language == "python" else "Node.js"
        return Verdict.unscored(TOOL, why("{name} is not installed on this PC, so the code could not be run.", name=name), method="execution")
    entry = spec["entry"]
    source = extract_code(answer.text, language)
    if source is None:
        return Verdict.failed(FORMAT, why("No code was found in the reply."), extracted=shorten(answer.text), expected=question.expected, method="execution", format_ok=False)
    workdir = sandbox.make_workdir(ctx.workdir)
    try:
        suffix = "py" if language == "python" else "js"
        (workdir / f"solution.{suffix}").write_text(source, encoding="utf-8")
        (workdir / "cases.json").write_text(json.dumps({"entry": entry, "cases": spec["cases"], "case_timeout": spec.get("case_timeout", 2.0)}), encoding="utf-8")
        harness = "harness.py" if language == "python" else "harness.js"
        (workdir / harness).write_text(sandbox.PY_HARNESS if language == "python" else sandbox.JS_HARNESS, encoding="utf-8")
        command = [interpreter.path, "-I", "-B", harness] if language == "python" else [interpreter.path, harness]
        limit = float(spec.get("timeout", 8))
        result = sandbox.run_process(command, workdir, timeout=limit)
    finally:
        sandbox.remove_workdir(workdir)
    shown = shorten(source, 600)
    if result.timed_out:
        return Verdict.failed(WRONG, why("The program did not finish within {seconds} seconds (an endless loop?).", seconds=f"{limit:g}"),
                              extracted=shown, expected=question.expected, method="execution")
    payload = None
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(sandbox.SENTINEL):
            try:
                payload = json.loads(line[len(sandbox.SENTINEL):])
            except ValueError:
                payload = None
            break
    if payload is None:
        tail = shorten((result.stderr or result.stdout).strip().splitlines()[-1] if (result.stderr or result.stdout).strip() else "no output", 160)
        return Verdict.failed(WRONG, why("The program stopped without running the test cases: {error}", error=tail), extracted=shown, expected=question.expected, method="execution")
    if payload.get("phase") == "load":
        if payload.get("missing"):
            return Verdict.failed(WRONG, why("The function {name} is not defined in the code.", name=payload["missing"]), extracted=shown, expected=question.expected, method="execution")
        template = "Syntax error: {error}" if payload.get("syntax") else "The code failed as soon as it was loaded: {error}"
        return Verdict.failed(WRONG, why(template, error=payload.get("error", "")), extracted=shown, expected=question.expected, method="execution")
    cases = spec["cases"]
    checks: list[Check] = []
    passed = 0
    for entry_result in payload.get("results", []):
        case = cases[entry_result["i"]]
        label = _shown_call(case)
        if entry_result.get("ok"):
            passed += 1
        elif entry_result.get("timeout"):
            checks.append(Check(False, why("Case {n}: {call} did not finish.", n=entry_result["i"] + 1, call=label)))
        elif entry_result.get("notrun"):
            checks.append(Check(False, why("Case {n}: {call} was not run, an earlier case never finished.", n=entry_result["i"] + 1, call=label)))
        elif "error" in entry_result:
            checks.append(Check(False, why("Case {n}: {call} raised {error}.", n=entry_result["i"] + 1, call=label, error=entry_result["error"])))
        else:
            expected = shorten(json.dumps(case.get("expected"), ensure_ascii=False), 80)
            checks.append(Check(False, why("Case {n}: {call} returned {got}, expected {expected}.", n=entry_result["i"] + 1, call=label, got=entry_result.get("got", "?"), expected=expected)))
    if passed:
        checks.append(Check(True, why("{n} test cases pass.", n=passed)))
    if passed == len(cases):
        return Verdict.passed(checks=checks, extracted=shown, expected=question.expected, method="execution")
    return Verdict.failed(WRONG, why("{n} of {total} test cases fail.", n=len(cases) - passed, total=len(cases)), checks=checks, extracted=shown, expected=question.expected, method="execution")
