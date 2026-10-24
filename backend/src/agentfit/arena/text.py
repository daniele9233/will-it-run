"""Reading what a model wrote: its reasoning apart from its answer, the marked final answer, numbers in Italian
and English formats, code blocks, words and sentences.

Everything here is deterministic string work. The verifiers build on it, so a number read one way here is read
the same way for every model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# -- reasoning ------------------------------------------------------------------

_THINK_TAGS = "think|thinking|thought|reasoning"
_THINK_BLOCK = re.compile(rf"<({_THINK_TAGS})>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_THINK_CLOSE = re.compile(rf"</({_THINK_TAGS})>", re.IGNORECASE)
_THINK_OPEN = re.compile(rf"<({_THINK_TAGS})>", re.IGNORECASE)


def strip_think(raw: str) -> tuple[str, str]:
    """(what the model answered, what it thought). Models print their reasoning inline between tags, or only the closing tag."""
    thoughts: list[str] = []
    out = raw
    for match in _THINK_BLOCK.finditer(out):
        thoughts.append(match.group(2).strip())
    out = _THINK_BLOCK.sub("", out)
    close = _THINK_CLOSE.search(out)
    if close:
        thoughts.append(out[: close.start()].strip())
        out = out[close.end() :]
    opened = _THINK_OPEN.search(out)
    if opened:
        # A thought that never ended: everything after the tag is thinking, and no answer was given.
        thoughts.append(out[opened.end() :].strip())
        out = out[: opened.start()]
    return out.strip(), "\n".join(t for t in thoughts if t)


# -- folding ----------------------------------------------------------------------

_ARTICLES = {"il", "lo", "la", "i", "gli", "le", "l", "un", "uno", "una", "the", "a", "an"}


def fold(value: str) -> str:
    """Lower case, no accents, no punctuation, single spaces: what two spellings of an answer have in common."""
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", value).strip()


def fold_answer(value: str) -> str:
    """``fold`` without the articles at the start: «L'Aquila» and «Aquila» are the same answer."""
    words = fold(value).split()
    while len(words) > 1 and words[0] in _ARTICLES:
        words.pop(0)
    return " ".join(words)


def contains_phrase(folded_text: str, folded_phrase: str) -> bool:
    """Whether the phrase occurs in the text as whole words. Both are already folded."""
    if not folded_phrase:
        return False
    return re.search(rf"(?:^| ){re.escape(folded_phrase)}(?: |$)", folded_text) is not None


# -- numbers ----------------------------------------------------------------------

