# Development

## Layout

```
backend/                Python core
  src/agentfit/         see docs/ARCHITECTURE.md
  tests/                pytest suite and hardware fixtures
  packaging/            PyInstaller entry point
design/                 interface and logo drafts (standalone HTML), see docs/DESIGN.md
desktop/                Tauri + React + TypeScript UI
  src/lib/              API client, types, store, formatting
  src/components/       shell and UI primitives
  src/pages/            one file per screen
  src-tauri/            Rust shell, installer config, icons
scripts/
  build.ps1             full build -> dist\WillItRun-Setup-x64.exe
  build_seed_catalog.py regenerate the offline model catalog from llmfit data
  build_context.py      write docs/map/ (the map of the code) from the code; --check for CI
  check.py              run the checks your change needs; --all is what CI runs
  hooks/                the scripts behind the git hooks and the Claude Code hooks
  make_hardware_fixtures.py
docs/                   DESIGN.md maps the design files, SCREENS.md the screenshots,
                        map/ is generated, DECISIONS.md and LESSONS.md are the memory in plain text
.githooks/              git hooks (turned on with: git config core.hooksPath .githooks)
.github/workflows/      CI
.claude/                Claude Code settings (hooks) and rules by path
AGENTS.md               the instructions every coding agent reads first (CLAUDE.md imports it)
```

Working with a model on this code is described in [AGENT_CONTEXT.md](AGENT_CONTEXT.md).

## Prerequisites

- Windows 11 x64
- Python 3.11+ (developed on 3.13) and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm
- Rust (stable) for the desktop shell
- LM Studio for anything that runs a model

## Setup

```powershell
uv venv backend\.venv --python 3.13
uv pip install --python backend\.venv\Scripts\python.exe -e "backend[dev]"
cd desktop; npm install
```

## Running

Two ways to work on the UI:

```powershell
# 1. In a browser, against a core you start yourself
backend\.venv\Scripts\willitrun.exe serve --port 8765
cd desktop; npm run dev          # http://127.0.0.1:1420, expects the core on 8765

# 2. In the real shell (starts the core from the venv itself)
cd desktop; npx tauri dev
```

`VITE_AGENTFIT_API` and `VITE_AGENTFIT_TOKEN` override where the browser build looks
for the core.

Set `AGENTFIT_HOME` to a scratch folder to keep development data away from your real
Will It Run data.

## Checks

```powershell
python scripts\check.py                     # what your change needs: lint, import contracts, the tests that import
                                            # what you touched, tsc, eslint, vitest, the Italian catalog
python scripts\check.py --all               # everything CI runs

cd backend
.venv\Scripts\python.exe -m pytest          # about 900 tests
.venv\Scripts\ruff.exe check src tests ..\scripts   # run from backend\: its settings name the `src` folders
.venv\Scripts\lint-imports.exe              # the architecture contracts in backend\.importlinter

cd ..\desktop
npm run typecheck                           # tsc, strict
npm run lint                                # eslint
npm test                                    # vitest
cd src-tauri; cargo test; cargo clippy
```

CI (`.github/workflows/ci.yml`, Windows) runs the same: ruff, the import contracts, pytest, the Italian catalog
(`it.ts` must be what `it.txt` says), `scripts/build_context.py --check` (the map must be what the code says), then tsc,
eslint and vitest in a second job.

### Hooks

`git config core.hooksPath .githooks` turns on the versioned git hooks (a Claude Code session does it by itself):
**pre-commit** regenerates `docs/map/` and adds it to the commit; **post-commit**, **post-merge** and **post-checkout**
bring the code graph (`codegraph sync`) and the project memory up to date in the background. They help and never
block a commit. The Claude Code hooks are in `.claude/settings.json`; both sets are described in [AGENT_CONTEXT.md](AGENT_CONTEXT.md).

### Migrations

`backend/src/agentfit/migrations/NNNN_name.sql`, forward-only. A committed migration is never edited: add the next
number (a Claude Code hook asks before it lets an edit through). Put a `--` comment above each `CREATE TABLE`; the map
copies it into `docs/map/db.md`.

