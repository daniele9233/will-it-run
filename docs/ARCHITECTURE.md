# Architecture

Will It Run is three layers on one machine.

```
Tauri shell (Rust)            desktop/src-tauri
  starts and stops the core, native window, notifications, opening links
        |
React + TypeScript UI         desktop/src
  talks to the core over HTTP on 127.0.0.1 with a per-launch bearer token
        |
Will It Run core (Python)        backend/src/agentfit
  +-- hardware/     detection and the usable-memory budget
  +-- fit/          quantization tables, memory, speed, fit categories
  +-- catalog/      discovery, search, classification, scoring
  +-- lmstudio/     CLI wrapper, REST client, runtime
  +-- downloads.py  disk-safety gate and download manager
  +-- benchmark/    local measurement
  +-- arena/        reliability tests: the question bank, the verifiers, the runner, scores, reports (see ARENA.md)
  +-- teams/        roles, specializations, team builder, resource optimizer
  +-- workflows/    YAML DSL, validator, LangGraph compiler, tools, run manager
  +-- brain/        the permanent memory of each project: store, search, graph, capture
  +-- cloud/        Claude, ChatGPT, DeepSeek, Gemini: keys (DPAPI), clients, HybridRuntime
  +-- clone/        Clone a site: the kit (pinned git), browser engine, agent tools, the question to the person, studies
  +-- api/          FastAPI server
  +-- cli.py        the `agentfit` command
        |
LM Studio                     the model runtime (not bundled)
Chrome or Edge + playwright-core   the browser the cloner drives (engine installed by the app into its data folder)
Cloud providers               optional, with the user's own API keys
```

`AppServices` (`app.py`) wires the engines together. Both the HTTP API and the CLI go
through it, so they cannot drift apart.

## Why this shape

**Tauri + React, not a native Windows toolkit.** The interface is dense and custom (model
rows, a node editor, a live activity feed). Web UI tooling does that well, and Tauri
packages it with the system WebView2 in a small installer. The Rust side stays tiny on
purpose: about 150 lines that manage a child process.

**A Python core, not a Rust one.** LangGraph is a Python library and the workflow engine
is the centre of the product. Putting hardware detection and scoring in the same process
keeps one language, one test suite and no IPC schema between "analysis" and
"orchestration". The costs are a bundled interpreter (about 40 MB frozen) and process
start-up time, both acceptable for a desktop app.

**HTTP between UI and core, not Tauri IPC.** The same API serves the desktop shell, a
browser during UI development and the test suite (`TestClient`). Live updates use one
Server-Sent Events stream.

**SQLite.** One file in `%LOCALAPPDATA%\Will It Run`, numbered SQL migrations, no server.
LangGraph checkpoints use a second SQLite file through `langgraph-checkpoint-sqlite`.

## llmfit: reused, adapted, wrapped or reimplemented

