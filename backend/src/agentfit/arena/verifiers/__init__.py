"""Verifiers: code that decides whether an answer is right.

``verify`` takes a question and what a model answered and returns a ``Verdict``. A verifier is registered under the
``type`` of the question's ``check`` spec. None of them asks a model anything; they parse, compare, execute or validate.

Two things are decided here, for every verifier alike:

- an empty reply is a failure of format (or, if the model ran out of tokens, a truncation);
- a verifier that crashes has verified nothing, so the verdict is a *technical error*, which is not the model's fault
  and is not counted against it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentfit.arena.model import FORMAT, TRUNCATED, WRONG, Answer, Check, Question, Verdict, why


@dataclass
class Context:
    """What the verifiers may use besides the answer itself."""

    # The probe of the tools installed on this PC (``arena.toolprobe.Tools``); None means none are available.
    tools: Any = None
    # A scratch folder for verifiers that run something. Each verifier makes its own subfolder in it.
    workdir: Path | None = None
    cancelled: Callable[[], bool] = field(default=lambda: False)


Verifier = Callable[[dict[str, Any], Answer, Question, Context], Verdict]
VERIFIERS: dict[str, Verifier] = {}


def register(name: str) -> Callable[[Verifier], Verifier]:
    def wrap(fn: Verifier) -> Verifier:
        VERIFIERS[name] = fn
        return fn

    return wrap


def verify(question: Question, answer: Answer, ctx: Context | None = None) -> Verdict:
    ctx = ctx or Context()
    spec = question.check
    verifier = VERIFIERS.get(str(spec.get("type")))
    if verifier is None:
        return Verdict.error(why("This question has no verifier called {name}.", name=str(spec.get("type"))))
    if not answer.text.strip() and not answer.tool_calls:
        if answer.finish_reason == "length":
            return Verdict.failed(TRUNCATED, why("The reply is empty: the model ran out of tokens before it wrote an answer."), expected=question.expected)
        return Verdict.failed(FORMAT, why("The reply is empty."), expected=question.expected, format_ok=False)
    try:
        verdict = verifier(spec, answer, question, ctx)
    except Exception as exc:  # a broken check is the run's problem, not the model's
        return Verdict.error(why("The check could not run: {error}", error=f"{type(exc).__name__}: {exc}"))
    if verdict.status == "failed" and answer.finish_reason == "length" and verdict.kind in (WRONG, FORMAT) and verdict.reason is not None:
        # It was cut off, so it never got the chance to be right: say so, and keep what the check found.
        verdict.checks.insert(0, Check(False, verdict.reason))
        verdict.kind = TRUNCATED
        verdict.reason = why("The reply was cut off at the token limit before it got to the answer.")
    return verdict


# Importing the modules registers their verifiers.
from agentfit.arena.verifiers import (  # noqa: E402, F401
    basic,
    calling,
    code,
    documents,
    honesty,
    iac,
    sql,
    structured,
)
