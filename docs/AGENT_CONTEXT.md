# Working with an AI model on this code

How a model that is asked to change Will It Run learns, in a fresh session, what the software is, how it works and where to touch, without reading every line and without inventing. Everything here is free, local and runs by itself; nothing needs an account, a key or a server.

The project is large (about 40,000 lines of Python, 20,000 of TypeScript, 133 routes, 23 tables). A model's window is not. The answer is not one big document: it is a short set of rules, a map made from the code, a graph to look symbols up in, and a memory that records what happened, each one cheap to read and none written by a model.

## The parts

| Part | What it gives | Made by | Kept current by |
|---|---|---|---|
| `AGENTS.md` (and `CLAUDE.md`, `QWEN.md`, `GEMINI.md`, which import it) | The protocol, the commands, the invariants, the working agreement with the owner. About 1,900 tokens | people | review |
| Nested `AGENTS.md` in `arena/`, `imagegen/`, `workflows/`, `brain/`, `desktop/` and `.claude/rules/` | What is not obvious in that area, loaded when you work there | people | review |
| `docs/map/` | `INDEX.md` (about 1,400 tokens), then a card per package: modules and what they do, who imports them, tests, routes, tables, UI files | `scripts/build_context.py`, from the code | the pre-commit hook, the Claude Code session start, `scripts/check.py`; CI runs it with `--check` |
| Code graph ([CodeGraph](https://github.com/colbymchenry/codegraph), MIT) | A symbol's source, callers, callees, blast radius, and the tests a change affects; as the `codegraph_explore` MCP tool or the `codegraph` command | `codegraph`, index in `.codegraph/` (not in git) | its file watcher while a client is connected; the git hooks run `codegraph sync` |
| Project memory (the Brain) | Decisions, lessons, what earlier sessions did, every failed check and the edits that fixed it, every commit | `brain/` (see [MEMORY.md](MEMORY.md)) | the writers in the next table |
| `docs/DECISIONS.md`, `docs/LESSONS.md` | Why things are the way they are; what went wrong and how to avoid it | people and agents | whoever settles a choice or makes a mistake worth keeping |
| The checks | `scripts/check.py`, `backend/.importlinter`, CI | | |

## What runs by itself

| When | What happens |
|---|---|
| A Claude Code session starts or resumes | Prints the state of git, the errors the memory holds open and a pointer to the map; regenerates `docs/map/`; turns on the git hooks for this clone |
| You type a prompt (Claude Code, CodeGraph's user-level hook) | The symbols of the index that match the prompt are named, so the model asks the graph before it greps |
| A model edits a file | A generated file (`docs/map/`, `it.ts`) is refused; a committed migration asks first; a Python or JSON file that does not parse is reported at once |
| A Claude Code turn ends | The session is read from its transcript and written to the memory: requests, project files written, checks and whether they passed, commits, failures and their fixes (in the background) |
| `git commit` | The map is regenerated and added to the commit |
| after `git commit`, `merge`, branch switch | In the background: `codegraph sync`, the commit written to the memory, the documents re-imported if they changed, the memory's import graph refreshed |
| push or pull request | CI: ruff, import contracts, pytest, the Italian catalog, the map's `--check`, then tsc, eslint, vitest |

The git hooks work for every tool that commits, because git runs them. The Claude Code hooks work in Claude Code only.

## With another tool

- **Codex, Cursor, opencode, Qwen Code, Gemini CLI** read `AGENTS.md` (or `QWEN.md`, `GEMINI.md`, which import it). They get the protocol, the map and the commands. Their sessions are recorded in the memory by the git hooks (commits), not by a transcript ledger.
- **A local model in LM Studio** (0.3.17 or later): `%USERPROFILE%\.lmstudio\mcp.json` lists two servers, `codegraph` (`codegraph serve --mcp --path D:\SotwareKikko`) and `willitrun-memory` (`willitrun memory mcp --workspace D:\SotwareKikko`). Turn them on per chat. Together they add about 760 tokens of tool definitions to every request, and LM Studio asks you to confirm each call. For a window of 8k tokens, prefer the command line: `codegraph node <symbol>` (about 1,400 tokens), `codegraph callers`, `codegraph impact` and `codegraph context "<task>"` (about 700) are small; `codegraph explore` returns 5,000 to 6,000 tokens even with `--max-files 3`.
- **A model with a small window** reads `AGENTS.md`, `docs/map/INDEX.md` and one package card (together about 5,000 tokens), then asks for symbols one at a time.

## Setting up a clone

```powershell
git clone https://github.com/daniele9233/will-it-run; cd will-it-run
uv venv backend\.venv --python 3.13
uv pip install --python backend\.venv\Scripts\python.exe -e "backend[dev]"
cd desktop; npm install; cd ..
git config core.hooksPath .githooks          # a Claude Code session does this by itself
npm install -g @colbymchenry/codegraph
codegraph install --yes --init               # wires the MCP server into the agents it finds and builds the index
codegraph telemetry off
```

The memory fills itself as sessions and commits happen. To catch up on a long history once:
`backend\.venv\Scripts\willitrun.exe memory commit HEAD --last 200` and `... memory import-docs`.

## What this cannot do

- **The map says what exists, not that it is right.** It is made from the code, so it is never stale by hand, but it is only as good as what its reader checks. The protocol says: a file wins over the map and the memory, and nothing is claimed to exist until a tool has shown it.
- **The memory can be wrong.** A note stays wrong until it is edited or replaced; the entries a program writes (tagged `auto`) are facts about a session, not judgements.
- **The ledger sees what Edit and Write did, not what a shell command did.** A fix made with `sed` or `python -c` is recorded as a failure without a solution.
- **A file that git does not know is not in the map.** `git add` it (or `git add -N`) and regenerate.
- **Imports are read for Python and TypeScript only.** The Rust shell is listed by its functions, not graphed.
- **Hooks help, they do not enforce.** A hook that fails is skipped. What enforces is CI.
- **The code graph does not see every call.** A call through an attribute is missed (`callers` found 1 of 10 sites of one method). It says where to look, not that nothing else exists. Whether it pays for itself here is under "Measured" below: so far it does not.

## Measured on this repository

*2026-10-27. One run per cell, so differences of about 10% are noise.*

Four read-only tasks taken from real work were each given to two fresh Haiku agents with no memory of any conversation: one on the project as it is now (told to follow `AGENTS.md`), one on a copy of the repository as it was at the last commit before this work (README and `docs/` only: no map, no graph, no memory). The right answer of each task was written down before the agents started.

| Task | Before: calls / tokens / seconds | After: calls / tokens / seconds | What they answered |
|---|---|---|---|
| A. Add an Arena category "geography" | 38 / 140k / 159 | 32 / 129k / 123 | Both complete. Both found files this repo's own instructions did not list (the UI labels, `test_arena_api.py`); only "after" also named `docs/ARENA.md` |
| B. Show component sizes in the Images model manager | 16 / 92k / 59 | 17 / 88k / 60 | Both found the endpoint and the UI file, and that the size is already shown |
| C. Callers of `BrainService.add` | 19 / 92k / 103 | 29 / 110k / 109 | Both right for their copy (4 and 10 call sites, and the tests to run) |
| D. "The Arena export has a PDF option" (it does not) | 17 / 105k / 51 | 20 / 94k / 59 | Both said plainly that there is no PDF export and named the real formats |
| Total | 90 / 429k / 372 | 98 / 421k / 351 | 8 of 8 correct, nothing invented |

**What this shows.** On these tasks a fresh model with the existing documents, a text search and a careful look at the code was already right, and the map, the graph and the memory did not make it measurably faster, cheaper or more accurate. The names in this codebase and its documents are good enough that finding the place to change is not where models fail here.

**What it does not show.** Harder or longer tasks; a small local model with an 8k window (where reading less matters most); anything that depends on a previous session (the memory); whether the rules in `AGENTS.md` keep a model from breaking an invariant while it *writes* code, because these tasks only read. Those are the cases the system was built for and none was measured.

**What it found, and what was done about it.**
- The agents mostly skipped the protocol: the graph was used in 2 of the 4 "after" runs (1 to 2 calls) and the memory in 1. Two gave a reason: the task was read-only, and `recall` updates a usage counter. `AGENTS.md` now says that neither changes the repository.
- `codegraph callers BrainService.add` lists 1 of its 10 call sites: calls through an attribute (`memory.service.add(...)`) are not resolved. The agent that noticed fell back to a text search and was right. `AGENTS.md` now says to confirm "all the callers" with a text search. The graph is a way to find where to look, not a proof of completeness.
- `willitrun memory recall` crashed on a Windows pipe in cp1252 when an entry held a character outside that code page. Fixed (the memory commands print UTF-8) and tested.
- The instructions for adding an Arena category stopped at the backend. Both agents found the UI label, the test lists and `docs/ARENA.md` that they left out; the nested `AGENTS.md` now lists them.
- CodeGraph, on this evidence, is neutral: it was optional for every answer. While mounted it costs about 390 tokens of tool definition in each request of each project (it is installed at user level). `codegraph uninstall` removes it if that is not worth it to you; nothing else here depends on it.

## Taking a part out

`git config --unset core.hooksPath` turns off the git hooks. Remove a block of `.claude/settings.json` to turn off a Claude Code hook. `codegraph uninstall` removes the code graph from the agents and `codegraph uninit` deletes `.codegraph/`. `willitrun memory` entries can be edited or deleted on the Brain page.
