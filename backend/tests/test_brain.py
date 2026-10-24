"""The Project Brain: storage, search by meaning, words and graph, the project map, what a run records and what it is given."""

from __future__ import annotations

import time
import zlib
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.brain import (
    BrainError,
    BrainService,
    EmbedStatus,
    RuntimeEmbedder,
    codemap,
    project_identity,
    text,
)
from agentfit.brain.capture import Recorder
from agentfit.brain.embed import choose_model, prefixes
from agentfit.brain.service import CODEMAP_SLOT, GUIDE
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.lmstudio.client import LMStudioClient
from agentfit.lmstudio.models import InstalledModel, LoadedInstance, LoadResult
from agentfit.workflows.dsl import load_yaml
from agentfit.workflows.model_manager import ModelManager
from agentfit.workflows.runner import RunManager
from fakes import FakeRuntime, tool_call

CONCEPTS = {
    "auth": ("login", "sign", "authentication", "token", "password", "credential", "accesso"),
    "storage": ("database", "sqlite", "storage", "persist", "table", "archivio"),
    "interface": ("button", "page", "layout", "colour", "interface", "screen"),
    "tests": ("test", "pytest", "assert", "failing"),
}


class ConceptEmbedder:
    """Vectors by topic, plus a little of the words themselves.

    Two texts about the same thing are close whatever words they use, and only two texts
    with the same words are (nearly) identical: enough of a real embedding model to test with.
    """

    def __init__(self) -> None:
        self.model = "fake-embed"
        self.available = True
        self.calls: list[tuple[list[str], bool]] = []

    def status(self) -> EmbedStatus:
        return EmbedStatus(available=self.available, model=self.model, reason="" if self.available else "No embedding model.")

    def embed(self, texts: list[str], *, query: bool = False) -> tuple[str, list[list[float]]] | None:
        if not self.available:
            return None
        self.calls.append((list(texts), query))
        return self.model, [self._vector(t) for t in texts]

    @staticmethod
    def _vector(value: str) -> list[float]:
        words = set(text.terms(value))
        topics = [float(sum(1 for w in words if any(w.startswith(stem[:5]) for stem in group))) for group in CONCEPTS.values()]
        wording = [0.0] * 512
        for word in words:
            wording[zlib.crc32(word.encode()) % 512] += 0.3
        return [*topics, *wording, 0.05]  # never a zero vector


@pytest.fixture
def embedder() -> ConceptEmbedder:
    return ConceptEmbedder()


@pytest.fixture
def brain(db, embedder) -> BrainService:
    return BrainService(db, embedder)


@pytest.fixture
def project(brain, tmp_path):
    return brain.ensure_project({"kind": "folder", "path": str(tmp_path)}, tmp_path)


# -- words and vectors --------------------------------------------------------


def test_terms_take_identifiers_apart_and_drop_filler():
    assert text.terms("The authService in auth_service.py") == ["auth", "service", "auth", "service", "py"]
    assert text.terms("Perché il login è rotto?") == ["login", "rotto"]


def test_match_query_stems_long_words_and_survives_punctuation():
    assert text.match_query('errors in "storage" AND (auth)') == '"error"* OR "storag"* OR "auth"*'
    assert text.match_query("the of and") is None


def test_the_same_memory_has_the_same_hash_whatever_its_spacing():
    assert text.content_hash("note", "Use  SQLite", "One file.\n") == text.content_hash("note", "use sqlite", " one   file. ")
    assert text.content_hash("note", "a", "b") != text.content_hash("decision", "a", "b")


def test_vectors_are_stored_at_unit_length():
    a, b = text.unpack(text.pack([3.0, 4.0])), text.unpack(text.pack([6.0, 8.0]))
    assert text.similarity(a, b) == pytest.approx(1.0)
    assert text.pack([0.0, 0.0]) is None and text.similarity(a, None) is None


# -- projects -----------------------------------------------------------------


def test_a_project_is_a_folder_or_a_repository_and_nothing_else(tmp_path):
    key, kind, name, folder = project_identity({"kind": "folder", "path": str(tmp_path)}, tmp_path)
    assert kind == "folder" and name == tmp_path.name and key.startswith("folder:") and folder == str(tmp_path.resolve())
    key, kind, name, _ = project_identity({"kind": "github", "url": "https://github.com/Owner/Repo"}, tmp_path / "copy")
    assert (key, kind, name) == ("github:owner/repo", "github", "Owner/Repo")
    # A run that was given no place of its own has no project to remember.
    assert project_identity({"kind": "none"}, tmp_path) is None and project_identity(None, tmp_path) is None


def test_the_same_repository_keeps_one_brain_across_branches(brain, tmp_path):
    first = brain.ensure_project({"kind": "github", "url": "https://github.com/o/r", "branch": "main"}, tmp_path / "a")
    second = brain.ensure_project({"kind": "github", "url": "https://github.com/o/r", "branch": "dev"}, tmp_path / "b")
    assert first.id == second.id and len(brain.projects()) == 1


# -- writing memories ---------------------------------------------------------


def test_a_memory_is_stored_once(brain, project):
    first, state = brain.add(project.id, kind="decision", title="Use SQLite", body="One file, no server.", files=["./src\\db.py"], tags=["storage"])
    again, state_again = brain.add(project.id, kind="decision", title="use sqlite", body="One file,  no server.")
    assert (state, state_again) == ("added", "known") and again.id == first.id
    assert first.files == ["src/db.py"] and brain.project(project.id).memories == 1


def test_a_newer_statement_with_the_same_title_replaces_the_older_one_which_is_kept(brain, project):
    old, _ = brain.add(project.id, kind="decision", title="Database", body="Use Postgres.")
    new, state = brain.add(project.id, kind="decision", title="Database", body="Use SQLite: it is one file.")
    assert state == "updated"
    assert brain.get(old.id).superseded_by == new.id
    assert [m.id for m in brain.memories(project.id)] == [new.id]
    assert {m.id for m in brain.memories(project.id, include_superseded=True)} == {old.id, new.id}
    # What was replaced is not found any more.
    assert [h.memory.id for h in brain.search(project.id, "postgres database").hits] == [new.id]


