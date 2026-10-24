"""The question bank: one module per category, each with a ``build(seed)`` that returns every question of the category.

A question that is fixed (a fact, a coding task) does not depend on the seed; a generated one (a sum, a puzzle, a picture)
draws its numbers from ``Rng(seed, question id)``, so the same seed always gives the same question.
"""

from __future__ import annotations

from agentfit.arena.bank import (
    ansible,
    coding,
    documents,
    general,
    impossible,
    injection,
    instructions,
    kit,
    logic,
    longctx,
    mathematics,
    pdfs,
    postgres,
    reading,
    structured,
    terraform,
    toolcalls,
    vision,
    writing,
)
from agentfit.arena.categories import CATEGORIES
from agentfit.arena.model import Question

# category id -> module. Imported by name rather than looked up by it, so that a frozen build finds every one of them.
MODULES = {
    "general": general, "reading": reading, "math": mathematics, "logic": logic, "coding": coding, "sql": postgres,
    "terraform": terraform, "ansible": ansible, "documents": documents, "pdf": pdfs, "json": structured, "vision": vision,
    "tools": toolcalls, "impossible": impossible, "instructions": instructions, "injection": injection, "longctx": longctx,
    "writing": writing,
}


def build_category(category: str, seed: int) -> list[Question]:
    return kit.assign_tiers(category, MODULES[category].build(seed))


def build_all(seed: int) -> list[Question]:
    questions: list[Question] = []
    for category in CATEGORIES:
        questions.extend(build_category(category, seed))
    return questions
