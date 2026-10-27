"""The commits of a project as entries of its memory: what changed, in whose words, in which files. No model is asked.

Every tool that commits leaves its commit here, because git runs the hook (scripts/hooks/git_hook.py) and not the tool: Claude
Code, Codex, Cursor and a person at a terminal all end up in the same memory. A commit has a slot, so recording it twice
rewrites the same entry.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentfit.brain.external import ProjectMemory

MAX_FILES = 40
GENERATED = ("docs/map/",)  # rewritten by every commit: they say nothing about what the commit was for
FIELD = "\x1f"


def _git(folder: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(folder), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def record(memory: ProjectMemory, rev: str = "HEAD", *, last: int = 1) -> int:
    """Write ``last`` commits, counting back from ``rev``, oldest first. Returns how many entries are new or changed."""
    folder = memory.workspace
    shas = _git(folder, "rev-list", f"--max-count={max(1, last)}", rev).split()
    written = 0
    for sha in reversed(shas):
        parts = _git(folder, "show", "-s", f"--format=%an{FIELD}%aI{FIELD}%s{FIELD}%b", sha).rstrip("\n").split(FIELD, 3)
        if len(parts) < 3 or not parts[2].strip():
            continue
        author, stamp, subject = parts[0], parts[1], parts[2].strip()
        body = parts[3].strip() if len(parts) > 3 else ""
        names = [name for name in _git(folder, "show", "--name-only", "--format=", sha).splitlines() if name and not name.startswith(GENERATED)]
        shown = ", ".join(names[:MAX_FILES]) + (" …" if len(names) > MAX_FILES else "")
        facts = f"Commit {sha[:10]} by {author}, {stamp[:10]}. {len(names)} files changed" + (f": {shown}" if names else ".")
        _, state = memory.service.add(
            memory.project.id, kind="change", title=subject[:200], body="\n\n".join(part for part in (subject, body, facts) if part),
            files=names[:MAX_FILES], tags=["commit", "auto"], source="engine", agent="git", slot=f"commit:{sha}", embed=False,
        )
        written += state != "known"
    return written
