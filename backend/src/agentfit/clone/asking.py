"""A question from the agent to the person, answered in the interface.

The kit's skill stops to ask which pages to clone ("the home page, or 2 pages, 3 pages..."), as
it does in opencode. ``ask_user`` is the tool that does it: the run announces the question
(``clone.ask``), waits, and gets the answer back as the tool's result. The page shows the
question with its options and a box for an answer in words; an answer is one short text.

Nothing else waits on the person here: a question is not an approval (those are for risky tool
calls), and a run that is stopped ends its question.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

MAX_OPTIONS = 8
POLL_SECONDS = 1.0


class Stopped(Exception):
    """The run was stopped while it was waiting for an answer."""


@dataclass
class _Gate:
    question: str
    options: list[str]
    event: threading.Event = field(default_factory=threading.Event)
    answer: str = ""


_gates: dict[str, _Gate] = {}
_lock = threading.Lock()


def ask(run_id: str, question: str, options: list[str], *, emit: Callable[[str, dict[str, Any]], None],
        cancelled: Callable[[], bool], notify: Callable[[str], None] | None = None) -> str:
    """Block until the person answers ``question`` in the interface; return the answer."""
    gate = _Gate(question.strip(), [o.strip() for o in options if o and o.strip()][:MAX_OPTIONS])
    with _lock:
        _gates[run_id] = gate
    emit("clone.ask", {"state": "waiting", "question": gate.question, "options": gate.options})
    if notify is not None:
        notify(gate.question)
    try:
        while not gate.event.wait(POLL_SECONDS):
            if cancelled():
                raise Stopped()
    finally:
        with _lock:
            _gates.pop(run_id, None)
    emit("clone.ask", {"state": "answered", "question": gate.question, "answer": gate.answer})
    return gate.answer


def pending(run_id: str) -> dict[str, Any] | None:
    with _lock:
        gate = _gates.get(run_id)
    return {"question": gate.question, "options": list(gate.options)} if gate else None


def answer(run_id: str, text: str) -> bool:
    """Give the waiting run its answer. False when nothing is waiting."""
    with _lock:
        gate = _gates.get(run_id)
    if gate is None or not text.strip():
        return False
    gate.answer = text.strip()
    gate.event.set()
    return True
