"""Enterprise documents: the sections, the tables and the totals a document must have, and the numbers it must not invent.

A document is checked piece by piece, so the verdict can say which piece is missing: this is the one family where
partial credit is the honest result (a purchase order with every section and a wrong total is not a failed document).
"""

from __future__ import annotations

import re
from typing import Any

from agentfit.arena import text as tx
from agentfit.arena.model import WRONG, Answer, Check, Question, Verdict, why
from agentfit.arena.verifiers import Context, register
from agentfit.arena.verifiers.basic import show_number

_BOLD_LINE = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?\s*$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.{1,60})$")
_LIST_MARK = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_PLACEHOLDERS = [
    # A checkbox («[ ]», «[x]») is not a placeholder, and neither is a link («[text](url)»).
    re.compile(r"\[(?![ xX]?\])[^\[\]\n]{1,60}\](?!\()"),
    re.compile(r"\{\{[^{}\n]*\}\}"),
    re.compile(r"<[A-Za-zÀ-ÿ][^<>\n]{0,40}>"),
    re.compile(r"\b(?:lorem ipsum|tbd|xxx+)\b", re.IGNORECASE),
]


def headings(body: str) -> list[tuple[int, str, bool]]:
    """The lines that act as headings, as (line number, folded title, whether only an exact match counts).

    A numbered line («1. Preparare il server») is a heading only when it is exactly a section's name: otherwise every
    step of a procedure would pass for one.
    """
    found = []
    for number, line in enumerate(body.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        title, exact_only = None, False
        if stripped.startswith("#"):
            title = stripped.lstrip("#")
        elif _BOLD_LINE.match(stripped):
            title = _BOLD_LINE.match(stripped).group(1)  # type: ignore[union-attr]
        elif _NUMBERED.match(stripped) and not stripped.endswith("."):
            title, exact_only = _NUMBERED.match(stripped).group(1), True  # type: ignore[union-attr]
        elif len(stripped) <= 60 and stripped.endswith(":") and not stripped.startswith(("|", "-", "*")):
            title = stripped[:-1]
        elif len(stripped) <= 60 and stripped.upper() == stripped and re.search(r"[A-ZÀ-Ý]{3}", stripped):
            title = stripped
        if title is not None:
            folded = tx.fold(title)
            if folded:
                found.append((number, folded, exact_only))
    return found


def _find_heading(marks: list[tuple[int, str, bool]], wanted: str, after: int) -> tuple[int, str, bool] | None:
    """The heading for a section: the best match at or after ``after`` (exact, then starting with it, then containing it)."""
    candidates = [m for m in marks if m[0] >= after]
    for matches in (lambda m: m[1] == wanted, lambda m: not m[2] and m[1].startswith(wanted), lambda m: not m[2] and tx.contains_phrase(m[1], wanted)):
        found = next((m for m in candidates if matches(m)), None)
        if found is not None:
            return found
    return None


def tables(body: str) -> list[list[list[str]]]:
    """Markdown tables as lists of rows of cells, header first, without the separator row."""
    out: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in [*body.splitlines(), ""]:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells) and any(c for c in cells):
                continue
            current.append(cells)
        elif current:
            out.append(current)
            current = []
    return out


def _numbers_of(cell: str) -> list[float]:
    return [c for hit in tx.find_numbers(cell) for c in hit.candidates]


