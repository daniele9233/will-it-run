"""Agent tools with risk classification.

Every tool call is classified LOW, MEDIUM or HIGH before it runs. HIGH-risk calls
(deleting files, installing software, privilege escalation, registry changes, destructive
PowerShell, changing remote infrastructure, driving the mouse and keyboard, ...) require
explicit human approval by default. File tools are confined to the run's workspace.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_OUTPUT = 12_000


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    workspace: Path
    timeout: float = 120.0
    # The workspace is a folder of the user's own, with files that were there before the run:
    # overwriting one of those asks first, like deleting does.
    protect_existing: bool = False
    # Files this run created. Rewriting its own work never asks.
    created: set[Path] = field(default_factory=set)
    # The run's cloning session (its browser and dev server), made on the first call that needs
    # it (``agentfit.clone.session``). None: the browser tools answer that there is no browser here.
    clone: Callable[[], Any] | None = None
    # complete(slot_id, messages, **options) -> an OpenAI-shaped response from that slot's model,
    # through the same gateway as the agents, and the slot whose model can see (empty: none can).
    complete: Callable[..., dict[str, Any]] | None = None
    vision_slot: str = ""
    # True once the user has stopped the run: a long tool call checks it and gives up.
    cancelled: Callable[[], bool] | None = None
    # Sends a progress event of the tool that is running, without storing it.
    progress: Callable[[dict[str, Any]], None] | None = None
    # Folders outside the workspace that read_file and list_dir may read, never write: an investigation's target.
    read_roots: list[Path] = field(default_factory=list)


@dataclass
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], str]
    # Baseline risk; ``classifier`` can raise or lower it from the actual arguments.
    risk: Risk = Risk.LOW
    classifier: Callable[[dict[str, Any]], tuple[Risk, str]] | None = None
    group: str = "Files"
    examples: list[str] = field(default_factory=list)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    def classify(self, arguments: dict[str, Any]) -> tuple[Risk, str]:
        if self.classifier:
            return self.classifier(arguments)
        return self.risk, f"{self.label} is a {self.risk.value}-risk tool"


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n... [truncated {len(text) - MAX_OUTPUT} characters]"


def _resolve(ctx: ToolContext, raw: str, *, read: bool = False) -> Path:
    """Resolve a path inside the workspace; refuse anything that escapes it.

    ``read`` also admits the run's read-only folders (``ToolContext.read_roots``).
    """
    workspace = ctx.workspace.resolve()
    candidate = (workspace / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if candidate == workspace or workspace in candidate.parents:
        return candidate
    if read:
        for root in ctx.read_roots:
            base = root.resolve()
            if candidate == base or base in candidate.parents:
                return candidate
    roots = "".join(f" or read {r}" for r in ctx.read_roots) if read else ""
    raise ToolError(f"Path '{raw}' is outside the workspace. File tools only work inside {workspace}{roots}.")


# -- file tools ---------------------------------------------------------------


def _read_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = _resolve(ctx, str(args.get("path", "")), read=True)
    if not path.is_file():
        raise ToolError(f"File not found: {args.get('path')}")
    with path.open("rb") as handle:
        head = handle.read(4096)
    if b"\x00" in head or head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"%PDF", b"PK\x03\x04")):
        # Seen on a 27B: a screenshot read as text put 12,000 characters of noise in its conversation.
        raise ToolError(f"{args.get('path')} is a binary file ({path.suffix or 'no extension'}, {path.stat().st_size} bytes): "
                        "read_file reads text only. Images are described in the text files next to them.")
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    part = text[offset : offset + MAX_OUTPUT]
    rest = len(text) - offset - len(part)
    if rest > 0:
        # A file longer than one result can still be read whole, a part at a time.
        part += f"\n... [{rest} more characters: call read_file again with offset={offset + len(part)}]"
    return part


def _write_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    raw = str(args.get("path", ""))
    path = _resolve(ctx, raw)
    content = str(args.get("content", ""))
    if raw.endswith(("/", "\\")) or (not content and not path.suffix and not path.exists()):
        # Seen on a 12B Builder: write_file with an empty text on "src/components/sites/brioni-com" to "make the folder".
        # It made an empty FILE of that name, and every file written inside it afterwards failed.
        raise ToolError("write_file writes a file with its text. Folders are made by themselves when you write a file inside them: do not make "
                        "a folder or an empty file. Write the real file, for example src/components/sites/<site>/Header.tsx.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError) as exc:
        blocker = next((p for p in [path.parent, *path.parent.parents] if p.is_file()), path.parent)
        shown = blocker.relative_to(ctx.workspace.resolve()) if ctx.workspace.resolve() in blocker.parents else blocker
        raise ToolError(f"{shown} is a file, not a folder, so nothing can be written inside it. Delete it with delete_file (an empty write_file "
                        "probably made it), then write the file again.") from exc
    append = str(args.get("append", "")).strip().lower() in ("true", "1", "yes") and path.is_file()
    if not path.exists():
        ctx.created.add(path)
    name = path.relative_to(ctx.workspace.resolve())
    if append:
        # A long file arrives in parts: a model can only write so much in one turn.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return f"Appended {len(content)} characters to {name} ({path.stat().st_size} bytes now)"
    path.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {name}"


def _edit_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Replace one exact piece of a file. A fix of three lines does not rewrite 20,000 characters."""
    path = _resolve(ctx, str(args.get("path", "")))
    if not path.is_file():
        raise ToolError(f"File not found: {args.get('path')}. To create a file, use write_file.")
    old = str(args.get("old", ""))
    new = str(args.get("new", ""))
    if not old:
        raise ToolError("old is empty: give the exact text to replace, as it is in the file.")
    text = path.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)
    if count == 0:
        raise ToolError(f"The text to replace is not in {args.get('path')}: it must match exactly, spaces and line breaks "
                        "included. Read that part of the file again and copy it, or use write_file for the whole file.")
    every = str(args.get("all", "")).strip().lower() in ("true", "1", "yes")
    if count > 1 and not every:
        raise ToolError(f"The text to replace is in {args.get('path')} {count} times. Include more of the text around it "
                        "so it is found once, or pass all=true to replace every occurrence.")
    path.write_text(text.replace(old, new) if every else text.replace(old, new, 1), encoding="utf-8")
    name = path.relative_to(ctx.workspace.resolve())
    return f"Replaced {count if every else 1} occurrence in {name} ({path.stat().st_size} bytes now)"


