"""A small, safe expression language for edge conditions.

    passed == true
    reviewer.approved == false and tester.passed == true
    len(failures) > 0

No ``eval``: expressions are tokenized and evaluated against agent outputs. A bare field
resolves against the output of the edge's source node; ``agent.field`` addresses another
agent's output.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(
    r"\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<str>'[^']*'|\"[^\"]*\")|(?P<op>==|!=|>=|<=|>|<)"
    r"|(?P<lp>\()|(?P<rp>\))|(?P<name>[A-Za-z_][A-Za-z0-9_.]*))"
)
_KEYWORDS = {"true": True, "false": False, "null": None, "none": None}


class ConditionError(ValueError):
    pass


def tokenize(expr: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        if expr[pos:].strip() == "":
            break
        match = _TOKEN.match(expr, pos)
        if not match:
            raise ConditionError(f"cannot parse condition near: {expr[pos:pos + 20]!r}")
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind)))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], outputs: dict[str, Any], default_source: str | None) -> None:
        self.tokens = tokens
        self.pos = 0
        self.outputs = outputs
        self.default_source = default_source

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise ConditionError("unexpected end of condition")
        self.pos += 1
        return token

    def parse(self) -> bool:
        value = self.parse_or()
        trailing = self.peek()
        if trailing is not None:
            raise ConditionError(f"unexpected token {trailing[1]!r}")
        return bool(value)

    def parse_or(self) -> Any:
        left = self.parse_and()
        while (tok := self.peek()) and tok == ("name", "or"):
            self.take()
            right = self.parse_and()
            left = bool(left) or bool(right)
        return left

    def parse_and(self) -> Any:
        left = self.parse_not()
        while (tok := self.peek()) and tok == ("name", "and"):
            self.take()
            right = self.parse_not()
            left = bool(left) and bool(right)
        return left

    def parse_not(self) -> Any:
        tok = self.peek()
        if tok == ("name", "not"):
            self.take()
            return not bool(self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self) -> Any:
        left = self.parse_value()
        tok = self.peek()
        if tok and tok[0] == "op":
            op = self.take()[1]
            right = self.parse_value()
            return _compare(left, op, right)
        return left

    def parse_value(self) -> Any:
        kind, text = self.take()
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind == "str":
            return text[1:-1]
        if kind == "lp":
            value = self.parse_or()
            if self.take()[0] != "rp":
                raise ConditionError("missing closing parenthesis")
            return value
        if kind == "name":
            lowered = text.lower()
            if lowered in _KEYWORDS:
                return _KEYWORDS[lowered]
            following = self.peek()
            if text == "len" and following is not None and following[0] == "lp":
                self.take()
                inner = self.parse_value()
                if self.take()[0] != "rp":
                    raise ConditionError("missing closing parenthesis after len(")
                return len(inner) if hasattr(inner, "__len__") else 0
            return self.resolve(text)
        raise ConditionError(f"unexpected token {text!r}")

    def resolve(self, path: str) -> Any:
        parts = path.split(".")
        if parts[0] in self.outputs:
            node: Any = self.outputs[parts[0]]
            parts = parts[1:]
        else:
            node = self.outputs.get(self.default_source or "", {})
        for part in parts:
            if isinstance(node, dict):
                node = node.get(part)
            else:
                return None
        return node


def _compare(left: Any, op: str, right: Any) -> bool:
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    try:
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
    except TypeError:
        return False
    raise ConditionError(f"unknown operator {op}")


def evaluate(expr: str, outputs: dict[str, Any], default_source: str | None = None) -> bool:
    """Evaluate ``expr`` against agent outputs. Missing fields compare as ``None``."""
    return _Parser(tokenize(expr), outputs, default_source).parse()


def check_syntax(expr: str) -> str | None:
    """Return an error message when ``expr`` is not a valid condition."""
    try:
        _Parser(tokenize(expr), {}, None).parse()
    except ConditionError as exc:
        return str(exc)
    return None
