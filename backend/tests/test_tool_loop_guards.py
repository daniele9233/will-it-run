"""What keeps a tool loop moving: a file read twice, rounds of reading without writing, a small
budget for a fix, edits instead of rewrites, and trimming that leaves the prompt cache alone.

Seen on curtisdesignr.me with a 27B Developer (2026-10-15): the fix after the Verifier read the
same files again and again for 70 minutes, two minutes a round, and wrote nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentfit.workflows import tools
from agentfit.workflows.compiler import fit_messages
from agentfit.workflows.tools import ToolContext, ToolError
from fakes import tool_call
from test_autotune_and_sources import conversation
from test_workflows import Harness, workflow_yaml

TOOLS = "tools: [write_file, read_file, powershell]"


def developer_messages(h: Harness) -> list[dict[str, Any]]:
    return [c for c in h.runtime.calls if c["role"] == "Developer"][-1]["messages"]


def tool_results(messages: list[dict[str, Any]]) -> list[str]:
    return [str(m["content"]) for m in messages if m.get("role") == "tool"]


def test_the_same_answer_is_not_sent_twice_while_the_first_is_still_whole(tmp_path, db):
    script = {
        "Planner": [{"summary": "plan"}],
        "Developer": [
            tool_call("write_file", path="a.txt", content="first version"),
            tool_call("read_file", path="a.txt"),
            tool_call("read_file", path="a.txt"),
            tool_call("edit_file", path="a.txt", old="first", new="second"),
            tool_call("read_file", path="a.txt"),
            {"summary": "done"},
        ],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    h = Harness(tmp_path, db, script)
    record = h.run(workflow_yaml().replace(TOOLS, "tools: [write_file, read_file, edit_file, powershell]"))
    assert record.status == "completed", record.error
    results = tool_results(developer_messages(h))
    assert results[1] == "first version"
    assert results[2].startswith("Unchanged: this is exactly what your identical call in round 2 returned")
    # Once the file has changed, the same call gets the new text.
    assert results[3].startswith("Replaced 1 occurrence in a.txt") and results[4] == "second version"


def rebuild_yaml(revision: int = 10, idle: int = 2) -> str:
    """The Tester fails the first time, so the Developer gets its work back once."""
    return workflow_yaml().replace(TOOLS, f"tools: [write_file, read_file, powershell], revision_tool_rounds: {revision}, max_idle_rounds: {idle}")


def test_a_fix_has_its_own_budget_and_reading_without_writing_is_stopped(tmp_path, db):
    script = {
        "Planner": [{"summary": "plan"}],
        "Developer": [
            tool_call("write_file", path="a.txt", content="x"),
            {"summary": "built"},
            # The fix: reading only, never a change.
            *[tool_call("read_file", path=f"missing-{i}.txt") for i in range(8)],
            {"summary": "fixed nothing"},
        ],
        "Tester": [{"passed": False, "summary": "a.txt is wrong", "failures": ["a.txt"]}, {"passed": True, "summary": "ok"}],
    }
    h = Harness(tmp_path, db, script)
    record = h.run(rebuild_yaml())
    assert record.status == "completed", record.error
    visits = [c for c in h.runtime.calls if c["role"] == "Developer"]
    assert "You have 30 rounds this time" in visits[0]["messages"][0]["content"]
    fix = visits[-1]["messages"]
    assert "You have 10 rounds this time" in fix[0]["content"]
    stalled = [e["payload"] for e in h.events(record.id, "node.stalled")]
    # Told after 2 idle rounds, tools taken away 4 rounds later: 6 reads, not 8.
    assert [(s["rounds"], s["stopped"]) for s in stalled] == [(2, False), (6, True)]
    assert sum(1 for m in fix if m.get("role") == "tool") == 6
    assert any("2 rounds in a row without changing any file" in str(m.get("content")) for m in fix)
    assert any("your tools are taken away" in str(m.get("content")) for m in fix)


def test_reading_before_the_first_file_of_a_build_is_not_idle(tmp_path, db):
    script = {
        "Planner": [{"summary": "plan"}],
        "Developer": [*[tool_call("read_file", path=f"missing-{i}.txt") for i in range(5)], tool_call("write_file", path="a.txt", content="x"), {"summary": "built"}],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    h = Harness(tmp_path, db, script)
    record = h.run(rebuild_yaml())
    assert record.status == "completed", record.error
    assert not h.events(record.id, "node.stalled")
    assert (tmp_path / "ws" / "a.txt").read_text(encoding="utf-8") == "x"


# -- edit_file ------------------------------------------------------------------------


def test_edit_file_replaces_one_exact_piece(tmp_path):
    ctx = ToolContext(workspace=tmp_path)
    (tmp_path / "styles.css").write_text(".a { color: red; }\n.b { color: red; }\n", encoding="utf-8")
    out = tools.execute("edit_file", {"path": "styles.css", "old": ".a { color: red; }", "new": ".a { color: blue; }"}, ctx)
    assert out.startswith("Replaced 1 occurrence")
    assert (tmp_path / "styles.css").read_text(encoding="utf-8") == ".a { color: blue; }\n.b { color: red; }\n"
    with pytest.raises(ToolError, match="2 times"):
        tools.execute("edit_file", {"path": "styles.css", "old": "color:", "new": "background:"}, ctx)
    tools.execute("edit_file", {"path": "styles.css", "old": "color:", "new": "background:", "all": True}, ctx)
    assert (tmp_path / "styles.css").read_text(encoding="utf-8") == ".a { background: blue; }\n.b { background: red; }\n"
    with pytest.raises(ToolError, match="must match exactly"):
        tools.execute("edit_file", {"path": "styles.css", "old": "purple", "new": "x"}, ctx)
    with pytest.raises(ToolError, match="use write_file"):
        tools.execute("edit_file", {"path": "new.css", "old": "a", "new": "b"}, ctx)


def test_editing_a_file_that_was_already_in_the_folder_asks_first(tmp_path):
    (tmp_path / "mine.css").write_text("a", encoding="utf-8")
    risk, reason = tools.classify("edit_file", {"path": "mine.css", "old": "a", "new": "b"}, ToolContext(workspace=tmp_path, protect_existing=True))
    assert risk == tools.Risk.HIGH and "changes a file" in reason


# -- trimming -------------------------------------------------------------------------


def test_trimming_frees_room_to_spare_so_the_next_turn_is_not_trimmed_again():
    """Cut by exactly what overflowed, a conversation is cut again on the next turn and LM Studio
    processes the whole prompt again. With room to spare the next turns only append."""
    messages = conversation([9000, 9000, 9000, 9000])
    assert fit_messages(messages, 12288) > 0
    messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": "n", "function": {"name": "read_file", "arguments": "{}"}}]})
    messages.append({"role": "tool", "tool_call_id": "n", "content": "y" * 3000})
    before = [m["content"] for m in messages]
    assert fit_messages(messages, 12288) == 0 and [m["content"] for m in messages] == before


def test_a_screenshot_is_not_read_as_text(tmp_path):
    """Seen on a 27B Reverse engineer: a PNG read as text put 12,000 characters of noise in its conversation."""
    (tmp_path / "desktop-00.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    with pytest.raises(ToolError, match=r"binary file .*read_file reads text only"):
        tools.execute("read_file", {"path": "desktop-00.png"}, ToolContext(workspace=tmp_path))
    (tmp_path / "notes.md").write_text("# plain text", encoding="utf-8")
    assert tools.execute("read_file", {"path": "notes.md"}, ToolContext(workspace=tmp_path)) == "# plain text"


# -- a script that changes the workspace is progress when the agent is said to work through scripts ----------------


def make_files_tool() -> tools.ToolSpec:
    """A tool that is not write_file or edit_file but writes a file, like one of the site cloner's scripts."""

    def handler(ctx: ToolContext, args: dict[str, Any]) -> str:
        (ctx.workspace / args["file"]).write_text("made by a script", encoding="utf-8")
        return f"wrote {args['file']}"

    return tools.ToolSpec(name="make_files", label="Make files", description="Writes a file.", parameters={"type": "object", "properties": {"file": {"type": "string"}}},
                          handler=handler)