def test_a_statement_that_means_the_same_replaces_the_older_one(brain, project):
    old, _ = brain.add(project.id, kind="decision", title="Where data lives", body="Everything is persisted in a sqlite database.")
    # About the same subject, and a different fact: it stays.
    related, _ = brain.add(project.id, kind="decision", title="Migrations", body="The sqlite database is migrated forward only, by numbered files.")
    new, state = brain.add(project.id, kind="decision", title="Where the data lives", body="Everything is persisted in the SQLite database!")
    assert state == "updated" and brain.get(old.id).superseded_by == new.id and brain.get(related.id).superseded_by is None


def test_events_are_not_merged_even_when_they_read_alike(brain, project):
    first, _ = brain.add(project.id, kind="session", title="Add login", body="Request: add login. It ended: completed.", source="engine")
    second, state = brain.add(project.id, kind="session", title="Add login", body="Request: add login. It ended: failed.", source="engine")
    assert state == "added" and brain.get(first.id).superseded_by is None and second.id != first.id


def test_secrets_do_not_reach_the_memory(brain, project):
    memory, _ = brain.add(project.id, kind="note", title="Deploy", body="Run with api_key=sk-abcdefghijklmnopqrstuvwxyz123456 on the server.")
    assert "sk-abcdefghijklmnop" not in memory.body and "[REDACTED]" in memory.body


def test_a_memory_needs_a_title_and_a_known_kind(brain, project):
    with pytest.raises(BrainError):
        brain.add(project.id, kind="note", title="  ", body="x")
    with pytest.raises(BrainError):
        brain.add(project.id, kind="gossip", title="x", body="x")
    with pytest.raises(BrainError):
        brain.add("nope", kind="note", title="x", body="x")


def test_editing_and_deleting(brain, project):
    memory, _ = brain.add(project.id, kind="note", title="Port", body="The server listens on 8000.", files=["server.py"])
    edited = brain.edit(memory.id, body="The server listens on 9000.", files=["app/server.py"])
    assert edited.body.endswith("9000.") and edited.files == ["app/server.py"]
    assert [row["label"] for row in brain.links(edited)] == ["app/server.py"]
    brain.delete(memory.id)
    assert brain.memories(project.id) == [] and brain.search(project.id, "server").hits == []
    with pytest.raises(BrainError):
        brain.get(memory.id)


def test_forgetting_a_project_removes_everything_it_knew(brain, project):
    brain.add(project.id, kind="note", title="Port", body="8000", files=["server.py"])
    brain.forget(project.id)
    assert brain.projects() == []
    assert brain.db.query("SELECT 1 FROM brain_memory") == [] and brain.db.query("SELECT 1 FROM brain_edge") == []
    assert brain.db.query("SELECT 1 FROM brain_fts") == []


# -- search -------------------------------------------------------------------


def fill(brain: BrainService, project_id: str) -> dict[str, int]:
    """A small project memory about three subjects, with one error that has a fix."""
    ids: dict[str, int] = {}

    def add(key: str, **fields: Any) -> None:
        ids[key] = brain.add(project_id, **fields)[0].id

    add("auth", kind="decision", title="Sessions use signed cookies", body="Authentication keeps a token in a signed cookie, refreshed on each request.", files=["src/auth.py"])
    add("db", kind="decision", title="SQLite holds everything", body="One database file, migrated forward only.", files=["src/db.py"])
    add("ui", kind="note", title="Accent colour", body="Every button of the interface uses the accent colour.")
    add("error", kind="error", title="Tests failed: 2 failed", body="pytest: assert refresh() is None in tests/test_auth.py", files=["src/auth.py"], tags=["tests"], source="engine")
    add("fix", kind="solution", title="Fix for: Tests failed: 2 failed", body="The cookie was read before it was set; the order was swapped.", fixes=ids["error"], source="engine")
    add("change", kind="change", title="2 files changed for: add logout", body="- src/auth.py: logout\n- src/routes.py: route", files=["src/auth.py", "src/routes.py"], source="engine")
    add("filler0", kind="note", title="Sidebar width", body="The sidebar is 212 pixels wide on every page.")
    add("filler1", kind="note", title="Toast timing", body="A toast stays on screen for five seconds.")
    add("filler2", kind="note", title="Row height", body="Rows are 44 pixels tall.")
    add("filler3", kind="note", title="Default theme", body="The default layout is dark.")
    return ids


def test_search_by_words_finds_exact_names(brain, project):
    ids = fill(brain, project.id)
    result = brain.search(project.id, "SQLite migrated")
    assert result.hits[0].memory.id == ids["db"] and result.hits[0].keyword and result.total == 10


def test_search_by_meaning_finds_what_shares_no_word(brain, project):
    ids = fill(brain, project.id)
    # "login" and "password" appear in no memory; the embedding model knows they are about authentication.
    result = brain.search(project.id, "login password credential")
    assert result.semantic and result.model == "fake-embed"
    top = result.hits[0]
    assert top.memory.id == ids["auth"] and not top.keyword and top.semantic and top.semantic > 0.8


def test_without_an_embedding_model_search_still_works_and_says_so(brain, project, embedder):
    embedder.available = False
    ids = fill(brain, project.id)
    result = brain.search(project.id, "signed cookies")
    assert not result.semantic and result.hits[0].memory.id == ids["auth"]
    assert brain.search(project.id, "login password credential").hits == []  # no words in common, no vectors
    # When the model comes back, the memories written meanwhile are embedded on the next search.
    embedder.available = True
    assert brain.search(project.id, "login password credential").hits[0].memory.id == ids["auth"]
    assert brain.overview(project.id)["embedded"] == 10