def _list_dir(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = _resolve(ctx, str(args.get("path") or "."), read=True)
    if not path.is_dir():
        raise ToolError(f"Directory not found: {args.get('path')}")
    rows = []
    for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:200]:
        rows.append(f"{'DIR ' if entry.is_dir() else 'FILE'} {entry.name}" + ("" if entry.is_dir() else f" ({entry.stat().st_size} bytes)"))
    return "\n".join(rows) or "(empty directory)"


def _delete_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = _resolve(ctx, str(args.get("path", "")))
    if not path.is_file():
        raise ToolError(f"File not found: {args.get('path')}")
    path.unlink()
    return f"Deleted {args.get('path')}"


# -- process tools ------------------------------------------------------------


def _run(cmd: list[str], ctx: ToolContext, timeout: float | None = None) -> str:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=ctx.workspace, capture_output=True, timeout=timeout or ctx.timeout,
            creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Timed out after {timeout or ctx.timeout:.0f}s") from exc
    except OSError as exc:
        raise ToolError(str(exc)) from exc
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    body = out + (("\n[stderr]\n" + err) if err.strip() else "")
    return _truncate(f"exit code {proc.returncode} ({time.perf_counter() - started:.1f}s)\n{body}".strip())


def _run_python(ctx: ToolContext, args: dict[str, Any]) -> str:
    code = str(args.get("code", ""))
    # The snippet lives outside the workspace and runs inside it. Written into the workspace,
    # an agent listing its files found it there and spent its rounds working out what it was.
    with tempfile.TemporaryDirectory(prefix="willitrun-") as folder:
        script = Path(folder) / "snippet.py"
        script.write_text(code, encoding="utf-8")
        return _run([_python_exe(), "-I", "-B", str(script)], ctx)


_JS_TEST = re.compile(r"(\.test|\.spec)\.(c|m)?js$|^test.*\.(c|m)?js$", re.IGNORECASE)
_PY_TEST = re.compile(r"^test_.*\.py$|_test\.py$")
_SKIPPED_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build"}