_GROUP_SPACES = "\u00a0\u202f\u2009' \u2019"
_NUM = re.compile(
    r"""
    (?<![\w.,])
    (?P<sign>[-+\u2212])?
    (?P<num>
        \d{1,3}(?:[\u00a0\u202f\u2009'\u2019 ]\d{3})+(?:[.,]\d+)?
      | \d+(?:[.,]\d+)*
    )
    (?:[eE](?P<exp>[-+]?\d+))?
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class NumberHit:
    raw: str
    start: int
    end: int
    candidates: tuple[float, ...]

    @property
    def value(self) -> float:
        return self.candidates[0]


def _candidates_of(num: str) -> list[float]:
    """Every reading of an unsigned number written with grouping and decimal marks of either language."""
    if any(c in num for c in _GROUP_SPACES):
        grouped = re.fullmatch(rf"(\d{{1,3}})((?:[{re.escape(_GROUP_SPACES)}]\d{{3}})+)([.,]\d+)?", num)
        if not grouped:
            return []
        digits = grouped.group(1) + re.sub(rf"[{re.escape(_GROUP_SPACES)}]", "", grouped.group(2))
        tail = grouped.group(3)
        return [float(digits + "." + tail[1:])] if tail else [float(digits)]
    dots, commas = num.count("."), num.count(",")
    if dots and commas:
        decimal = "." if num.rfind(".") > num.rfind(",") else ","
        group = "," if decimal == "." else "."
        whole, _, frac = num.rpartition(decimal)
        if group in frac or not re.fullmatch(rf"\d{{1,3}}(?:\{group}\d{{3}})*", whole):
            return []
        return [float(whole.replace(group, "") + "." + frac)]
    mark = "." if dots else "," if commas else ""
    if not mark:
        return [float(num)]
    parts = num.split(mark)
    if len(parts) > 2:
        # 1.234.567: the mark is a thousands separator, so every group after the first has three digits.
        if all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
            return [float("".join(parts))]
        return []
    whole, frac = parts
    if len(frac) == 3 and 1 <= len(whole) <= 3 and whole != "0":
        return [float(f"{whole}.{frac}"), float(whole + frac)]
    return [float(f"{whole}.{frac}")]


def number_candidates(raw: str) -> list[float]:
    """The values a written number can mean: one, or two when ``1.234`` could be a thousand or a decimal."""
    value = raw.strip().replace("\u2212", "-")
    sign = -1.0 if value.startswith("-") else 1.0
    value = value.lstrip("+-")
    exponent = 0
    scientific = re.fullmatch(r"(.+?)[eE]([+-]?\d+)", value)
    if scientific:
        value, exponent = scientific.group(1), int(scientific.group(2))
    try:
        found = _candidates_of(value)
    except ValueError:
        return []
    return [sign * v * 10.0**exponent for v in found]


def find_numbers(value: str) -> list[NumberHit]:
    hits: list[NumberHit] = []
    for match in _NUM.finditer(value):
        raw = match.group("num")
        exponent = match.group("exp")
        written = (match.group("sign") or "") + raw + (f"e{exponent}" if exponent else "")
        candidates = number_candidates(written)
        if candidates:
            hits.append(NumberHit(raw=raw, start=match.start(), end=match.end(), candidates=tuple(candidates)))
    return hits


# -- the marked final answer -------------------------------------------------------

_MARK = r"(?:\*\*|__)?"
_MARKER = re.compile(
    rf"^[ \t>*_#-]*{_MARK}(?:risposta(?:\s+finale)?|risultato(?:\s+finale)?|final\s+answer|answer|result)"
    rf"{_MARK}\s*[:=\uff1a]\s*{_MARK}\s*(?P<body>.+?)\s*{_MARK}\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BOXED = re.compile(r"\\boxed\{([^{}]*)\}")


def final_answer(reply: str) -> tuple[str | None, str]:
    """The answer a model marked as final: the last «Risposta: …» line, else the last ``\\boxed{…}``, else None."""
    marked = list(_MARKER.finditer(reply))
    if marked:
        return marked[-1].group("body").strip(), "marker"
    boxed = list(_BOXED.finditer(reply))
    if boxed:
        return boxed[-1].group(1).strip(), "boxed"
    return None, "none"


# -- code ---------------------------------------------------------------------------

_FENCE = re.compile(r"^[ \t]*```([\w+#.-]*)[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class CodeBlock:
    lang: str
    code: str
    closed: bool = True


def code_blocks(reply: str) -> list[CodeBlock]:
    """The fenced blocks of a reply. A block still open at the end (the reply was cut off) is returned with ``closed=False``."""
    fences = list(_FENCE.finditer(reply))
    blocks: list[CodeBlock] = []
    i = 0
    while i < len(fences):
        opening = fences[i]
        body_start = opening.end() + 1
        if i + 1 < len(fences):
            closing = fences[i + 1]
            blocks.append(CodeBlock(opening.group(1).lower(), reply[body_start : closing.start()]))
            i += 2
        else:
            blocks.append(CodeBlock(opening.group(1).lower(), reply[body_start:], closed=False))
            i += 1
    return blocks


# -- words and sentences ---------------------------------------------------------------

_ABBREVIATIONS = {"dr", "dott", "dottssa", "sig", "sigg", "sig.ra", "prof", "ing", "avv", "mr", "mrs", "ms", "jr", "sr", "st", "es", "ecc", "etc", "vs", "cfr", "p", "pp", "n", "no", "fig"}


def count_words(value: str) -> int:
    return sum(1 for token in value.split() if re.search(r"\w", token))


def count_sentences(value: str) -> int:
    count = 0
    pending = False
    for token in value.split():
        pending = True
        bare = token.rstrip(".!?…\"'»)]*_")
        if bare != token and token.rstrip("\"'»)]*_")[-1:] in ".!?…":
            if token.rstrip("\"'»)]*_")[-1:] == "." and bare.lower() in _ABBREVIATIONS:
                continue
            count += 1
            pending = False
    return count + (1 if pending else 0)
