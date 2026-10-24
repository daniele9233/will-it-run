"""JSON answers and puzzle answers: structure that can be parsed and compared field by field."""

from __future__ import annotations

import json
import re
from typing import Any

from agentfit.arena import jsonschema
from agentfit.arena import text as tx
from agentfit.arena.model import FORMAT, WRONG, Answer, Check, Question, Verdict, why
from agentfit.arena.verifiers import Context, register
from agentfit.arena.verifiers.basic import shorten, show_number

# -- extracting JSON ---------------------------------------------------------------------------


def extract_json(reply: str) -> tuple[Any, str]:
    """The JSON value in a reply and how it was found: ``whole``, ``fenced`` (the only thing in the reply, in a code block)
    or ``embedded`` (somewhere in a longer text). Raises ValueError, with the parser's own complaint, when there is none."""
    body = reply.strip()
    try:
        return json.loads(body), "whole"
    except ValueError as first:
        error = first
    blocks = tx.code_blocks(reply)
    if body.startswith("```") and body.endswith("```") and len(blocks) == 1:
        try:
            return json.loads(blocks[0].code.strip()), "fenced"
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    for block in blocks:
        try:
            value = json.loads(block.code.strip())
        except ValueError:
            continue
        if isinstance(value, (dict, list)):
            return value, "embedded"
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[{\[]", reply):
        try:
            value, _end = decoder.raw_decode(reply[match.start():])
        except ValueError:
            continue
        if isinstance(value, (dict, list)):
            return value, "embedded"
    raise ValueError(str(error))


def _show(value: Any, limit: int = 60) -> str:
    if isinstance(value, float):
        return show_number(value)
    return shorten(json.dumps(value, ensure_ascii=False), limit)


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class _Compare:
    def __init__(self, tolerance: float, fold: bool, unordered: bool) -> None:
        self.tolerance, self.fold, self.unordered = tolerance, fold, unordered
        self.failures: list[Check] = []
        self.right = 0

    def leaf_ok(self, expected: Any, actual: Any) -> bool:
        if _is_num(expected) and _is_num(actual):
            return abs(expected - actual) <= self.tolerance
        if isinstance(expected, str) and isinstance(actual, str):
            return tx.fold(expected) == tx.fold(actual) if self.fold else expected.strip() == actual.strip()
        return type(expected) is type(actual) and expected == actual

    def run(self, expected: Any, actual: Any, path: str) -> None:
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                self.failures.append(Check(False, why("{path}: expected an object, found {found}.", path=path, found=_show(actual))))
                return
            for key, value in expected.items():
                if key not in actual:
                    self.failures.append(Check(False, why("{path}.{key} is missing: expected {expected}.", path=path, key=key, expected=_show(value))))
                else:
                    self.run(value, actual[key], f"{path}.{key}")
            return
        if isinstance(expected, list):
            if not isinstance(actual, list):
                self.failures.append(Check(False, why("{path}: expected a list, found {found}.", path=path, found=_show(actual))))
                return
            scalars = all(not isinstance(x, (dict, list)) for x in expected + actual)
            if self.unordered and scalars:
                pool = list(actual)
                for item in expected:
                    hit = next((i for i, other in enumerate(pool) if self.leaf_ok(item, other)), None)
                    if hit is None:
                        self.failures.append(Check(False, why("{path}: {expected} is missing from the list.", path=path, expected=_show(item))))
                    else:
                        pool.pop(hit)
                        self.right += 1
                if pool:
                    self.failures.append(Check(False, why("{path}: unexpected items {found}.", path=path, found=_show(pool))))
                return
            if len(expected) != len(actual):
                self.failures.append(Check(False, why("{path}: expected {n} items, found {m}.", path=path, n=len(expected), m=len(actual))))
            for index, value in enumerate(expected[: len(actual)]):
                self.run(value, actual[index], f"{path}[{index}]")
            return
        if self.leaf_ok(expected, actual):
            self.right += 1
        else:
            self.failures.append(Check(False, why("{path}: expected {expected}, found {found}.", path=path, expected=_show(expected), found=_show(actual))))


@register("json")
def json_answer(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    try:
        value, how = extract_json(answer.text)
    except ValueError as exc:
        return Verdict.failed(FORMAT, why("The reply is not valid JSON: {error}", error=str(exc)),
                              checks=[Check(False, why("The reply is not valid JSON: {error}", error=str(exc)))],
                              extracted=shorten(answer.text), expected=question.expected, method="json", format_ok=False)
    if spec.get("strict") and how == "embedded":
        return Verdict.failed(FORMAT, why("The reply must be the JSON and nothing else: there is text around it."),
                              checks=[Check(False, why("The reply must be the JSON and nothing else: there is text around it."))],
                              extracted=shorten(answer.text), expected=question.expected, method="json", format_ok=False)
    checks = [Check(True, why("The reply is valid JSON."))]
    right, total = 1, 1
    schema = spec.get("schema")
    if schema:
        errors = jsonschema.validate(value, schema)
        total += 1
        if errors:
            checks.append(Check(False, why("It does not fit the schema: {errors}", errors="; ".join(errors[:3]) + (f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""))))
        else:
            right += 1
            checks.append(Check(True, why("It fits the schema.")))
    if "expect" in spec:
        compare = _Compare(float(spec.get("tolerance", 1e-6)), bool(spec.get("fold")), bool(spec.get("unordered")))
        compare.run(spec["expect"], value, "$")
        checks.extend(compare.failures)
        if compare.right:
            checks.append(Check(True, why("{n} values are right.", n=compare.right)))
        right += compare.right
        total += compare.right + len(compare.failures)
    format_ok = how != "embedded"
    shown = shorten(json.dumps(value, ensure_ascii=False))
    expected = question.expected or shorten(json.dumps(spec.get("expect"), ensure_ascii=False)) if "expect" in spec else question.expected
    if right == total:
        return Verdict.passed(checks=checks, extracted=shown, expected=expected, method="json", format_ok=format_ok)
    failing = [c for c in checks if not c.ok]
    reason = failing[0].reason if len(failing) == 1 else why("{n} of {total} checks failed.", n=len(failing), total=len(checks))
    if right == 1:
        # Only «it parses» is right: that is not an answer.
        return Verdict.failed(WRONG, reason, checks=checks, extracted=shown, expected=expected, method="json", format_ok=format_ok)
    return Verdict.partial(right / total, reason, checks=checks, extracted=shown, expected=expected, method="json", format_ok=format_ok)


# -- puzzles ---------------------------------------------------------------------------------------


@register("puzzle")
def puzzle(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    segment, _method = tx.final_answer(answer.text)
    marked = segment is not None
    body = segment if segment is not None else answer.text
    kind = spec["kind"]
    if kind == "mapping":
        return _mapping(spec, body, marked, question)
    if kind == "order":
        return _order(spec, body, marked, question)
    raise ValueError(f"unknown puzzle kind: {kind}")


_PAIR = re.compile(r"^\s*(?P<k>[^=:>\-→]+?)\s*(?:=|:|->|→|-|\bè\b|\be'\b|\bis\b)\s*(?P<v>.+?)\s*$", re.IGNORECASE)


def _mapping(spec: dict[str, Any], body: str, marked: bool, question: Question) -> Verdict:
    solution: dict[str, str] = spec["solution"]
    synonyms: dict[str, list[str]] = spec["values"]
    given: dict[str, str] = {}
    for piece in re.split(r"[,;\n]+", body):
        match = _PAIR.match(piece)
        if not match:
            continue
        key = tx.fold(match.group("k"))
        value = tx.fold(match.group("v"))
        for real in solution:
            if tx.fold(real) != key:
                continue
            for name, words in synonyms.items():
                if any(tx.contains_phrase(value, tx.fold(w)) for w in words):
                    given.setdefault(real, name)
    expected = ", ".join(f"{k}={v}" for k, v in solution.items())
    if not given:
        return Verdict.failed(FORMAT, why("No answer of the form «name = value» could be read."), extracted=shorten(body),
                              expected=expected, method="puzzle", format_ok=False)
    shown = ", ".join(f"{k}={v}" for k, v in given.items())
    missing = [k for k in solution if k not in given]
    wrong = [k for k in solution if k in given and given[k] != solution[k]]
    if not missing and not wrong:
        return Verdict.passed(extracted=shown, expected=expected, method="puzzle", format_ok=marked)
    parts = []
    if wrong:
        parts.append(why("Wrong for {names}.", names=", ".join(wrong)))
    if missing:
        parts.append(why("Missing: {names}.", names=", ".join(missing)))
    reason = why("Expected {expected}, found {found}.", expected=expected, found=shown)
    return Verdict.failed(WRONG, reason, checks=[Check(False, p) for p in parts], extracted=shown, expected=expected, method="puzzle", format_ok=marked)


def _order(spec: dict[str, Any], body: str, marked: bool, question: Question) -> Verdict:
    solution: list[str] = spec["solution"]
    names: list[str] = spec.get("names") or solution
    folded = tx.fold(body)
    positions = []
    for name in names:
        match = re.search(rf"(?:^| ){re.escape(tx.fold(name))}(?: |$)", folded)
        if match:
            positions.append((match.start(), name))
    positions.sort()
    given = [name for _pos, name in positions]
    expected = " > ".join(solution)
    if not given:
        return Verdict.failed(FORMAT, why("None of the names could be read in the reply."), extracted=shorten(body), expected=expected, method="puzzle", format_ok=False)
    shown = " > ".join(given)
    if given == solution:
        return Verdict.passed(extracted=shown, expected=expected, method="puzzle", format_ok=marked)
    return Verdict.failed(WRONG, why("Expected {expected}, found {found}.", expected=expected, found=shown), extracted=shown, expected=expected, method="puzzle", format_ok=marked)
