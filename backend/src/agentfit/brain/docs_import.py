"""Bring the project's own decisions and lessons into its memory, from the documents where people and models write them.

``docs/DECISIONS.md`` and ``docs/LESSONS.md`` are the source of truth: a section is one entry. They are plain files, in git,
read by any model without a tool. This copies them into the Brain so ``recall`` finds them by words next to what the
sessions recorded. Every section has a slot, so running it again rewrites the sections that changed and adds the new ones.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentfit.brain.external import ProjectMemory

# document -> (kind of entry, tag)
DOCUMENTS = {"DECISIONS.md": ("decision", "decision-log"), "LESSONS.md": ("note", "lesson")}
_PATH = re.compile(r"`([\w./\\-]+\.[A-Za-z0-9]{1,5})`")


def sections(markdown: str) -> list[tuple[str, str]]:
    """(title, body) of every ``## `` section, in order. The text before the first one is not an entry."""
    found: list[tuple[str, list[str]]] = []
    for line in markdown.replace("\r\n", "\n").splitlines():
        if line.startswith("## "):
            found.append((line[3:].strip(), []))
        elif found:
            found[-1][1].append(line)
    return [(title, "\n".join(body).strip()) for title, body in found if "\n".join(body).strip()]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def import_docs(memory: ProjectMemory, docs_dir: Path | None = None) -> dict[str, int]:
    """Write every section of the documents into the memory. Returns how many entries each document gave."""
    folder = docs_dir or memory.workspace / "docs"
    counts: dict[str, int] = {}
    for name, (kind, tag) in DOCUMENTS.items():
        path = folder / name
        if not path.exists():
            continue
        entries = sections(path.read_text(encoding="utf-8-sig"))
        for title, body in entries:
            files = [p.replace("\\", "/") for p in _PATH.findall(body) if (memory.workspace / p).exists()]
            memory.service.add(
                memory.project.id, kind=kind, title=title[:200], body=body, files=list(dict.fromkeys(files))[:20], tags=[tag],
                source="user", agent="docs", slot=f"doc:{name}:{_slug(title)}", embed=False,
            )
        counts[name] = len(entries)
    return counts
