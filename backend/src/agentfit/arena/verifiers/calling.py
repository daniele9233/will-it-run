"""Tool calling: did the model call the right function with the right arguments, or rightly call nothing."""

from __future__ import annotations

import json
import re
from typing import Any

from agentfit.arena import text as tx
from agentfit.arena.model import (
    FABRICATED,
    FORMAT,
    WRONG,
    Answer,
    Check,
    Question,
    Reason,
    ToolCall,
    Verdict,
    why,
)
from agentfit.arena.verifiers import VERIFIERS, Context, register
from agentfit.arena.verifiers.basic import shorten

_ASK_WORDS = re.compile(
    r"\b(quale|quali|dove|quando|chi|puoi (?:dirmi|indicare|specificare)|mi (?:serve|servono|occorre)|potresti|specifica|indica|"
    r"which|where|what|could you|can you (?:tell|specify|provide)|please (?:provide|specify|tell)|i need to know|need more)\b",
    re.IGNORECASE,
)


def _kind_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _arg_problem(fn: str, arg: str, rule: Any, value: Any) -> Reason | None:
    """What is wrong with one argument against its rule, or None. A rule that is not a dict means «equal to»."""
    if not isinstance(rule, dict):
        rule = {"eq": rule}
    if "type" in rule:
        wanted, kind = rule["type"], _kind_of(value)
        integer_ok = wanted == "integer" and kind == "number" and float(value).is_integer()
        if kind != wanted and not integer_ok:
            return why("{fn}: argument {arg} must be a {wanted}, it is a {kind}.", fn=fn, arg=arg, wanted=wanted, kind=kind)
    if "eq" in rule:
        eq = rule["eq"]
        if isinstance(eq, str):
            same = isinstance(value, str) and (value.strip() == eq.strip() if rule.get("exact") else tx.fold(value) == tx.fold(eq))
        elif isinstance(eq, bool) or eq is None:
            same = value == eq and type(value) is type(eq)
        elif isinstance(eq, (int, float)):
            same = _kind_of(value) == "number" and abs(float(value) - float(eq)) <= float(rule.get("tol", 1e-9))
        else:
            same = value == eq
        if not same:
            return why("{fn}: argument {arg} is {found}, expected {expected}.", fn=fn, arg=arg, found=_j(value), expected=_j(eq))
    if "in" in rule:
        options = rule["in"]
        inside = tx.fold(value) in {tx.fold(str(o)) for o in options} if isinstance(value, str) else value in options
        if not inside:
            return why("{fn}: argument {arg} is {found}, expected one of {options}.", fn=fn, arg=arg, found=_j(value), options=_j(options))
    if "regex" in rule and not (isinstance(value, str) and re.search(rule["regex"], value)):
        return why("{fn}: argument {arg} is {found}, which does not match {pattern}.", fn=fn, arg=arg, found=_j(value), pattern=rule["regex"])
    if "contains" in rule and not (isinstance(value, str) and tx.contains_phrase(tx.fold(value), tx.fold(rule["contains"]))):
        return why("{fn}: argument {arg} does not mention {text}.", fn=fn, arg=arg, text=rule["contains"])
    if "min" in rule and not (_kind_of(value) == "number" and value >= rule["min"]):
        return why("{fn}: argument {arg} is {found}, expected at least {limit}.", fn=fn, arg=arg, found=_j(value), limit=rule["min"])
    if "max" in rule and not (_kind_of(value) == "number" and value <= rule["max"]):
        return why("{fn}: argument {arg} is {found}, expected at most {limit}.", fn=fn, arg=arg, found=_j(value), limit=rule["max"])
    return None


def _call_problems(expected: dict[str, Any], actual: ToolCall) -> list[Reason]:
    fn = actual.name
    if actual.arguments is None:
        return [why("{fn}: the arguments are not valid JSON.", fn=fn)]
    problems: list[Reason] = []
    optional = set(expected.get("optional", []))
    for arg, rule in (expected.get("args") or {}).items():
        if arg not in actual.arguments:
            if arg not in optional:
                problems.append(why("{fn}: argument {arg} is missing.", fn=fn, arg=arg))
            continue
        problem = _arg_problem(fn, arg, rule, actual.arguments[arg])
        if problem is not None:
            problems.append(problem)
    if expected.get("strict_args"):
        for arg in actual.arguments:
            if arg not in (expected.get("args") or {}):
                problems.append(why("{fn}: argument {arg} was not asked for.", fn=fn, arg=arg))
    return problems


