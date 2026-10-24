"""Words and vectors: what keyword search and semantic search are computed from."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from array import array
from collections.abc import Iterable, Sequence

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# authService -> auth Service, HTTPServer -> HTTP Server
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Words that say nothing about what a text is about, in the two languages of the interface.
_STOPWORD_LIST = """
    a an and are as at be been but by can could did do does for from had has have how i if in into is it its
    may more most must my no not of on or our should so some such than that the their them then there these
    they this those to was we were what when where which who why will with would you your about after all
    also any before each new now only other out over same too under up use used using very via
    il lo la le gli un uno una di da del dello della dei degli delle al allo alla ai agli alle dal dallo
    dalla dai dagli dalle nel nello nella nei negli nelle sul sullo sulla sui sugli sulle con su per tra fra
    che chi cui non come dove quando perche ma se ed anche piu meno molto poco questo questa questi queste
    quello quella quelli quelle sono sei siamo siete era erano essere ha hai ho hanno abbiamo avere fare
    fatto deve devo devi puo posso puoi mi ti ci vi si ne li io tu lui lei noi voi loro mio tuo suo
    """
STOPWORDS = frozenset(_STOPWORD_LIST.split())


def _plain(text: str) -> str:
    """Lower case without accents, so "perché" and "perche" are the same word."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def terms(text: str) -> list[str]:
    """The words of a text, in order, with repeats.

    Identifiers are taken apart (``auth_service.py`` and ``authService`` both give
    ``auth`` and ``service``), so a question in plain words finds a file name and a file
    name finds prose about it.
    """
    out: list[str] = []
    for word in _WORD.findall(text or ""):
        for part in _CAMEL.sub(" ", word).split():
            term = _plain(part)
            if len(term) >= 2 and term not in STOPWORDS:
                out.append(term)
    return out


def stem(term: str) -> str:
    """A longer word cut one letter short, so "errors", "errore" and "errori" are one word. Crude, and the same for both languages."""
    return term[:-1] if len(term) >= 5 else term


def stems(text: str) -> set[str]:
    return {stem(term) for term in terms(text)}


def shared(query_stems: set[str], text: str) -> int:
    """How many of the query's words a text has."""
    words = set(terms(text))
    return sum(1 for s in query_stems if any(w.startswith(s) if len(s) >= 4 else w == s for w in words))


def index_text(*parts: str) -> str:
    """What is stored in the keyword index for these texts."""
    return " ".join(terms(" ".join(p for p in parts if p)))


def match_query(text: str, limit: int = 24) -> str | None:
    """An FTS5 query that finds memories sharing words with ``text``. None when it has no words.

    A word of four letters or more matches by its stem, as a prefix: "errors" finds ``error*``.
    """
    seen: dict[str, None] = {}
    for term in terms(text):
        seen.setdefault(f'"{stem(term)}"*' if len(term) >= 4 else f'"{term}"')
        if len(seen) >= limit:
            break
    return " OR ".join(seen) if seen else None


def content_hash(kind: str, title: str, body: str) -> str:
    """The same memory written twice has the same hash, whatever its spacing and case."""
    canonical = " ".join(_plain(f"{kind} {title} {body}").split())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def clean_path(raw: str) -> str:
    """A file path as the graph names it: relative, with forward slashes."""
    path = (raw or "").strip().strip("\"'`").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


def looks_like_path(text: str) -> bool:
    """True for "src/app.py" or "README.md"; false for "the storage layer"."""
    candidate = text.strip()
    if not candidate or " " in candidate or len(candidate) > 200:
        return False
    return "/" in candidate or "\\" in candidate or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", candidate))


# -- vectors ------------------------------------------------------------------


def pack(vector: Sequence[float]) -> bytes | None:
    """A vector as stored: unit length, 32-bit floats. None for an empty or zero vector."""
    norm = math.sqrt(math.fsum(v * v for v in vector))
    if not vector or norm == 0.0 or not math.isfinite(norm):
        return None
    return array("f", (v / norm for v in vector)).tobytes()


def unpack(blob: bytes | None) -> array[float] | None:
    if not blob:
        return None
    vector: array[float] = array("f")
    vector.frombytes(blob)
    return vector


def similarity(a: Sequence[float] | None, b: Sequence[float] | None) -> float | None:
    """Cosine of two stored vectors. They are unit length, so it is their dot product."""
    if a is None or b is None or len(a) != len(b):
        return None
    return float(math.sumprod(a, b))


def standing(values: Iterable[float]) -> tuple[float, float]:
    """Mean and spread of similarities, to tell a real match from the model's usual level.

    Every embedding model has its own baseline: two unrelated texts score 0.1 with one and
    0.45 with another. A match is what stands above the rest of the project, not what
    passes a fixed number.
    """
    data = list(values)
    if not data:
        return 0.0, 0.0
    mean = math.fsum(data) / len(data)
    spread = math.sqrt(math.fsum((v - mean) ** 2 for v in data) / len(data))
    return mean, spread
