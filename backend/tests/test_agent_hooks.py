"""The scripts that keep the map and the memory current and stop edits that cannot work (scripts/hooks, .githooks, .claude/settings.json)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / "scripts" / "hooks"


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(HOOKS))  # the scripts import their shared helpers by name
    spec = importlib.util.spec_from_file_location(f"hooks_{name}", HOOKS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def common() -> ModuleType:
    """The helpers module the scripts themselves import (the same object, so a test can move its ROOT)."""
    sys.path.insert(0, str(HOOKS))
    return importlib.import_module("_common")


@pytest.fixture
def claude(common) -> ModuleType:
    return _load("claude_hook")


@pytest.fixture
def git(common) -> ModuleType:
    return _load("git_hook")


def _edit(path: str, tool: str = "Edit") -> dict[str, Any]:
    return {"tool_name": tool, "tool_input": {"file_path": str(REPO / path)}}


# -- Claude Code: before and after an edit --------------------------------------


@pytest.mark.parametrize("path", ["docs/map/INDEX.md", "docs/map/api.md", "desktop/src/locales/it.ts"])
def test_a_generated_file_is_refused_and_the_reason_names_the_command_that_makes_it(claude, path):
    decision = claude.pre_edit(_edit(path))["hookSpecificOutput"]

    assert decision["hookEventName"] == "PreToolUse" and decision["permissionDecision"] == "deny"
    assert "scripts/build_" in decision["permissionDecisionReason"]


def test_a_migration_that_was_committed_asks_and_a_new_one_goes_through(claude, monkeypatch):
    monkeypatch.setattr(claude, "committed", lambda path: path.endswith("0001_initial.sql"))

    asked = claude.pre_edit(_edit("backend/src/agentfit/migrations/0001_initial.sql"))["hookSpecificOutput"]
    assert asked["permissionDecision"] == "ask" and "next numbered file" in asked["permissionDecisionReason"]
    assert claude.pre_edit(_edit("backend/src/agentfit/migrations/0099_next.sql")) is None


def test_ordinary_files_and_files_outside_the_project_are_not_judged(claude, tmp_path):
    assert claude.pre_edit(_edit("backend/src/agentfit/arena/export.py")) is None
    assert claude.pre_edit({"tool_input": {"file_path": str(tmp_path / "scratch.py")}}) is None
    assert claude.pre_edit({"tool_input": {}}) is None
    assert claude.pre_edit({}) is None


def test_a_file_that_cannot_be_parsed_comes_back_to_the_model_and_a_good_one_does_not(claude, common, tmp_path, monkeypatch):
    for module in (claude, common):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "good.py").write_text("﻿x = 1\n", encoding="utf-8")  # a byte order mark is not an error
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "bad.json").write_text('{"a": 1,}', encoding="utf-8")
    (tmp_path / "notes.md").write_text("def broken(:", encoding="utf-8")

    def payload(name: str) -> dict[str, Any]:
        return {"tool_input": {"file_path": str(tmp_path / name)}}

    assert claude.post_edit(payload("good.py")) is None
    assert "not valid Python" in claude.post_edit(payload("bad.py")) and "bad.py" in claude.post_edit(payload("bad.py"))
    assert "not valid JSON" in claude.post_edit(payload("bad.json"))
    assert claude.post_edit(payload("notes.md")) is None  # only code is parsed
    assert claude.post_edit(payload("missing.py")) is not None  # it was just written: not being there is a problem too


def test_the_start_of_a_session_says_where_to_start_even_when_nothing_else_is_installed(claude, monkeypatch):
    monkeypatch.setattr(claude, "run", lambda *args, **kwargs: (0, ""))
    monkeypatch.setattr(claude, "willitrun", lambda: None)

    text = claude.session_start()

    assert "AGENTS.md" in text and "docs/map/INDEX.md" in text and "python scripts/check.py" in text
    assert len(text) < 2500  # it is added to every session: it has to stay small


def test_open_errors_of_the_memory_are_part_of_the_start_of_a_session(claude, monkeypatch):
    def fake_run(args, **kwargs):
        return (0, "#7 error, 2026-10-27 [open]: pytest failed: FAILED tests/test_x.py::t\n") if "recent" in args else (0, "")

    monkeypatch.setattr(claude, "run", fake_run)
    monkeypatch.setattr(claude, "willitrun", lambda: ["willitrun"])

    assert "#7 error" in claude.session_start()


def test_every_hook_in_the_settings_calls_an_event_the_script_knows():
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    source = (HOOKS / "claude_hook.py").read_text(encoding="utf-8")
    called = []
    for entries in settings["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["type"] == "command" and hook["command"] == "python"
                script, event = hook["args"]
                assert script == "${CLAUDE_PROJECT_DIR}/scripts/hooks/claude_hook.py"
                called.append(event)
    assert sorted(called) == ["post-edit", "pre-edit", "session-start", "stop"]
    assert all(f'"{event}"' in source for event in called)
    assert settings["hooks"]["Stop"][0]["hooks"][0]["async"] is True  # the memory is written after the answer, never before it


# -- git ----------------------------------------------------------------------


class Calls:
    """Stands in for ``run``: records every command and answers from a table keyed by what the command starts with."""

    def __init__(self, answers: dict[tuple[str, ...], tuple[int, str]] | None = None) -> None:
        self.answers = answers or {}
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> tuple[int, str]:
        self.commands.append(list(args))
        for prefix, answer in self.answers.items():
            if tuple(args[: len(prefix)]) == prefix:
                return answer
        return 0, ""

    def ran(self, *words: str) -> bool:
        return any(all(word in " ".join(command) for word in words) for command in self.commands)


def test_a_commit_that_touches_code_regenerates_the_map_and_adds_it(git, monkeypatch):
    calls = Calls({("git", "diff"): (0, "backend/src/agentfit/arena/export.py\n"), ("git", "status"): (0, " M docs/map/arena.md\n")})
    monkeypatch.setattr(git, "run", calls)

    git.pre_commit()

    assert calls.ran("build_context.py", "--quiet")
    assert calls.ran("git", "add", "docs/map")


def test_a_commit_that_changes_nothing_the_map_reads_leaves_it_alone(git, monkeypatch):
    calls = Calls({("git", "diff"): (0, "README.md\nLICENSE\n")})
    monkeypatch.setattr(git, "run", calls)

    git.pre_commit()

    assert not calls.ran("build_context.py")


def test_a_map_that_comes_out_the_same_is_not_added_again(git, monkeypatch):
    calls = Calls({("git", "diff"): (0, "scripts/check.py\n"), ("git", "status"): (0, "")})
    monkeypatch.setattr(git, "run", calls)

    git.pre_commit()

    assert calls.ran("build_context.py") and not calls.ran("git", "add")


def test_a_map_that_cannot_be_made_does_not_stop_the_commit(git, monkeypatch, capsys):
    calls = Calls({("git", "diff"): (0, "backend/x.py\n")})
    monkeypatch.setattr(git, "run", lambda args, **kwargs: (1, "") if "build_context.py" in " ".join(args) else calls(args, **kwargs))

    git.pre_commit()  # returns: no exception, no exit code

    assert "The commit goes on" in capsys.readouterr().err


def test_after_a_commit_the_catch_up_runs_apart_and_reads_the_documents_only_when_they_changed(git, monkeypatch):
    started: list[list[str]] = []
    monkeypatch.setattr(git, "detach", started.append)

    monkeypatch.setattr(git, "run", Calls({("git", "diff-tree"): (0, "docs/DECISIONS.md\nbackend/x.py\n")}))
    git.post_commit()
    monkeypatch.setattr(git, "run", Calls({("git", "diff-tree"): (0, "backend/x.py\n")}))
    git.post_commit()

    assert started[0][-2:] == ["commit", "documents"]
    assert started[1][-1:] == ["commit"]


def test_switching_branch_catches_up_and_checking_out_one_file_does_not(git, monkeypatch):
    started: list[list[str]] = []
    monkeypatch.setattr(git, "detach", started.append)

    git.main(["git_hook.py", "post-checkout", "a" * 40, "b" * 40, "1"])
    git.main(["git_hook.py", "post-checkout", "a" * 40, "b" * 40, "0"])
    git.main(["git_hook.py", "post-merge", "0"])

    assert len(started) == 2


def test_the_catch_up_goes_through_the_graph_then_the_memory_one_step_after_the_other(git, monkeypatch):
    calls = Calls()
    monkeypatch.setattr(git, "run", calls)
    monkeypatch.setattr(git, "willitrun", lambda: ["willitrun"])
    monkeypatch.setattr(git.shutil, "which", lambda name: "codegraph" if name == "codegraph" else None)
    monkeypatch.setattr(Path, "exists", lambda self: True)

    git.sync(commit=True, documents=True)

    order = [command[0:3] for command in calls.commands]
    assert order == [["codegraph", "sync"], ["willitrun", "memory", "commit"], ["willitrun", "memory", "import-docs"], ["willitrun", "memory", "map"]]


@pytest.mark.parametrize("name", ["pre-commit", "post-commit", "post-merge", "post-checkout"])
def test_the_git_hook_files_are_shell_scripts_with_unix_line_endings_that_never_block(name):
    data = (REPO / ".githooks" / name).read_bytes()

    assert data.startswith(b"#!/bin/sh\n") and b"\r" not in data
    assert data.rstrip().endswith(b"exit 0")  # whatever happens above, the commit goes on
    assert b"git_hook.py" in data