def test_an_error_comes_back_with_its_fix_and_a_fix_with_its_error(brain, project):
    ids = fill(brain, project.id)
    found = [h.memory.id for h in brain.search(project.id, "pytest assert refresh", limit=1).hits]
    assert found == [ids["error"], ids["fix"]]
    found = [h.memory.id for h in brain.search(project.id, "cookie order swapped", limit=1).hits]
    assert found == [ids["error"], ids["fix"]]


def test_naming_a_file_brings_what_is_known_about_it(brain, project):
    ids = fill(brain, project.id)
    result = brain.search(project.id, "what happened to routes.py")
    hit = next(h for h in result.hits if h.memory.id == ids["change"])
    assert "file src/routes.py" in hit.via


def test_the_graph_brings_neighbours_of_a_match(brain, project):
    ids = fill(brain, project.id)
    # The change shares src/auth.py with the decision the words find.
    result = brain.search(project.id, "signed cookies refreshed")
    neighbour = next(h for h in result.hits if h.memory.id == ids["change"])
    assert neighbour.via == ["file src/auth.py"] and not neighbour.keyword


def test_context_search_is_strict_and_a_plain_search_is_generous(brain, project):
    fill(brain, project.id)
    strict = brain.search(project.id, "signed cookies refreshed", strict=True)
    assert {h.memory.kind for h in strict.hits} == {"decision"}  # no neighbours pulled in by the graph alone
    assert brain.search(project.id, "quarterly marketing budget", strict=True).hits == []


def test_search_can_be_limited_to_a_kind(brain, project):
    ids = fill(brain, project.id)
    result = brain.search(project.id, "auth cookie tests", kinds=["error"])
    assert [h.memory.id for h in result.hits] == [ids["error"]] and result.total == 1


def test_one_file_card_lists_imports_and_memories(brain, project):
    ids = fill(brain, project.id)
    brain.store.replace_code_edges(project.id, [("src/routes.py", "src/auth.py"), ("src/auth.py", "src/db.py")])
    card = brain.searcher.about_file(project.id, "auth.py")
    assert card["file"] == "src/auth.py" and card["imports"] == ["src/db.py"] and card["imported_by"] == ["src/routes.py"]
    assert {m.id for m in card["memories"]} == {ids["auth"], ids["error"], ids["change"]}
    assert brain.searcher.about_file(project.id, "nowhere.py") is None


def test_the_graph_holds_memories_files_and_their_edges(brain, project):
    ids = fill(brain, project.id)
    graph = brain.graph(project.id)
    labels = {n.label for n in graph.nodes}
    assert {"src/auth.py", "src/db.py", "tests"} <= labels
    relations = {(e.relation, e.source, e.target) for e in graph.edges}
    assert ("fixed_by", f"m:{ids['error']}", f"m:{ids['fix']}") in relations
    assert any(r == "touches" and s == f"m:{ids['change']}" for r, s, _ in relations)
    small = brain.graph(project.id, limit=10)
    assert len(small.nodes) == 10 and small.total_nodes == graph.total_nodes and all(n.type == "memory" for n in small.nodes)


# -- the project map ----------------------------------------------------------


