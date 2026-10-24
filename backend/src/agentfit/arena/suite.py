"""The suite: which questions a run asks, built from a seed, with a version and a hash that say exactly what they were.

Two hashes are kept. ``bank_hash`` identifies the whole question bank as it is for a seed (every pack, every category),
and the tests pin it for the canonical seed, so the bank cannot change without a new version. ``hash`` identifies the
questions this run actually asks. Two runs with the same ``hash`` were asked byte for byte the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from agentfit.arena import SUITE_ID, SUITE_VERSION
from agentfit.arena.bank import build_all
from agentfit.arena.categories import CATEGORIES, PRESETS
from agentfit.arena.model import Question, digest

CANONICAL_SEED = 42
DEFAULT_SEED = CANONICAL_SEED


@dataclass
class Suite:
    seed: int
    preset: str | None
    categories: list[str]
    questions: list[Question]
    hash: str
    bank_hash: str
    id: str = SUITE_ID
    version: str = SUITE_VERSION
    counts: dict[str, int] = field(default_factory=dict)


@lru_cache(maxsize=6)
def _bank(seed: int) -> tuple[tuple[Question, ...], str]:
    questions = tuple(build_all(seed))
    return questions, digest({"version": SUITE_VERSION, "questions": [q.fingerprint() for q in questions]})


def build_suite(seed: int = DEFAULT_SEED, preset: str | None = "standard", categories: list[str] | None = None) -> Suite:
    """The questions of a pack (``quick``, ``standard`` or ``full``), optionally limited to some categories, in the bank's order."""
    bank, bank_hash = _bank(seed)
    tier = PRESETS[preset] if preset else 3
    wanted = [c for c in CATEGORIES if categories is None or c in categories]
    chosen = [q for q in bank if q.tier <= tier and q.category in wanted]
    return Suite(
        seed=seed, preset=preset, categories=wanted, questions=chosen,
        hash=digest({"version": SUITE_VERSION, "seed": seed, "questions": [q.fingerprint() for q in chosen]}), bank_hash=bank_hash,
        counts={c: sum(1 for q in chosen if q.category == c) for c in wanted},
    )
