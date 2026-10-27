"""The hooks of Claude Code for this project (wired in .claude/settings.json). One script, four events:

    session-start   prints what a session needs to know that AGENTS.md cannot say: the state of git, the errors the project
                    memory still holds as open, a map freshly regenerated. Its output becomes part of the model's context.
    pre-edit        refuses to edit a generated file (it would be overwritten and fail CI) and asks before editing a migration
                    that was already committed (an applied migration is never rewritten).
    post-edit       parses the Python or JSON file that was just written and says so at once if it cannot be read.
    stop            records the session in the project memory: requests, files, checks, commits, errors and their fixes.

Standard library only. A hook that crashes must not stop the work it watches: every event ends with exit 0, except post-edit,
whose exit 2 is how Claude Code returns a message to the model.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import ROOT, project_python, relative, run, utf8_output, willitrun

# path (prefix) -> how the file is made. Editing it by hand is wasted: the next run rewrites it.
GENERATED = (
    ("docs/map/", "python scripts/build_context.py"),
    ("desktop/src/locales/it.ts", "python scripts/build_locale.py (the source is desktop/src/locales/it.txt)"),
    (".codegraph/", "codegraph sync"),
)
MIGRATIONS = "backend/src/agentfit/migrations/"
GUIDE = (
    "Where to start: AGENTS.md, then docs/map/INDEX.md; find symbols with `codegraph explore \"<names>\" --max-files 6`; "
    "recall decisions and past errors with `willitrun memory recall \"<topic>\"`. A file always wins over the map and the memory. "
    "Before saying a change is done: `python scripts/check.py`."
)


def payload_of(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def edited_path(payload: dict[str, Any]) -> str | None:
    args = payload.get("tool_input")
    if not isinstance(args, dict):
        return None
    return relative(str(args.get("file_path") or args.get("notebook_path") or ""))


# -- session-start -------------------------------------------------------------------------------


def committed(path: str) -> bool:
    """True when HEAD has this file: it was committed, so a database somewhere may have run it."""
    return run(["git", "cat-file", "-e", f"HEAD:{path}"])[0] == 0


def ensure_git_hooks() -> str:
    """The versioned hooks of .githooks/ only run once git is told where they are, and that setting is local to each clone."""
    if not (ROOT / ".githooks").is_dir():
        return ""
    if run(["git", "config", "--get", "core.hooksPath"])[1].strip() == ".githooks":
        return ""
    run(["git", "config", "core.hooksPath", ".githooks"])
    return "Git hooks of .githooks/ are now on for this clone (core.hooksPath): commits regenerate docs/map/ and feed the memory."


def session_start() -> str:
    lines = ["Will It Run: state of the repository (scripts/hooks/claude_hook.py)"]
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])[1].strip() or "unknown branch"
    status = [line for line in run(["git", "status", "--porcelain"])[1].splitlines() if line.strip()]
    untracked = sum(line.startswith("??") for line in status)
    lines.append(f"Branch {branch}: {len(status) - untracked} changed and {untracked} new files not committed." if status else f"Branch {branch}: clean.")
    commits = run(["git", "log", "-3", "--format=  %h %s"])[1].rstrip()
    if commits:
        lines += ["Last commits:", commits]
    notice = ensure_git_hooks()
    if notice:
        lines.append(notice)

    code, _ = run([project_python(), str(ROOT / "scripts" / "build_context.py"), "--quiet"], timeout=60)
    lines.append("docs/map/ was regenerated from the code just now." if code == 0 else "docs/map/ could not be regenerated (`python scripts/build_context.py` says why): trust the code over the map.")

    cli = willitrun()
    if cli:
        recent = run([*cli, "memory", "recent", "--open", "--workspace", str(ROOT)], timeout=20)[1].strip()
        if recent and not recent.startswith("No entry"):
            lines += ["Errors the project memory still holds as open (read one with `willitrun memory recall --id N`; confirm it is gone before trusting it):", recent]
    if (ROOT / ".codegraph" / "codegraph.db").exists():
        lines.append("CodeGraph index present: `codegraph explore`, `callers`, `callees`, `impact`, `affected` answer from it.")
    lines.append(GUIDE)
    return "\n".join(lines)


# -- pre-edit ------------------------------------------------------------------------------------


def pre_edit(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The permission decision for an edit, or None to let it through."""
    path = edited_path(payload)
    if not path:
        return None
    for prefix, how in GENERATED:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return _decision("deny", f"{path} is generated: do not edit it. Change its source and run {how}.")
    if path.startswith(MIGRATIONS) and path.endswith(".sql") and committed(path):
        return _decision(
            "ask",
            f"{path} was already committed, and a migration that ran is never rewritten. Add the next numbered file instead "
            "(docs/DEVELOPMENT.md). Allow this only if no database has run it.",
        )
    return None


def _decision(kind: str, reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind, "permissionDecisionReason": reason}}


# -- post-edit -----------------------------------------------------------------------------------


def post_edit(payload: dict[str, Any]) -> str | None:
    """A message for the model when the file it just wrote cannot be parsed; None when it can."""
    path = edited_path(payload)
    if not path or not path.endswith((".py", ".json")):
        return None
    target = ROOT / path
    try:
        source = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return f"{path} was written but cannot be read as UTF-8 text: {exc}"
    try:
        if path.endswith(".py"):
            ast.parse(source, filename=path)
        else:
            json.loads(source)
    except SyntaxError as exc:
        return f"{path} was written but is not valid Python: {exc.msg} (line {exc.lineno}). Fix it before going on."
    except ValueError as exc:
        return f"{path} was written but is not valid JSON: {exc}. Fix it before going on."
    return None


# -- stop ----------------------------------------------------------------------------------------


def stop(raw_payload: str) -> None:
    """Hand the session's transcript to the memory. Claude Code runs this hook in the background, after every turn."""
    cli = willitrun()
    if cli:
        run([*cli, "memory", "ledger", "--hook"], stdin=raw_payload, timeout=120)


def main(argv: list[str]) -> int:
    utf8_output()
    event = argv[1] if len(argv) > 1 else ""
    try:
        if event == "session-start":
            sys.stdout.write(session_start() + "\n")
        elif event == "pre-edit":
            decision = pre_edit(payload_of(sys.stdin.read()))
            if decision:
                sys.stdout.write(json.dumps(decision) + "\n")
        elif event == "post-edit":
            problem = post_edit(payload_of(sys.stdin.read()))
            if problem:
                sys.stderr.write(problem + "\n")
                return 2
        elif event == "stop":
            stop(sys.stdin.read())
        else:
            sys.stderr.write("usage: claude_hook.py session-start | pre-edit | post-edit | stop\n")
    except Exception as exc:  # a hook must never be the reason the session fails
        sys.stderr.write(f"claude_hook {event}: {type(exc).__name__}: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