The Python tests never touch LM Studio, the network or your data: `conftest.py` points
`AGENTFIT_HOME` at a temp directory, hardware comes from fixtures, LM Studio from
`httpx.MockTransport` and a fake CLI, Hugging Face from a fake hub, and LangGraph runs
against `tests/fakes.py::FakeRuntime`, a scripted model.

### What is covered

| File | Covers |
|---|---|
| `test_hardware.py` | NVIDIA / AMD / Intel parsing, RAM, storage, VRAM reserve, multi-GPU, all fixtures |
| `test_fit.py` | Quantization parsing and sizes, memory, KV cache, fit categories, speed, quant choice |
| `test_catalog.py` | Alignment detection, uncensored filters, no-penalty scoring, ranking, search parsing, metadata honesty, offline |
| `test_lmstudio.py` | Discovery, load, unload, offline, API errors, model not found, download endpoints, CLI parsing |
| `test_downloads.py` | Disk-safety planning, refusal when unsafe, resume, hash mismatch, cancel, both backends |
| `test_workflows.py` | Conditions, DSL, validator, tools and risk, model manager, LangGraph execution paths |
| `test_gateway.py` | Several models in one run on a simulated GPU: context fitted to free VRAM, swap and co-load, saturation retry, learning the real cost, wide-context variant, idle timeouts |
| `test_teams.py` | Role selection, model sharing, resource optimizer, preferences, low memory, disk full |
| `test_api.py` | HTTP wiring, token guard, error mapping, the plan → create → run acceptance flow |
| `test_model_quirks.py` | Turns LM Studio cannot read (gpt-oss), recovered instead of failing; JavaScript tests run with Node |
| `test_followups.py` | After a session: a question answered by one model where the team worked, not by the team again; the next request in the same workspace |
| `test_brain.py` | Project memory: storing once and replacing, search by meaning, words and graph, the project map, what a run records, what an agent is given, `recall` and `remember`, isolation of verification workflows, the embedding model |
| `test_arena_bank.py` | The question bank: the packs hold 48 / 115 / 192 questions, every question passes its own reference answer and fails its wrong ones (for the canonical seed and five others), the bank is pinned by hash |
| `test_arena_basics.py`, `test_arena_scoring.py` | The generator of numbers, JSON Schema, reading numbers and answers; scores, intervals, ranking |
| `test_arena_verifiers_text.py`, `_data.py`, `test_arena_exec.py`, `_sql.py`, `_iac.py`, `_assets.py` | The verifiers: short answers, honesty and injections, JSON, tool calls, documents, code in the sandbox, SQL, Terraform and Ansible (with and without the tools), the pictures and PDFs the bank draws |
| `test_arena_runner.py` | A run end to end on a fake runtime: the same prompt for every model, one model in memory, stop and resume, run errors kept apart from wrong answers, models that do not load, what needs the person's agreement |
| `test_arena_api.py` | The Arena over HTTP: overview, models, a run from start to report, the answers, the files in four formats, Italian |
| `test_core.py` | Migrations, settings, redaction, paths, events, benchmark storage |
| `test_project_memory.py` | The memory for coding agents: the session ledger, commits, `docs/DECISIONS.md` and `docs/LESSONS.md` as entries, the MCP server |
| `test_agent_hooks.py` | The hook scripts (`scripts/hooks`), `.githooks/` and `.claude/settings.json` |
| `test_context_map.py` | The generated map: its routes are the routes the app serves, it is deterministic and small enough to read |

### Checks against real hardware

These are not in the automated suite because they need a GPU and LM Studio. They were
run by hand during development on an RTX 4080 SUPER / 32 GB / LM Studio machine:

```powershell
willitrun hardware
willitrun doctor
willitrun model search "Qwen uncensored 27B"
willitrun benchmark spark-x2.5-4b
willitrun workflow run template:documentation --model spark-x2.5-4b --auto-approve --task "..."
```

### Trying the interface without a GPU

`scripts/dev/lmstudio_stub.py` is a stand-in for LM Studio's local server: it answers the
endpoints the core uses and "generates" fixed text a word at a time, so a run, a chat,
Stop and a waiting request can be watched in the interface while the real LM Studio is
busy or absent. `scripts/dev/core_with_stub.py` starts the core against it in a home of
its own, with the `lms` CLI switched off: otherwise a load would go to the real program
and unload whatever the user has in memory there.