def _offered(question: Question) -> set[str]:
    return {t["function"]["name"] for t in (question.tools or ()) if isinstance(t, dict) and "function" in t}


def _shown(calls: list[ToolCall]) -> str:
    return "; ".join(f"{c.name}({', '.join(f'{k}={_j(v)}' for k, v in (c.arguments or {}).items())})" for c in calls)


def _expected_text(calls: list[dict[str, Any]]) -> str:
    parts = []
    for call in calls:
        args = []
        for name, rule in (call.get("args") or {}).items():
            value = rule.get("eq", rule.get("in", rule.get("type", "…"))) if isinstance(rule, dict) else rule
            args.append(f"{name}={_j(value)}")
        parts.append(f"{call['name']}({', '.join(args)})")
    return "; ".join(parts)


@register("toolcall")
def toolcall(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    mode = spec.get("mode", "call")
    calls = answer.tool_calls
    names = ", ".join(c.name for c in calls)
    if mode == "no_call":
        if calls:
            return Verdict.failed(WRONG, why("It called {names} although no tool was needed.", names=names), extracted=_shown(calls),
                                  expected=question.expected, method="toolcall")
        nested = spec.get("answer")
        if nested:
            verdict = VERIFIERS[nested["type"]](nested, answer, question, ctx)
            verdict.method = "toolcall + " + verdict.method
            return verdict
        return Verdict.passed(extracted=shorten(answer.text), expected=question.expected, method="toolcall")
    if mode == "ask":
        missing = str(spec.get("missing", "detail"))
        if calls:
            return Verdict.failed(FABRICATED, why("It called {names} with a made-up value for {missing} instead of asking for it.", names=names, missing=missing),
                                  extracted=_shown(calls), expected=question.expected, method="toolcall")
        if "?" in answer.text or _ASK_WORDS.search(answer.text):
            return Verdict.passed(extracted=shorten(answer.text), expected=question.expected, method="toolcall")
        return Verdict.failed(WRONG, why("It neither called the tool nor asked for the missing {missing}.", missing=missing),
                              extracted=shorten(answer.text), expected=question.expected, method="toolcall")
    expected_calls: list[dict[str, Any]] = list(spec["calls"])
    wanted = _expected_text(expected_calls)
    expected_text = question.expected or wanted
    if not calls:
        return Verdict.failed(FORMAT, why("It answered in words instead of calling {names}.", names=", ".join(c["name"] for c in expected_calls)),
                              extracted=shorten(answer.text), expected=expected_text, method="toolcall", format_ok=False)
    offered = _offered(question)
    unknown = [c.name for c in calls if offered and c.name not in offered]
    if unknown:
        return Verdict.failed(WRONG, why("It called {name}, a tool that does not exist here.", name=unknown[0]),
                              extracted=_shown(calls), expected=expected_text, method="toolcall")
    broken = next((c for c in calls if c.arguments is None), None)
    if broken is not None:
        return Verdict.failed(FORMAT, why("The arguments of {name} are not valid JSON: {raw}", name=broken.name, raw=shorten(broken.raw, 80)),
                              extracted=_shown(calls), expected=expected_text, method="toolcall", format_ok=False)
    pool = list(calls)
    checks: list[Check] = []
    matched = 0
    for expected in expected_calls:
        found = None
        best: list[Reason] | None = None
        for index, actual in enumerate(pool):
            if actual.name != expected["name"]:
                continue
            problems = _call_problems(expected, actual)
            if not problems:
                found = index
                break
            if best is None or len(problems) < len(best):
                best = problems
        if found is not None:
            pool.pop(found)
            matched += 1
            checks.append(Check(True, why("{name} was called correctly.", name=expected["name"])))
        elif best is not None:
            checks.extend(Check(False, problem) for problem in best)
        else:
            checks.append(Check(False, why("Expected a call to {name}, it called {names}.", name=expected["name"], names=names)))
    extras = len(calls) - len(expected_calls)
    if matched == len(expected_calls) and extras == 0:
        return Verdict.passed(checks=checks, extracted=_shown(calls), expected=expected_text, method="toolcall")
    if extras > 0:
        checks.append(Check(False, why("{n} more calls than were needed.", n=extras)))
    reason = next(c.reason for c in checks if not c.ok)
    if matched == 0:
        return Verdict.failed(WRONG, reason, checks=checks, extracted=_shown(calls), expected=expected_text, method="toolcall")
    return Verdict.partial(matched / max(len(expected_calls), len(calls)), reason, checks=checks, extracted=_shown(calls), expected=expected_text, method="toolcall")
