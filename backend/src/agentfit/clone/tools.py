"""The tools the agent that clones a website is given, in place of the ones opencode has.

The kit's skill was written for a coding agent that has a shell, sub-agents and, for judgment
work, a browser MCP server. Here the agent is whatever model the person chose (a model in LM
Studio or a cloud model), working in the run's folder with these tools: ``run_command`` runs the
kit's scripts and the project's npm scripts (a shell it is not: no pipes, no redirects), the
``browser_*`` tools are named like the Playwright MCP's, ``preview_start`` serves the project,
``ask_user`` is the question the skill asks the person (which pages to clone) and ``look`` is
what a text-only model cannot do: another model, one that sees, describes a screenshot.

Every file a tool writes is inside the run's workspace, like the other file tools.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from agentfit.clone import proc
from agentfit.clone.browser import BrowserCancelled, BrowserError
from agentfit.clone.devserver import DevServerError
from agentfit.clone.install import CloneSetupError
from agentfit.workflows import tools as base
from agentfit.workflows.tools import Risk, ToolContext, ToolError, ToolSpec

BROWSER_TOOLS = [
    "browser_navigate", "browser_resize", "browser_take_screenshot", "browser_snapshot", "browser_evaluate", "browser_click",
    "browser_hover", "browser_scroll", "browser_press_key", "browser_wait_for", "browser_network_requests", "browser_console_messages",
]
PROJECT_TOOLS = ["run_command", "json_outline", "preview_start", "preview_stop", "ask_user"]
MAX_LOOK_HEIGHT = 2000
LOOK_TOKENS = 1800
NEED_CONTEXT = 16384

_PACKAGE = re.compile(r"^(@[a-z0-9][a-z0-9-._~]*/)?[a-z0-9][a-z0-9-._~]*(@[\w.^~<>=*-]+)?$")
_SCRIPT = re.compile(r"^[A-Za-z0-9:_.-]{1,60}$")

LOOK_SYSTEM = """You look at screenshots of a web page for a front-end developer who has to rebuild it. Say only what is visible.
- Positions and sizes in px of the screenshot (x from the left edge, y from the top); colours as hex when you can read them.
- Top to bottom, for each thing on the screen: what it is, where it is, how big, and how it looks.
- Type: size, weight, case, serif or sans, colour, alignment. Short texts (headings, buttons, labels) are quoted as they are written.
- Layout: number of columns, alignment, spacing between things, what is cut at the edges (a strip of repeated items is a ticker or a carousel).
- Pictures: what kind they are (photo, flat or 3D illustration, logo row, icon, pattern, video frame, gradient), their palette and composition.
- Signs of state: a hover effect, an open menu, a half faded-in element, motion blur.
Plain short lines, no introduction, no closing remarks. When you are asked for the differences between two pictures, list them most important first, each saying which element, where, and what it should look like."""


# -- helpers ------------------------------------------------------------------


def _session(ctx: ToolContext) -> Any:
    if ctx.clone is None:
        raise ToolError("There is no browser in this run.")
    return ctx.clone()


def _host(ctx: ToolContext) -> Any:
    try:
        return _session(ctx).browser()
    except (BrowserError, CloneSetupError) as exc:
        raise ToolError(str(exc)) from exc


def _call(ctx: ToolContext, cmd: str, args: dict[str, Any] | None = None, timeout: float = 120.0) -> Any:
    host = _host(ctx)
    try:
        return host.call(cmd, args or {}, timeout=timeout)
    except BrowserCancelled as exc:
        raise ToolError("Stopped.") from exc
    except BrowserError as exc:
        raise ToolError(str(exc)) from exc


def _address(raw: Any) -> str:
    url = str(raw or "").strip()
    if not url:
        raise ToolError("Give the address of the page.")
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):(.*)$", url)
    if scheme and not scheme.group(2).startswith("//") and not scheme.group(2)[:1].isdigit():
        # "javascript:...", "data:...", "mailto:...": a scheme without slashes. "localhost:3000" is a host and a port.
        raise ToolError("Only http and https addresses can be opened.")
    if "://" not in url:
        url = "https://" + url
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise ToolError("Only http and https addresses can be opened.")
    return url


def _relative(ctx: ToolContext, path: Path) -> str:
    try:
        return str(path.relative_to(ctx.workspace.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _out(path: Path, ctx: ToolContext, default_dir: str, suffix: str = ".png") -> Path:
    """Where a file the tool writes goes: inside the workspace. A name that is not already under docs/ or .tasks/
    goes under ``default_dir`` (seen: a 12B saved its screenshots in a folder named after the site, at the root)."""
    name = str(path).replace("\\", "/").lstrip("/")
    if not name.startswith(("docs/", ".tasks/")):
        name = f"{default_dir}/{name}"
    target = base._resolve(ctx, name)
    if suffix and target.suffix.lower() != suffix:
        target = target.with_suffix(suffix)
    return target


def _png_height(path: Path) -> int | None:
    try:
        head = path.read_bytes()[:24]
        return int.from_bytes(head[20:24], "big") if head[:8] == b"\x89PNG\r\n\x1a\n" else None
    except OSError:
        return None


# -- browser tools --------------------------------------------------------------


def _navigate(ctx: ToolContext, args: dict[str, Any]) -> str:
    r = _call(ctx, "navigate", {"url": _address(args.get("url"))}, timeout=150)
    text = (f"Opened {r['url']} (HTTP {r['status']}, {r['ms']} ms). Title: {r['title']!r}. "
            f"The page is {r['width']} px wide and {r['height']} px tall at this viewport.")
    if r["status"] and r["status"] >= 400:
        text += (f" HTTP {r['status']} is an error page, not the site: a server that refuses automated browsers answers like this. "
                 "Do not describe it as the site's content. Try another address of the same site once; if it answers the same, say plainly that the site cannot be opened and stop.")
    return text


def _resize(ctx: ToolContext, args: dict[str, Any]) -> str:
    try:
        width, height = int(args.get("width")), int(args.get("height"))
    except (TypeError, ValueError) as exc:
        raise ToolError("Give width and height in px, for example 390 and 844.") from exc
    if not (200 <= width <= 3840 and 200 <= height <= 3840):
        raise ToolError("Width and height must be between 200 and 3840 px.")
    _call(ctx, "resize", {"width": width, "height": height})
    return f"The viewport is now {width}x{height}. Scroll or reload if the page lays itself out on load."


def _screenshot(ctx: ToolContext, args: dict[str, Any]) -> str:
    name = str(args.get("filename") or "").strip() or f"screenshot-{int(time.time())}.png"
    target = _out(Path(name), ctx, "docs/design-references")
    full = str(args.get("fullPage", "")).strip().lower() in ("true", "1", "yes")
    r = _call(ctx, "screenshot", {"path": str(target), "fullPage": full, "selector": str(args.get("selector") or "")}, timeout=120)
    note = f"Saved {_relative(ctx, target)} ({r['width']}x{r['height']} px, {r['bytes'] // 1024} KB)."
    return note + (" A picture, not text: use the look tool to see it." if ctx.vision_slot else " A picture, not text: read the page with browser_snapshot and browser_evaluate.")


def _snapshot(ctx: ToolContext, args: dict[str, Any]) -> str:
    return str(_call(ctx, "snapshot", {"selector": str(args.get("selector") or "")}, timeout=60)["text"])


def _evaluate(ctx: ToolContext, args: dict[str, Any]) -> str:
    code = str(args.get("function") or args.get("expression") or "").strip()
    if not code:
        raise ToolError('Give the JavaScript to run in the page, for example "() => getComputedStyle(document.body).fontFamily".')
    return str(_call(ctx, "evaluate", {"expression": code}, timeout=60)["value"])


def _pointer(cmd: str, ctx: ToolContext, args: dict[str, Any]) -> str:
    selector, text = str(args.get("selector") or "").strip(), str(args.get("text") or args.get("element") or "").strip()
    if not selector and not text:
        raise ToolError("Give a CSS selector or the visible text of what to point at.")
    r = _call(ctx, cmd, {"selector": selector, "text": text}, timeout=60)
    return f"Done ({cmd}). The page is at {r.get('url', 'the same address')}." if cmd == "click" else "Pointer is over it."


def _click(ctx: ToolContext, args: dict[str, Any]) -> str:
    return _pointer("click", ctx, args)


def _hover(ctx: ToolContext, args: dict[str, Any]) -> str:
    return _pointer("hover", ctx, args)


def _scroll(ctx: ToolContext, args: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for key in ("to", "by"):
        if args.get(key) not in (None, ""):
            try:
                payload[key] = int(args[key])
            except (TypeError, ValueError) as exc:
                raise ToolError(f"{key} must be a number of px.") from exc
    if args.get("selector"):
        payload["selector"] = str(args["selector"])
    if not payload:
        payload["by"] = 600
    r = _call(ctx, "scroll", payload, timeout=60)
    return f"Scrolled with the mouse wheel: now at y {r['y']} of {r['height']} px."


def _press(ctx: ToolContext, args: dict[str, Any]) -> str:
    key = str(args.get("key") or "").strip()
    if not key:
        raise ToolError("Give a key, for example Escape or ArrowDown.")
    _call(ctx, "press", {"key": key})
    return f"Pressed {key}."


def _wait_for(ctx: ToolContext, args: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    if args.get("text"):
        payload["text"] = str(args["text"])
    elif args.get("selector"):
        payload["selector"] = str(args["selector"])
    else:
        try:
            payload["ms"] = int(float(args.get("time") or 1) * 1000)
        except (TypeError, ValueError) as exc:
            raise ToolError("time is a number of seconds.") from exc
    _call(ctx, "wait", payload, timeout=60)
    return "Done waiting."


def _network(ctx: ToolContext, args: dict[str, Any]) -> str:
    r = _call(ctx, "network", {"filter": str(args.get("filter") or ""), "limit": 120}, timeout=30)
    kinds = ", ".join(f"{k} {v}" for k, v in sorted(r["by_type"].items(), key=lambda kv: -kv[1]))
    lines = [f"{r['total']} requests since the page was opened ({kinds})."]
    for item in r["requests"]:
        size = f" {item['bytes'] // 1024} KB" if item.get("bytes") else ""
        lines.append(f"{item['status']} {item['type']} {item['mime'] or '-'}{size} {item['url']}")
    return "\n".join(lines)


def _console(ctx: ToolContext, args: dict[str, Any]) -> str:
    messages = _call(ctx, "console", {}, timeout=30)["messages"]
    return "\n".join(f"[{m['type']}] {m['text']}" for m in messages) or "(nothing in the console)"


# -- assets and pictures -----------------------------------------------------------

_MIME_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/avif": ".avif", "image/gif": ".gif",
                "image/svg+xml": ".svg", "video/mp4": ".mp4", "video/webm": ".webm", "font/woff2": ".woff2", "font/woff": ".woff",
                "font/ttf": ".ttf", "font/otf": ".otf"}


def _download(ctx: ToolContext, args: dict[str, Any]) -> str:
    url = _address(args.get("url"))
    raw = str(args.get("path") or "").strip()
    if not raw:
        name = re.sub(r"[^A-Za-z0-9._-]", "-", url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]) or "asset"
        raw = f"public/images/{name}"
    target = base._resolve(ctx, raw)
    r = _call(ctx, "fetch", {"url": url, "path": str(target)}, timeout=120)
    ctx.created.add(target)
    note = ""
    expected = _MIME_SUFFIX.get(r.get("mime") or "")
    if expected and target.suffix.lower() not in (expected, ".jpeg" if expected == ".jpg" else expected):
        note = f" Careful: the server says {r['mime']} but the file is named {target.suffix or 'without an extension'}; rename it to {expected}."
    return f"Saved {_relative(ctx, target)} ({r['bytes']} bytes, {r.get('mime') or 'unknown type'}).{note}"


def _look(ctx: ToolContext, args: dict[str, Any]) -> str:
    if ctx.complete is None or not ctx.vision_slot:
        raise ToolError("No model that can see is set for this run. Read the page with browser_snapshot and browser_evaluate instead.")
    path = base._resolve(ctx, str(args.get("path") or ""), read=True)
    if not path.is_file():
        raise ToolError(f"File not found: {args.get('path')}")
    other = base._resolve(ctx, str(args["other"]), read=True) if args.get("other") else None
    if other is not None and not other.is_file():
        raise ToolError(f"File not found: {args.get('other')}")
    top = int(args.get("from_y") or 0)
    height = int(args.get("height") or 0)
    total = _png_height(path)
    note = ""
    if total and (height or top or total > MAX_LOOK_HEIGHT):
        height = height or MAX_LOOK_HEIGHT
        part = Path(ctx.workspace) / ".cache" / "look" / f"{path.stem}-{top}-{height}.png"
        _call(ctx, "crop", {"path": str(path), "top": top, "height": height, "out": str(part)}, timeout=60)
        if top + height < total:
            note = (f" The picture is {total} px tall; this shows y {top} to {min(total, top + height)}. "
                    f"For the next part call look again with from_y={top + height}.")
        path = part
    images = [path] + ([other] if other is not None else [])
    question = str(args.get("question") or "").strip() or ("List the differences between image 1 and image 2." if other is not None else "Describe what this shows.")
    content: list[dict[str, Any]] = [{"type": "text", "text": question}]
    for image in images:
        data = base64.b64encode(image.read_bytes()).decode("ascii")
        mime = "image/jpeg" if image.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    messages = [{"role": "system", "content": LOOK_SYSTEM}, {"role": "user", "content": content}]
    try:
        response = ctx.complete(ctx.vision_slot, messages, need_context=NEED_CONTEXT, max_tokens=LOOK_TOKENS, temperature=0.2)
    except Exception as exc:
        raise ToolError(f"The model that sees could not read the picture: {exc}") from exc
    message = ((response.get("choices") or [{}])[0].get("message") or {})
    text = re.sub(r"<think>.*?</think>", "", str(message.get("content") or ""), flags=re.DOTALL).strip()
    return (text or "The model that sees gave no description.") + note


# -- the project: commands, the app, the question ---------------------------------------------------

COMMAND_SECONDS = 600
MAX_COMMAND_SECONDS = 1800
GIT_READ = {"status", "log", "diff", "show", "rev-parse", "ls-files", "branch", "describe", "blame"}
NPX_BINS = {"tsc", "eslint", "next", "playwright"}
NPM_BLOCKING = {"start", "dev"}
# Options that cannot send the install somewhere else (a registry, a prefix, a script shell).
NPM_INSTALL_FLAGS = {"--no-fund", "--no-audit", "--legacy-peer-deps", "--save-dev", "-D", "--save-exact", "-E", "--silent", "--no-progress", "--prefer-offline",
                     "--ignore-scripts", "--loglevel=error", "--loglevel=warn", "--force"}
_NO_SHELL = ("Shell syntax is not available here (pipes, redirects, a lone &, $VARIABLES, backticks). Run each command on its own and read its "
             "output; put an address that has an & in it in quotes.")


def _clip(text: str, limit: int = 12_000) -> str:
    """A result that fits one answer: the start (what ran) and the end (how it ended, where the errors are)."""
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    return text[: limit // 3] + f"\n... [{cut} characters cut] ...\n" + text[-(limit - limit // 3):]


def _split_command(command: str) -> list[str]:
    """The commands of a line joined with && ; or newlines, without a shell: quotes are kept, # starts a comment, other shell syntax is refused."""
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    text = command.strip()
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch == "#" and (not current or current[-1].isspace()):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("&&", i):
            parts.append("".join(current))
            current = []
            i += 1
        elif ch in ";\n":
            parts.append("".join(current))
            current = []
        elif ch in "|<>&`" or text.startswith("$(", i) or (ch == "$" and text[i + 1: i + 2].isalpha()):
            raise ToolError(_NO_SHELL)
        else:
            current.append(ch)
        i += 1
    if quote:
        raise ToolError("A quote is not closed in the command.")
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _argv(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError as exc:
        raise ToolError(f"The command could not be read: {exc}") from exc


def _program(argv: list[str]) -> str:
    return re.sub(r"\.(cmd|exe|bat)$", "", argv[0].lower()) if argv else ""


def _check_node(ctx: ToolContext, argv: list[str]) -> None:
    if len(argv) >= 2 and argv[1] in ("--version", "-v"):
        return
    if len(argv) < 2 or argv[1].startswith("-"):
        raise ToolError("node runs a script of the project: node scripts/extract/page.mjs <address>. Options such as -e, -p or --import are not available. To look inside a JSON file use json_outline.")
    script = base._resolve(ctx, argv[1])
    if script.suffix.lower() not in (".mjs", ".js", ".cjs") or not script.is_file():
        raise ToolError(f"{argv[1]} is not a script of the project (a .mjs, .js or .cjs file inside it).")


def _check_npm(ctx: ToolContext, argv: list[str]) -> None:
    sub = argv[1] if len(argv) > 1 else ""
    if sub in ("run", "run-script"):
        script = argv[2] if len(argv) > 2 else ""
        if not _SCRIPT.match(script):
            raise ToolError("Give the name of a script from package.json, for example build, lint, typecheck or check.")
        package = ctx.workspace / "package.json"
        if not package.is_file() or f'"{script}"' not in package.read_text(encoding="utf-8", errors="replace"):
            raise ToolError(f'package.json has no "{script}" script.')
        if script in NPM_BLOCKING:
            raise ToolError(f"npm run {script} does not end: it serves the project. Use preview_start (and preview_stop), which keeps it running for you.")
        if not (ctx.workspace / "node_modules").is_dir():
            raise ToolError("The packages are not installed (no node_modules): run npm install first.")
    elif sub == "start":
        raise ToolError("npm start does not end: it serves the project. Use preview_start with production=true (it builds first), and preview_stop.")
    elif sub in ("install", "i", "add", "ci"):
        for flag in (a for a in argv[2:] if a.startswith("-")):
            if flag not in NPM_INSTALL_FLAGS:
                raise ToolError(f"npm install takes only these options here: {', '.join(sorted(NPM_INSTALL_FLAGS))}. {flag} is not one of them.")
        packages = [a for a in argv[2:] if not a.startswith("-")]
        if len(packages) > 8:
            raise ToolError("Add at most 8 packages at a time.")
        for item in packages:
            if not _PACKAGE.match(item):
                raise ToolError(f"'{item}' is not a package name (name or name@version).")
        if not (ctx.workspace / "package.json").is_file():
            raise ToolError("The workspace has no package.json.")
    elif sub not in ("ls", "list"):
        raise ToolError("npm is available for: run <script>, install, ci, ls.")


def _check_npx(argv: list[str]) -> None:
    bins = [a for a in argv[1:] if not a.startswith("-")]
    # The program comes first: an option before it (--package=..., -p ...) could name another one to run.
    if len(argv) < 2 or argv[1].startswith("-") or not bins or bins[0] not in NPX_BINS:
        raise ToolError(f"npx is available for: {', '.join(sorted(NPX_BINS))} (programs the project already has).")
    if bins[0] == "playwright" and (len(bins) < 2 or bins[1] != "install"):
        raise ToolError("npx playwright is available for: playwright install chromium.")
    if bins[0] == "next" and (len(bins) < 2 or bins[1] not in ("build", "lint", "info")):
        raise ToolError("npx next is available for: next build, next lint. To serve the project use preview_start.")


def _check_git(argv: list[str]) -> None:
    sub = next((a for a in argv[1:] if not a.startswith("-")), "")
    if sub not in GIT_READ:
        raise ToolError(f"git is read-only here: {', '.join(sorted(GIT_READ))}.")
    if any(a.startswith(("--output", "--ext-diff", "--exec")) for a in argv[1:]):
        raise ToolError("git is read-only here: it cannot write to a file or run another program.")
    if sub == "branch" and any(not a.startswith("-") or a in ("-d", "-D", "-m", "-M", "-c", "-C") for a in argv[argv.index("branch") + 1:]):
        raise ToolError("git branch only lists here (git branch, -a, -r, --show-current).")


def _validate(ctx: ToolContext, command: str) -> list[list[str]]:
    segments = [_argv(part) for part in _split_command(command)]
    if not segments:
        raise ToolError("Give a command, for example: node scripts/extract/page.mjs https://example.com")
    for argv in segments:
        program = _program(argv)
        if program == "node":
            _check_node(ctx, argv)
        elif program == "npm":
            _check_npm(ctx, argv)
        elif program == "npx":
            _check_npx(argv)
        elif program == "git":
            _check_git(argv)
        elif program in ("mkdir", "ls", "dir", "cat", "type", "cp", "copy", "mv", "move", "rm", "del", "touch", "echo", "cd"):
            raise ToolError(f"{program} is not available: use read_file, list_dir, write_file (folders are made as needed), edit_file and delete_file for files and folders.")
        else:
            raise ToolError(f"'{argv[0]}' is not available. run_command runs: node scripts/..., npm run/install/ci, npx tsc/eslint/playwright install/next build, and read-only git.")
    return segments


def _executable(ctx: ToolContext, program: str) -> str:
    install = _session(ctx).install
    if program == "node":
        found = install.node_exe()
    elif program == "npm":
        found = install.npm_exe()
    else:
        found = shutil.which(program) or (shutil.which(program + ".cmd") if program == "npx" else None)
    if not found:
        raise ToolError(f"{program} is not on this PC.")
    return str(found)


def _run_command(ctx: ToolContext, args: dict[str, Any]) -> str:
    segments = _validate(ctx, str(args.get("command") or ""))
    try:
        budget = min(MAX_COMMAND_SECONDS, max(10, int(args.get("timeout_seconds") or COMMAND_SECONDS)))
    except (TypeError, ValueError) as exc:
        raise ToolError("timeout_seconds is a number of seconds.") from exc
    started = time.perf_counter()
    report: list[str] = []
    for argv in segments:
        program = _program(argv)
        argv = [_executable(ctx, program), *argv[1:]]
        if program == "npx" and "--no-install" not in argv:
            argv.insert(1, "--no-install")
        left = budget - (time.perf_counter() - started)
        if left <= 0:
            raise ToolError(f"The commands did not finish within {budget} s.\n{_clip(chr(10).join(report), 4000)}")
        try:
            code, lines, timed_out = proc.run_streaming(argv, ctx.workspace, cancelled=ctx.cancelled, timeout=left, env={"NEXT_TELEMETRY_DISABLED": "1", "FORCE_COLOR": "0"})
        except proc.Cancelled as exc:
            raise ToolError("Stopped.") from exc
        except OSError as exc:
            raise ToolError(str(exc)) from exc
        shown = " ".join(shlex.quote(a) for a in argv[1:])[:160]
        report.append(f"$ {program} {shown}\n" + "\n".join(lines))
        if timed_out:
            raise ToolError(f"{program} did not finish within {budget} s and was stopped.\n{_clip(chr(10).join(report), 6000)}")
        if code != 0:
            return _clip(f"exit code {code} ({time.perf_counter() - started:.0f}s)\n" + "\n".join(report))
    return _clip(f"exit code 0 ({time.perf_counter() - started:.0f}s)\n" + "\n".join(report))


def _classify_command(arguments: dict[str, Any]) -> tuple[Risk, str]:
    try:
        parts = [_argv(p) for p in _split_command(str(arguments.get("command") or ""))]
    except ToolError:
        return Risk.MEDIUM, "Command may change local state"
    for argv in parts:
        if _program(argv) == "npm" and len(argv) > 1 and argv[1] in ("install", "i", "add") and any(not a.startswith("-") for a in argv[2:]):
            return Risk.HIGH, "It adds packages to the project and npm runs their install scripts"
    if parts and all(_program(a) == "git" for a in parts):
        return Risk.LOW, "Read-only Git command"
    return Risk.MEDIUM, "Runs a script or a build of the project"


def _preview_start(ctx: ToolContext, args: dict[str, Any]) -> str:
    production = str(args.get("production", "")).strip().lower() in ("true", "1", "yes")
    session = _session(ctx)
    dev = session.dev()
    if dev.running and dev.production != production:
        dev.stop()
    try:
        url = dev.start(cancelled=ctx.cancelled, production=production)
    except DevServerError as exc:
        raise ToolError(str(exc)) from exc
    how = "its production build (npm run build, then npm start)" if production else "npm run dev"
    return (f"The project is served at {url} from {how}. Open it with browser_navigate; a page of the app is at {url}/<route>. "
            "The server stays up until you call preview_stop or the run ends." + ("" if production else " Edits are picked up by themselves."))


def _preview_stop(ctx: ToolContext, args: dict[str, Any]) -> str:
    return "The server was stopped." if _session(ctx).stop_dev() else "No server was running."


def _ask_user(ctx: ToolContext, args: dict[str, Any]) -> str:
    question = str(args.get("question") or "").strip()
    if not question:
        raise ToolError("Write the question.")
    options = args.get("options")
    listed = [str(o) for o in options] if isinstance(options, list) else []
    try:
        answer = _session(ctx).ask(question, listed)
    except BrowserError as exc:
        raise ToolError(str(exc)) from exc
    return f"The person answered: {answer}"


# -- reading a big JSON file in small pieces ----------------------------------------------------

OUTLINE_BUDGET = 3500
OUTLINE_KEYS = 40
OUTLINE_ITEMS = 3
OUTLINE_BYTES = 8_000_000
# A part this small is shown whole: its values are what the agent came for. Seen on a 27B Cloner (brioni.com, 2026-10-23): the
# outline of the 8 colours of tokens.json showed the first one, and it asked for /colors/3, /colors/4, ... one call each.
OUTLINE_WHOLE = 3000


def _brief(value: Any) -> str:
    """One line about a value: its kind and size, the first keys of an object, a string cut short."""
    if isinstance(value, dict):
        keys = list(value)
        return f"object, {len(keys)} keys: " + ", ".join(str(k) for k in keys[:6]) + (", ..." if len(keys) > 6 else "")
    if isinstance(value, list):
        first = value[0] if value else None
        kind = "empty" if not value else type(first).__name__
        keys = f" with keys: {', '.join(str(k) for k in list(first)[:6])}" if isinstance(first, dict) else ""
        return f"array, {len(value)} items ({kind}){keys}"
    if isinstance(value, str):
        return json.dumps(value[:100], ensure_ascii=False) + (f"... ({len(value)} characters)" if len(value) > 100 else "")
    return json.dumps(value, ensure_ascii=False)


def _render(value: Any, depth: int, indent: int, out: list[str]) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        shown = list(value.items())[:OUTLINE_KEYS]
        labels = [(str(key), child) for key, child in shown]
        rest = len(value) - len(shown)
    elif isinstance(value, list):
        labels = [(f"[{i}]", child) for i, child in enumerate(value[:OUTLINE_ITEMS])]
        rest = len(value) - len(labels)
    else:
        return
    for label, child in labels:
        out.append(f"{pad}{label}: {_brief(child)}")
        if depth > 0 and isinstance(child, (dict, list)) and child:
            _render(child, depth - 1, indent + 1, out)
    if rest > 0:
        out.append(f"{pad}... and {rest} more {'keys' if isinstance(value, dict) else 'items'}")


def _descend(data: Any, pointer: str) -> tuple[Any, str]:
    """The part of ``data`` a pointer names: ``/fonts/0/src`` (JSON Pointer) or ``fonts.0.src``."""
    text = pointer.strip()
    if not text or text == "/":
        return data, "/"
    tokens = [t.replace("~1", "/").replace("~0", "~") for t in text.split("/")[1:]] if text.startswith("/") else text.split(".")
    node, where = data, ""
    for token in tokens:
        try:
            if isinstance(node, dict):
                node = node[token]
            elif isinstance(node, list):
                node = node[int(token)]
            else:
                raise KeyError(token)
        except (KeyError, IndexError, ValueError):
            if isinstance(node, dict):
                have = ", ".join(str(k) for k in list(node)[:30])
            elif isinstance(node, list):
                have = f"an array of {len(node)} items: use a number from 0 to {len(node) - 1}"
            else:
                have = "a value that has nothing inside"
            raise ToolError(f"Nothing called '{token}' at {where or '/'}. There: {have}.") from None
        where += "/" + token
    return node, where


def _json_outline(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = base._resolve(ctx, str(args.get("path") or ""), read=True)
    if not path.is_file():
        raise ToolError(f"File not found: {args.get('path')}")
    if path.stat().st_size > OUTLINE_BYTES:
        raise ToolError(f"{path.name} is {path.stat().st_size // 1_000_000} MB: too big to outline.")
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError as exc:
        raise ToolError(f"{path.name} is not valid JSON ({exc}). Read it with read_file.") from exc
    node, where = _descend(data, str(args.get("pointer") or ""))
    if isinstance(node, (dict, list)):
        whole = json.dumps(node, ensure_ascii=False)
        if len(whole) <= OUTLINE_WHOLE:
            return f"{args.get('path')} {where} (all of it): {whole}"
    try:
        depth = max(0, min(4, int(args.get("depth") if args.get("depth") is not None else 2)))
    except (TypeError, ValueError):
        depth = 2
    out = [f"{args.get('path')} {where}: {_brief(node)}"]
    _render(node, depth, 1, out)
    text = "\n".join(out)
    if len(text) > OUTLINE_BUDGET:
        text = text[:OUTLINE_BUDGET] + "\n... [cut: give a pointer to look at one part, or a smaller depth]"
    return text + f"\n(a pointer to a part under {OUTLINE_WHOLE} characters shows all of it)"


# -- registry --------------------------------------------------------------------------

_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


def _spec(name: str, label: str, description: str, parameters: dict[str, Any], handler: Any, risk: Risk = Risk.LOW,
          group: str = "Browser", classifier: Any = None) -> ToolSpec:
    return ToolSpec(name, label, description, parameters, handler, risk, classifier, group=group)


SPECS = [
    _spec("browser_navigate", "Open page", "Open an address in the browser and wait for it to load. Returns the title, the size of the page "
          "and the HTTP status.", _obj({"url": _S}, ["url"]), _navigate),
    _spec("browser_resize", "Resize browser", "Set the viewport, for example 1440x900 (desktop), 768x1024 (tablet) or 390x844 (phone).",
          _obj({"width": _I, "height": _I}, ["width", "height"]), _resize),
    _spec("browser_take_screenshot", "Screenshot", "Save a PNG of the page into the workspace. fullPage=true takes the whole page "
          "(scrolled once first so lazy images load); selector takes one element. Files are saved under docs/design-references/ (or docs/ or .tasks/ when the name starts with that).",
          _obj({"filename": _S, "fullPage": _B, "selector": _S}), _screenshot, Risk.MEDIUM),
    _spec("browser_snapshot", "Page structure", "The structure of the page (or of one element) as text: roles, names, links, headings.",
          _obj({"selector": _S}), _snapshot),
    _spec("browser_evaluate", "Run script in page", "Run JavaScript in the page and return its result as JSON, for example "
          "\"() => getComputedStyle(document.querySelector('h1')).fontSize\". Use it to read computed styles, sizes, positions, "
          "image sources and libraries, instead of guessing from a picture. Return only what you need (a getComputedStyle result is "
          "reduced to the ~30 properties that matter for a rebuild).", _obj({"function": _S}, ["function"]), _evaluate),
    _spec("browser_click", "Click", "Click an element, given by CSS selector or by its visible text.",
          _obj({"selector": _S, "text": _S}), _click),
    _spec("browser_hover", "Hover", "Move the pointer over an element, given by CSS selector or by its visible text, to see its hover state.",
          _obj({"selector": _S, "text": _S}), _hover),
    _spec("browser_scroll", "Scroll", "Scroll like a visitor, with the mouse wheel: to a position (to), by a distance (by) or to an element "
          "(selector). Scroll-linked animations and smooth-scroll libraries only react to this, not to a script.",
          _obj({"to": _I, "by": _I, "selector": _S}), _scroll),
    _spec("browser_press_key", "Press key", "Press a key, for example Escape, Tab or ArrowDown.", _obj({"key": _S}, ["key"]), _press),
    _spec("browser_wait_for", "Wait", "Wait for some seconds (time), for text to appear (text) or for an element (selector).",
          _obj({"time": {"type": "number"}, "text": _S, "selector": _S}), _wait_for),
    _spec("browser_network_requests", "Network requests", "The requests the page made since it was opened: status, kind, size and "
          "address. filter keeps those whose address, kind (image, font, media, script, stylesheet) or type contains it.",
          _obj({"filter": _S}), _network),
    _spec("browser_console_messages", "Console", "What the page wrote to the browser console, errors included.", _obj({}), _console),
    _spec("download_asset", "Download file", "Save a file the page uses (image, video, font, SVG) into the workspace, with the page's own "
          "cookies. path is inside the workspace, for example public/images/hero.jpg.", _obj({"url": _S, "path": _S}, ["url"]),
          _download, Risk.MEDIUM),
    _spec("look", "Look at a picture", "A model that can see describes a picture saved in the workspace (a screenshot): positions, sizes, "
          "colours, type, layout. question narrows it; other is a second picture to compare with; for a tall picture from_y and height "
          "choose the part.", _obj({"path": _S, "question": _S, "other": _S, "from_y": _I, "height": _I}, ["path"]), _look),
    _spec("run_command", "Run command", "Run a command in the project's root and return its output: node scripts/<script>.mjs <args> (the kit's scripts), "
          "npm run <script> / npm install / npm ci, npx tsc / eslint / playwright install / next build, read-only git. Several commands can be "
          "joined with &&. It is not a shell: no cd, pipes, redirects, & or $VARIABLES. A command has 10 minutes; timeout_seconds allows up to 30.",
          _obj({"command": _S, "timeout_seconds": _I}, ["command"]), _run_command, Risk.MEDIUM, "Project", _classify_command),
    _spec("json_outline", "Outline a JSON file", "The shape of a JSON file in a few lines: its keys, the size of each array, the start of each value. "
          "Use it instead of reading a big file (tokens.json, css.json, assets.json, the manifest) page by page, which fills your memory. "
          "pointer goes into one part, for example /fontFaces/0 or fontFaces.0.src, and a part under 3000 characters is shown whole, with every "
          "value; depth (0-4, default 2) says how many levels of a bigger part to open.",
          _obj({"path": _S, "pointer": _S, "depth": _I}, ["path"]), _json_outline, Risk.LOW, "Project"),
    _spec("preview_start", "Start the app", "Serve the project on this PC and return its address. production=true builds it first (npm run build) and "
          "serves the build (npm start); without it, npm run dev. Stop it with preview_stop before you start it in the other way.",
          _obj({"production": _B}), _preview_start, Risk.MEDIUM, "Project"),
    _spec("preview_stop", "Stop the app", "Stop the server started by preview_start.", _obj({}), _preview_stop, Risk.LOW, "Project"),
    _spec("ask_user", "Ask the person", "Ask the person a question in the interface and wait for the answer (use it where the skill says to ask the user, "
          "and for nothing you can decide yourself). options are short answers shown as buttons; the person can also write their own.",
          _obj({"question": _S, "options": {"type": "array", "items": _S}}, ["question"]), _ask_user, Risk.LOW, "Project"),
]


def register() -> None:
    """Add the cloning tools to the ones every workflow can use."""
    for spec in SPECS:
        base.TOOLS[spec.name] = spec


register()

__all__ = ["BROWSER_TOOLS", "PROJECT_TOOLS", "SPECS", "register"]
