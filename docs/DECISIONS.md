# Decisions

The choices that shaped Will It Run, with the reason for each. One `##` section is one decision. Read this before reopening one of them: the reason is usually the thing that is easy to forget.

Every section is also copied into the project memory (`willitrun memory import-docs`, run by the git hooks), so `willitrun memory recall "<topic>"` finds it by words. The longer arguments stay in the document an entry points to.

To add a decision: a new `##` section at the end, with the date, what was decided, why, and where it shows in the code. To change one: edit its section and say what replaced it and when. Do not delete the old reason.

## Tauri and React for the interface, not a native Windows toolkit
*From the first version. Detail: ARCHITECTURE.md.*
The interface is dense and custom (model rows, a node editor, a live activity feed), which web tooling does well, and Tauri packages it with the system WebView2 in a small installer. The Rust side stays tiny on purpose, about 150 lines that manage a child process (`desktop/src-tauri`). Do not move logic into Rust.

## A Python core, not a Rust one
*From the first version. Detail: ARCHITECTURE.md.*
LangGraph is a Python library and the workflow engine is the centre of the product. Hardware detection and scoring live in the same process, so there is one language, one test suite and no IPC schema between "analysis" and "orchestration". The cost is a bundled interpreter (about 40 MB frozen) and a slower start, accepted. Code: `backend/src/agentfit`.

## HTTP between the interface and the core, not Tauri IPC
*From the first version. Detail: ARCHITECTURE.md.*
The same API serves the desktop shell, a browser during UI development and the test suite (`TestClient`). Live updates use one Server-Sent Events stream. The core listens on 127.0.0.1 with a bearer token chosen at each launch (SECURITY.md, "The local API"). Every new endpoint is written as `@app.get("/api/...")` in `backend/src/agentfit/api/`, because the generated map and a test read routes in that form.

## SQLite, one file, numbered migrations
*From the first version. Detail: ARCHITECTURE.md.*
One file in `%LOCALAPPDATA%\Will It Run`, no server. Schema changes are numbered SQL files in `backend/src/agentfit/migrations/` (`0001_initial.sql` ... `0006_image_components.sql`), recorded in `schema_migrations`. A migration that was committed is never edited: add the next number. LangGraph checkpoints use a second SQLite file.

## llmfit is adapted and reused as data, never wrapped
*Commit studied: 1a4e9a0. Detail: ARCHITECTURE.md, table "llmfit: reused, adapted, wrapped or reimplemented".*
The quantization tables, the memory and KV-cache formulas, the bandwidth model of speed and the GPU tables were adapted to Python (`fit/`, `hardware/gpu_tables.py`). The model database is reused as an offline seed (`scripts/build_seed_catalog.py`). Hardware detection was rewritten for Windows only. The `llmfit-core` crate is not linked: it would add a second engine and an IPC schema for a few hundred lines of arithmetic.

## LM Studio is the model runtime and is not bundled
*From the first version. Detail: LM_STUDIO.md.*
Will It Run talks to LM Studio's REST API and its `lms` CLI. The model list is merged from both because neither knows everything: the CLI knows each model's file path (which maps it back to its Hugging Face repository), the REST API knows capabilities and what is loaded. Either one alone still works. Use only documented LM Studio and LangGraph behaviour, and list new usage in LM_STUDIO.md or LANGGRAPH.md.

## The Project Brain is built into the app
*2026-10-13. Detail: MEMORY.md, "Why it is built in, and what it borrows".*
The permanent memory of a project is tables in the SQLite file the app already has, an FTS5 index, and vectors from the runtime the app already uses. No memory library was added: mem0 calls a language model twice per write and needs a graph database for its graph, claude-mem is a plugin for other tools, LightRAG and GraphRAG index documents rather than what agents do, Graphiti needs a graph database. Adopted ideas: typed entries captured from tool calls (claude-mem), one store searched by vectors and graph together with a new fact replacing the old (mem0), what was replaced is kept and marked (Graphiti). Code: `backend/src/agentfit/brain`.

## Image generation is not a ModelRuntime, and ComfyUI is the engine
*2026-10. Detail: IMAGE_STUDIO.md, ARCHITECTURE.md ("Runtime abstraction").*
A diffusion model is not loaded, given a conversation and unloaded: it is a graph of nodes that runs once and writes a file, and its "model" is several files whose names must match. So `agentfit/imagegen` is a job runner of its own against ComfyUI's HTTP API. ComfyUI is GPL-3.0 and is not bundled or installed by the app: embedding a diffusion runtime would mean shipping gigabytes of wheels, CUDA kernels and model licences in an app whose job is to measure what a PC can run. Rule for the whole feature: nothing is claimed as installed unless the file is on disk where ComfyUI looks for it, and nothing is queued unless every node class of the graph exists.

