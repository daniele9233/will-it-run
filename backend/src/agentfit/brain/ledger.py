"""What a Claude Code session did, read from its transcript and written to the project's memory. No model is asked.

A session is a file of JSON lines: what the person typed, every tool the model called and what came back. Four things in it
are facts that no summary is needed for:

- the **requests**: what the person typed, as typed (the lines Claude Code itself marks as coming from a human);
- the **files** that were written or edited, when the tool said it worked;
- the **checks** that were run (tests, lint, type check, translations, the map) and whether they passed, from the exit status;
- the **commits** that were made, from what ``git commit`` printed.

A check that failed and later passed is an error and its fix: the fix is the files edited in between. A check whose last
run failed is an error that is still open, which is what the next session most needs to know. Everything is recorded
with a ``slot``, so reading the same session again after another turn rewrites the same entries instead of piling up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentfit.brain.external import ProjectMemory

EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
MAX_REQUESTS = 30
HEAD_REQUESTS = 5
REQUEST_WIDTH = 220
MAX_FILES = 40

# family -> pattern on the command line. The first match decides; "check.py" runs several of them and counts as its own.
CHECKS: list[tuple[str, re.Pattern[str]]] = [
    ("check.py", re.compile(r"scripts[/\\]check\.py")),
    ("pytest", re.compile(r"\bpytest\b")),
    ("ruff", re.compile(r"\bruff\b.*\bcheck\b")),
    ("lint-imports", re.compile(r"\blint-imports\b")),
    ("typescript", re.compile(r"\btsc\b|run typecheck")),
    ("eslint", re.compile(r"\beslint\b|run lint")),
    ("vitest", re.compile(r"\bvitest\b|npm (?:run )?test\b")),
    ("translations", re.compile(r"build_locale\.py")),
    ("map", re.compile(r"build_context\.py")),
]
COMMIT = re.compile(r"^\[(?P<branch>[\w./-]+)(?: \(root-commit\))? (?P<sha>[0-9a-f]{7,40})\] (?P<subject>.+)$", re.M)
FAILURE_LINE = re.compile(r"(FAILED|ERROR|Error|error:|failed|Traceback|AssertionError)")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\2\b", re.S)
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", re.S)
# The shell failed before the check ran (a quoting mistake, a missing program): that says nothing about the code.
SHELL_FAILURE = re.compile(r"^(?:/usr/bin/)?(?:ba)?sh: |command not found|is not recognized as the name of a cmdlet|^The term '")
NO_OUTPUT = "failed without saying why"
EXIT_STATUS = re.compile(r"^Exit code \d+$")
DIAGNOSTIC = re.compile(r"^\S+:\d+:\d+: [A-Z]+\d+|error TS\d+|^\S+\(\d+,\d+\): error")  # ruff, mypy-style, tsc
EXCEPTION = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt)\b")  # the last line of a traceback
NOT_A_REQUEST = ("<", "[Request interrupted", "Caveat:", "[Image: source", "This session is being continued")


@dataclass
class CheckRun:
    family: str
    ok: bool
    summary: str
    edits_before: int  # how many file edits had happened when it ran


@dataclass
class Ledger:
    session_id: str = ""
    cwd: str = ""
    branch: str = ""
    started: str = ""
    ended: str = ""
    requests: list[str] = field(default_factory=list)
    files: dict[str, int] = field(default_factory=dict)
    edit_order: list[str] = field(default_factory=list)  # one path per edit, in order
    commits: list[tuple[str, str]] = field(default_factory=list)
    checks: list[CheckRun] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.requests or self.files or self.commits or self.checks)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_request(entry: dict[str, Any], text: str) -> bool:
    """A line the person typed, not a reminder, a hook's output or a tool's reply."""
    if entry.get("isSidechain") or entry.get("isMeta"):
        return False
    origin = entry.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    if entry.get("turnOrigin") not in (None, "human"):
        return False
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith(NOT_A_REQUEST)


def _summary(text: str) -> str:
    """The one line of a failed check's output that says what failed: pytest's FAILED line, a lint or type diagnostic, the end of a traceback."""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not EXIT_STATUS.match(line.strip())]
    pick = (
        next((line for line in lines if line.startswith(("FAILED ", "ERROR "))), "")
        or next((line for line in lines if DIAGNOSTIC.search(line)), "")
        or next((line for line in reversed(lines) if EXCEPTION.match(line)), "")
        or next((line for line in lines if FAILURE_LINE.search(line)), "")
        or (lines[0] if lines else "")
    )
    return re.sub(r"\s+", " ", pick)[:170]


def _family(command: str) -> str | None:
    """Which check a shell command runs. What is quoted or in a heredoc is text, not a command: a commit message may say 'pytest'."""
    bare = QUOTED.sub("", HEREDOC.sub("", command))
    return next((name for name, pattern in CHECKS if pattern.search(bare)), None)


def read_transcript(path: str | Path) -> Ledger:
    """The ledger of one session file. Lines that are not JSON are skipped: the file is written while it is read."""
    ledger = Ledger()
    uses: dict[str, tuple[str, dict[str, Any]]] = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            ledger.session_id = ledger.session_id or str(entry.get("sessionId") or "")
            ledger.cwd = str(entry.get("cwd") or ledger.cwd)
            ledger.branch = str(entry.get("gitBranch") or ledger.branch)
            stamp = str(entry.get("timestamp") or "")
            if stamp:
                ledger.started = ledger.started or stamp
                ledger.ended = stamp
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            kind = entry.get("type")
            if kind == "user" and not _has_block(content, "tool_result"):
                text = _text_of(content)
                if _is_request(entry, text) and len(ledger.requests) < 400:
                    ledger.requests.append(" ".join(text.split()))
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if kind == "assistant" and block.get("type") == "tool_use":
                    uses[str(block.get("id"))] = (str(block.get("name")), block.get("input") if isinstance(block.get("input"), dict) else {})
                elif kind == "user" and block.get("type") == "tool_result":
                    _result(ledger, uses.get(str(block.get("tool_use_id"))), block)
    return ledger


def _has_block(content: Any, kind: str) -> bool:
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == kind for b in content)


def _result(ledger: Ledger, use: tuple[str, dict[str, Any]] | None, block: dict[str, Any]) -> None:
    if use is None:
        return
    name, args = use
    failed = bool(block.get("is_error"))
    if name in EDIT_TOOLS and not failed:
        path = str(args.get("file_path") or args.get("notebook_path") or "")
        if path:
            ledger.files[path] = ledger.files.get(path, 0) + 1
            ledger.edit_order.append(path)
        return
    if name not in SHELL_TOOLS:
        return
    command = str(args.get("command") or "")
    text = _text_of(block.get("content"))
    for match in COMMIT.finditer(text):
        entry = (match.group("sha"), match.group("subject").strip())
        if entry not in ledger.commits:
            ledger.commits.append(entry)
    family = _family(command)
    if family:
        summary = "" if not failed else _summary(text) or NO_OUTPUT
        if not SHELL_FAILURE.search(summary):
            ledger.checks.append(CheckRun(family, not failed, summary, len(ledger.edit_order)))


@dataclass
class Pair:
    family: str
    summary: str
    files: list[str]
    open: bool


def errors_of(ledger: Ledger) -> list[Pair]:
    """Failures that were fixed (with the files edited meanwhile) and failures that are still there."""
    pairs: dict[str, Pair] = {}
    by_family: dict[str, list[CheckRun]] = {}
    for run in ledger.checks:
        by_family.setdefault(run.family, []).append(run)
    for family, runs in by_family.items():
        pending: CheckRun | None = None
        for run in runs:
            if not run.ok and pending is None:
                pending = run
            elif run.ok and pending is not None:
                files = list(dict.fromkeys(ledger.edit_order[pending.edits_before : run.edits_before]))
                pairs[f"{family}:{_signature(pending.summary)}"] = Pair(family, pending.summary, files, open=False)
                pending = None
        if pending is not None:
            pairs[f"{family}:{_signature(pending.summary)}"] = Pair(family, pending.summary, [], open=True)
    return list(pairs.values())


def _signature(summary: str) -> str:
    return re.sub(r"[0-9a-f]{7,}|\d+", "#", summary.lower())[:60]


def _rel(path: str, root: str) -> str | None:
    """``path`` relative to the project folder; None for a file outside it (a scratch script is not part of the project)."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _inside(paths: Any, root: str) -> list[str]:
    return list(dict.fromkeys(rel for rel in (_rel(p, root) for p in paths) if rel))


