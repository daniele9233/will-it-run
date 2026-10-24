"""Automatic context for a model, conversations that outgrow it, and where a run works.

The GPU is the simulated one of ``test_gateway``; Git is the real one, pointed at a
repository made on disk, so nothing here touches the network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentfit.events import EventBus
from agentfit.lmstudio.models import InstalledModel
from agentfit.workflows import sources, tools
from agentfit.workflows.compiler import SHORTENED, conversation_demand, fit_messages
from agentfit.workflows.dsl import LoadingPolicy, ModelSlot, dump_yaml, load_yaml
from agentfit.workflows.model_manager import AUTO_BASE_CONTEXT, MemoryProfile, estimate_profile, safe_context
from agentfit.workflows.runner import RunManager
from fakes import CutOffCall, FakeRuntime, tool_call
from test_gateway import TOTAL, GpuRuntime, gateway, phases


def auto(model: str = "spark-4b", **extra: Any) -> dict[str, ModelSlot]:
    return {"work": ModelSlot(model_key=model, auto_tune=True, **extra)}


def supports(runtime: FakeRuntime, model: str, tokens: int | None) -> None:
    runtime.installed = [InstalledModel(model_key=model, display_name=model, max_context_length=tokens)]


# -- automatic context --------------------------------------------------------


def test_automatic_slot_gives_a_small_request_a_comfortable_context():
    runtime = GpuRuntime()
    manager, events = gateway(runtime, auto(context_length=4096, loading_policy=LoadingPolicy.UNLOAD_AFTER_USE))
    lease = manager.acquire("work", need_context=3000)
    # The slot's own number (4096) is ignored: a small request still gets the base context.
    assert lease.context_length == AUTO_BASE_CONTEXT == 16384
    ready = phases(events, "ready")[0]
    assert ready["auto"] is True and ready["requested_context"] == 16384
    # "Unload after use" is ignored too: the gateway decides what stays.
    manager.release("work")
    assert "spark-4b" in runtime.loaded


def test_automatic_slot_sizes_the_context_to_the_request_with_headroom():
    runtime = GpuRuntime()
    manager, _ = gateway(runtime, auto())
    lease = manager.acquire("work", need_context=20000)
    assert lease.context_length == 25600  # 20000 * 1.25, rounded up to a step of 1024
    assert runtime.load_options[0]["context_length"] == 25600


def test_automatic_slot_grows_when_a_request_needs_more_and_never_shrinks():
    runtime = GpuRuntime()
    manager, _ = gateway(runtime, auto())
    assert manager.acquire("work", need_context=3000).context_length == 16384
    assert manager.acquire("work", need_context=12000).context_length == 16384  # already enough: no reload
    assert manager.acquire("work", need_context=30000).context_length == 37888  # outgrown: one reload, larger
    assert manager.acquire("work", need_context=3000).context_length == 37888  # a small request reuses it
    assert runtime.load_calls == ["spark-4b", "spark-4b"] and runtime.unload_calls == ["spark-4b"]


def test_automatic_slot_never_asks_for_more_than_the_model_supports():
    runtime = GpuRuntime()
    supports(runtime, "spark-4b", 8192)
    manager, _ = gateway(runtime, auto())
    lease = manager.acquire("work", need_context=30000)
    assert lease.context_length == 8192
    # Asking again for more does not reload a model that is already at its limit.
    assert manager.acquire("work", need_context=30000).context_length == 8192
    assert runtime.load_calls == ["spark-4b"]


def test_automatic_slot_stops_at_what_the_vram_holds():
    runtime = GpuRuntime(desktop=2300)  # 14076 MiB free
    manager, events = gateway(runtime, auto("qwen-27b"))
    lease = manager.acquire("work", need_context=60000)
    # Free 14076 - margin 600 - fixed 12670 = 806 MiB at 38 MiB per 1000 tokens -> 21210 -> 20480.
    # The request needs more, so more is tried once (63488, as far past the plan as is allowed):
    # it fills the card and the model goes back to the plan.
    assert [o["context_length"] for o in runtime.load_options] == [20480, 63488, 20480]
    assert lease.context_length == 20480
    ready = phases(events, "ready")[0]
    assert ready["saturated"] is False and ready["vram_free_mib"] >= 600
    # The limit is remembered: the same oversized request does not reload it.
    assert manager.acquire("work", need_context=60000).context_length == 20480
    assert len(runtime.load_calls) == 3


def test_automatic_and_fixed_slots_live_in_one_workflow():
    runtime = GpuRuntime()
    slots = {**auto("spark-4b"), "fixed": ModelSlot(model_key="gemma-12b", context_length=8192)}
    manager, events = gateway(runtime, slots)
    assert manager.acquire("work", need_context=2000).context_length == 16384
    assert manager.acquire("fixed", need_context=2000).context_length == 8192
    assert [e["auto"] for e in phases(events, "ready")] == [True, False]
    assert runtime.loaded.keys() == {"spark-4b", "gemma-12b"}  # both measurably fit: neither was evicted


def test_auto_tune_survives_yaml_and_is_left_out_when_off():
    text = """