[llmfit](https://github.com/AlexsJones/llmfit) (MIT) solves hardware-aware model sizing
well. Each part was evaluated separately. Commit studied: `1a4e9a0`.

| llmfit component | Decision | Why |
|---|---|---|
| Quantization tables (bytes/param, speed multipliers, quality penalties) | **Adapted** to Python in `fit/quant.py` | Small, well-calibrated data. Extended to parse any `Q*`/`IQ*` label and K-quant suffixes, because real Hugging Face repos ship far more variants than the six in llmfit's ladder. |
| Memory estimate and KV-cache formula | **Adapted** in `fit/memory.py` | Same formulas. Will It Run prefers the real file size from the Hub over params x bytes/param, and adds vision projector, parallel slots and draft-model terms. |
| Bandwidth-roofline tok/s model, 0.55 efficiency, MoE overhead tiers, partial-offload split | **Adapted** in `fit/speed.py` | The physical model is sound and validated against measurements. The architecture-aware MoE "tier 1" path was not ported: it needs per-layer metadata most Hub entries lack. |
| GPU memory-bandwidth and compute-capability tables | **Adapted** in `hardware/gpu_tables.py` | Reference data. Apple and datacenter rows dropped (Windows desktop only). |
| Registry query for true VRAM (`HardwareInformation.qwMemorySize`) | **Adapted** in `hardware/windows.py` | The right fix for WMI's 4 GB `AdapterRAM` cap. Folded into a single PowerShell round-trip with the other CIM queries. |
| Model database (`hf_models.json`, 16,001 entries) | **Reused** as data, filtered | `scripts/build_seed_catalog.py` keeps the 1,958 GGUF-runnable text/vision models and only the fields the fit engine uses (90 KB compressed). It is the offline seed; exact files always come live from the Hub. |
| Per-family task-strength table | **Reused** as data | `catalog/data/family_scores.json`, with one documented change. |
| Fit levels (4) and composite score (4 factors) | **Reimplemented** | Will It Run needs eight fit categories and an eight-factor, configurable score with an explicit alignment preference. |
| Hardware detection (Rust, cross-platform) | **Reimplemented** for Windows | llmfit's detector spends most of its 6,000 lines on Linux, macOS, Docker and SoCs. A Windows-only detector is about 500 lines and adds drives, RAM modules, CUDA and CPU features. |
| The `llmfit-core` crate itself | **Not wrapped** | Linking Rust into the shell and marshalling results to Python would add a second engine and an IPC schema for logic that is a few hundred lines of arithmetic. |
| LM Studio provider, TUI, web UI, serving | **Not used** | Will It Run talks to LM Studio's official REST API and `lms` CLI directly and has its own UI. |

Attribution is in file headers and [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Data model

SQLite tables (`migrations/0001_initial.sql`): `hardware_profile`, `model_metadata`,
`installed_model`, `benchmark`, `arena_run`, `arena_question`, `arena_model`, `arena_result`, `download`, `workflow`, `team`, `workflow_run`,
`run_event`, `model_usage`, `tool_call`, `approval`, plus `kv` for JSON caches and
`schema_migrations`.

The Project Brain adds `brain_project`, `brain_memory`, `brain_entity` and `brain_edge`
(`migrations/0003_brain.sql`) and an FTS5 table for keyword search. It was built into
this file instead of adding a memory library: the reasons, and what was borrowed from
claude-mem, mem0 and Graphiti, are in [MEMORY.md](MEMORY.md).

The image studio adds `image_generation` (`migrations/0005_images.sql`): one row per picture,
with the prompt, the sampler settings, the seed and the LoRAs that produced it, and the path of
the app's own copy of the file. ComfyUI's output folder is not the record — it can be cleared.

Settings are a YAML file (`%APPDATA%\Will It Run\settings.yaml`) so they can be read and
edited by hand.

## Runtime abstraction

Runs call a `cloud.HybridRuntime`: model keys spelled `cloud:<provider>/<model>` go to the
provider's API (see [CLOUD.md](CLOUD.md)), everything else to LM Studio. The model gateway hands
a cloud slot out without loading anything.

`runtime.ModelRuntime` is the interface the team builder and workflow engine depend on:
`list_installed`, `list_loaded`, `load`, `unload`, `complete`, `measure`, and a set of
`modalities` the runtime can actually serve. `embed` is optional: a runtime that has it
gives the Project Brain its vectors. `LMStudioRuntime` is the only
implementation.

Image generation is **not** a `ModelRuntime`, deliberately. A diffusion model is not loaded,
given a conversation and unloaded: it is a graph of nodes that runs once and writes a file, and
its "model" is three separate files whose names must match. Forcing it into `complete()` would
have meant pretending a workflow is a chat. Instead `agentfit/imagegen` is a job runner of its
own, against ComfyUI's HTTP API, and the Images page is its front end. What the two share is the
rule: the engine is asked what it can do (`/object_info`, `/models/<folder>`) and nothing is
claimed that it did not confirm. See [IMAGE_STUDIO.md](IMAGE_STUDIO.md).

## Offline behaviour

| Works offline | Needs Internet |
|---|---|
| Hardware analysis | Live Hugging Face search |
| Installed models, load / unload | Exact file lists for models not yet cached |
| Search over the seed catalog and cached metadata | Downloads |
| Saved workflows and runs | |
| Benchmarks | |
| The Arena (local models; a cloud model needs the Internet) | |

The UI states which results are live and which are cached.
