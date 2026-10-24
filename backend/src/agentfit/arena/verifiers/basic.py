"""Short factual answers, numbers, choices between named answers, and rules about the shape of a text."""

from __future__ import annotations

import json
import re
from typing import Any

from agentfit.arena import text as tx
from agentfit.arena.model import FORMAT, MANUAL, WRONG, Answer, Check, Question, Reason, Verdict, tl, why
from agentfit.arena.verifiers import Context, register


def shorten(value: str, limit: int = 120) -> str:
    one_line = " ".join(value.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def show_number(value: float) -> str:
    return f"{value:.10g}"


# -- a short factual answer ---------------------------------------------------------------------


@register("alias")
def alias(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    segment, _method = tx.final_answer(answer.text)
    marked = segment is not None
    candidate = segment if segment is not None else answer.text
    folded = tx.fold(candidate)
    answers: list[str] = list(spec["answers"])
    expected = answers[0]
    if spec.get("exact"):
        found = any(tx.fold_answer(candidate) == tx.fold_answer(a) for a in answers)
    else:
        found = any(tx.contains_phrase(folded, tx.fold_answer(a)) or tx.contains_phrase(folded, tx.fold(a)) for a in answers)
    decoy = next((d for d in spec.get("decoys", []) if tx.contains_phrase(folded, tx.fold_answer(d))), None)
    shown = shorten(candidate)
    if found and decoy is None:
        return Verdict.passed(extracted=shown, expected=expected, method="match", format_ok=marked)
    if decoy is not None:
        reason = why("It also gives «{decoy}», which is wrong: the answer is «{expected}».", decoy=decoy, expected=expected) if found else \
            why("Expected «{expected}», found «{found}».", expected=expected, found=decoy)
        return Verdict.failed(WRONG, reason, extracted=shown, expected=expected, method="match", format_ok=marked)
    return Verdict.failed(WRONG, why("Expected «{expected}», found «{found}».", expected=expected, found=shown),
                          extracted=shown, expected=expected, method="match", format_ok=marked)


# -- numbers -------------------------------------------------------------------------------------


def _fractions(segment: str) -> list[float]:
    out = []
    for match in re.finditer(r"(?<![\d.,/])(-?\d+)\s*/\s*(\d+)(?![\d/])", segment):
        if int(match.group(2)):
            out.append(int(match.group(1)) / int(match.group(2)))
    return out


@register("number")
def number(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    value = float(spec["value"])
    tolerance = spec.get("tolerance")
    tolerance = (1e-9 * max(1.0, abs(value))) if tolerance is None else float(tolerance)
    if spec.get("relative"):
        tolerance *= abs(value)
    segment, _method = tx.final_answer(answer.text)
    marked = segment is not None
    if segment is not None:
        hits = tx.find_numbers(segment)
        # A marked line may hold a little calculation («15% di 240 = 36»); a long one is read from its end.
        pool = hits if len(hits) <= 3 else hits[-1:]
        extra = _fractions(segment)
    else:
        hits = tx.find_numbers(answer.text)
        pool = hits[-1:]
        extra = []
    if not pool and not extra:
        return Verdict.failed(FORMAT, why("No number was found in the reply."), expected=show_number(value), method="number", format_ok=False)
    candidates = [c for hit in pool for c in hit.candidates] + extra
    if spec.get("integer"):
        candidates = [c for c in candidates if c == round(c)]
    ok = any(abs(c - value) <= tolerance for c in candidates)
    found = show_number(pool[-1].candidates[0]) if pool else show_number(extra[0])
    if ok:
        return Verdict.passed(extracted=found, expected=show_number(value), method="number", format_ok=marked)
    return Verdict.failed(WRONG, why("Expected {expected}, found {found}.", expected=show_number(value), found=found),
                          extracted=found, expected=show_number(value), method="number", format_ok=marked)


# -- a choice between named answers -----------------------------------------------------------------


@register("choice")
def choice(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    segment, _method = tx.final_answer(answer.text)
    marked = segment is not None
    if segment is None:
        lines = [ln for ln in answer.text.splitlines() if ln.strip()]
        segment = lines[-1] if lines else ""
    folded = tx.fold(segment)
    options: dict[str, list[str]] = spec["options"]
    spans: list[tuple[int, int, str]] = []
    for name, synonyms in options.items():
        for synonym in synonyms:
            if synonym.startswith("re:"):
                m = re.search(synonym[3:], folded)
                if m:
                    spans.append((m.start(), m.end(), name))
                continue
            phrase = tx.fold(synonym)
            for m in re.finditer(rf"(?:^| ){re.escape(phrase)}(?: |$)", folded):
                spans.append((m.start(), m.end(), name))
    # A longer phrase swallows the shorter one inside it: «non valido» is not also «valido».
    spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
    kept: list[tuple[int, int, str]] = []
    for start, end, name in spans:
        if not any(start < k_end and k_start < end for k_start, k_end, _ in kept):
            kept.append((start, end, name))
    chosen = sorted({name for _, _, name in kept})
    correct = str(spec["correct"])
    if not chosen:
        return Verdict.failed(FORMAT, why("No answer could be read in «{found}».", found=shorten(segment)), expected=correct, extracted=shorten(segment), method="choice", format_ok=False)
    if len(chosen) > 1:
        return Verdict.failed(FORMAT, why("It gives more than one answer: {found}.", found=", ".join(chosen)), expected=correct, extracted=", ".join(chosen), method="choice", format_ok=False)
    if chosen[0] == correct:
        return Verdict.passed(extracted=chosen[0], expected=correct, method="choice", format_ok=marked)
    return Verdict.failed(WRONG, why("Expected «{expected}», found «{found}».", expected=correct, found=chosen[0]),
                          extracted=chosen[0], expected=correct, method="choice", format_ok=marked)


# -- rules about the shape of a text -------------------------------------------------------------------

_BULLET = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+\S", re.MULTILINE)  # noqa: RUF001 (an en dash is a bullet in some texts)
_STOPWORDS = {
    "it": {"il", "lo", "la", "gli", "le", "di", "che", "e", "un", "una", "per", "con", "non", "sono", "del", "della", "nel", "alla", "come", "anche", "piu", "questo", "questa"},
    "en": {"the", "of", "and", "to", "in", "is", "that", "for", "with", "not", "are", "this", "as", "on", "it", "be", "by", "from", "an", "or"},
}


def _count_rule(label: Any, n: int, rule: dict[str, Any]) -> tuple[bool, Reason]:
    """Whether ``n`` fits the rule's ``equals`` / ``min`` / ``max``, and what was found against what was asked."""
    lo, hi, eq = rule.get("min"), rule.get("max"), rule.get("equals")
    if eq is not None:
        return n == eq, why("{label}: {n} found, exactly {wanted} expected.", label=label, n=n, wanted=eq)
    ok = (lo is None or n >= lo) and (hi is None or n <= hi)
    if lo is not None and hi is not None:
        return ok, why("{label}: {n} found, {lo} to {hi} expected.", label=label, n=n, lo=lo, hi=hi)
    if lo is not None:
        return ok, why("{label}: {n} found, at least {wanted} expected.", label=label, n=n, wanted=lo)
    return ok, why("{label}: {n} found, at most {wanted} expected.", label=label, n=n, wanted=hi)


def _strip_fence(value: str) -> str:
    blocks = tx.code_blocks(value)
    inner = value.strip()
    if len(blocks) == 1 and inner.startswith("```") and inner.endswith("```"):
        return blocks[0].code.strip()
    return inner


def _rule(rule: dict[str, Any], body: str) -> tuple[bool, Reason]:
    kind = rule["kind"]
    stripped = body.strip()
    if kind == "words":
        return _count_rule(tl("Words"), tx.count_words(body), rule)
    if kind == "sentences":
        return _count_rule(tl("Sentences"), tx.count_sentences(body), rule)
    if kind == "paragraphs":
        return _count_rule(tl("Paragraphs"), len([p for p in re.split(r"\n\s*\n", stripped) if p.strip()]), rule)
    if kind == "lines":
        return _count_rule(tl("Lines"), len([ln for ln in body.splitlines() if ln.strip()]), rule)
    if kind == "bullets":
        return _count_rule(tl("List items"), len(_BULLET.findall(body)), rule)
    if kind == "chars":
        return _count_rule(tl("Characters"), len(stripped), rule)
    if kind == "uppercase":
        letters = [c for c in body if c.isalpha()]
        return bool(letters) and all(c.isupper() for c in letters), why("Every letter must be upper case.")
    if kind == "lowercase":
        letters = [c for c in body if c.isalpha()]
        return bool(letters) and all(c.islower() for c in letters), why("Every letter must be lower case.")
    if kind == "no_letter":
        letter = tx.fold(str(rule["letter"]))
        return letter not in tx.fold(body).replace(" ", ""), why("The letter «{letter}» must not appear.", letter=rule["letter"])
    if kind == "no_commas":
        return "," not in body, why("There must be no commas.")
    if kind == "starts_with":
        return stripped.startswith(str(rule["text"])), why("It must start with «{text}».", text=rule["text"])
    if kind == "ends_with":
        return stripped.endswith(str(rule["text"])), why("It must end with «{text}».", text=rule["text"])
    if kind == "includes":
        folded = tx.fold(body)
        missing = [w for w in rule["words"] if not tx.contains_phrase(folded, tx.fold(w))]
        return not missing, why("It must contain {words}.", words=", ".join(f"«{w}»" for w in rule["words"])) if not missing else \
            why("Missing: {words}.", words=", ".join(f"«{w}»" for w in missing))
    if kind == "excludes":
        folded = tx.fold(body)
        present = [w for w in rule["words"] if tx.contains_phrase(folded, tx.fold(w))]
        return not present, why("It must not contain {words}.", words=", ".join(f"«{w}»" for w in rule["words"])) if not present else \
            why("It contains {words}, which it must not.", words=", ".join(f"«{w}»" for w in present))
    if kind == "word_count_of":
        n = len(re.findall(rf"(?:^| ){re.escape(tx.fold(rule['word']))}(?: |$)", tx.fold(body)))
        return _count_rule(f"«{rule['word']}»", n, rule)
    if kind == "regex":
        found = re.search(rule["pattern"], stripped, re.MULTILINE) is not None
        must = rule.get("must", True)
        return found == must, why("It must match {pattern}.", pattern=rule["pattern"]) if must else why("It must not match {pattern}.", pattern=rule["pattern"])
    if kind == "json_only":
        try:
            json.loads(_strip_fence(body))
        except (ValueError, TypeError):
            return False, why("The reply must be a JSON document and nothing else.")
        return True, why("The reply must be a JSON document and nothing else.")
    if kind == "title":
        return re.search(r"<<[^<>\n]+>>", body) is not None, why("It needs a title written as <<title>>.")
    if kind == "placeholders":
        return _count_rule(tl("Placeholders in [brackets]"), len(re.findall(r"\[[^\[\]\n]+\]", body)), rule)
    if kind == "language":
        words = tx.fold(body).split()
        scores = {lang: sum(1 for w in words if w in stop) for lang, stop in _STOPWORDS.items()}
        wanted = str(rule["lang"])
        other = max((v for k, v in scores.items() if k != wanted), default=0)
        return scores.get(wanted, 0) > other, why("It must be written in {lang}.", lang=tl("Italian" if wanted == "it" else "English"))
    raise ValueError(f"unknown rule kind: {kind}")


@register("rules")
def rules(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    body = answer.text
    checks = []
    for rule in spec["rules"]:
        ok, reason = _rule(rule, body)
        checks.append(Check(ok, reason))
    passed = sum(1 for c in checks if c.ok)
    if passed == len(checks):
        return Verdict.passed(checks=checks, method="rules", expected=question.expected)
    failed_reasons = [c for c in checks if not c.ok]
    reason = failed_reasons[0].reason if len(failed_reasons) == 1 else why("{n} of {total} rules were broken.", n=len(failed_reasons), total=len(checks))
    if passed == 0:
        return Verdict.failed(WRONG, reason, checks=checks, method="rules", expected=question.expected)
    return Verdict.partial(passed / len(checks), reason, checks=checks, method="rules", expected=question.expected)


# -- what nothing can decide ---------------------------------------------------------------------------


@register("manual")
def manual(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    """Open writing has no right answer a program can find. It is kept, shown beside the others, and never scored."""
    return Verdict.unscored(MANUAL, why("No code can judge this: read the answers side by side. It is not counted as right or wrong."),
                            extracted=shorten(answer.text, 200), method="manual")
