# Lessons

Things that went wrong once and cost time, with the cause and the rule that prevents them. One `##` section is one lesson. Read the ones that touch the area you are about to change.

Every section is also copied into the project memory (`willitrun memory import-docs`), so `willitrun memory recall "<error or topic>"` finds it by words. Add a lesson when a fix was not obvious or a mistake is easy to repeat: a new `##` section at the end, with what happened, why, and the rule. Decisions (why something is the way it is) go in DECISIONS.md.

## Civitai answers 401 without a personal token
*Found 2026-10-27, in `backend/src/agentfit/imagegen`.* Models listed from Civitai could not be downloaded: its API refuses a download that carries no token. The catalog keeps those entries flagged `needs_token`, the page says "Needs your Civitai token" and refuses before queueing, instead of failing halfway. The owner does not want to use Civitai at all: add models from Hugging Face files that need no token, verify the URL, size and checksum, and never ask for or look for a token.

## Qwen-Image 2.1 needs the leejet branch of ComfyUI-GGUF
*Found 2026-10-27.* With city96's ComfyUI-GGUF the Qwen-Image 2.1 files fail to load with "Unknown model architecture". The component in `imagegen/data/components.json` installs `https://github.com/leejet/ComfyUI-GGUF` and `tests/test_imagegen.py` asserts it. Restart ComfyUI after installing a custom node. The flow the owner confirmed in ComfyUI is `imagegen/data/workflows/qwen_image_txt2img.json`: keep its defaults.

## Pony V6 draws an illustration from a sentence
*Found 2026-10-27.* Asked with a descriptive sentence it drew an illustration; asked with photographic tags (`photo (medium), realistic, real life, 1girl, solo, ...`) it made a photograph. Its entry carries a photographic negative prompt and its own workflow, `pony_txt2img`. Test a new model by looking at its picture, not by reading its model card.

## A model-written `<script>` must never reach an exported page
*Arena exports, `arena/export.py` and `arena/answers.py`.* The HTML reports contain no `<script` and no external address, and an answer that contains `<script>` is shown escaped (`&lt;script&gt;`). `tests/test_arena_api.py` checks both. A report is opened by a person with a browser; the text in it was written by a model.

## The Windows console is cp1252
A Python script that prints a character outside that code page (a star, an arrow, a dash) dies with `UnicodeEncodeError` when its output goes to a pipe or a console. Run scripts with `python -X utf8` (or `PYTHONUTF8=1`); the hook scripts switch their own output to UTF-8 and CI runs the scripts with `-X utf8`. The same applies to reading: open text files with an explicit `encoding`. The `willitrun memory` commands switch their own output to UTF-8 (`memory_output` in `cli.py`); before that, `memory recall` crashed on a cp1252 pipe when an entry held an arrow or a star.

## The code graph does not see calls made through an attribute
`codegraph callers BrainService.add` lists one caller; a text search finds ten, most of them written `memory.service.add(...)` or `self.service.add(...)`. Use the graph to find where to look, then confirm "these are all the callers" with `grep -rn "\.add(" backend/src` before changing a signature. Found by a fresh agent in the 2026-10-27 test (AGENT_CONTEXT.md, "Measured").

## A link with a `download` attribute does nothing inside the desktop window
The Images page saved pictures with `<a href="..." download>`. In the Tauri window (WebView2) the picture lives on another origin (the core on 127.0.0.1) and the attribute is ignored, so the button looked dead. Save through the window's Save dialog and a route that writes the file (`lib/imageSave.ts`, `POST /api/images/gallery/{id}/save`), the way the Arena saves its reports, and fall back to a fetched blob only in a plain browser.

## A text file that does not end with a newline glues the next appended line to it
`desktop/src/locales/it.txt` has Windows line endings and no newline after its last line; `cat >> it.txt` joined the first new pair to the last old one and the catalog check reported one string missing. Look at the end of a file (`tail -c 100 file | od -c`) before appending, and keep one kind of line ending in it.

## Some source files begin with a byte order mark
`backend/src/agentfit/api/server.py` is one. Text read as plain UTF-8 starts with U+FEFF and `ast.parse` fails with "invalid non-printable character U+FEFF". Read source with `encoding="utf-8-sig"` (the map generator and the hooks do).