@register("document")
def document(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    body = answer.text
    folded = tx.fold(body)
    checks: list[Check] = []

    def check(ok: bool, reason: Any) -> None:
        checks.append(Check(ok, reason))

    # Sections, present and in order.
    marks = headings(body)
    last_line, last_title = -1, ""
    for title in spec.get("sections", []):
        wanted = tx.fold(title)
        hit = _find_heading(marks, wanted, last_line + 1)
        if hit is None:
            # Not after the previous section: it may still be there, earlier, which is a matter of order and not of presence.
            hit = _find_heading(marks, wanted, 0)
            if hit is None:
                check(False, why("The section «{title}» is missing.", title=title))
            else:
                check(False, why("The sections are out of order: «{first}» must come before «{second}».", first=title, second=last_title))
            continue
        check(True, why("The section «{title}» is there.", title=title))
        last_line, last_title = hit[0], title

    # Facts that have to be written down.
    for fact in spec.get("facts", []):
        check(tx.contains_phrase(folded, tx.fold(fact)), why("«{fact}» is mentioned.", fact=fact) if tx.contains_phrase(folded, tx.fold(fact)) else why("«{fact}» is not mentioned.", fact=fact))

    # Tables: the columns, the rows, the numbers in a column.
    found_tables = tables(body)
    for wanted in spec.get("tables", []):
        columns = [tx.fold(c) for c in wanted["columns"]]
        match = next((t for t in found_tables if t and all(any(h == c or h.startswith(c) for h in [tx.fold(x) for x in t[0]]) for c in columns)), None)
        if match is None:
            check(False, why("There is no table with the columns {columns}.", columns=", ".join(wanted["columns"])))
            continue
        check(True, why("The table with the columns {columns} is there.", columns=", ".join(wanted["columns"])))
        rows = [r for r in match[1:] if r and r[0]]
        if "min_rows" in wanted:
            check(len(rows) >= wanted["min_rows"], why("The table has {n} rows, at least {wanted} expected.", n=len(rows), wanted=wanted["min_rows"]))
        header = [tx.fold(x) for x in match[0]]
        for column, values in (wanted.get("column_values") or {}).items():
            wanted_column = tx.fold(column)
            index = next((i for i, h in enumerate(header) if h == wanted_column or h.startswith(wanted_column)), None)
            if index is None:
                check(False, why("The column «{column}» is missing.", column=column))
                continue
            for row_number, expected in enumerate(values):
                cell = rows[row_number][index] if row_number < len(rows) and index < len(rows[row_number]) else ""
                ok = any(abs(c - expected) <= 0.01 for c in _numbers_of(cell))
                check(ok, why("Row {row}, {column}: {value}.", row=row_number + 1, column=column, value=cell or "—") if ok else
                      why("Row {row}, {column}: expected {expected}, found «{found}».", row=row_number + 1, column=column, expected=show_number(expected), found=cell or "—"))

    # Totals: a line that names the total and carries its number.
    lines = body.splitlines()
    for total in spec.get("totals", []):
        label = tx.fold(total["label"])
        tolerance = float(total.get("tolerance", 0.01))
        related = [ln for ln in lines if tx.contains_phrase(tx.fold(ln), label)]
        numbers = [c for ln in related for c in _numbers_of(_LIST_MARK.sub("", ln))]
        if any(abs(n - float(total["value"])) <= tolerance for n in numbers):
            check(True, why("{label}: {value}.", label=total["label"], value=show_number(float(total["value"]))))
        elif not related:
            check(False, why("There is no line for «{label}», expected {value}.", label=total["label"], value=show_number(float(total["value"]))))
        else:
            shown = ", ".join(show_number(n) for n in numbers[:4]) or "—"
            check(False, why("{label}: expected {expected}, found {found}.", label=total["label"], expected=show_number(float(total["value"])), found=shown))

    # No number that nobody gave it.
    if "numbers_allowed" in spec:
        # Whatever number the question itself gives (an item called «Monitor 27 pollici») and whatever it asks to compute.
        allowed = [float(x) for x in spec["numbers_allowed"]] + [c for hit in tx.find_numbers(question.prompt) for c in hit.candidates]
        ignore = int(spec.get("ignore_up_to", 0))
        invented: list[str] = []
        for line in lines:
            for hit in tx.find_numbers(_LIST_MARK.sub("", line)):
                if any(abs(c - a) <= 0.005 for c in hit.candidates for a in allowed):
                    continue
                if ignore and hit.value == int(hit.value) and 0 <= hit.value <= ignore:
                    continue
                if hit.raw not in invented:
                    invented.append(hit.raw)
        check(not invented, why("Every number comes from the data.") if not invented else why("Numbers that are not in the data: {numbers}.", numbers=", ".join(invented[:8])))

    if spec.get("no_placeholders"):
        left = [m.group(0) for pattern in _PLACEHOLDERS for m in pattern.finditer(body)]
        check(not left, why("No placeholders are left.") if not left else why("It still has placeholders: {found}.", found=", ".join(left[:4])))

    if "words" in spec:
        count = tx.count_words(body)
        lo, hi = spec["words"].get("min"), spec["words"].get("max")
        ok = (lo is None or count >= lo) and (hi is None or count <= hi)
        check(ok, why("The document has {n} words.", n=count) if ok else why("The document has {n} words, it should have {lo} to {hi}.", n=count, lo=lo if lo is not None else 0, hi=hi if hi is not None else "…"))

    good = sum(1 for c in checks if c.ok)
    if not checks or good == len(checks):
        return Verdict.passed(checks=checks, method="document", expected=question.expected)
    failing = [c for c in checks if not c.ok]
    reason = failing[0].reason if len(failing) == 1 else why("{n} of {total} requirements are not met.", n=len(failing), total=len(checks))
    if good == 0:
        return Verdict.failed(WRONG, reason, checks=checks, method="document", expected=question.expected)
    return Verdict.partial(good / len(checks), reason, checks=checks, method="document", expected=question.expected)
