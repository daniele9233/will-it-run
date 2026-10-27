"""The memory of a project for programs that are not a run of the app: a coding agent over MCP, a hook, a person at a terminal.

A run reaches the Brain through ``BrainSession``. These callers have no run: they name a folder, and the memory of that
folder is the project. No embedding model is asked for. A hook or a tool call must not load a model onto the card, so the
search is by words and through the graph; the entries get their vectors the next time the app has a model loaded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentfit.brain.embed import NoEmbedder
from agentfit.brain.models import Memory, Project
from agentfit.brain.service import BrainService, recall_text, remember_text
from agentfit.db import Database, get_db


def project_root(path: str | Path) -> Path:
    """The folder a project lives in: the nearest one above ``path`` that holds a ``.git``, else ``path`` itself."""
    start = Path(path).resolve()
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return start


class ProjectMemory:
    def __init__(self, service: BrainService, project: Project, workspace: Path) -> None:
        self.service = service
        self.project = project
        self.workspace = workspace

    @classmethod
    def open(cls, workspace: str | Path, *, db: Database | None = None) -> ProjectMemory:
        """The memory of a folder, started if it has none. ``db`` defaults to the app's own database."""
        folder = Path(workspace).resolve()
        service = BrainService(db or get_db(), NoEmbedder("A coding agent does not load an embedding model: words and the graph only."))
        project = service.ensure_project({"kind": "folder"}, folder)
        if project is None:  # pragma: no cover - a folder always has an identity
            raise ValueError(f"{folder} cannot be a project")
        return cls(service, project, folder)

    def recall(self, query: str = "", file: str = "", id: int = 0, kind: str = "") -> str:
        return recall_text(self.service, self.project, {"query": query, "file": file, "id": id or None, "kind": kind})

    def remember(self, kind: str, title: str, text: str, files: str = "", fixes: int = 0, agent: str = "coding-agent") -> str:
        message, _ = remember_text(
            self.service, self.project, {"kind": kind, "title": title, "text": text, "files": files, "fixes": fixes or None}, agent=agent, run_id=None,
        )
        return message

    def recent(self, kind: str = "", open_only: bool = False, limit: int = 8) -> str:
        """The newest entries, one line each. ``open_only`` keeps the errors nobody has fixed."""
        entries = self.service.memories(self.project.id, kind=kind or ("error" if open_only else None), limit=200)
        if open_only:
            entries = [m for m in entries if m.kind == "error" and "open" in m.tags]
        if not entries:
            return "No entry matches."
        return "\n".join(_line(m) for m in entries[: max(1, limit)])

    def scan(self) -> dict[str, Any] | None:
        """Read the folder again for the import graph, when its files changed."""
        return self.service.map_project(self.project, self.workspace)


def _line(memory: Memory) -> str:
    state = " [open]" if "open" in memory.tags else ""
    return f"#{memory.id} {memory.kind}, {memory.updated_at[:10]}{state}: {memory.title}"