## Do not write code through a shell heredoc full of escapes
*This session's mistake, twice.* A `python - <<'EOF'` that patches a file with `\n` escapes turned them into real line breaks and corrupted `brain/service.py`; the fix was `git checkout -- <file>` and writing the change with the editor tools. Create and edit files with the file tools (Write, Edit), not with a command that rewrites text inside a quoted string. Heredocs are fine for short read-only probes.

## Run ruff from `backend/`
Ruff's `src = ["src", "tests"]` is relative to where it is started. From the repository root it does not find `agentfit`, treats it as a third-party package and reports I001 (import order) on every file. CI and `scripts/check.py` run it from `backend/` (`ruff check src tests ../scripts`). Run `lint-imports` there as well.

## A `# noqa` for a rule that is not enabled is itself an error
With `RUF` selected in `backend/pyproject.toml`, `# noqa: A002` (or ANN201, E402 after a `sys.path` change) is reported as RUF100 because those rules are not on. Add a `noqa` only for a rule that is selected. The Arena's exceptions are already in `[tool.ruff.lint.per-file-ignores]` (RUF001 in the question bank and the export, which must say × and − as a reader sees them).

## The map lists what git has: stage a new file before expecting it in `docs/map/`
`scripts/build_context.py` reads the files that git tracks or has staged, so a scratch file nobody added cannot make the map differ from CI's. A brand new module is missing from the map until `git add <file>` (or `git add -N <file>`), then `python scripts/build_context.py`. The pre-commit hook does this for a commit.

## Routes must be written where the map and its test can read them
`docs/map/api.md` and `tests/test_context_map.py` read `@app.get("/api/...")` decorators in `backend/src/agentfit/api/`. A route added through a router, a computed path or `add_api_route` is served but absent from the map, and the test names the difference. Write the decorator directly, or teach `load_routes` in `scripts/build_context.py` the new form.

## The REST call that loads a model has no GPU offload and no idle timeout
*LM_STUDIO.md.* To load with a GPU ratio the app runs `lms load --gpu <ratio>`; workflow loads that need an idle timeout go through `lms load <key> -c <context> --ttl <seconds> -y` when the CLI is installed, and through REST without a timeout when it is not. Check for the CLI before assuming either.

## The MCP SDK renamed its server class
`mcp` 2.x moved `FastMCP` to `mcp.server.mcpserver.MCPServer`, and a tool object has `input_schema` instead of `inputSchema`. `brain/mcp_server.py` imports whichever exists and the tests only use `list_tools()` and `call_tool()`. When the package is upgraded, run `tests/test_project_memory.py` first.

## A fix made by a shell command is invisible to the session ledger
The ledger reads the file edits a session made through Edit and Write. A fix applied with `sed` or `python -c` leaves a failed check with no edit between the failure and the pass, so the error is recorded without a solution. Prefer the editor tools for changes to the project.

## Development screenshots are never committed
`.shots/` holds screenshots taken while testing, and they can show generated pictures. The GitHub repository is public. It is in `.gitignore`: keep it there, and do not add generated pictures to the repository.

## Two import cycles exist; do not add a third
`teams` and `workflows` import each other (a team is built from workflow definitions, a workflow store seeds the team templates), and so do `lmstudio` and `runtime`. `backend/.importlinter` leaves them alone on purpose. A new cycle between packages means the new code belongs somewhere else, usually lower: pass the thing in as an argument or put the shared part in a module both can import.

## A shell can have no `cargo` on PATH, and the release found out last
*Found 2026-10-27, publishing 0.2.0.* `scripts/release.ps1` passed lint, the tests and the frozen core, then stopped in the Tauri step with "failed to run 'cargo metadata' ... program not found". The Terminal panel's shell had no Rust folder on PATH: on this PC `~\.cargo\bin` does not exist, the compiler lives in `~\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin`, and the user PATH holds a broken entry made of PowerShell text. The release had already written the version into six files, so the tree stayed modified and the next run refused to start. `build.ps1` now looks for cargo at its start (PATH, `CARGO_HOME`, `~\.cargo\bin`, the rustup toolchains with stable first), for that run only, and stops there with a clear message when it is nowhere. If a release stops after the bump, restore the version files with `git checkout --` before running it again (`docs/RELEASING.md`).