def scripted_build(watches: bool) -> tuple[dict[str, list[Any]], str]:
    script = {
        "Planner": [{"summary": "plan"}],
        "Developer": [tool_call("write_file", path="a.txt", content="x"), *[tool_call("make_files", file=f"f{i}.txt") for i in range(6)], {"summary": "built"}],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    flag = "true" if watches else "false"
    yaml_text = workflow_yaml().replace(TOOLS, f"tools: [write_file, read_file, make_files], max_idle_rounds: 2, idle_watches_workspace: {flag}")
    return script, yaml_text


def test_rounds_of_scripts_that_write_files_are_not_idle_for_an_agent_that_watches_the_workspace(tmp_path, db, monkeypatch):
    monkeypatch.setitem(tools.TOOLS, "make_files", make_files_tool())
    script, yaml_text = scripted_build(watches=True)
    h = Harness(tmp_path, db, script)
    record = h.run(yaml_text)
    assert record.status == "completed", record.error
    assert not h.events(record.id, "node.stalled")
    assert sorted(p.name for p in (tmp_path / "ws").glob("f*.txt")) == [f"f{i}.txt" for i in range(6)]


def test_the_same_scripts_do_stall_an_agent_that_only_counts_its_own_file_edits(tmp_path, db, monkeypatch):
    monkeypatch.setitem(tools.TOOLS, "make_files", make_files_tool())
    script, yaml_text = scripted_build(watches=False)
    h = Harness(tmp_path, db, script)
    record = h.run(yaml_text)
    assert record.status == "completed", record.error
    assert [(e["payload"]["rounds"], e["payload"]["stopped"]) for e in h.events(record.id, "node.stalled")] == [(2, False), (6, True)]


def test_the_workspace_stamp_follows_what_is_in_the_folder_and_skips_installed_packages(tmp_path):
    from agentfit.workflows.compiler import workspace_stamp

    assert workspace_stamp(None) is None and workspace_stamp(tmp_path / "missing") is None
    empty = workspace_stamp(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("one", encoding="utf-8")
    one = workspace_stamp(tmp_path)
    assert one != empty and one[0] == 1
    (tmp_path / "docs" / "a.md").write_text("one and more", encoding="utf-8")
    assert workspace_stamp(tmp_path) != one
    for skipped in ("node_modules", ".next", ".git"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "noise.js").write_text("noise" * 50, encoding="utf-8")
    assert workspace_stamp(tmp_path) == workspace_stamp(tmp_path) and workspace_stamp(tmp_path)[0] == 1


# -- an agent that only reads keeps just its writing tools until it writes -----------------------------------------
#
# Seen on a 27B Cloner (brioni.com, 2026-10-23): 110 rounds of reading, the same 9 KB file twenty times, not one file written,
# and a closing report that showed it knew what to write. A reminder in the conversation did not move it; no reading tool did.


def focus_yaml(after: int = 3, idle: int = 12) -> str:
    return workflow_yaml().replace(
        TOOLS, f"tools: [write_file, read_file, ask_user], max_idle_rounds: {idle}, idle_watches_workspace: true, "
               f"focus_after_idle_rounds: {after}, focus_hint: 'Write notes.txt first.'")


def asking_tool() -> tools.ToolSpec:
    return tools.ToolSpec(name="ask_user", label="Ask", description="Asks the person.", parameters={"type": "object", "properties": {}},
                          handler=lambda ctx, args: "The person answered: the home page")


def offered(h: Harness) -> list[list[str]]:
    return [[t["function"]["name"] for t in c["kwargs"].get("tools") or []] for c in h.runtime.calls if c["role"] == "Developer"]


def test_after_enough_rounds_without_a_change_only_the_writing_tools_are_offered_until_a_file_is_written(tmp_path, db, monkeypatch):
    monkeypatch.setitem(tools.TOOLS, "ask_user", asking_tool())
    script = {
        "Planner": [{"summary": "plan"}],
        "Developer": [
            *[tool_call("read_file", path=f"missing-{i}.txt") for i in range(3)],
            tool_call("read_file", path="missing-3.txt"),  # not offered any more, but the model calls it as it did a moment ago
            tool_call("write_file", path="notes.txt", content="x"),
            tool_call("read_file", path="notes.txt"),
            {"summary": "built"},
        ],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    h = Harness(tmp_path, db, script)
    record = h.run(focus_yaml())
    assert record.status == "completed", record.error
    names = offered(h)
    assert all("read_file" in n for n in names[:3])
    assert names[3] == ["write_file", "ask_user"] and names[4] == ["write_file", "ask_user"]
    assert "read_file" in names[5]
    results = tool_results(developer_messages(h))
    assert "3 rounds in a row without changing any file" in results[2] and "Write notes.txt first." in results[2]
    assert "your reading tools are taken away until you change a file" in results[2]
    assert results[3].startswith("ERROR: 'read_file' is taken away until you change a file (3 rounds without a change)")
    assert results[4].startswith("Wrote 1 characters to notes.txt") and results[5] == "x"
    stalled = [e["payload"] for e in h.events(record.id, "node.stalled")]
    assert [(s["rounds"], s["stopped"], s.get("focused")) for s in stalled] == [(3, False, True)]
    assert (tmp_path / "ws" / "notes.txt").read_text(encoding="utf-8") == "x"


def test_the_persons_answer_is_not_an_idle_round_and_the_question_stays_offered(tmp_path, db, monkeypatch):
    monkeypatch.setitem(tools.TOOLS, "ask_user", asking_tool())
    script = {
        "Planner": [{"summary": "plan"}],
        "Developer": [
            tool_call("read_file", path="missing-0.txt"),
            tool_call("read_file", path="missing-1.txt"),
            tool_call("ask_user"),  # offered while the reading tools are gone
            tool_call("read_file", path="missing-2.txt"),
            tool_call("write_file", path="notes.txt", content="x"),
            {"summary": "built"},
        ],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    h = Harness(tmp_path, db, script)
    record = h.run(focus_yaml(after=2))
    assert record.status == "completed", record.error
    names = offered(h)
    assert "ask_user" in names[2] and "read_file" not in names[2]
    # The answer ended the stretch: the reading tools are back and the count starts again.
    assert "read_file" in names[3]
    assert [s["payload"]["rounds"] for s in h.events(record.id, "node.stalled")] == [2]


def test_an_agent_that_never_writes_loses_its_tools_in_the_end_even_when_it_has_only_writing_tools_left(tmp_path, db, monkeypatch):
    monkeypatch.setitem(tools.TOOLS, "ask_user", asking_tool())
    script = {
        "Planner": [{"summary": "plan"}],
        "Developer": [*[tool_call("read_file", path=f"missing-{i}.txt") for i in range(12)], {"summary": "gave up"}],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    h = Harness(tmp_path, db, script)
    record = h.run(focus_yaml(after=2, idle=4))
    assert record.status == "completed", record.error
    stalled = [e["payload"] for e in h.events(record.id, "node.stalled")]
    # Focused after 2, the idle note at 4 is not repeated on top of it, and 4 more rounds end the visit.
    assert [(s["rounds"], s["stopped"]) for s in stalled] == [(2, False), (4, False), (8, True)]
    messages = developer_messages(h)
    assert not any("rounds in a row without changing any file. Stop reading" in str(m.get("content")) for m in messages)


def test_an_agent_without_a_focus_setting_keeps_every_tool(tmp_path, db):
    script = {
        "Planner": [{"summary": "plan"}],
        "Developer": [*[tool_call("read_file", path=f"missing-{i}.txt") for i in range(4)], tool_call("write_file", path="a.txt", content="x"), {"summary": "built"}],
        "Tester": [{"passed": True, "summary": "ok"}],
    }
    h = Harness(tmp_path, db, script)
    record = h.run(rebuild_yaml())
    assert record.status == "completed", record.error
    assert all("read_file" in n for n in offered(h)[:5])


def test_an_outline_asked_twice_is_not_sent_twice_while_the_first_is_whole():
    import json

    from agentfit.workflows.compiler import AgentProgress, _unrepeated

    progress = AgentProgress(messages=[{"role": "system", "content": "s"}], instance_id="i")
    call = {"id": "1", "function": {"name": "json_outline", "arguments": json.dumps({"path": "tokens.json", "pointer": "/colors"})}}
    first = _unrepeated(progress, call, "json_outline", "tokens.json /colors (all of it): []")
    progress.messages.append({"role": "tool", "tool_call_id": "1", "content": first})
    again = _unrepeated(progress, call, "json_outline", "tokens.json /colors (all of it): []")
    assert first.startswith("tokens.json /colors") and again.startswith("Unchanged: this is exactly what your identical call")
    other = {"id": "2", "function": {"name": "json_outline", "arguments": json.dumps({"path": "tokens.json", "pointer": "/fonts"})}}
    assert _unrepeated(progress, other, "json_outline", "tokens.json /fonts (all of it): []").startswith("tokens.json /fonts")
