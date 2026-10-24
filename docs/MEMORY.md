# Project Brain

Every project has a permanent memory: how it is built, what was decided, which files
were changed and why, what went wrong and what fixed it. A run writes to it when it
ends; the next run, days later and with other models, is handed the few entries its
task needs.

Code: `backend/src/agentfit/brain`. Interface: the **Memory** page.

## What a project is

A project is the place the work happens:

| Where the run works | Project | Memory |
|---|---|---|
| A folder on this PC | That folder (by its full path) | Yes |
| A GitHub repository | `owner/repository`, whatever the branch | Yes |
| Nothing chosen | None: a workspace made for the run and not used again | No |

A chat is the same: with a folder or a repository it reads and writes that project's
memory, without one it has only its own conversation.

Verification workflows (those with an evidence dossier, or of category `verification`)
are the exception on purpose. They answer from the evidence they are given and nothing
else, so they are shown no memory and leave none: an earlier conclusion must not become
evidence for the next one.

## What is remembered

Seven kinds of entry.

| Kind | What it holds | Written by |
|---|---|---|
| `architecture` | The **project map** (below), and the design a team settled on | The engine, an agent, you |
| `decision` | A choice and its reason: the plan, each design decision, what you wrote when approving or rejecting at a gate | The engine, an agent, you |
| `change` | The files a run wrote or deleted, each with the purpose its agent gave | The engine |
| `error` | A check that failed: the closing line of the test run, the reviewer's findings. Marked *open* until something fixes it | The engine, an agent, you |
| `solution` | What fixed an error: the work done and the files changed between the failure and the pass, linked to the error | The engine, an agent, you |
| `note` | Anything else worth knowing tomorrow | An agent, you |
| `session` | One request: what was asked, how it ended, what was left open | The engine |

### What the engine records, without asking a model

`brain/capture.py` watches the events of a run and writes down what they establish. No
model is asked to summarise, so nothing here can be invented:

- **File changes** come from the `write_file` and `delete_file` calls that succeeded.
- **Errors and their fixes** come from verdicts. A `run_tests` that exits non-zero, or a
  step whose typed output says no (`passed: false`, `approved: false`, `complete: false`,
  `needs_rewrite: true`), opens an error. When the same check later passes, the error is
  closed and a solution is written from what happened in between: the summaries of the
  steps that ran and the files they wrote. A check that never passes leaves the error
  open, which is what the next session most needs to know.
- **Decisions** come from the typed outputs: a `Plan`, an `ArchitectureDesign` (and each
  of its `decisions` as an entry of its own), a `SceneDesign`, an `EditorialBrief`, and
  the comment you wrote at an approval gate.
- **The session** is the request, the status the run ended with, and its final result.

A chat turn that changed no file and fixed nothing leaves no entry: a question that was
asked is not knowledge about the project.

### The project map

`brain/codemap.py` reads the project's files when a run starts and again when it ends,
only if something changed. It lists the files by language, finds entry points, and reads
what imports what in Python (through the syntax tree) and JavaScript/TypeScript (through
their import statements). The result is one `architecture` entry, rewritten in place,
and the `imports` edges of the knowledge graph. Other languages are counted, not parsed.
Folders such as `.git`, `node_modules` and virtual environments are skipped; the walk
stops at 4,000 files or 8 seconds and says so.

### What agents and you add

Every agent that works with tools gets two more, whatever its workflow lists:

| Tool | Risk | What it does |
|---|---|---|
| `recall` | low | `query`: search in your own words. `file`: everything known about one file (what it imports, what imports it, every entry about it). `id`: one entry in full, with what it is linked to |
| `remember` | low | Save one fact: `kind`, `title`, `text`, optional `files`, and `fixes` (the number of the error entry a solution closes) |

They read and write the memory only, never the workspace. On the Memory page you can add,
edit and delete entries yourself; yours are marked as written by you.

Secrets are removed before anything is stored, with the same patterns the logs use.

## Nothing is stored twice

- The same text again only refreshes its date.
- A decision, architecture note or note with **the title of an older one** replaces it.
  So does one that is the older one again **in nearly the same words** (similarity 0.97
  or more). The older entry is kept and marked *replaced by #n*; it is no longer found
  by search, and the Memory page can show it.
