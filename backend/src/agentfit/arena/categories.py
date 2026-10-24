"""The categories of the suite, how they are grouped, and which radar axis ("area") each belongs to.

The counts are the plan the user approved: how many questions a category has in the quick, standard and complete
packs. ``tests/test_arena_bank.py`` checks the question bank against them, so a category cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass

# A pack is every question whose tier is at most this.
PRESETS: dict[str, int] = {"quick": 1, "standard": 2, "full": 3}


@dataclass(frozen=True)
class CategoryInfo:
    id: str
    group: str
    area: str
    # Questions in the quick / standard / complete pack.
    counts: tuple[int, int, int]
    # False for the one category that is read by people and never given a score.
    scored: bool = True
    # What the model must be able to do: "vision" makes a question skipped for a model that cannot see.
    needs: tuple[str, ...] = ()


_ALL = (
    CategoryInfo("general", "knowledge", "knowledge", (4, 8, 14)),
    CategoryInfo("reading", "knowledge", "knowledge", (2, 5, 10)),
    CategoryInfo("math", "knowledge", "math", (4, 9, 14)),
    CategoryInfo("logic", "knowledge", "logic", (3, 7, 12)),
    CategoryInfo("coding", "code", "coding", (4, 8, 14)),
    CategoryInfo("sql", "code", "infra", (3, 7, 12)),
    CategoryInfo("terraform", "code", "infra", (2, 6, 10)),
    CategoryInfo("ansible", "code", "infra", (2, 6, 10)),
    CategoryInfo("documents", "documents", "documents", (2, 6, 10)),
    CategoryInfo("pdf", "documents", "documents", (2, 5, 8)),
    CategoryInfo("json", "documents", "agents", (3, 7, 12)),
    CategoryInfo("vision", "documents", "vision", (2, 6, 10), needs=("vision",)),
    CategoryInfo("tools", "agents", "agents", (4, 9, 14)),
    CategoryInfo("impossible", "agents", "honesty", (4, 9, 14)),
    CategoryInfo("instructions", "agents", "honesty", (3, 7, 12)),
    CategoryInfo("injection", "agents", "honesty", (2, 5, 8)),
    CategoryInfo("longctx", "agents", "knowledge", (0, 2, 4)),
    CategoryInfo("writing", "agents", "writing", (2, 3, 4), scored=False),
)
CATEGORIES: dict[str, CategoryInfo] = {c.id: c for c in _ALL}
GROUPS = ("knowledge", "code", "documents", "agents")
# The axes of the radar, in the order they are drawn. "writing" is never one of them: nothing in it is scored.
AREAS = ("knowledge", "math", "logic", "coding", "infra", "documents", "agents", "honesty", "vision")


def area_of(category: str) -> str | None:
    info = CATEGORIES.get(category)
    return info.area if info else None


def order_of(category: str) -> int:
    ids = list(CATEGORIES)
    return ids.index(category) if category in ids else len(ids)