def make_project(root: Path) -> None:
    files = {
        "pyproject.toml": "[project]\nname = 'shop'\n",
        "src/shop/__init__.py": "",
        "src/shop/db.py": "import sqlite3\n\ndef connect():\n    return sqlite3.connect(':memory:')\n",
        "src/shop/models.py": "from shop import db\nfrom .util import slug\n",
        "src/shop/util.py": "def slug(value):\n    return value.lower()\n",
        "src/shop/main.py": "from shop.models import slug\nimport shop.db\n\nif __name__ == '__main__':\n    print(slug('A'))\n",
        "web/app.ts": "import { api } from './api';\nimport React from 'react';\nexport const App = () => api();\n",
        "web/api.ts": "export function api() { return 1; }\n",
        "web/index.ts": "export * from './app';\n",
        "node_modules/react/index.js": "module.exports = {};\n",
        ".venv/lib/site.py": "import os\n",
        "broken.py": "def (:\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_the_project_map_reads_imports_from_the_files(tmp_path):
    make_project(tmp_path)
    code = codemap.scan(tmp_path)
    paths = {f.path for f in code.files}
    assert "src/shop/db.py" in paths and "broken.py" in paths
    assert not any(p.startswith(("node_modules/", ".venv/")) for p in paths)
    assert set(code.imports) == {
        ("src/shop/models.py", "src/shop/db.py"), ("src/shop/models.py", "src/shop/util.py"),
        ("src/shop/main.py", "src/shop/models.py"), ("src/shop/main.py", "src/shop/db.py"),
        ("web/app.ts", "web/api.ts"), ("web/index.ts", "web/app.ts"),
    }
    summary = codemap.overview(code, "shop", "2026-10-13")
    assert "not written by a model" in summary and "src/shop/db.py (imported by 2)" in summary
    assert "src/shop/main.py" in summary.split("Entry points:")[1].splitlines()[0]
    assert "pyproject.toml" in summary


def test_the_map_is_made_again_only_when_the_files_change(brain, tmp_path):
    make_project(tmp_path)
    project = brain.ensure_project({"kind": "folder"}, tmp_path)
    first = brain.map_project(project, tmp_path)
    entry = brain.store.by_slot(project.id, CODEMAP_SLOT)
    assert first["imports"] == 6 and entry.kind == "architecture" and entry.title == "Project map"
    assert brain.map_project(project, tmp_path) == first  # nothing changed: nothing is read
    (tmp_path / "src/shop/cart.py").write_text("from shop import db\n", encoding="utf-8")
    second = brain.map_project(project, tmp_path)
    assert second["signature"] != first["signature"] and second["imports"] == 7
    # Rewritten in place: one map, not one per run.
    assert brain.store.by_slot(project.id, CODEMAP_SLOT).id == entry.id
    assert [m.title for m in brain.memories(project.id)] == ["Project map"]
    with pytest.raises(BrainError):
        brain.edit(entry.id, body="hand-made")


def test_an_empty_folder_has_no_map(brain, tmp_path):
    project = brain.ensure_project({"kind": "folder"}, tmp_path)
    brain.map_project(project, tmp_path)
    assert brain.memories(project.id) == []


# -- what a run records -------------------------------------------------------


def recorder(**kwargs: Any) -> Recorder:
    return Recorder(
        task="Add a logout button", workflow_name="Coding Team",
        roles={"planner": "Planner", "architect": "Architect", "developer": "Developer", "tester": "Test Runner"},
        schemas={"planner": "Plan", "architect": "ArchitectureDesign", "developer": "CodeChange", "tester": "TestReport"}, **kwargs,
    )


def wrote(node: str, path: str, tool: str = "write_file") -> tuple[str, dict[str, Any]]:
    return "tool.call", {"node": node, "tool": tool, "status": "success", "arguments": {"path": path, "content": "x"}}


def ran_tests(node: str, code: int, closing: str) -> tuple[str, dict[str, Any]]:
    return "tool.call", {"node": node, "tool": "run_tests", "status": "success", "arguments": {}, "output": f"exit code {code} (1.2s)\nFAILED tests/test_auth.py::test_logout\n{closing}"}


def done(node: str, **output: Any) -> tuple[str, dict[str, Any]]:
    return "node.completed", {"node": node, "output": output}


def test_a_failed_check_that_later_passes_is_an_error_with_its_fix():
    rec = recorder()
    for event in (
        wrote("developer", "src/auth.py"),
        done("developer", summary="Added logout", files=[{"path": "src/auth.py", "purpose": "logout endpoint"}]),
        ran_tests("tester", 1, "1 failed, 4 passed in 0.31s"),
        done("tester", passed=False, summary="logout test fails", failures=["test_logout: 401 != 200"]),
        wrote("developer", ".\\src\\session.py"),
        done("developer", summary="Cleared the session before answering", files=[{"path": "src/session.py", "purpose": "clear the session"}]),
        ran_tests("tester", 0, "5 passed in 0.30s"),
        done("tester", passed=True, summary="all green"),
    ):
        rec.observe(*event)
    drafts = rec.drafts("completed", None, {"final": {"summary": "Logout works"}, "outputs": {}})
    kinds = [d.kind for d in drafts]
    assert kinds == ["session", "change", "error", "solution"]
    session, change, error, fix = drafts
    assert "Result: Logout works" in session.body and "It ended: completed" in session.body
    assert change.files == ["src/auth.py", "src/session.py"]
    assert "- src/session.py: clear the session (Developer)" in change.body
    assert error.title == "Tests failed: 1 failed, 4 passed in 0.31s" and "test_logout: 401 != 200" in error.body
    assert error.tags == ["tests"] and error.key and fix.fixes == error.key
    # The fix is what happened between the failure and the pass, nothing before it.
    assert fix.files == ["src/session.py"] and "Cleared the session before answering" in fix.body and "Added logout" not in fix.body


def test_a_check_that_never_passes_is_an_error_left_open():
    rec = recorder()
    rec.observe(*done("tester", passed=False, summary="cannot import app", failures=["ImportError: app"]))
    rec.observe(*done("tester", passed=False, summary="still cannot import app", failures=["ImportError: app.main"]))
    drafts = rec.drafts("retry_limit", "A loop reached its retry limit.", None)
    error = next(d for d in drafts if d.kind == "error")
    assert error.tags == ["open"] and "still cannot import app" in error.title and "app.main" in error.body
    assert not any(d.kind == "solution" for d in drafts)
    assert "retry limit" in drafts[0].body


def test_no_tests_collected_is_not_an_error():
    rec = recorder()
    rec.observe("tool.call", {"node": "tester", "tool": "run_tests", "status": "success", "arguments": {}, "output": "exit code 5 (0.2s)\nno tests ran"})
    assert [d.kind for d in rec.drafts("completed", None, None)] == ["session"]


def test_the_plan_the_design_and_each_decision_are_kept():
    rec = recorder()
    rec.observe(*done("planner", summary="Three steps", steps=[{"title": "Endpoint", "detail": "POST /logout"}], files_or_components=["src/auth.py", "session store"]))
    rec.observe(*done(
        "architect", summary="Stateless API with a session table", components=["api", "session table"],
        decisions=["Sessions live in SQLite because there is one process.", "No JWT: revocation must be instant."],
    ))
    drafts = rec.drafts("completed", None, None)
    plan = next(d for d in drafts if d.title.startswith("Plan:"))
    assert plan.kind == "decision" and plan.files == ["src/auth.py"] and plan.tags == ["session store"] and "1. Endpoint: POST /logout" in plan.body
    design = next(d for d in drafts if d.kind == "architecture")
    assert design.title == "Design: Stateless API with a session table" and "- session table" in design.body
    assert [d.title for d in drafts if d.kind == "decision" and d is not plan] == [
        "Sessions live in SQLite because there is one process.", "No JWT: revocation must be instant.",
    ]


def test_what_the_user_said_at_a_gate_is_a_decision_of_theirs():
    rec = recorder()
    rec.observe("approval.decided", {"kind": "gate", "prompt": "Review the plan.", "approved": False, "comment": "No new dependencies."})
    rec.observe("approval.decided", {"kind": "gate", "prompt": "Review the plan.", "approved": True, "comment": ""})
    rec.observe("approval.decided", {"kind": "gate", "prompt": "Review the plan.", "approved": True, "comment": "x", "automatic": True})
    rec.observe("approval.decided", {"kind": "tool", "prompt": "delete?", "approved": False, "comment": "no"})
    decisions = [d for d in rec.drafts("completed", None, None) if d.kind == "decision"]
    assert len(decisions) == 1 and decisions[0].source == "user" and decisions[0].title == "You rejected: No new dependencies."


def test_a_chat_turn_that_changed_nothing_leaves_nothing():
    rec = recorder(conversation=True)
    assert rec.drafts("completed", None, {"final": {"text": "Hello"}}) == []
    rec.observe(*wrote("assistant", "notes.txt"))
    assert [d.kind for d in rec.drafts("completed", None, {"final": {"text": "Done"}})] == ["session", "change"]


# -- a run with a memory ------------------------------------------------------

FLOW = """
workflow: {id: memory-flow, name: Memory Flow, max_steps: 30}
models:
  main: {model_key: main-model, context_length: 32768}
agents:
  - {id: planner, role: Planner, goal: plan the work, model: main, output_schema: Plan, system_prompt: "You plan carefully and report only what you actually decided."}
  - {id: developer, role: Developer, goal: build it, model: main, output_schema: CodeChange, tools: [write_file, read_file], system_prompt: "You write real files and report only what you actually did."}
  - {id: tester, role: Tester, goal: verify it, model: main, output_schema: TestReport, system_prompt: "You verify the work and report only what you actually checked."}
edges:
  - {from: START, to: planner}
  - {from: planner, to: developer}
  - {from: developer, to: tester}
  - {from: tester, to: developer, when: "passed == false", max_loops: 2}
  - {from: tester, to: END, when: "passed == true"}
"""


def start(db: Database, brain: BrainService | None, script: dict[str, list[Any]], folder: Path, task: str, yaml_text: str = FLOW, **kwargs: Any) -> tuple[Any, RunManager, FakeRuntime]:
    runtime = FakeRuntime(script)
    manager = RunManager(db, runtime, bus=EventBus(), checkpoint_path=":memory:", brain=brain)
    folder.mkdir(parents=True, exist_ok=True)
    kwargs.setdefault("source", {"kind": "folder", "path": str(folder)})
    record = manager.start(load_yaml(yaml_text), task, workspace=str(folder), background=False, **kwargs)
    return record, manager, runtime


def events_of(manager: RunManager, run_id: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in manager.events(run_id) if e["type"] == kind]


FIRST_RUN = {
    "Planner": [{"summary": "Add token authentication", "steps": [{"title": "Write auth.py", "detail": "token check"}], "files_or_components": ["auth.py"]}],
    "Developer": [
        tool_call("write_file", path="auth.py", content="def check(token):\n    return token == 'x'\n"),
        {"summary": "Wrote the token check", "files": [{"path": "auth.py", "purpose": "token authentication"}]},
        tool_call("write_file", path="auth.py", content="def check(token):\n    return bool(token)\n"),
        {"summary": "Accept any non-empty token", "files": [{"path": "auth.py", "purpose": "token authentication"}]},
    ],
    "Tester": [{"passed": False, "summary": "empty token accepted", "failures": ["check('') returned True"]}, {"passed": True, "summary": "ok"}],
}


def test_a_run_writes_down_what_it_did_and_the_next_one_is_given_it(db, brain, tmp_path):
    folder = tmp_path / "shop"
    record, manager, _ = start(db, brain, FIRST_RUN, folder, "Add token authentication to the login")
    assert record.status == "completed"
    project = brain.projects()[0]
    assert project.kind == "folder" and project.name == "shop"
    kinds = sorted(m.kind for m in brain.memories(project.id))
    assert kinds == ["architecture", "change", "decision", "error", "session", "solution"]
    saved = events_of(manager, record.id, "memory.saved")[0]
    assert saved["saved"] == {"session": 1, "decision": 1, "change": 1, "error": 1, "solution": 1} and saved["name"] == "shop"
    assert events_of(manager, record.id, "run.started")[0]["memory"]["memories"] == 0
    # Nothing to recall on the first run of a project.
    assert events_of(manager, record.id, "memory.recalled") == []
    error = next(m for m in brain.memories(project.id) if m.kind == "error")
    assert error.run_id == record.id and "open" not in error.tags

    # A later session on the same folder, about the same subject in other words.
    second, manager2, runtime2 = start(
        db, brain, {"Planner": [{"summary": "p"}], "Developer": [{"summary": "d"}], "Tester": [{"passed": True, "summary": "ok"}]},
        folder, "Users cannot sign in with their password",
    )
    recalled = events_of(manager2, second.id, "memory.recalled")
    assert [r["node"] for r in recalled] == ["planner", "developer", "tester"]
    titles = {item["title"] for item in recalled[0]["items"]}
    assert "Project map" in titles and any(t.startswith("Plan: Add token authentication") for t in titles)
    planner_prompt = next(c for c in runtime2.calls if c["role"] == "Planner")["messages"][1]["content"]
    assert "## Project memory" in planner_prompt and "the file is right" in planner_prompt
    assert planner_prompt.index("## Task") < planner_prompt.index("## Project memory")
    # The error arrives with what fixed it.
    assert "fixed by #" in planner_prompt and "Fix for: Tester: empty token accepted" in planner_prompt
    # What an agent was shown is on the ledger, like the outputs it read.
    ledger = {entry["node"]: entry for entry in second.result["ledger"]}
    assert ledger["planner"]["read"]["project_memory"] == recalled[0]["sha256"]
    assert events_of(manager2, second.id, "run.started")[0]["memory"]["memories"] == 6
    assert brain.get(error.id).uses >= 1


def test_agents_with_tools_get_recall_and_remember_whatever_the_workflow_lists(db, brain, tmp_path):
    folder = tmp_path / "shop"
    script = {
        "Planner": [{"summary": "p"}],
        "Developer": [
            tool_call("remember", kind="decision", title="Tokens are opaque", text="No JWT: a token is a random id looked up in the table.", files=["auth.py"]),
            tool_call("recall", query="how are tokens stored"),
            tool_call("recall", file="auth.py"),
            tool_call("recall", id=999),
            {"summary": "done"},
        ],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    record, manager, runtime = start(db, brain, script, folder, "Decide how tokens work")
    assert record.status == "completed"
    developer = next(c for c in runtime.calls if c["role"] == "Developer")
    offered = [t["function"]["name"] for t in developer["kwargs"]["tools"]]
    assert offered == ["write_file", "read_file", "recall", "remember"]
    assert GUIDE in developer["messages"][0]["content"]
    planner = next(c for c in runtime.calls if c["role"] == "Planner")
    assert not planner["kwargs"].get("tools") and "recall searches it" not in planner["messages"][0]["content"]
    calls = [e for e in events_of(manager, record.id, "tool.call") if e["status"] != "running"]
    outputs = {(c["tool"], str(c["arguments"])): c["output"] for c in calls}
    saved = next(o for (tool, _), o in outputs.items() if tool == "remember")
    assert saved.startswith("Saved as #") and all(c["risk"] == "low" and c["memory"] for c in calls)
    found = next(o for (tool, args), o in outputs.items() if tool == "recall" and "query" in args)
    assert "Tokens are opaque" in found and "by Developer" in found and "Searched by meaning" in found
    card = next(o for (tool, args), o in outputs.items() if tool == "recall" and "file" in args)
    assert card.startswith("auth.py") and "Tokens are opaque" in card
    assert next(o for (tool, args), o in outputs.items() if "999" in args).startswith("ERROR: there is no entry #999")
    project = brain.projects()[0]
    decision = next(m for m in brain.memories(project.id) if m.title == "Tokens are opaque")
    assert decision.source == "agent" and decision.agent == "Developer" and decision.run_id == record.id
    # Tied to the session it was written in.
    assert any(row["relation"] == "from_session" for row in brain.links(decision))
    assert events_of(manager, record.id, "memory.saved")[0]["by_agents"] == 1


def test_an_agent_can_record_the_fix_of_an_open_error(db, brain, tmp_path):
    folder = tmp_path / "shop"
    project = brain.ensure_project({"kind": "folder"}, folder)
    error, _ = brain.add(project.id, kind="error", title="Build fails on Windows", body="Path separator.", tags=["open"], source="engine")
    script = {
        "Planner": [{"summary": "p"}],
        "Developer": [tool_call("remember", kind="solution", title="Use pathlib", text="Join paths with pathlib, not with '/'.", fixes=error.id), {"summary": "d"}],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    record, _, _ = start(db, brain, script, folder, "Fix the build")
    assert record.status == "completed"
    fixed = brain.get(error.id)
    assert "open" not in fixed.tags
    assert [row["label"] for row in brain.links(fixed) if row["relation"] == "fixed_by"] == ["Use pathlib"]


def test_a_fix_that_was_already_written_down_still_closes_the_error(brain, project):
    error, _ = brain.add(project.id, kind="error", title="Build fails", body="Path separator.", tags=["open"], source="engine")
    fix, _ = brain.add(project.id, kind="solution", title="Use pathlib", body="Join paths with pathlib.")
    again, state = brain.add(project.id, kind="solution", title="Use pathlib", body="Join paths with pathlib.", fixes=error.id)
    assert state == "known" and again.id == fix.id
    assert "open" not in brain.get(error.id).tags
    assert [h.memory.id for h in brain.search(project.id, "build fails path separator", limit=1).hits] == [error.id, fix.id]
    with pytest.raises(BrainError):
        brain.add(project.id, kind="solution", title="Other", body="x", fixes=fix.id)  # a fix fixes an error, not another fix


def test_an_agent_with_a_reading_list_is_shown_no_memory(db, brain, tmp_path):
    folder = tmp_path / "shop"
    start(db, brain, FIRST_RUN, folder, "Add token authentication to the login")
    isolated = FLOW.replace("tools: [write_file, read_file],", "tools: [write_file, read_file], reads: [planner],")
    record, manager, runtime = start(db, brain, {"*": [{"summary": "x", "passed": True}]}, folder, "Add token authentication again", yaml_text=isolated)
    developer = next(c for c in runtime.calls if c["role"] == "Developer")
    assert "## Project memory" not in developer["messages"][1]["content"] and "recall" not in developer["messages"][0]["content"]
    assert [t["function"]["name"] for t in developer["kwargs"]["tools"]] == ["write_file", "read_file"]
    assert "developer" not in [r["node"] for r in events_of(manager, record.id, "memory.recalled")]


def test_a_run_without_a_project_or_with_memory_off_remembers_nothing(db, brain, tmp_path):
    record, manager, runtime = start(db, brain, FIRST_RUN, tmp_path / "ws", "Add token authentication", source={"kind": "none"})
    assert record.status == "completed" and brain.projects() == []
    assert events_of(manager, record.id, "run.started")[0]["memory"] is None
    assert all("recall" not in str(c["kwargs"].get("tools")) for c in runtime.calls)
    off = BrainService(db, ConceptEmbedder(), enabled=lambda: False)
    start(db, off, FIRST_RUN, tmp_path / "shop", "Add token authentication")
    assert off.projects() == []


def test_a_verification_workflow_neither_reads_nor_writes_the_memory(db, brain, tmp_path):
    folder = tmp_path / "shop"
    start(db, brain, FIRST_RUN, folder, "Add token authentication to the login")
    before = len(brain.memories(brain.projects()[0].id, include_superseded=True))
    verification = FLOW.replace("{id: memory-flow, name: Memory Flow, max_steps: 30}", "{id: v-flow, name: V, max_steps: 30, category: verification}")
    record, manager, runtime = start(db, brain, {"*": [{"summary": "x", "passed": True}]}, folder, "Add token authentication again", yaml_text=verification)
    assert record.status == "completed" and events_of(manager, record.id, "memory.recalled") == []
    assert all("## Project memory" not in c["messages"][1]["content"] for c in runtime.calls)
    assert len(brain.memories(brain.projects()[0].id, include_superseded=True)) == before


def test_a_broken_memory_never_stops_a_run(db, brain, tmp_path, monkeypatch):
    folder = tmp_path / "shop"
    start(db, brain, FIRST_RUN, folder, "Add token authentication to the login")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("index is corrupt")

    monkeypatch.setattr(brain.searcher, "search", boom)
    monkeypatch.setattr(brain, "add", boom)
    record, manager, _ = start(db, brain, {"*": [{"summary": "x", "passed": True}]}, folder, "Add token authentication again")
    assert record.status == "completed"
    assert "index is corrupt" in events_of(manager, record.id, "memory.recalled")[0]["error"]


# -- the embedding model ------------------------------------------------------


class EmbedRuntime(FakeRuntime):
    """A runtime with embedding models, recording how they are loaded and what is sent to them."""

    def __init__(self, keys: list[str], *, cpu_load_fails: bool = False) -> None:
        super().__init__()
        self.installed = [InstalledModel(model_key=k, display_name=k, type="embedding") for k in keys] + [InstalledModel(model_key="chat-model", display_name="Chat")]
        self.cpu_load_fails = cpu_load_fails
        self.loads: list[dict[str, Any]] = []
        self.embedded: list[tuple[str, list[str]]] = []
        self.fail_embed = False

    def list_loaded(self) -> list[LoadedInstance]:
        return [LoadedInstance(instance_id=i, model_key=k, type="embedding" if "embed" in k else "llm") for k, i in self.loaded.items()]

    def load(self, model_key: str, *, context_length: int | None = None, gpu: str | None = None, ttl_seconds: int | None = None) -> LoadResult:
        self.loads.append({"model": model_key, "gpu": gpu, "ttl": ttl_seconds})
        if gpu and self.cpu_load_fails:
            raise RuntimeError("lms CLI is not installed")
        self.loaded[model_key] = model_key
        return LoadResult(instance_id=model_key, model_key=model_key)

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if self.fail_embed:
            raise RuntimeError("model crashed")
        self.embedded.append((model, texts))
        return [[1.0, float(len(t))] for t in texts]


def test_the_embedding_model_is_chosen_and_loaded_with_its_weights_in_ram():
    runtime = EmbedRuntime(["text-embedding-nomic-embed-text-v1.5"])
    embedder = RuntimeEmbedder(lambda: runtime)
    assert embedder.status() == EmbedStatus(available=True, model="text-embedding-nomic-embed-text-v1.5")
    model, vectors = embedder.embed(["a decision", "b"])
    assert model == "text-embedding-nomic-embed-text-v1.5" and len(vectors) == 2
    assert runtime.loads == [{"model": model, "gpu": "off", "ttl": 300}]
    # nomic was trained with a prefix that says what the text is for.
    assert runtime.embedded[0][1] == ["search_document: a decision", "search_document: b"]
    embedder.embed(["a question"], query=True)
    assert runtime.embedded[1][1] == ["search_query: a question"] and len(runtime.loads) == 1  # already loaded


def test_embedding_falls_back_then_gives_up_quietly():
    runtime = EmbedRuntime(["embed-a"], cpu_load_fails=True)
    embedder = RuntimeEmbedder(lambda: runtime)
    assert embedder.embed(["x"]) is not None
    assert [load["gpu"] for load in runtime.loads] == ["off", None]  # no CLI: the runtime's own way
    runtime.fail_embed = True
    assert embedder.embed(["x"]) is None
    status = embedder.status()
    assert not status.available and "did not answer" in status.reason
    runtime.fail_embed = False
    assert embedder.embed(["x"]) is None  # left alone for a minute, so a run does not wait on it twice


def test_the_embedding_model_is_loaded_only_when_the_graphics_card_has_room_for_it():
    # Even with its weights in RAM it takes about 450 MiB of VRAM (measured), which a working model may need.
    full = EmbedRuntime(["embed-a"])
    embedder = RuntimeEmbedder(lambda: full, free_vram_mib=lambda: 700.0)
    assert embedder.embed(["x"]) is None
    assert full.loads == [] and "embed-a" not in full.loaded
    assert "700 MiB are free" in embedder.last_skip
    # Not a failure: it is tried again at the next search, and used when there is room.
    assert embedder.status().available
    roomy = EmbedRuntime(["embed-a"])
    assert RuntimeEmbedder(lambda: roomy, free_vram_mib=lambda: 6000.0).embed(["x"]) is not None
    # Already loaded: used whatever is free, it takes nothing more.
    loaded = EmbedRuntime(["embed-a"])
    loaded.loaded = {"embed-a": "embed-a"}
    assert RuntimeEmbedder(lambda: loaded, free_vram_mib=lambda: 100.0).embed(["x"]) is not None and loaded.loads == []
    # No reading (no NVIDIA card): loaded as before.
    unknown = EmbedRuntime(["embed-a"])
    assert RuntimeEmbedder(lambda: unknown, free_vram_mib=lambda: None).embed(["x"]) is not None


def test_a_search_while_the_card_is_full_says_why_it_used_no_vectors(db, tmp_path):
    runtime = EmbedRuntime(["embed-a"])
    brain = BrainService(db, RuntimeEmbedder(lambda: runtime, free_vram_mib=lambda: 300.0))
    project = brain.ensure_project({"kind": "folder"}, tmp_path)
    brain.add(project.id, kind="decision", title="Sessions use cookies", body="A signed cookie holds the token.")
    session = brain.open("r1", load_yaml(FLOW), {"kind": "folder"}, tmp_path, "x")
    answer = session.call("recall", {"query": "cookie token"}, "Developer")
    assert "Sessions use cookies" in answer and "300 MiB are free" in answer

def test_a_load_that_hangs_does_not_hold_up_a_run():
    class Hanging(EmbedRuntime):
        def load(self, model_key: str, **options: Any) -> LoadResult:
            self.loads.append({"model": model_key, **options})
            time.sleep(0.4)
            return LoadResult(instance_id=model_key, model_key=model_key)

    runtime = Hanging(["embed-a"])
    embedder = RuntimeEmbedder(lambda: runtime, load_deadline=0.05)
    started = time.perf_counter()
    assert embedder.embed(["x"]) is None
    assert time.perf_counter() - started < 0.3
    assert len(runtime.loads) == 1  # no second way of loading is tried on a command that hangs
    assert "did not load within" in embedder.status().reason


def test_which_embedding_model_is_used():
    assert choose_model(["nomic-v1.5", "bge-m3"]) == "bge-m3"  # multilingual first
    assert choose_model(["nomic-v1.5", "bge-m3"], "nomic-v1.5") == "nomic-v1.5"  # the user's choice wins
    assert choose_model(["something-new"]) == "something-new" and choose_model([]) is None
    assert prefixes("multilingual-e5-small") == ("passage: ", "query: ") and prefixes("bge-m3") == ("", "")
    no_models = RuntimeEmbedder(lambda: EmbedRuntime([]))
    assert "No embedding model is installed" in no_models.status().reason and no_models.embed(["x"]) is None
    assert "no embeddings" in RuntimeEmbedder(lambda: FakeRuntime()).status().reason


def test_the_gateway_unloads_the_embedding_model_when_a_model_needs_the_room():
    runtime = EmbedRuntime(["embed-a"])
    runtime.loaded = {"embed-a": "embed-a"}
    definition = load_yaml(FLOW)
    manager = ModelManager(runtime, definition.models, lambda *_: None)
    lease = manager.acquire("main", 4000)
    assert lease.model_key == "main-model" and "embed-a" in runtime.unload_calls

@respx.mock
def test_the_client_asks_lm_studio_for_embeddings_in_order():
    route = respx.post("http://127.0.0.1:1234/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]})
    )
    assert LMStudioClient().embeddings("embed-a", ["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert route.calls[0].request.content == b'{"model":"embed-a","input":["first","second"]}'


# -- the API ------------------------------------------------------------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    services = AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())
    services._brain = BrainService(services.db, ConceptEmbedder())
    folder = tmp_path / "shop"
    folder.mkdir()
    project = services.brain.ensure_project({"kind": "folder"}, folder)
    return TestClient(create_app(services)), services, project, folder


def test_api_lists_searches_and_edits_memories(api):
    client, services, project, _ = api
    ids = fill(services.brain, project.id)
    listing = client.get("/api/brain").json()
    assert listing["enabled"] and listing["semantic"]["model"] == "fake-embed"
    assert listing["projects"][0]["memories"] == 10 and listing["projects"][0]["counts"]["decision"] == 2

    rows = client.get(f"/api/brain/projects/{project.id}/memories").json()
    assert not rows["searched"] and len(rows["rows"]) == 10
    only = client.get(f"/api/brain/projects/{project.id}/memories", params={"kind": "error"}).json()["rows"]
    assert [r["memory"]["id"] for r in only] == [ids["error"]]
    assert any(link["relation"] == "fixed_by" and link["memory_id"] == ids["fix"] for link in only[0]["links"])

    found = client.get(f"/api/brain/projects/{project.id}/memories", params={"q": "login password credential"}).json()
    assert found["searched"] and found["semantic"] and found["rows"][0]["memory"]["id"] == ids["auth"]

    added = client.post(f"/api/brain/projects/{project.id}/memories", json={"kind": "decision", "title": "No ORM", "body": "Plain SQL.", "files": ["src/db.py"]}).json()
    assert added["state"] == "added" and added["memory"]["source"] == "user"
    memory_id = added["memory"]["id"]
    assert client.put(f"/api/brain/memories/{memory_id}", json={"body": "Plain SQL, by hand."}).json()["memory"]["body"] == "Plain SQL, by hand."
    assert client.post(f"/api/brain/projects/{project.id}/memories", json={"kind": "gossip", "title": "x"}).status_code == 400
    assert client.delete(f"/api/brain/memories/{memory_id}").json() == {"deleted": memory_id}
    assert client.put(f"/api/brain/memories/{memory_id}", json={"body": "x"}).status_code == 404

    graph = client.get(f"/api/brain/projects/{project.id}/graph").json()
    assert graph["total_nodes"] >= 13 and {"source", "target", "relation", "origin"} <= set(graph["edges"][0])
    overview = client.get(f"/api/brain/projects/{project.id}").json()
    assert overview["project"]["name"] == "shop" and overview["embedded"] == 10 and overview["keyword_index"]
    assert client.post(f"/api/brain/projects/{project.id}/reindex").json()["newly_embedded"] == 0


def test_api_finds_the_memory_of_a_place_before_a_run_starts(api):
    client, services, project, folder = api
    assert client.get("/api/brain/lookup", params={"workspace": str(folder)}).json()["project"]["id"] == project.id
    assert client.get("/api/brain/lookup", params={"workspace": str(folder.parent / "elsewhere")}).json()["project"] is None
    assert client.get("/api/brain/lookup", params={"repo_url": "https://github.com/o/r"}).json()["project"] is None
    services.brain.ensure_project({"kind": "github", "url": "https://github.com/o/r"}, folder)
    assert client.get("/api/brain/lookup", params={"repo_url": "https://github.com/O/R.git"}).json()["project"]["kind"] == "github"
    assert client.get("/api/brain/lookup").json() == {"enabled": True, "project": None}


def test_api_forgets_a_project(api):
    client, services, project, _ = api
    fill(services.brain, project.id)
    assert client.delete(f"/api/brain/projects/{project.id}").json() == {"deleted": project.id}
    assert client.get(f"/api/brain/projects/{project.id}").status_code == 404
    assert client.get("/api/brain").json()["projects"] == []


def test_memory_can_be_turned_off_in_settings(api):
    client, services, _, _ = api
    services._brain = None
    assert client.get("/api/settings").json()["memory"] == {"enabled": True, "embedding_model": ""}
    client.put("/api/settings", json={"memory": {"enabled": False}})
    assert client.get("/api/brain").json()["enabled"] is False