workflow: {id: w, name: W}
models:
  a: {model_key: m1, auto_tune: true}
  b: {model_key: m2}
agents:
  - {id: one, role: One, model: a}
  - {id: two, role: Two, model: b}
edges:
  - {from: START, to: one}
  - {from: one, to: two}
  - {from: two, to: END}
"""
    definition = load_yaml(text)
    assert definition.models["a"].auto_tune and not definition.models["b"].auto_tune
    dumped = dump_yaml(definition)
    assert dumped.count("auto_tune: true") == 1 and "auto_tune: false" not in dumped


def test_safe_context_is_what_fits_the_room_and_zero_when_the_model_does_not():
    profile = MemoryProfile(fixed_mib=12670, per_1k_mib=38)
    assert safe_context(profile, 14541, 131072) == 49152  # (14541 - 12670) / 38 * 1000 = 49236 -> 48 steps of 1024
    assert safe_context(profile, 14541, 32768) == 32768  # never above what the model supports
    assert safe_context(profile, 12000, 131072) == 0  # the weights alone do not fit
    runtime = GpuRuntime()
    estimated = estimate_profile(runtime, "spark-4b", 4096, 12288)
    assert estimated is not None and (estimated.fixed_mib, estimated.per_1k_mib, estimated.source) == (4200.0, 55.0, "estimate")
    assert estimate_profile(FakeRuntime(), "spark-4b", 4096, 12288) is None  # a runtime with no estimator


# -- conversations that outgrow the context -----------------------------------


def conversation(tool_outputs: list[int], opening: int = 2000) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": "S" * 800}, {"role": "user", "content": "U" * opening}]
    for index, size in enumerate(tool_outputs):
        messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{index}", "function": {"name": "read_file", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": f"result {index} " + "x" * size})
    return messages


def test_old_tool_results_are_shortened_first_and_instructions_never():
    messages = conversation([9000, 9000, 9000])
    before = [m["content"] for m in messages]
    removed = fit_messages(messages, 8192)  # room for about 21000 characters
    assert removed > 0
    assert messages[0]["content"] == before[0] and messages[1]["content"] == before[1]  # instructions and task untouched
    assert messages[3]["content"].endswith(SHORTENED) and messages[3]["content"].startswith("result 0")
    assert messages[-1]["content"] == before[-1]  # the newest result is still whole
    total = sum(len(str(m["content"])) for m in messages)
    assert total <= (8192 - 1024) * 3.5 * 0.85
    # Enough room: nothing is touched.
    roomy = conversation([9000, 9000, 9000])
    assert fit_messages(roomy, 32768) == 0 and roomy == conversation([9000, 9000, 9000])


def test_the_latest_result_and_then_the_opening_are_cut_only_when_nothing_else_is_left():
    messages = conversation([30000])
    fit_messages(messages, 4096)
    assert messages[-1]["content"].endswith(SHORTENED) and len(messages[1]["content"]) == 2000
    huge_opening = conversation([], opening=40000)
    fit_messages(huge_opening, 4096)
    assert huge_opening[1]["content"].endswith(SHORTENED) and len(huge_opening[0]["content"]) == 800


def test_what_earlier_calls_wrote_goes_before_any_tool_result_and_stays_valid_json():
    page = "<p>" + "x" * 9000 + "</p>"
    messages = conversation([3000, 3000])
    messages[2]["tool_calls"][0]["function"] = {"name": "write_file", "arguments": json.dumps({"path": "index.html", "content": page})}
    messages[4]["tool_calls"][0]["function"] = {"name": "write_file", "arguments": json.dumps({"path": "styles.css", "content": page})}
    results = [messages[3]["content"], messages[5]["content"]]
    removed = fit_messages(messages, 7800)  # room for about 20000 characters, 7000 too few
    old = json.loads(messages[2]["tool_calls"][0]["function"]["arguments"])
    assert old["path"] == "index.html" and old["content"].startswith("<p>xxx") and "more characters were sent" in old["content"]
    assert removed > 8000 and len(old["content"]) < 300
    # That was enough: the results, and the page the latest call wrote, are whole.
    assert [messages[3]["content"], messages[5]["content"]] == results
    assert json.loads(messages[4]["tool_calls"][0]["function"]["arguments"])["content"] == page
    # With still less room the latest call is cut too, and it is still JSON.
    fit_messages(messages, 2400)
    assert len(json.loads(messages[4]["tool_calls"][0]["function"]["arguments"])["content"]) < 300


def test_the_room_the_tool_descriptions_take_is_left_free():
    messages = conversation([9000, 9000])
    fit_messages(messages, 8192, reserved_chars=6000)
    assert sum(len(str(m["content"])) for m in messages) <= (8192 - 1024) * 3.5 * 0.85 - 6000


def test_a_measured_ratio_of_characters_to_tokens_leaves_the_room_it_promises():
    """Code is about 2 characters a token, not 3.5: trimmed with the measured ratio, the same
    conversation keeps less, and a smaller conversation asks for the context it really takes."""
    assumed, measured = conversation([9000, 9000, 9000]), conversation([9000, 9000, 9000])
    fit_messages(assumed, 8192)
    fit_messages(measured, 8192, chars_per_token=2.0)
    kept = sum(len(str(m["content"])) for m in measured)
    assert kept < sum(len(str(m["content"])) for m in assumed) and kept <= (8192 - 1024) * 2.0 * 0.85
    assert conversation_demand(measured, 2.0) > conversation_demand(measured)


def test_a_cut_off_turn_makes_an_automatic_slot_reload_with_more_room(db, tmp_path):
    script = {"Reader": [CutOffCall("read_file", '{"path": "big'), tool_call("read_file", path="big.txt"), {"summary": "read it"}]}
    record, runtime, _ = run_with_tools(db, tmp_path, ModelSlot(model_key="spark-4b", auto_tune=True), big=500, script=script)
    assert record.status == "completed", record.error
    # 16384 was not enough to finish the turn: half as much again is asked for, plus the usual headroom.
    assert [o["context_length"] for o in runtime.load_options] == [16384, 30720]
    # A context the user fixed is never exceeded: the agent is told to send less, and that is all.
    fixed = {"Reader": [CutOffCall("read_file", '{"path": "big'), tool_call("read_file", path="big.txt"), {"summary": "read it"}]}
    record, runtime, _ = run_with_tools(db, tmp_path / "fixed", ModelSlot(model_key="spark-4b", context_length=8192), big=500, script=fixed)
    assert record.status == "completed" and [o["context_length"] for o in runtime.load_options] == [8192]


def test_an_agent_with_tools_starts_with_room_to_work_in_where_the_plan_gives_less(db, tmp_path):
    """Seen in a real run: the plan was 7168 tokens, the Developer's prompt fit, and after its
    first file the conversation was already being cut. Three loads followed in two minutes."""
    edge = {"desktop": 1713, "squeeze": 1000}  # the 14 GB model on the 16 GB card: the plan is 4096 tokens
    # An agent without tools is served by the plan's context: its prompt fits.
    plain = load_yaml("""