```powershell
backend\.venv\Scripts\python.exe scripts\dev\lmstudio_stub.py 12399
backend\.venv\Scripts\python.exe scripts\dev\core_with_stub.py 8791 12399
$env:VITE_AGENTFIT_API = 'http://127.0.0.1:8791'; npm --prefix desktop run dev
```

For the Arena, `scripts/dev/seed_arena_demo.py` fills the stub core's home with a finished challenge between five made-up
models (about four minutes, nothing reaches LM Studio), so the ranking, the matrix and the duel can be looked at. Its
numbers are invented: never use them in a screenshot of the documentation. Stop the stub core while it works: a core that
starts with a run still marked as working stops it.

What the stand-in says depends on the request: one that mentions `notes.txt` gets a
`write_file` call and then an answer, one that mentions `long` (or `very long`) gets a few
hundred words, anything else a short answer. It also has an embedding model, `stub-embed`,
whose vectors are made from the words of the text: enough to watch the project memory
rank and draw its graph, and nothing like a real model's sense of meaning. Before sending anything, check that
`/api/models/installed` on the test core lists only `stub-` models.

## Building the installer

```powershell
.\scripts\build.ps1               # checks, freeze core, build app, build installer
.\scripts\build.ps1 -SkipTests
.\scripts\build.ps1 -CoreOnly     # just the frozen Python core
```

Steps: PyInstaller freezes the core into `backend\dist\willitrun-core` (one folder,
about 40 MB) → copied to `desktop\src-tauri\resources\willitrun-core` → `tauri build`
compiles the shell and produces the NSIS installer → copied to
`dist\WillItRun-Setup-x64.exe`.

## Conventions

- **Python**: type hints everywhere, Pydantic models at boundaries, `ruff` clean,
  structured logging through `logging_setup.log_event`.
- **TypeScript**: strict mode with `noUncheckedIndexedAccess`; API shapes in
  `lib/types.ts` mirror the Pydantic models.
- **No fabrication**: a value that is not known is `None` in Python and rendered as
  "Unknown" or "Not measured" in the UI. Do not add defaults that look like data.
- **Explain automatic choices**: anything Will It Run decides for the user returns its
  reasons (`reasons`, `why`, `quant_reasoning`, `explanations`).
- **LM Studio and LangGraph APIs**: use only what is documented, and list new usage in
  `docs/LM_STUDIO.md` or `docs/LANGGRAPH.md`.
- **Branding**: the product is called *Will It Run*. The name lives in
  `backend/src/agentfit/branding.py` and `desktop/src-tauri/tauri.conf.json`
  (`productName`, window title, `mainBinaryName`, identifier), the logo in
  `desktop/app-icon.svg` (run `npx tauri icon app-icon.svg` in `desktop` after changing
  it) and in the `Mark` component of `desktop/src/components/Shell.tsx`. Interface
  texts name the product directly, in `t("...")` strings and `locales/it.txt`.
- **Names that did not change**: the project started as *AgentFit*, and the internal
  names still say so on purpose: the Python package `agentfit`, the environment
  variables (`AGENTFIT_HOME`, `AGENTFIT_TOKEN`, `AGENTFIT_PARENT_PID`), the database file
  `agentfit.sqlite3` and the Cargo package. Nothing a user sees depends on them. The
  `agentfit` command still works as an alias of `willitrun`.

## Design system

`desktop/src/styles.css` holds the tokens (OKLCH): a chroma-0 near-black surface, a cool
second neutral for panels, one configurable accent, and semantic status colors (green
perfect, accent active, yellow warning, orange limited, red error, gray unavailable).
UI primitives are in `components/ui.tsx`. Product context for design work is in
`PRODUCT.md`.

## Updating the model catalog

```powershell
git clone --depth 1 https://github.com/AlexsJones/llmfit
backend\.venv\Scripts\python.exe scripts\build_seed_catalog.py llmfit\llmfit-core\data\hf_models.json
```