def _requests_of(requests: list[str]) -> list[str]:
    """Every request of a short session; of a long one the first few and the latest, with the gap said."""
    if len(requests) <= MAX_REQUESTS:
        return [f"- {r[:REQUEST_WIDTH]}" for r in requests]
    head, tail = requests[:HEAD_REQUESTS], requests[-(MAX_REQUESTS - HEAD_REQUESTS) :]
    skipped = len(requests) - len(head) - len(tail)
    return [f"- {r[:REQUEST_WIDTH]}" for r in head] + [f"- … {skipped} requests in between"] + [f"- {r[:REQUEST_WIDTH]}" for r in tail]


def write(memory: ProjectMemory, ledger: Ledger) -> dict[str, int]:
    """Put the session in the memory. Safe to repeat: every entry has a slot, so a second call rewrites instead of adding."""
    if ledger.empty or not ledger.session_id:
        return {}
    root = str(memory.workspace)
    touched = _inside(ledger.files, root)
    short = ledger.session_id[:8]
    day = (ledger.started or "")[:10]
    lines = [f"Claude Code session {short} on {ledger.branch or 'a branch'}, {day} to {(ledger.ended or '')[:16].replace('T', ' ')}.", "", "Requests (as typed):"]
    lines += _requests_of(ledger.requests)
    if ledger.commits:
        lines += ["", "Commits:"] + [f"- {sha} {subject}" for sha, subject in ledger.commits]
    if touched:
        lines += ["", f"Files written or edited ({len(touched)}): " + ", ".join(touched[:MAX_FILES]) + (" …" if len(touched) > MAX_FILES else "")]
    last: dict[str, CheckRun] = {}
    for run in ledger.checks:
        last[run.family] = run
    if last:
        lines += ["", "Checks, as they stood at the end:"] + [f"- {fam}: {'passed' if run.ok else 'FAILED: ' + run.summary}" for fam, run in last.items()]
    title = f"Session {day or short}: " + (ledger.requests[0][:70] if ledger.requests else "no request typed")
    saved = {"session": 0, "error": 0, "solution": 0}
    session, state = memory.service.add(
        memory.project.id, kind="session", title=title, body="\n".join(lines), files=touched[:MAX_FILES], tags=["claude-code", "auto"],
        source="engine", agent="claude-code", slot=f"claude:{ledger.session_id}", embed=False,
    )
    saved["session"] += state != "known"
    for pair in errors_of(ledger):
        if pair.summary == NO_OUTPUT and not pair.open:
            continue  # a failure nobody can learn from, already fixed
        fixed_files = _inside(pair.files, root)
        slot = f"claude-error:{ledger.session_id}:{pair.family}:{_signature(pair.summary)}"
        error, state = memory.service.add(
            memory.project.id, kind="error", title=f"{pair.family} failed: {pair.summary}"[:200], body=f"The check '{pair.family}' failed with: {pair.summary}",
            files=fixed_files, tags=["claude-code", "auto"] + (["open"] if pair.open else []),
            source="engine", agent="claude-code", slot=slot, session=session.id, embed=False,
        )
        saved["error"] += state != "known"
        if not pair.open and fixed_files:
            _, state = memory.service.add(
                memory.project.id, kind="solution", title=f"Fixed {pair.family}: {pair.summary}"[:200],
                body="Files edited between the failure and the run that passed: " + ", ".join(fixed_files),
                files=fixed_files, tags=["claude-code", "auto"], source="engine", agent="claude-code",
                slot=f"claude-fix:{ledger.session_id}:{pair.family}:{_signature(pair.summary)}", fixes=error.id, session=session.id, embed=False,
            )
            saved["solution"] += state != "known"
    return saved