workflow: {id: plain, name: Plain}
models: {work: {model_key: qwen-q3, auto_tune: true}}
agents:
  - {id: writer, role: Writer, goal: write, model: work, system_prompt: "Answer the question in one short paragraph and nothing else."}
edges:
  - {from: START, to: writer}
  - {from: writer, to: END}
""")
    runtime = GpuRuntime(**edge)
    manager = RunManager(db, runtime, bus=EventBus(), vram_probe=runtime.probe, checkpoint_path=":memory:")
    assert manager.start(plain, "Why is the sky blue?", workspace=str(tmp_path / "plain"), background=False).status == "completed"
    assert [o["context_length"] for o in runtime.load_options] == [4096]
    # An agent with tools, same model, same PC, a prompt just as small: it gets the base context,
    # loaded past the plan and kept because the model answers at the speed measured a moment ago.
    record, runtime, _ = run_with_tools(db, tmp_path, ModelSlot(model_key="qwen-q3", auto_tune=True), big=500, reads=1, gpu=edge)
    assert record.status == "completed", record.error
    assert [o["context_length"] for o in runtime.load_options] == [16384]


def run_with_tools(
    db, tmp_path: Path, slot: ModelSlot, big: int, reads: int = 3, script: dict[str, list[Any]] | None = None,
    gpu: dict[str, Any] | None = None,
) -> tuple[Any, GpuRuntime, RunManager]:
    (tmp_path / "ws").mkdir(parents=True)
    # One file per read: the engine does not send the same answer twice while the first is still whole.
    names = ["big.txt", *[f"big{i}.txt" for i in range(1, reads)]]
    for name in names:
        (tmp_path / "ws" / name).write_text("L" * big, encoding="utf-8")
    script = script or {"Reader": [*[tool_call("read_file", path=name) for name in names], {"summary": "read it"}]}
    runtime = GpuRuntime(script=script, **(gpu or {}))
    definition = load_yaml("""