## The Images catalog is photographic, with one exception
*2026-10-27. Detail: IMAGE_STUDIO.md.*
The person who owns the project wants photoreal pictures (including uncensored ones of adults) and said cartoon models do not interest them. NoobAI, Illustrious and the Kroma builds were removed. The one exception, asked for by name, is Pony Diffusion V6 XL, which draws anime by default: its entry starts from a photographic negative prompt (`source_anime, source_cartoon, 3d, drawing`), and on this PC it only made a photograph when prompted with photographic tags, not with a descriptive sentence. Do not add anime or illustration models back without being asked.

## Every model comes from a free source, Hugging Face first
*2026-10-27. Detail: IMAGE_STUDIO.md.*
The owner wants everything free and does not want to use Civitai. Civitai answers 401 to a download without a personal API token, so its entries stay in the catalog flagged `needs_token`, are refused before queueing, and are never pushed on the person. New models are added from Hugging Face files that need no token. A source is verified (the URL answers, the size and checksum match) before it is written into `data/components.json`.

## The Arena has no judge model: code checks the answers
*Detail: ARENA.md.*
Each question has a verifier written as code, a reference answer and wrong answers that must fail. What no code can judge is never counted as right or wrong: it is shown as **not evaluated**, and a person's mark on it never enters a score. Every model gets the same messages byte for byte and the same sampling. Every score has a 95% interval from a bootstrap with a fixed seed.

## The Arena suite is versioned and pinned by hash
*Detail: ARENA.md, "Reproducible" and "In the code" (the last lines say how to add a question).*
`SUITE_VERSION` (`backend/src/agentfit/arena/__init__.py`) changes whenever a question, a verifier or the way an answer is read changes, because results of two versions are not comparable. The canonical seed (42) is pinned by hash in `tests/test_arena_bank.py`: change a question and the test stops you until you also bump the version and pin the new hash it prints. A run stores its own questions, so it can be read after the bank changes.

## Interface text: English is the key, Italian lives in a catalog
*Detail: LOCALIZATION.md.*
Both sides use the English text as the key, gettext style: `t("Explore Models")` in the interface and `tr("...")` in the core. The Italian is in `desktop/src/locales/it.txt` (which generates `it.ts`, never edited by hand) and `backend/src/agentfit/locales/it.py`. A string without an Italian translation, or a translation that drops a placeholder, fails the suite. Text sent to models and logs are never translated. The Arena stores the reason of a verdict as an English template and translates it when it is read.

## No fabrication, and every automatic choice explains itself
*Detail: DEVELOPMENT.md, "Conventions".*
A value that is not known is `None` in Python and "Unknown" or "Not measured" in the interface; no default may look like data. Anything Will It Run decides for the person returns its reasons (`reasons`, `why`, `quant_reasoning`, `explanations`).

## The internal name stays agentfit
*Detail: DEVELOPMENT.md, "Names that did not change".*
The project started as AgentFit. The Python package `agentfit`, the environment variables (`AGENTFIT_HOME`, `AGENTFIT_TOKEN`, `AGENTFIT_PARENT_PID`), the database file `agentfit.sqlite3` and the Cargo package keep that name on purpose; nothing a user sees depends on them. The `agentfit` command is an alias of `willitrun`.

## Context for coding agents: a map made from the code, a code graph, a memory made from facts
*2026-10-27. Detail: AGENT_CONTEXT.md.*
The project is large (about 40,000 lines of Python, 20,000 of TypeScript, 133 routes, 23 tables) and models change it in sessions that start empty. Chosen: `AGENTS.md` (rules, short) plus `docs/map/` generated from the code by `scripts/build_context.py` (never written by a model, so never wrong in its own way); CodeGraph (MIT, local, one MCP tool, updates itself) for symbols, callers and blast radius; and the Project Brain as the memory, fed without a model by a transcript ledger, by git hooks and by `docs/DECISIONS.md` and `docs/LESSONS.md`, and served over MCP and the command line. Everything is free, local and runs by itself. Turned down, with the reasons: GitNexus (non-commercial licence, 19 tools, too heavy for a local model with an 8k window), Serena (about 29 tools), codebase-memory-mcp (17 tools, a binary with documented Defender false positives), claude-mem (hosted observer), Graphiti (a graph database), Graphify and CodeGraph's hosted platforms (accounts, paid tiers). Evidence is mixed: vendors report large savings, an independent benchmark found none below about 25,000 nodes, and this repo has about 3,000 symbols. CodeGraph therefore stays only while it helps (`codegraph uninstall` removes it); the map and the memory do not depend on it. First measurement, the same day: on four read-only tasks fresh Haiku agents with and without all of this were equally right, equally fast and equally cheap, and the graph's caller list was incomplete (AGENT_CONTEXT.md, "Measured"). So the benefit is unproven here; it was not tested on edits, on a small local model or across sessions.