def _test_files(root: Path, pattern: re.Pattern[str], limit: int = 200) -> list[Path]:
    found: list[Path] = []
    if root.is_file():
        return [root] if pattern.search(root.name) else []
    for path in sorted(root.rglob("*")):
        if any(part in _SKIPPED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and pattern.search(path.name):
            found.append(path)
            if len(found) >= limit:
                break
    return found


def _run_tests(ctx: ToolContext, args: dict[str, Any]) -> str:
    """The tests of the project: pytest for Python test files, Node's own runner for JavaScript ones."""
    target = str(args.get("path") or ".")
    root = _resolve(ctx, target)
    import shutil

    js = _test_files(root, _JS_TEST)
    if js and not _test_files(root, _PY_TEST) and shutil.which("node"):
        files = [str(p.relative_to(ctx.workspace.resolve())) for p in js]
        output = _run(["node", "--test", *files], ctx, timeout=300)
        return output + ("\n[Run with node --test, the test runner built into Node.js. A test that requires a package "
                         "needs it installed in the project (npm install) first.]" if "Cannot find module" in output else "")
    # No cache folders: the workspace can be a folder of the user's own.
    output = _run([_python_exe(), "-B", "-m", "pytest", "-q", "--no-header", "-x", "-p", "no:cacheprovider", target], ctx, timeout=300)
    if output.startswith("exit code 5"):
        hint = ("pytest found no tests. Python test files are named test_*.py or *_test.py. JavaScript test files "
                "(*.test.js) are run with Node when the project has no Python tests")
        output += f"\n[{hint}{'' if shutil.which('node') else ', and Node.js is not installed on this PC'}.]"
    return output


def _python_exe() -> str:
    # A frozen build has no interpreter of its own to hand out; use the system one.
    if getattr(sys, "frozen", False):
        import shutil

        found = shutil.which("python") or shutil.which("py")
        if not found:
            raise ToolError("Python is not installed on this computer, so Python tools are unavailable.")
        return found
    return sys.executable


_PS_READ_ONLY = re.compile(
    r"^\s*(get-[\w-]+|select-[\w-]+|where-object|format-[\w-]+|measure-object|sort-object|test-path|test-connection|"
    r"resolve-path|write-output|echo|ls|dir|cat|type|pwd|whoami|hostname|ipconfig|systeminfo|"
    r"kubectl\s+(get|describe|logs|top|explain|version|config\s+(view|get-contexts|current-context))|"
    r"helm\s+(list|ls|status|template|lint|show|version|history|get)|"
    r"terraform\s+(plan|validate|fmt|show|version|providers|output|graph)|"
    r"ansible(-playbook)?\s+.*--(check|syntax-check|list-tasks|list-hosts)|ansible-lint|"
    r"docker\s+(ps|images|logs|inspect|version|info|stats)|"
    r"git\s+(status|log|diff|show|branch|remote|rev-parse|ls-files|describe|blame)|"
    r"python\s+--version|node\s+--version|nvidia-smi)\b",
    re.IGNORECASE,
)

_PS_HIGH: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(remove-item|rm|del|erase|rmdir|rd)\b", re.I), "deletes files"),
    (re.compile(r"\b(format-volume|clear-disk|initialize-disk|remove-partition|diskpart)\b", re.I), "formats or repartitions a drive"),
    (re.compile(r"\b(winget|choco|scoop|msiexec|install-package|install-module|add-appxpackage|pip\s+install|npm\s+(i|install)\s+-g)\b", re.I), "installs software"),
    (re.compile(r"(-verb\s+runas|\bsudo\b|\brunas\b|start-process\b.*-credential)", re.I), "escalates privileges"),
    (re.compile(r"\b(reg(\.exe)?\s+(add|delete|import)|set-itemproperty|new-itemproperty|remove-itemproperty)\b.*(hk[lc]m|hkey|registry)|hk[lc]m:", re.I), "changes the registry"),
    (re.compile(r"\b(stop-computer|restart-computer|shutdown|stop-service|set-service|sc\.exe\s+(delete|stop|config)|stop-process|taskkill|set-executionpolicy|bcdedit|netsh\s+advfirewall)\b", re.I), "changes system state"),
    (re.compile(r"\bkubectl\s+(apply|delete|create|replace|patch|scale|rollout|drain|cordon|exec|edit|label|annotate|set)\b", re.I), "changes a Kubernetes cluster"),
    (re.compile(r"\bhelm\s+(install|upgrade|uninstall|delete|rollback)\b", re.I), "changes a Helm release"),
    (re.compile(r"\bterraform\s+(apply|destroy|import|state\s+(rm|mv|push)|taint)\b", re.I), "changes infrastructure through Terraform"),
    (re.compile(r"\bansible(-playbook)?\b(?!.*--(check|syntax-check|list-tasks|list-hosts))", re.I), "runs Ansible against hosts"),
    (re.compile(r"\b(ssh|scp|sftp|invoke-command|enter-pssession|new-pssession)\b", re.I), "acts on a remote machine"),
    (re.compile(r"\bgit\s+(push|reset\s+--hard|clean|rebase|filter-branch)\b", re.I), "rewrites or publishes Git history"),
    (re.compile(r"\b(invoke-webrequest|invoke-restmethod|curl|wget|iwr|irm)\b.*-(method|x)\s+(post|put|delete|patch)", re.I), "sends data to a remote service"),
    (re.compile(r"\b(send-mailmessage|docker\s+(push|rm|rmi|system\s+prune))\b", re.I), "sends messages or deletes containers"),
    (re.compile(r"\b(invoke-expression|iex)\b|downloadstring|-encodedcommand|\s-enc\s", re.I), "runs dynamically built code"),
]


