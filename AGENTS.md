# Will It Run: instructions for any coding agent

Read this first. It is short on purpose: anything bigger is looked up with a tool, not remembered or guessed.

## What this is

A Windows 11 desktop app (Tauri shell, React UI, Python core) that tells a person which local AI models their PC can run, installs them through LM Studio, tests them (the Arena), builds LangGraph agent teams, generates images through ComfyUI, keeps a permanent memory per project (the Brain), and clones websites. Product context: `PRODUCT.md`. The owner writes Italian: answer them in Italian. The interface is English with an Italian catalog. Layout: `backend/src/agentfit` (core), `backend/tests`, `desktop/src` (UI), `desktop/src-tauri` (Rust, tiny), `scripts/`, `docs/`.

## Before you change anything

1. **Map.** Read `docs/map/INDEX.md`, then the card of the package you will touch (`docs/map/<package>.md`). They are generated from the code: what each module does, who imports it, which tests cover it, every route, table and UI file. With a small context window (16k or less) read only this file, the index and one card.
2. **Symbols.** Find them with the code graph, never by guessing a name: `codegraph explore "<exact names>" --max-files 6` (source, callers, blast radius), `codegraph callers <symbol>`, `codegraph callees <symbol>`, `codegraph impact <symbol>`. If the `codegraph_explore` tool is mounted, call it once with the relevant names. The graph updates itself. These commands only read: use them even in a read-only task. The graph misses calls made through an attribute (`memory.service.add(...)`): before you say "these are all the callers", confirm with a text search (`grep -rn "\.add(" backend/src`).
3. **Memory.** `backend/.venv/Scripts/willitrun.exe memory recall "<topic or error>"` gives decisions, lessons, earlier sessions and the errors they fixed; `... recall --file <path>` gives everything known about one file; `... recent --open` lists errors nobody fixed. They do not change the repository (a recall only updates a usage counter in the app's database), so use them in a read-only task too. `docs/DECISIONS.md` and `docs/LESSONS.md` hold the same in plain text.
4. **Read the file you will edit.** A file always wins over the map and the memory; if they disagree, the file is right and the other is stale.
5. **Never say a function, route, table, setting or file exists unless a tool showed it to you in this session.** If you cannot find it, say so and ask. Do not invent names, flags or endpoints.
6. **After the edit run `python scripts/check.py`.** It runs only the checks your change needs (ruff, import contracts, the tests that import what you touched, tsc, eslint, vitest, the Italian catalog). `--all` runs everything CI runs, `--plan` only says what it would run. A change is not done until it passes.
7. **Write down what is worth keeping.** A design choice becomes a section of `docs/DECISIONS.md`; a mistake that was easy to make or a fix that was not obvious becomes a section of `docs/LESSONS.md`. Skip it for the trivial.

## Where things are

| Area | Code (`backend/src/agentfit/`) | Read |
|---|---|---|
| Hardware, fit, scoring | `hardware/`, `fit/`, `catalog/scoring.py` | `docs/HARDWARE_DETECTION.md`, `docs/MODEL_SCORING.md` |
| Catalog, downloads, LM Studio | `catalog/`, `downloads.py`, `lmstudio/` | `docs/LM_STUDIO.md` |
| Workflows, teams, model gateway | `workflows/`, `teams/` | `docs/WORKFLOWS.md`, `docs/LANGGRAPH.md` |
| The Arena (reliability tests) | `arena/` | `docs/ARENA.md` |
| Images (ComfyUI) | `imagegen/` | `docs/IMAGE_STUDIO.md` |
| Project Brain (memory) | `brain/` | `docs/MEMORY.md` |
| Clone a site, cloud models | `clone/`, `cloud/` | `docs/CLONE.md`, `docs/CLOUD.md` |
| HTTP API (133 routes) | `api/` | `docs/map/api.md` |
| UI | `desktop/src/pages`, `components`, `lib` | `docs/DESIGN.md`, `docs/LOCALIZATION.md` |

`AppServices` (`app.py`) wires the engines; the HTTP API and the CLI both go through it. "Does it already exist?" is answered by the map and the graph: Arena, Brain, Images, Clone, Teams, Workflows, Cloud, the model gateway, downloads and hardware are all built. Reuse them.

## Commands (Windows; the venv is `backend\.venv`)

| What | Command |
|---|---|
| Checks for what you changed | `python scripts/check.py` (`--all`, `--plan`) |
| Backend tests | `cd backend; .venv\Scripts\python.exe -m pytest [tests/test_x.py]` |
| Lint | `cd backend; .venv\Scripts\ruff.exe check src tests ../scripts` (from `backend/`, see LESSONS) |
| Architecture contracts | `cd backend; .venv\Scripts\lint-imports.exe` |
| UI checks | `cd desktop; npm run typecheck; npm run lint; npm test` |
| Italian catalog | `python -X utf8 scripts/build_locale.py` |
| Regenerate the map | `python -X utf8 scripts/build_context.py` (`--check` in CI) |
| Run core / UI | `backend\.venv\Scripts\willitrun.exe serve --port 8765`; `cd desktop; npm run dev` |
| Keep your data out | set `AGENTFIT_HOME` to a scratch folder |
| Installer | `.\scripts\build.ps1` |

## Invariants (CI or the owner will notice)

- **Text.** Interface strings are `t("English")` with a line `English ||| Italiano` in `desktop/src/locales/it.txt`; core strings are `tr("English")` with the pair in `backend/src/agentfit/locales/it.py`. Placeholders must survive. Never edit `desktop/src/locales/it.ts`, `docs/map/` or `.codegraph/`: they are generated.
- **Arena.** Changing a question, a verifier or how an answer is read means bumping `SUITE_VERSION` and pinning the new hash in `tests/test_arena_bank.py`. No model judges; what code cannot judge is "not evaluated".
- **Tests** never touch LM Studio, the network, a GPU or the owner's data: `conftest.py` points `AGENTFIT_HOME` at a temp folder; use the fakes (`tests/fakes.py`, `httpx.MockTransport`).
- **Database.** Schema changes are a new numbered file in `backend/src/agentfit/migrations/`. A committed migration is never edited.
- **Routes** are written as `@app.get("/api/...")` in `api/`; the map and a test read them in that form.
- **Imports** obey `backend/.importlinter` (entry stack cli > api > doctor > app; engines never import the entry points; Brain depends on db and log only; Images independent of the rest). The two existing cycles (`teams`/`workflows`, `lmstudio`/`runtime`) stay as they are; do not add a third.
- **No fabrication.** An unknown value is `None` / "Unknown", never a default that looks like data. Anything chosen automatically returns its reasons.
- **Safety.** Tools that act on the owner's machine go through approval (`docs/SECURITY.md`); do not shorten the list of high-risk actions. Keys are stored with DPAPI (`cloud/`); logs redact secrets.
- **Images.** The catalog is photographic (Pony V6 XL is the one exception); models come from free sources, Hugging Face first; Civitai entries stay `needs_token` and are not pushed on the owner. Nothing is claimed installed unless the file is on disk. Verify a URL, size and checksum before adding a source. Pictures made to test the pipeline use a plain clothed-portrait prompt.
- **Names.** The package, environment variables and database file stay `agentfit`; the product is "Will It Run".
- **Windows.** Run scripts with `python -X utf8`, open text files with an explicit encoding (`utf-8-sig` for source), use `pathlib`. CRLF/LF warnings from git are normal.

## Working agreement with the owner

- **Commit and push only when asked.** "Push to main" means push `master`. Never commit `.shots/` or generated pictures: the repository is public.
- **Free, local, automatic.** No paid or hosted service, no account, no API key the owner has to create; turn telemetry off when a tool offers it. Do the setup yourself and say what then runs by itself; do not end with "now you have to...".
- **Say what you did not verify.** If a test, build or check was not run, say so; do not report a guess as a result.
- Hooks (`.githooks/`, `.claude/settings.json`) save work and never block it; CI (`.github/workflows/ci.yml`) is the gate. How the context system works and what it cannot do: `docs/AGENT_CONTEXT.md`.