workflow: {id: reader, name: Reader}
models: {work: {model_key: spark-4b}}
agents:
  - {id: reader, role: Reader, goal: read, model: work, tools: [read_file], system_prompt: "Read the file with the tool and then summarise what it contains in a sentence."}
edges:
  - {from: START, to: reader}
  - {from: reader, to: END}
""")
    definition.models["work"] = slot
    manager = RunManager(db, runtime, bus=EventBus(), vram_probe=runtime.probe, checkpoint_path=":memory:")
    record = manager.start(definition, "Summarise big.txt", workspace=str(tmp_path / "ws"), background=False)
    return record, runtime, manager


def test_a_tool_loop_that_outgrows_a_fixed_context_is_trimmed_instead_of_failing(db, tmp_path):
    record, runtime, manager = run_with_tools(db, tmp_path, ModelSlot(model_key="spark-4b", context_length=4096), big=11000)
    assert record.status == "completed", record.error
    trimmed = [e for e in manager.events(record.id) if e["type"] == "node.trimmed"]
    assert trimmed and trimmed[0]["payload"]["context_length"] == 4096
    # Every request that was sent fits the context the model was loaded with.
    for call in runtime.calls:
        assert sum(len(str(m.get("content") or "")) for m in call["messages"]) <= (4096 - 1024) * 3.5
    assert runtime.load_calls == ["spark-4b"]


def test_an_automatic_slot_reloads_with_more_context_instead_of_trimming(db, tmp_path):
    # Five results of 11000 characters: about 24000 tokens with room to answer, more than the first 16384.
    record, runtime, manager = run_with_tools(db, tmp_path, ModelSlot(model_key="spark-4b", auto_tune=True), big=11000, reads=5)
    assert record.status == "completed", record.error
    contexts = [o["context_length"] for o in runtime.load_options]
    assert contexts[0] == 16384 and len(contexts) >= 2 and contexts == sorted(contexts)  # grew, never shrank
    assert not [e for e in manager.events(record.id) if e["type"] == "node.trimmed"]
    # The last request carries all five results whole.
    final = runtime.calls[-1]["messages"]
    assert sum(1 for m in final if m.get("role") == "tool" and len(m["content"]) >= 11000) == 5
    assert conversation_demand(final) <= contexts[-1]


# -- overwriting the user's files ---------------------------------------------


def test_overwriting_a_file_that_was_already_there_asks_first(tmp_path):
    (tmp_path / "notes.md").write_text("mine", encoding="utf-8")
    own = tools.ToolContext(workspace=tmp_path)
    protected = tools.ToolContext(workspace=tmp_path, protect_existing=True)
    assert tools.classify("write_file", {"path": "notes.md", "content": "x"}, own)[0] == tools.Risk.MEDIUM
    risk, reason = tools.classify("write_file", {"path": "notes.md", "content": "x"}, protected)
    assert risk == tools.Risk.HIGH and "already in your folder" in reason
    # A new file does not ask, and neither does rewriting a file this run created.
    assert tools.classify("write_file", {"path": "new.md", "content": "x"}, protected)[0] == tools.Risk.MEDIUM
    tools.execute("write_file", {"path": "new.md", "content": "first"}, protected)
    assert tools.classify("write_file", {"path": "new.md", "content": "second"}, protected)[0] == tools.Risk.MEDIUM
    # Reading never asks; deleting always does.
    assert tools.classify("read_file", {"path": "notes.md"}, protected)[0] == tools.Risk.LOW
    assert tools.classify("delete_file", {"path": "new.md"}, own)[0] == tools.Risk.HIGH


def test_a_run_in_the_users_folder_pauses_before_it_overwrites(db, tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "main.py").write_text("print('original')\n", encoding="utf-8")
    definition = load_yaml("""