## Architecture contracts record today's layering
*2026-10-27. Detail: `backend/.importlinter`.*
`lint-imports` (import-linter) checks eight contracts computed from the real import graph: the entry stack is cli over api over doctor over app; engines and libraries never import the entry points; paths, config, database, events, translations and branding import no feature; hardware and fit know nothing of models or workflows; the Brain depends on the database and the log only; the image studio is independent of the Arena, workflows, teams, cloning and the LLM runtime; the Arena, benchmark and chat do not import one another; the Arena imports no other feature directly. Two cycles exist and are accepted: `teams` and `workflows`, `lmstudio` and `runtime`. Do not add a third. To loosen a contract, change the file and add the reason here.

## Hooks help, CI decides, generated files are never edited
*2026-10-27. Detail: AGENT_CONTEXT.md.*
The git hooks (`.githooks/`, turned on by `core.hooksPath`) and the Claude Code hooks (`.claude/settings.json`) save work and never block it: a failure inside them is skipped. The gate is CI (`.github/workflows/ci.yml`: ruff, import contracts, pytest, the Italian catalog, the map with `--check`, then tsc, eslint and vitest). `docs/map/` and `desktop/src/locales/it.ts` are generated; edit their sources and run the generator (a Claude Code hook refuses to edit them by hand).

## Commit and push only when asked, and "main" means master
*2026-10-27. Standing instruction from the owner.*
Nothing is committed or pushed unless the person asks. The repository's default branch is `master`; "push to main" means push `master`. `.shots/` (development screenshots, which can show generated pictures) is never committed: the GitHub repository is public.

## A clone is the page as the browser rendered it, not a model's description of it
*2026-10-27. Detail: CLONE.md.*
The first version had a model follow the skill of ai-site-cloner. On a local 27B model (about 27K tokens of context, 12K of them the skill) a run of 2 h 39 min on brioni.com made 776 tool calls and built no component; guards around the loop did not change that, because the skill assumes a model that keeps a whole site in its head. The default is now an exact copy: Chrome renders the page, `capture.mjs` freezes what it rendered (markup, style rules, pictures, fonts, shadow DOM flattened, a phone variant of each section), `replica.py` writes a Next.js project and `measure.mjs` compares it with the original's screenshots. Accuracy on brioni.com, measured on the built project: 99.9%, in 65 seconds, with no model for the copy. The agent mode stays for the models it works with. Code: `backend/src/agentfit/clone/pipeline.py`.

## A local model does small jobs that a number can check
*2026-10-27. Detail: CLONE.md, "From a picture".*
A model of 27B on a 16 GB card has about 27K tokens, and a long tool loop spends them on re-reading. So a model is used as a function: one bounded question (name these sections, write this region from its crop, rewrite it from its score), an answer that code validates, and a measure that decides whether the answer stays. Places and sizes come from data, not from the model; a rewrite that does not raise the score is undone; motion that moves the page at rest is removed, and the motion is a fixed GSAP component that only takes numbers (the model chose the GSAP code once and the measure threw it out). The photographs of a picture are its own crops, and the report says which share of the page that is, so the accuracy cannot be raised by copying. A step that is code (capture, build, measure) never asks a model. Code: `clone/picture.py`.

## Skills are fetched at a pinned commit and a step is given only the sentences it needs
*2026-10-27, changed 2026-10-28. Detail: CLONE.md, "Skills".*
GSAP's, Anthropic's frontend-design, Vercel's, Three.js and Playwright's skills are installed the way the clone kit is: `git fetch` of one commit, checked against its hash, never bundled, kept with their licences in `tools/skills/<owner>__<repo>@<commit>`, once per PC. A run copies the ones it wants into its project (`.skills/`). A step of a run is given a few sentences of a skill, picked by a phrase (`SkillStore.excerpt`, `picture.GUIDANCE`), only where they bear on the choice the step asks of the model: today the motion, which gets the design skill's paragraph on motion and the GSAP skill's note on the strength of the eases. Composition and refinement get none, because the design skill tells a model to take aesthetic risks and what they write is a copy. The first version said the model reads the skills with `read_file` and sees a line of each in its prompt; no step has that tool and nothing called the function that wrote the line, so no model was shown a skill. The Playwright CLI is not installed as a global npm package. Code: `clone/skills.py`, `clone/picture.py`.
