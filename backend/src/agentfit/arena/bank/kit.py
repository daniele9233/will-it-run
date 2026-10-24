"""What every category of the bank is made with: how a question is put together, the instructions that make answers
readable, and the scripted answers the bank tests its own verifiers with."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from agentfit.arena.categories import CATEGORIES
from agentfit.arena.model import Answer, Question, ToolCall

# The line every question that wants a short answer ends with. The same words for every model.
IT_LINE = "Rispondi con una sola riga nel formato «Risposta: <risposta breve>»."
EN_LINE = "Reply with a single line in the form «Answer: <short answer>»."
IT_NUM = "Ragiona se ti serve, poi scrivi l'ultima riga nel formato «Risposta: <numero>» (solo il numero, senza unità)."
EN_NUM = "Think it through if you need to, then write the last line as «Answer: <number>» (the number only, no units)."


def make(category: str, number: int, title: str, prompt: str, check: dict[str, Any], expected: str, reference: str, *,
         wrong: tuple[str, ...] | list[str] = (), lang: str = "it", **fields: Any) -> Question:
    """One question. Its tier is set afterwards from the category's plan, so authors list questions in order of importance."""
    return Question(
        id=f"{category}.{number:03d}", category=category, title=title, lang=lang, prompt=prompt.strip(), check=check,
        expected=expected, reference=reference, wrong=tuple(wrong), **fields,
    )


def wrong_numbers(correct: float, *candidates: float | str, label: str = "Risposta", fmt=None, tol: float = 1e-9) -> list[str]:  # type: ignore[no-untyped-def]
    """«Risposta: x» lines for the candidates that really are different from the right answer, without repeats.

    ``tol`` is how close two numbers may be and still be the same answer: a question that is answered to two decimals
    takes 0.006, since a slip that rounds to the right figure is the right figure.
    """
    seen: list[float] = [float(correct)]
    out: list[str] = []
    for candidate in candidates:
        try:
            value = float(str(candidate).replace(",", "."))
        except ValueError:
            out.append(f"{label}: {candidate}")
            continue
        if any(abs(value - s) <= tol for s in seen):
            continue
        seen.append(value)
        out.append(f"{label}: {fmt(value) if fmt else candidate}")
    step = 1
    while len(out) < 2:
        value = float(correct) + step
        step += 1
        if not any(abs(value - s) <= tol for s in seen):
            seen.append(value)
            out.append(f"{label}: {fmt(value) if fmt else (int(value) if value == int(value) else value)}")
    return out


def assign_tiers(category: str, questions: list[Question]) -> list[Question]:
    """The first questions go in every pack, the next in the standard one, the rest only in the complete one."""
    quick, standard, full = CATEGORIES[category].counts
    if len(questions) != full:
        raise ValueError(f"{category} has {len(questions)} questions, the plan says {full}")
    return [dataclasses.replace(q, tier=1 if i < quick else 2 if i < standard else 3) for i, q in enumerate(questions)]


# -- scripted answers, for the bank's own tests --------------------------------------------------------------


def calls(*items: tuple[str, dict[str, Any] | None], text: str = "") -> str:
    """A scripted reply that calls tools: ``calls(("get_weather", {"city": "Milano"}))``. ``None`` arguments mean broken JSON."""
    return "CALLS:" + json.dumps({"text": text, "calls": [{"name": n, "arguments": a} for n, a in items]})


def scripted(entry: str) -> Answer:
    """The answer a reference or wrong entry stands for: text, or the tool calls of a ``calls(...)`` entry."""
    if entry.startswith("CALLS:"):
        payload = json.loads(entry[6:])
        found = [ToolCall(name=c["name"], arguments=c["arguments"], raw=json.dumps(c["arguments"]) if c["arguments"] is not None else "{broken") for c in payload["calls"]]
        return Answer(text=payload.get("text", ""), raw=payload.get("text", ""), tool_calls=found, finish_reason="tool_calls")
    return Answer(text=entry, raw=entry, finish_reason="stop")


def tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """An OpenAI-style function tool definition."""
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required or []}},
    }