workflow: {id: dev, name: Dev}
models: {work: {model_key: m}}
agents:
  - {id: dev, role: Developer, goal: write, model: work, tools: [write_file], system_prompt: "Write the files the task asks for using the tool, then report what you changed."}
edges:
  - {from: START, to: dev}
  - {from: dev, to: END}
""")
    script = {"Developer": [tool_call("write_file", path="main.py", content="print('changed')\n"), {"summary": "done"}]}
    manager = RunManager(db, FakeRuntime(script), bus=EventBus(), checkpoint_path=":memory:")
    run = manager.start(definition, "Change main.py", workspace=str(folder), protect_existing=True, source={"kind": "folder", "path": str(folder)})
    record = None
    for _ in range(400):
        record = manager.get(run.id)
        if record.pending_approvals or record.status not in ("running", "waiting_approval"):
            break
        import time

        time.sleep(0.02)
    assert record is not None and record.pending_approvals, record.status
    assert (folder / "main.py").read_text(encoding="utf-8") == "print('original')\n"  # nothing written while it waits
    assert "already in your folder" in record.pending_approvals[0].prompt
    manager.decide(run.id, record.pending_approvals[0].id, False, "keep mine")
    finished = manager.wait(run.id, 20)
    assert finished.status == "completed"
    assert (folder / "main.py").read_text(encoding="utf-8") == "print('original')\n"  # denied: still the user's file
    started = manager.events(run.id)[0]["payload"]
    assert started["source"] == {"kind": "folder", "path": str(folder)} and started["protect_existing"] is True


# -- folders and GitHub -------------------------------------------------------


def test_only_github_addresses_are_accepted():
    assert sources.parse_github("https://github.com/octocat/Hello-World") == ("octocat", "Hello-World")
    assert sources.parse_github(" https://github.com/octocat/Hello-World.git/ ") == ("octocat", "Hello-World")
    for bad in (
        "https://gitlab.com/a/b", "http://github.com/a/b", "https://github.com/a", "https://github.com/a/b/tree/main",
        "https://user:token@github.com/a/b", "git@github.com:a/b.git", "https://github.com/a/b --upload-pack=x", "https://github.com/a/..",
        "file:///c:/repo", "",
    ):
        with pytest.raises(sources.SourceError):
            sources.parse_github(bad)


def test_a_folder_must_be_a_real_project_folder(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    assert sources.user_folder(f'"{project}"') == project.resolve()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for bad, why in (("", "Choose a folder"), ("relative\\path", "full path"), (str(tmp_path / "missing"), "does not exist"),
                     (project.anchor, "not a whole drive"), (str(tmp_path), "not the user folder itself")):
        with pytest.raises(sources.SourceError, match=why):
            sources.user_folder(bad)
    import os

    with pytest.raises(sources.SourceError, match="belongs to Windows"):
        sources.user_folder(os.environ["SYSTEMROOT"])


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin(tmp_path) -> Path:
    """A repository on disk that stands in for GitHub."""
    repo = tmp_path / "origin"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "first", cwd=repo)
    return repo


def local_runner(origin: Path):
    """Runs real git, with the GitHub address swapped for the repository on disk."""
    seen: list[list[str]] = []

    def run(args: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
        seen.append(list(args))
        real = [origin.as_uri() if a == "https://github.com/octocat/Hello-World.git" else a for a in args]
        return sources._run_git(real, cwd, timeout)

    run.seen = seen  # type: ignore[attr-defined]
    return run


def test_a_repository_is_cloned_once_and_then_reused(tmp_path, origin):
    runner = local_runner(origin)
    base = tmp_path / "repos"
    first = sources.checkout("https://github.com/octocat/Hello-World", base=base, runner=runner)
    assert first.cloned and first.path == base / "octocat__hello-world" and (first.path / "README.md").read_text(encoding="utf-8") == "# Demo\n"
    # The address reaches git after "--", as one argument: it cannot be read as an option.
    clone = runner.seen[0]
    assert clone[:4] == ["clone", "--depth", "1", "--single-branch"] and clone[-3] == "--"

    # New work upstream: a clean copy is fast-forwarded.
    (origin / "NEW.md").write_text("new\n", encoding="utf-8")
    git("add", ".", cwd=origin)
    git("commit", "-q", "-m", "second", cwd=origin)
    again = sources.checkout("https://github.com/octocat/Hello-World", base=base, runner=runner)
    assert not again.cloned and again.note == "Updated the copy from an earlier run." and (again.path / "NEW.md").exists()

    # A copy the agents changed is left exactly as it is.
    (again.path / "README.md").write_text("# Changed by an agent\n", encoding="utf-8")
    kept = sources.checkout("https://github.com/octocat/Hello-World", base=base, runner=runner)
    assert "Not updated from GitHub" in kept.note
    assert (kept.path / "README.md").read_text(encoding="utf-8") == "# Changed by an agent\n"


def test_a_failed_clone_leaves_nothing_behind_and_says_why(tmp_path):
    def refuse(args: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
        Path(args[-1]).mkdir(parents=True)  # git creates the folder before it fails
        return subprocess.CompletedProcess(args, 128, "", "remote: Repository not found.\nfatal: repository not found\n")

    with pytest.raises(sources.SourceError, match="Could not clone a/private: fatal: repository not found"):
        sources.checkout("https://github.com/a/private", base=tmp_path, runner=refuse)
    assert not (tmp_path / "a__private").exists()
    with pytest.raises(sources.SourceError, match="branch name is not valid"):
        sources.checkout("https://github.com/a/b", "--upload-pack=evil", base=tmp_path, runner=refuse)


def test_a_place_is_resolved_from_a_folder_a_repository_or_nothing(tmp_path, origin):
    project = tmp_path / "project"
    project.mkdir()
    folder = sources.resolve(folder=str(project), takes_evidence=True)
    assert folder.workspace == folder.evidence_dir == str(project.resolve()) and folder.protect_existing
    assert folder.source == {"kind": "folder", "path": str(project.resolve())}
    assert sources.resolve(folder=str(project), protect_existing=False).protect_existing is False

    def clone(url: str, branch: str | None) -> sources.Checkout:
        return sources.checkout(url, branch, base=tmp_path / "repos", runner=local_runner(origin))

    repo = sources.resolve(repo_url="https://github.com/octocat/Hello-World", folder=str(project), clone=clone)
    assert repo.source["kind"] == "github" and repo.source["url"] == "https://github.com/octocat/Hello-World"
    assert repo.workspace == str(tmp_path / "repos" / "octocat__hello-world") and repo.evidence_dir is None
    assert repo.protect_existing is False  # a copy under version control: overwriting is the work

    nothing = sources.resolve()
    assert (nothing.workspace, nothing.evidence_dir, nothing.source) == (None, None, {"kind": "none"})


def test_earlier_work_is_summarised_for_the_next_request():
    text = sources.earlier_work("Build a parser", {"final": {"summary": "Parser written, 4 tests pass."}})
    assert "The previous request was: Build a parser" in text and "Parser written, 4 tests pass." in text
    assert "produced no final result" in sources.earlier_work("x", None)
    assert len(sources.earlier_work("x", {"final": {"markdown": "m" * 9000}})) < 2700


def test_gpu_total_constant_is_the_simulated_card():
    # Guards the arithmetic in the comments above against a change of the shared simulation.
    assert TOTAL == 16376
