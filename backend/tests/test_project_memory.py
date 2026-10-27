"""The memory of a project for coding agents: what a session leaves behind, the documents it reads, and the MCP server in front of it."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agentfit.brain import docs_import, gitlog, ledger
from agentfit.brain.external import ProjectMemory, project_root


@pytest.fixture
def memory(db, tmp_path) -> ProjectMemory:
    folder = tmp_path / "project"
    (folder / ".git").mkdir(parents=True)
    (folder / "src").mkdir()
    (folder / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return ProjectMemory.open(folder, db=db)


class Transcript:
    """A Claude Code session file, written the way Claude Code writes it: one JSON object per line."""

    def __init__(self, path: Path, cwd: Path) -> None:
        self.path = path
        self.cwd = cwd
        self.lines: list[dict[str, Any]] = []

    def _add(self, kind: str, content: Any, **extra: Any) -> None:
        stamp = f"2026-10-27T10:{len(self.lines) % 60:02d}:00.000Z"
        self.lines.append(
            {"type": kind, "sessionId": "sess-0001-abcdef", "cwd": str(self.cwd), "gitBranch": "master", "timestamp": stamp,
             "message": {"role": kind, "content": content}, **extra},
        )

    def says(self, text: str, **extra: Any) -> None:
        self._add("user", text, **extra)

    def uses(self, tool: str, args: dict[str, Any], output: str, *, failed: bool = False) -> None:
        use_id = f"toolu_{len(self.lines)}"
        self._add("assistant", [{"type": "tool_use", "id": use_id, "name": tool, "input": args}])
        self._add("user", [{"type": "tool_result", "tool_use_id": use_id, "content": output, "is_error": failed}])

    def save(self) -> Path:
        self.path.write_text("\n".join(json.dumps(line) for line in self.lines) + "\n", encoding="utf-8")
        return self.path


@pytest.fixture
def transcript(memory, tmp_path) -> Transcript:
    return Transcript(tmp_path / "session.jsonl", memory.workspace)


FAILED_TEST = "Exit code 1\n=== FAILURES ===\nFAILED tests/test_a.py::test_x - AssertionError: nope\n1 failed, 3 passed"


# -- the ledger ---------------------------------------------------------------


def test_a_session_gives_its_requests_files_checks_and_commits(memory, transcript, tmp_path):
    root = memory.workspace
    transcript.says("add the report to the arena")
    transcript.says("<system-reminder>written by Claude Code, not by a person</system-reminder>")
    transcript.says("This session is being continued from a previous conversation that ran out of context.")
    transcript.says("a reminder", isMeta=True)
    transcript.uses("Write", {"file_path": str(root / "src" / "a.py")}, "created")
    transcript.uses("Write", {"file_path": str(tmp_path / "scratch.py")}, "created")
    transcript.uses("Bash", {"command": "cd backend && pytest tests/test_a.py"}, FAILED_TEST, failed=True)
    transcript.uses("Edit", {"file_path": str(root / "src" / "a.py")}, "edited")
    transcript.uses("Bash", {"command": "cd backend && pytest tests/test_a.py"}, "4 passed")
    transcript.uses("Bash", {"command": 'git commit -m "run pytest first"'}, "[master abc1234] run pytest first\n 1 file changed")

    session = ledger.read_transcript(transcript.save())

    assert session.requests == ["add the report to the arena"]
    assert [(run.family, run.ok) for run in session.checks] == [("pytest", False), ("pytest", True)]  # a commit message is text, not a run
    assert session.checks[0].summary == "FAILED tests/test_a.py::test_x - AssertionError: nope"
    assert session.commits == [("abc1234", "run pytest first")]

    assert ledger.write(memory, session) == {"session": 1, "error": 1, "solution": 1}
    entry = memory.recall(query="arena report")
    assert "src/a.py" in entry and "scratch.py" not in entry  # a scratch file outside the folder is not the project's
    assert "abc1234 run pytest first" in entry
    fix = memory.recall(kind="solution", query="pytest")
    assert "fixes #" in fix and "src/a.py" in fix  # the edit between the failure and the run that passed


def test_reading_the_same_session_again_rewrites_it_instead_of_adding(memory, transcript):
    transcript.says("first thing")
    transcript.uses("Write", {"file_path": str(memory.workspace / "src" / "a.py")}, "created")
    assert ledger.write(memory, ledger.read_transcript(transcript.save()))["session"] == 1
    assert ledger.write(memory, ledger.read_transcript(transcript.path)) == {"session": 0, "error": 0, "solution": 0}

    transcript.says("second thing")
    ledger.write(memory, ledger.read_transcript(transcript.save()))

    assert len(memory.recent(kind="session").splitlines()) == 1
    assert "second thing" in memory.recall(query="second thing")


def test_a_check_that_still_fails_is_an_error_left_open(memory, transcript):
    transcript.says("fix the tests")
    transcript.uses("Bash", {"command": "ruff check src"}, "Exit code 1\nsrc/a.py:3:1: F401 `os` imported but unused\nFound 1 error.", failed=True)

    ledger.write(memory, ledger.read_transcript(transcript.save()))

    assert memory.recent(open_only=True).startswith("#")
    assert "[open]" in memory.recent(open_only=True)
    assert "src/a.py:3:1: F401" in memory.recent(open_only=True)


def test_a_failure_of_the_shell_is_not_a_failure_of_the_check(memory, transcript):
    transcript.says("run the tests")
    transcript.uses("Bash", {"command": "pytest -q"}, "Exit code 2\n/usr/bin/bash: -c: line 4: unexpected EOF while looking for matching `''", failed=True)

    session = ledger.read_transcript(transcript.save())

    assert session.checks == []
    assert ledger.write(memory, session) == {"session": 1, "error": 0, "solution": 0}


def test_a_failure_with_nothing_to_learn_from_is_kept_only_while_it_is_open(memory, transcript):
    transcript.says("build")
    transcript.uses("Bash", {"command": "python scripts/check.py"}, "Exit code 1", failed=True)
    transcript.uses("Edit", {"file_path": str(memory.workspace / "src" / "a.py")}, "edited")
    transcript.uses("Bash", {"command": "python scripts/check.py"}, "ok")

    assert ledger.write(memory, ledger.read_transcript(transcript.save())) == {"session": 1, "error": 0, "solution": 0}


def test_a_failed_check_names_the_line_that_says_what_failed(memory, transcript):
    traceback = "Exit code 1\nTraceback (most recent call last):\n  File \"x.py\", line 1\nSyntaxError: invalid non-printable character U+FEFF"
    transcript.says("x")
    transcript.uses("Bash", {"command": "python scripts/build_context.py"}, traceback, failed=True)
    transcript.uses("Bash", {"command": "tsc --noEmit"}, "Exit code 2\nsrc/App.tsx(4,2): error TS2304: Cannot find name 'x'.", failed=True)

    runs = ledger.read_transcript(transcript.save()).checks

    assert runs[0].summary == "SyntaxError: invalid non-printable character U+FEFF"
    assert runs[1].summary == "src/App.tsx(4,2): error TS2304: Cannot find name 'x'."


def test_a_session_file_that_is_still_being_written_is_read_up_to_the_last_whole_line(memory, transcript):
    transcript.says("hello")
    path = transcript.save()
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "user", "message": {"role": "us')  # the line Claude Code is in the middle of

    assert ledger.read_transcript(path).requests == ["hello"]


def test_a_session_with_nothing_in_it_writes_nothing(memory, tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    assert ledger.write(memory, ledger.read_transcript(empty)) == {}
    assert memory.recent() == "No entry matches."


def test_a_long_session_keeps_its_first_and_latest_requests_and_says_what_it_left_out(memory, transcript):
    for n in range(40):
        transcript.says(f"request number {n}")

    lines = ledger._requests_of(ledger.read_transcript(transcript.save()).requests)

    assert lines[0] == "- request number 0"
    assert lines[-1] == "- request number 39"
    assert any("requests in between" in line for line in lines)
    assert len(lines) == ledger.MAX_REQUESTS + 1


# -- the memory of a folder ---------------------------------------------------


def test_the_project_is_the_folder_with_the_git_directory(memory):
    assert project_root(memory.workspace / "src") == memory.workspace
    assert project_root(memory.workspace) == memory.workspace


def test_what_is_remembered_is_found_by_words_and_by_file(memory):
    saved = memory.remember("decision", "The Arena has no judge model", "Answers are checked by code, never by a model.", files="src/a.py")

    assert saved.startswith("Saved as #")
    assert "no judge model" in memory.recall(query="judge")
    assert "no judge model" in memory.recall(file="src/a.py")
    assert memory.recent(kind="decision").endswith("The Arena has no judge model")
    assert memory.recall(query="zebra").startswith("Nothing matches")


def test_a_second_program_opening_the_same_folder_sees_what_the_first_wrote(db, memory):
    memory.remember("note", "Shared", "Written by one program.")

    assert "Shared" in ProjectMemory.open(memory.workspace, db=db).recent()


def test_the_memory_commands_print_characters_the_console_code_page_cannot_hold(monkeypatch):
    from agentfit import cli

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))  # what a Windows pipe or console often is

    cli.memory_output()
    sys.stdout.write("Fixed → ★ 日本\n")
    sys.stdout.flush()

    assert "Fixed → ★ 日本".encode() in raw.getvalue()


# -- the commits --------------------------------------------------------------


@pytest.fixture
def repo(db, tmp_path) -> ProjectMemory:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    folder = tmp_path / "repo"
    (folder / "src").mkdir(parents=True)
    (folder / "docs" / "map").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
    return ProjectMemory.open(folder, db=db)


def _commit(folder: Path, message: str, **files: str) -> None:
    for name, content in files.items():
        path = folder / name.replace("__", "/")
        path.write_text(content, encoding="utf-8")
    config = ["-c", "user.name=Tester", "-c", "user.email=tester@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "add", "-A"], cwd=folder, check=True)
    subprocess.run(["git", *config, "commit", "-q", "-m", message], cwd=folder, check=True)


def test_a_commit_becomes_an_entry_with_its_message_and_files_and_is_not_written_twice(repo):
    _commit(repo.workspace, "Add the report\n\nThe arena needs one page per run.", src__a="x = 1\n", docs__map__INDEX="generated\n")
    _commit(repo.workspace, "Fix the report title", src__a="x = 2\n")

    assert gitlog.record(repo, "HEAD", last=2) == 2
    assert gitlog.record(repo, "HEAD", last=2) == 0

    newest_first = repo.recent(kind="change").splitlines()
    assert newest_first[0].endswith("Fix the report title") and newest_first[1].endswith("Add the report")
    found = repo.recall(query="one page per run")
    assert "Add the report" in found and "Tester" in found
    assert "src/a" in found and "docs/map" not in found  # the generated map changes with every commit and says nothing
    assert "Add the report" in repo.recall(file="src/a")


def test_a_folder_that_is_not_a_repository_records_nothing(db, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()

    assert gitlog.record(ProjectMemory.open(folder, db=db)) == 0


# -- the documents ------------------------------------------------------------


def test_every_section_of_the_documents_is_one_entry_and_a_second_import_rewrites(memory):
    docs = memory.workspace / "docs"
    docs.mkdir()
    (docs / "DECISIONS.md").write_text(
        "# Decisions\n\nIntro that is not an entry.\n\n## Photographic images only\nThe studio makes photos, never cartoons. See `src/a.py`.\n\n## HTTP, not IPC\nThe UI talks to the engine over HTTP.\n",
        encoding="utf-8",
    )
    (docs / "LESSONS.md").write_text("# Lessons\n\n## Civitai needs a token\nThe API answers 401 without one.\n", encoding="utf-8")

    assert docs_import.import_docs(memory) == {"DECISIONS.md": 2, "LESSONS.md": 1}
    assert "never cartoons" in memory.recall(query="photographic")
    assert "Photographic images only" in memory.recall(file="src/a.py")  # a path in backticks links the entry to that file
    assert len(memory.recent(limit=50).splitlines()) == 3

    (docs / "DECISIONS.md").write_text("## Photographic images only\nThe studio makes photos. One exception: Pony.\n\n## HTTP, not IPC\nThe UI talks to the engine over HTTP.\n", encoding="utf-8")
    docs_import.import_docs(memory)

    assert len(memory.recent(limit=50).splitlines()) == 3
    assert "One exception: Pony" in memory.recall(query="photographic")


def test_a_folder_without_decision_documents_imports_nothing(memory):
    assert docs_import.import_docs(memory) == {}


# -- the MCP server -----------------------------------------------------------


def _said(result: Any) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, tuple):  # the first versions of the SDK return (content, structured)
        content = content[0]
    return "".join(block.text for block in content if hasattr(block, "text"))


def test_the_mcp_server_offers_three_small_tools_and_answers_them(memory):
    pytest.importorskip("mcp")
    from agentfit.brain.mcp_server import build_server

    server = build_server(memory)

    tools = asyncio.run(server.list_tools())
    assert sorted(tool.name for tool in tools) == ["recall", "recent", "remember"]
    assert sum(len(tool.description or "") for tool in tools) < 1500  # every tool is sent to the model on every request

    saved = _said(asyncio.run(server.call_tool("remember", {"kind": "note", "title": "Written over MCP", "text": "A tool call saved this."})))
    assert saved.startswith("Saved as #")
    assert "Written over MCP" in _said(asyncio.run(server.call_tool("recall", {"query": "tool call"})))
    assert "Written over MCP" in _said(asyncio.run(server.call_tool("recent", {"limit": 3})))
