"""The git hooks of this project (.githooks/ calls this). They help and never block: whatever fails here is skipped.

    pre-commit      regenerates docs/map/ and adds it to the commit, so the map is never older than the code it describes
    post-commit     in the background: the commit goes into the project memory, the code graph and the memory's map catch up
    post-merge      in the background: the same catch-up after a pull or a merge
    post-checkout   in the background: the same catch-up after switching branch

They work for every tool that commits (Claude Code, Codex, Cursor, a person), because git calls them, not the tool.
Turn them on in a clone with:  git config core.hooksPath .githooks
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import ROOT, detach, project_python, run, utf8_output, willitrun

# A commit that touches none of these cannot change the map.
WATCHED = ("backend/", "desktop/src/", "docs/", "scripts/")
DOCUMENTS = ("docs/DECISIONS.md", "docs/LESSONS.md")


def pre_commit() -> None:
    staged = run(["git", "diff", "--cached", "--name-only"])[1].splitlines()
    if not any(name.startswith(WATCHED) for name in staged):
        return
    code, _ = run([project_python(), str(ROOT / "scripts" / "build_context.py"), "--quiet"], timeout=60)
    if code != 0:
        sys.stderr.write("pre-commit: docs/map/ could not be regenerated (`python scripts/build_context.py` says why). The commit goes on.\n")
        return
    if run(["git", "status", "--porcelain", "--", "docs/map"])[1].strip():
        run(["git", "add", "--", "docs/map"])
        sys.stderr.write("pre-commit: docs/map/ regenerated and added to this commit.\n")


def sync(commit: bool, documents: bool) -> None:
    """The slow part, run apart from the git command that asked for it. One step after the other: they share a database."""
    codegraph = shutil.which("codegraph")
    if codegraph and (ROOT / ".codegraph").exists():
        run([codegraph, "sync"], timeout=300)
    cli = willitrun()
    if not cli:
        return
    here = ["--workspace", str(ROOT)]
    if commit:
        run([*cli, "memory", "commit", "HEAD", *here], timeout=120)
    if documents:
        run([*cli, "memory", "import-docs", *here], timeout=120)
    run([*cli, "memory", "map", *here], timeout=300)


def catch_up(commit: bool = False, documents: bool = True) -> None:
    flags = [name for name, on in (("commit", commit), ("documents", documents)) if on]
    detach([project_python(), str(Path(__file__).resolve()), "sync", *flags])


def post_commit() -> None:
    changed = run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"])[1].splitlines()
    catch_up(commit=True, documents=any(name in DOCUMENTS for name in changed))


def main(argv: list[str]) -> int:
    utf8_output()
    name = argv[1] if len(argv) > 1 else ""
    try:
        if name == "pre-commit":
            pre_commit()
        elif name == "post-commit":
            post_commit()
        elif name in ("post-merge", "post-rewrite"):
            catch_up()
        elif name == "post-checkout":
            if len(argv) > 4 and argv[4] == "1":  # git passes: previous head, new head, 1 for a branch switch (0 for a file checkout)
                catch_up()
        elif name == "sync":
            sync(commit="commit" in argv[2:], documents="documents" in argv[2:])
    except Exception as exc:  # a git hook of this project never stops a commit
        sys.stderr.write(f"git_hook {name}: {type(exc).__name__}: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