- Anything less alike stays a separate entry. Telling a fact that changed ("port 9000,
  not 8000") from a different fact that reads alike takes judgement, and a wrong merge
  would hide a memory, so the engine does not guess. An agent that knows a fact changed
  gives the new entry the old title.
- Sessions, changes, errors and solutions are events: they are never merged.

## Finding the few entries a task needs

`brain/search.py` looks three ways and merges the three rankings by reciprocal rank
fusion (a memory scores by its position in each list, since the numbers of the three
cannot be compared):

| | Finds | How |
|---|---|---|
| **By meaning** | "login is broken" finds "authentication fails after refresh" | The question and every entry are vectors from an embedding model; the closest are taken |
| **By words** | `AuthService`, an error code, a file name | An SQLite FTS5 index. Identifiers are taken apart (`auth_service.py`, `authService`), accents ignored, long words matched by their stem |
| **By the graph** | The change that touched the same file as a good match; every entry about a file the question names | Edges between entries, files and topics |

Two rules sit on top:

- **An error never comes back without its fix, nor a fix without its error**, error
  first.
- "Close by meaning" is relative. Every embedding model has its own baseline (two
  unrelated texts score 0.1 with one model and 0.45 with another), so an entry counts
  when it stands above the project's own average by a margin, not when it passes a fixed
  number.

### What an agent is handed without asking

When an agent starts, the engine searches with the request, the agent's role and, when
the previous step reported a failure, that failure. What matches goes in the prompt as
a `## Project memory` section, after the task:

- at most 8% of the model's context and never more than 6,000 characters;
- stricter than a search someone reads: an entry found by words alone must share two of
  them, and one found by meaning alone must stand clearly above the rest;
- the project map first, when there is room;
- each entry cut to its opening, with its number, so `recall id=…` gets the rest.

The session shows which entries each step was given, and the SHA-256 of that section is
on the run's ledger next to the outputs the step read.

An agent with a reading list (`reads`) is shown what its list says and nothing else: no
memory section and no memory tools.

## Semantic search needs an embedding model

Vectors come from an embedding model in LM Studio. LM Studio ships with one
(`text-embedding-nomic-embed-text-v1.5`, 84 MB), so normally there is nothing to
download. *Settings → Workflows → Embedding model* chooses another; on Automatic a
multilingual one is preferred when several are installed.

The embedding model is loaded with its weights in system RAM (`lms load --gpu off`). That
does **not** keep it off the graphics card: measured on 2026-10-14 on an RTX 4080 SUPER,
it took 457 MiB of VRAM (954 MiB used without it, 1411 with it), which is what LM
Studio's engine process holds on the card. On a card a 14 GB model nearly fills, that is
enough to push the model into system RAM. So:

- It is loaded only when at least 1,100 MiB of VRAM are free (its 500 plus the margin the
  gateway keeps). Otherwise the search runs by words and through the graph, and `recall`
  says why.
- An agent's memory is searched **before** its model is loaded, when the card is as free as
  it will be. When the agent's model then needs the room, the gateway unloads the
  embedding model first: no slot uses it.
- It unloads itself after five idle minutes.
- At the end of a run the run's models are unloaded first, so the new entries usually get
  their vectors then.

With a model that nearly fills the card, search by meaning mostly happens at the start of
a run and on the Memory page; in between, words and the graph do the work.

Without an embedding model, or with LM Studio closed, search runs by words and through
the graph only. The Memory page and the `recall` tool both say so; entries written
meanwhile get their vectors the next time a model is there, or with *Index now*.

## The knowledge graph

Nodes are entries, files and topics (the tags of entries). Edges:

| Edge | From → to |
|---|---|
| `touches` | a change → a file it wrote or deleted |
| `about` | any other entry → a file or topic it concerns |
| `fixed_by` | an error → its solution |
| `from_session` | an entry → the session it was recorded in |
| `supersedes` | an entry → the one it replaced |
| `imports` | a file → a file, from the project map |

The Memory page draws it (the 160 most connected nodes when there are more). Selecting a
node lights what it is linked to.

## Storage

Four tables in Will It Run's SQLite file (`migrations/0003_brain.sql`): `brain_project`,
`brain_memory` (with its vector as 32-bit floats at unit length), `brain_entity`,
`brain_edge`, plus the FTS5 table `brain_fts` created at start. Deleting a run or a chat
does not touch the memory; *Forget this project* on the Memory page deletes all of it.

*Settings → Workflows → Project memory* turns the whole thing off: runs then neither
read nor write it, and what is already remembered stays.

## API

| | |
|---|---|
| `GET /api/brain` | Projects, whether memory is on, the state of semantic search |
| `GET /api/brain/lookup?workspace=…` or `?repo_url=…` | The memory of a place before a run starts there |
| `GET /api/brain/projects/{id}` | One project: counts, project map, how many entries have vectors |
| `GET /api/brain/projects/{id}/memories?q=&kind=&replaced=` | Entries, newest first; with `q`, the ones that answer it, with why |
| `POST /api/brain/projects/{id}/memories` | Add an entry by hand |
| `PUT`, `DELETE /api/brain/memories/{id}` | Edit, delete |
| `GET /api/brain/projects/{id}/graph` | Nodes and edges |
| `POST /api/brain/projects/{id}/reindex` | Give a vector to every entry that has none |
| `DELETE /api/brain/projects/{id}` | Forget the project |

Run events: `memory.recalled` (what a step was given) and `memory.saved` (what the run
left behind); `run.started` carries `memory` with the project and how much it held.

## Why it is built in, and what it borrows

Before writing this, the most used open-source memory projects were looked at (stars as
GitHub reported them on 2026-10-13):

| Project | Stars | What it is | Decision |
|---|---|---|---|
| [claude-mem](https://github.com/thedotmack/claude-mem) | 100k | Captures what a coding agent does as typed observations, gives them back a little at a time | **Approach adopted**: typed entries captured from tool calls, a compact list first and one entry in full on request. It is a TypeScript plugin for other tools, so nothing could be reused as code |
| [mem0](https://github.com/mem0ai/mem0) | 67k | A memory layer: vectors plus an optional graph, new facts update old ones | **Approach adopted**: one store searched by vectors and graph together; a new fact replaces the old instead of piling up. **Not added as a dependency**: every write calls a language model twice, outside the gateway that keeps one model in VRAM; its graph memory needs a graph database server; it sends telemetry unless told not to |
| [LightRAG](https://github.com/HKUDS/LightRAG), [GraphRAG](https://github.com/microsoft/graphrag) | 40k, 36k | Build a graph from documents with a model, then answer over it | **Not used**: they index documents, not what agents do, and building the graph takes many model calls per file |
| [cognee](https://github.com/topoteretes/cognee), [Graphiti](https://github.com/getzep/graphiti) | 32k, 32k | Knowledge graphs for agents; Graphiti keeps when a fact stopped being true | **Idea adopted** from Graphiti: what was replaced is kept and marked, not deleted. Both need a graph or vector database beside the app |

The brain is therefore tables in the SQLite file the app already has, an index SQLite
already provides, and vectors from the runtime the app already uses. No new dependency,
no server, nothing leaves the PC. No code of these projects is used.

## What it does not do

- **It does not judge what is true.** An entry says what a run did or what an agent
  wrote. A local model can save a wrong note; the prompt tells agents the files win when
  the two disagree, and you can edit or delete any entry.
- **It does not decide by itself that a fact changed**, beyond the same title or nearly
  the same words (above).
- **Imports are read for Python and JavaScript/TypeScript only.**
- **Search is a scan, not an index of vectors.** Every vector of the project is compared
  with the question, in Python. That is a few milliseconds for thousands of entries and
  would not be for millions.
- **The quality of search by meaning is the embedding model's.** The bundled one is
  trained mostly on English.
- **Memory content is in the language it was written in**: the engine's entries in
  English, an agent's in whatever the agent wrote.
- **It has been tried on LM Studio's own embedding model once, briefly.** On 2026-10-13
  ten entries were embedded with `text-embedding-nomic-embed-text-v1.5`, loaded through
  `lms load --gpu off` in 4.4 seconds, and a question about a failing test ranked the
  error first (similarity 0.81), its fix second (0.75) and a related decision third
  (0.64). Everything else was verified with fakes and with a stand-in model. The gates
  that decide what counts as a match were set from how embedding models generally
  behave, not tuned on a real project's memory. What the embedding model costs in VRAM
  was measured once, on one card (above).