def classify_powershell(arguments: dict[str, Any]) -> tuple[Risk, str]:
    """Risk of a PowerShell command line. Unknown commands default to MEDIUM."""
    command = str(arguments.get("command", ""))
    for pattern, reason in _PS_HIGH:
        if pattern.search(command):
            return Risk.HIGH, f"Command {reason}"
    segments = [s for s in re.split(r"[;|\n]|&&|\|\|", command) if s.strip()]
    if segments and all(_PS_READ_ONLY.match(s) for s in segments) and ">" not in command:
        return Risk.LOW, "Read-only command"
    return Risk.MEDIUM, "Command may change local state"


def _powershell(ctx: ToolContext, args: dict[str, Any]) -> str:
    command = str(args.get("command", ""))
    if not command.strip():
        raise ToolError("Empty command")
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], ctx)


_GIT_READ = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "describe", "blame"}
_GIT_HIGH = {"push", "clean", "rebase", "filter-branch"}


def classify_git(arguments: dict[str, Any]) -> tuple[Risk, str]:
    parts = str(arguments.get("args", "")).split()
    sub = parts[0] if parts else ""
    if sub in _GIT_READ:
        return Risk.LOW, "Read-only Git command"
    if sub in _GIT_HIGH or (sub == "reset" and "--hard" in parts):
        return Risk.HIGH, "Rewrites or publishes Git history"
    return Risk.MEDIUM, "Changes the local repository"


def _git(ctx: ToolContext, args: dict[str, Any]) -> str:
    import shlex

    parts = shlex.split(str(args.get("args", "")), posix=False)
    if not parts:
        raise ToolError("No git arguments given")
    return _run(["git", *parts], ctx)


# -- Windows computer-use tools -----------------------------------------------


def _user32() -> Any:
    try:
        return ctypes.windll.user32  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise ToolError("Windows desktop APIs are unavailable.") from exc


def _window_title(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _list_windows(ctx: ToolContext, args: dict[str, Any]) -> str:
    user32 = _user32()
    titles: list[str] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            title = _window_title(hwnd)
            if title.strip():
                titles.append(title)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return "\n".join(titles[:80]) or "(no visible windows)"


def _active_window(ctx: ToolContext, args: dict[str, Any]) -> str:
    return _window_title(_user32().GetForegroundWindow()) or "(no active window)"


def _read_clipboard(ctx: ToolContext, args: dict[str, Any]) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"], ctx, timeout=15)


def _screenshot(ctx: ToolContext, args: dict[str, Any]) -> str:
    target = ctx.workspace / f"screenshot-{int(time.time())}.png"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
        "$g=[System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); "
        f"$bmp.Save('{target}'); \"$($b.Width)x$($b.Height)\""
    )
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], ctx, timeout=30)
    return f"Saved screenshot to {target.name} ({out.splitlines()[-1] if out else 'unknown size'})"


def _open_application(ctx: ToolContext, args: dict[str, Any]) -> str:
    name = str(args.get("name", "")).strip()
    if not name or re.search(r"[;&|<>`$]", name):
        raise ToolError("Give a plain application name or path.")
    subprocess.Popen(["cmd", "/c", "start", "", name], creationflags=NO_WINDOW, cwd=ctx.workspace)
    return f"Asked Windows to open {name}"


def _type_text(ctx: ToolContext, args: dict[str, Any]) -> str:
    text = str(args.get("text", ""))
    escaped = re.sub(r"([+^%~(){}\[\]])", r"{\1}", text).replace("'", "''")
    script = f"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('{escaped}')"
    _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], ctx, timeout=30)
    return f"Typed {len(text)} characters into the active window"


def _click(ctx: ToolContext, args: dict[str, Any]) -> str:
    user32 = _user32()
    x, y = int(args.get("x", 0)), int(args.get("y", 0))
    user32.SetCursorPos(x, y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up
    return f"Clicked at ({x}, {y})"


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or list(properties)}


_S = {"type": "string"}
_I = {"type": "integer"}

TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            "read_file", "Read Files",
            "Read a text file from the workspace. A long file comes in parts: pass offset to go on from that character.",
            _obj({"path": _S, "offset": _I}, ["path"]), _read_file, Risk.LOW,
        ),
        ToolSpec("list_dir", "List Files", "List files and folders in a workspace directory.", _obj({"path": _S}, []), _list_dir, Risk.LOW),
        ToolSpec(
            "write_file", "Write Files",
            "Create or overwrite a text file in the workspace. Folders are made as needed: write the file, never an empty file to make a "
            "folder. For a long file, write the first part, then call again with append=true for each following part.",
            _obj({"path": _S, "content": _S, "append": {"type": "boolean"}}, ["path", "content"]), _write_file, Risk.MEDIUM,
        ),
        ToolSpec(
            "edit_file", "Edit Files",
            "Change part of an existing text file: replace the exact text old (copied from the file, spaces and line "
            "breaks included) with new. old must be found once; all=true replaces every occurrence.",
            _obj({"path": _S, "old": _S, "new": _S, "all": {"type": "boolean"}}, ["path", "old", "new"]), _edit_file, Risk.MEDIUM,
        ),
        ToolSpec("delete_file", "Delete Files", "Delete a file in the workspace.", _obj({"path": _S}), _delete_file, Risk.HIGH),
        ToolSpec("run_python", "Python", "Run a Python snippet in the workspace and return its output.", _obj({"code": _S}), _run_python, Risk.MEDIUM, group="Execution"),
        ToolSpec(
            "run_tests", "Test Runner",
            "Run the project's tests and return the result: pytest for Python tests, node --test for JavaScript tests (*.test.js).",
            _obj({"path": _S}, []), _run_tests, Risk.MEDIUM, group="Execution",
        ),
        ToolSpec("git", "Git", "Run a git command in the workspace, e.g. args='status'.", _obj({"args": _S}), _git, Risk.MEDIUM, classify_git, group="Execution"),
        ToolSpec(
            "powershell", "PowerShell",
            "Run a PowerShell command (also kubectl, helm, terraform, docker, ansible). Read-only commands run "
            "directly; anything that changes state asks the user first.",
            _obj({"command": _S}), _powershell, Risk.MEDIUM, classify_powershell, group="Execution",
        ),
        ToolSpec("list_windows", "List Windows", "List the titles of visible desktop windows.", _obj({}, []), _list_windows, Risk.LOW, group="Computer Use"),
        ToolSpec("active_window", "Active Window", "Get the title of the active window.", _obj({}, []), _active_window, Risk.LOW, group="Computer Use"),
        ToolSpec("read_clipboard", "Read Clipboard", "Read the text on the clipboard.", _obj({}, []), _read_clipboard, Risk.MEDIUM, group="Computer Use"),
        ToolSpec("screenshot", "Screenshot", "Capture the screen to a PNG file in the workspace.", _obj({}, []), _screenshot, Risk.MEDIUM, group="Computer Use"),
        ToolSpec("open_application", "Open Application", "Open an application or document by name.", _obj({"name": _S}), _open_application, Risk.HIGH, group="Computer Use"),
        ToolSpec("type_text", "Keyboard", "Type text into the active window.", _obj({"text": _S}), _type_text, Risk.HIGH, group="Computer Use"),
        ToolSpec("click", "Mouse", "Click the left mouse button at screen coordinates.", _obj({"x": _I, "y": _I}), _click, Risk.HIGH, group="Computer Use"),
    ]
}


def classify(name: str, arguments: dict[str, Any], ctx: ToolContext) -> tuple[Risk, str]:
    """Risk of one call, from its arguments and from where the run is working."""
    risk, reason = TOOLS[name].classify(arguments)
    if name in ("write_file", "edit_file") and ctx.protect_existing and risk != Risk.HIGH:
        try:
            target = _resolve(ctx, str(arguments.get("path", "")))
        except ToolError:
            return risk, reason
        if target.is_file() and target not in ctx.created:
            return Risk.HIGH, ("It overwrites a file that was already in your folder" if name == "write_file"
                               else "It changes a file that was already in your folder")
    return risk, reason


def tool_catalog() -> list[dict[str, Any]]:
    """Tool metadata for the workflow editor's node panel."""
    return [
        {"name": t.name, "label": t.label, "description": t.description, "risk": t.risk.value, "group": t.group}
        for t in TOOLS.values()
    ]


def tool_schemas(names: list[str]) -> list[dict[str, Any]]:
    return [TOOLS[n].schema() for n in names if n in TOOLS]


def execute(name: str, arguments: dict[str, Any], ctx: ToolContext) -> str:
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"Unknown tool '{name}'")
    return spec.handler(ctx, arguments)
