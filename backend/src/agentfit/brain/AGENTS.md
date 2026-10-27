# Project Brain (`backend/src/agentfit/brain`)

The permanent memory of a project: typed entries (architecture, decision, change, error, solution, note, session) in the app's SQLite file, searched by meaning, words and graph. Read `docs/MEMORY.md` (design, tools, API, what it does not do) and `docs/map/brain.md`. It serves two kinds of caller: a **run** of the app (through `BrainSession`) and **programs that are not a run** (a coding agent over MCP, a hook, a person at a terminal).

## Pieces

`service.py` (`BrainService`: add, search, project map; `recall_text` / `remember_text` are what both kinds of caller share), `store.py`, `search.py` (embeddings + FTS5 + graph, fused), `embed.py` (the runtime's embedding model, or `NoEmbedder`), `codemap.py` (import graph of Python and TypeScript), `capture.py` (what a run records). For callers without a run:

- `external.py`: `ProjectMemory.open(folder)` is the memory of a folder; `recall`, `remember`, `recent`. It never loads an embedding model (words and the graph only).
- `ledger.py`: reads a Claude Code session file and writes requests, files, checks, commits, errors and their fixes (`willitrun memory ledger --hook`, from the Stop hook).
- `gitlog.py`: a commit becomes a `change` entry (`willitrun memory commit`, from the post-commit hook).
- `docs_import.py`: every `##` section of `docs/DECISIONS.md` and `docs/LESSONS.md` becomes an entry (`willitrun memory import-docs`).
- `mcp_server.py`: three tools over MCP (`recall`, `remember`, `recent`); every tool a client lists is sent to the model on each request, so there are only three.

## Rules

- **Depends on the database and the log only** (`backend/.importlinter`). Do not import workflows, runtime, catalog or lmstudio; take what you need as an argument.
- **Idempotent writes.** An entry written by a program has a `slot` (`claude:<session>`, `commit:<sha>`, `doc:<file>:<slug>`): writing it again rewrites it instead of adding. Keep that when adding a new writer.
- **Nothing stored twice; a newer statement replaces an older one, which is kept and marked.** Events (sessions, changes) are never merged.
- **Secrets never reach the memory** (`redact_text` runs on everything stored).
- **The memory can be wrong; the files win.** The text given to agents says so; keep it.
- The ledger sees edits made with Edit and Write, not with a shell command. Entries it writes are tagged `auto`.

## Tests

`test_brain.py` (storage, search, graph, map, what a run records and is given), `test_project_memory.py` (ledger, commits, documents, MCP), `test_agent_hooks.py` (the hook scripts that feed it).
