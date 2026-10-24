# Workflows

A workflow is a graph of agents, human approval gates and conditional edges.

```
workflow.yaml  ->  validator  ->  compiler  ->  LangGraph
```

The definition is plain data and independent of the runtime. Code:
`backend/src/agentfit/workflows`. For how it executes, see [LANGGRAPH.md](LANGGRAPH.md).

## The DSL

```yaml
workflow:
  id: coding
  name: Coding Team
  description: Tech lead, planner, human-approved plan, developer, test loop and code review.
  max_steps: 40                     # hard stop for the whole run

models:                             # one entry = one loaded model instance
  reasoning:
    label: Reasoning
    model_key: ""                   # LM Studio model key; empty until assigned
    repo: unsloth/Qwen3-14B-GGUF    # where it comes from
    quant: Q5_K_S
    context_length: 16384
    loading_policy: load_on_demand
    vram_gb: 12.6
  coding: { ... }

agents:
  - id: developer
    role: Developer
    goal: Implement the approved plan as working code in the workspace, with tests.
    responsibilities: [ ... ]
    model: coding                   # which slot; agents sharing a slot share the instance
    system_prompt: |
      ## How you work
      - Use write_file for every file; code that is only described does not exist.
    tools: [list_dir, read_file, write_file, run_python]
    input: The brief, the approved plan, and any test failures or review findings.
    output_schema: CodeChange       # structured output, validated
    loading_policy: shared_instance
    max_retries: 3
    escalation: fail                # fail | human | continue

human_approval:
  - id: approve_plan
    prompt: Review the implementation plan. Approve to start coding.
    show: planner                   # whose output you are shown
    on_reject: planner              # send back, or END
    max_rejections: 2

edges:
  - { from: START, to: tech_lead }
  - { from: planner, to: approve_plan }
  - { from: test_runner, to: developer,     when: "passed == false", label: FAIL, max_loops: 3 }
  - { from: test_runner, to: code_reviewer, when: "passed == true",  label: PASS }
  - { from: [modeler_3d, shader_engineer, animation_engineer], to: integrator }   # join

conditions:                         # optional named conditions usable in `when`
  tests_pass: "test_runner.passed == true"

shared_state: {}
resource_plan: {}                   # written by the team builder
layout: {}                          # node positions for the visual editor
```

### Agents

Every agent defines role, goal, responsibilities, input, output schema, system prompt,
tools, model, loading policy, retry policy and escalation policy. The validator warns
about prompts under 40 characters; "You are a coding expert." is not a definition.
The built-in role library is `teams/roles.py`.

### Edges

- **Conditional**: `when` is tried in order; the first true edge is taken.
- **Loop**: any edge that returns to an earlier node must carry `max_loops`. When the
  bound is hit while the condition still holds, the run ends with status `retry_limit`,
  or continues to `on_exhausted` if one is set.
- **Fan-out**: several unconditional edges from one node run their targets in parallel.
- **Join**: `from: [a, b, c]` waits for all sources.

### Conditions

A small expression language, evaluated without `eval`:

```
passed == false
reviewer.approved == false and tester.passed == true
len(failures) > 0
root_cause == 'design'
not complete
```

A bare field refers to the source node's output; `agent.field` to another agent's.
Operators: `== != > >= < <=`, `and`, `or`, `not`, parentheses, `len(...)`.
Literals: numbers, quoted strings, `true`, `false`, `null`.

### Structured outputs

Agents exchange typed data, not prose. `output_schema` names a schema from
`workflows/schemas.py`: `TaskBrief`, `Plan`, `ArchitectureDesign`, `CodeChange`,
`TestReport`, `CodeReview`, `Implementation`, `ValidationReport`, `Documentation`,
`TechnicalReview`, `SpecialistAnalysis`, `EditorialBrief`, `Draft`, `EditReview`,
`FactCheck`, `SceneDesign`, `PerformanceReport`, `PermissionCheck`, `Observation`,
`ActionDecision`, `RiskAssessment`, `ExecutionResult`, `Verification`, `FinalReport`,
`GenericOutput`.

The schema is sent to LM Studio as a JSON-schema response format and the answer is
validated again on receipt. An invalid answer is retried with the validation error as
feedback, up to `max_retries`.

## Validation

`willitrun workflow validate file.yaml`, and continuously in the editor. Errors block
saving and running:

- unknown model slot, output schema or tool
- duplicate or invalid node ids
- an edge to or from a node that does not exist
- no entry point, a node unreachable from START, END unreachable
- a node with no outgoing edge
- an invalid condition
- **an unbounded loop** (a cycle with no `max_loops`)
- a node that both feeds a join and has its own edges
- an agent that `reads` a node that does not exist, or restricts a field its schema lacks
- a check of unknown kind, or one whose parameters point at a missing node
- to *run*: a model slot with no model assigned

## Built-in templates

| Template | Shape |
|---|---|
| **Coding** | Tech Lead → Planner → *human approval* → Developer → Test Runner (FAIL → Developer ×3) → Code Reviewer (CHANGES → Developer ×2) → final |
| **DevOps** | Supervisor → Infrastructure Architect → *human approval* → DevOps Engineer → Validation (ERROR → Engineer ×3, design error → Architect ×1) → Documentation → Technical Review (CHANGES ×2) → final |
| **Documentation** | Supervisor → Documentation Specialist → Technical Reviewer (CHANGES ×2) → final |
| **Three.js** | Art Director → Scene Architect → 3D / Shader / Animation *in parallel* → Integrator → Browser Test (FAIL ×2) → Performance Check (FAIL → Performance Engineer ×2) → build |
| **Writing** | Editorial Director → Author → Editor (REWRITE → Author ×2) → Fact Checker → Proofreader → Final Review |
| **Computer Use** | Planner → Permission Check → Observer → Decision → Risk Classifier (RISKY → *human approval*) → Executor → Verifier (INCOMPLETE → Observer ×8) → Final Report |

Templates ship without models. Opening one in the editor and saving creates your own
copy; the team builder produces workflows with models already assigned.

## Verification workflows

The team templates assume each role does its job and the next one builds on it. The
verification templates assume the opposite: any model may invent, so nothing an agent
says counts until something it cannot influence has checked it. They are in the editor
under *Verification workflows*.

| Template | Who distrusts whom | What stops an invented answer | What is recorded |
|---|---|---|---|
| **Adversarial Blackboard** | A skeptic audits the researcher's claims and is never shown the researcher's reasoning | Every claim needs a quote that exists word for word in the evidence and a verdict that it holds; the rest is discarded; the writer sees only what survived | A content id per claim, the hash of every step and of what it read |
| **Commit DAG** | Two authors work on one specification without seeing each other; a reviewer neither of them is judges both | A commit passes only if it was approved and its hash, recomputed now, equals the hash of what the reviewer read; the merger never sees a dropped commit | Each commit by hash, linked to the hashes of what it was built from; fixed input budget per agent |
| **Fail-Closed Swarm** | A router that can only name an expert from a fixed list, or refuse | A gate before any model; a validator and a quote check after the expert; three expert activations at most, then the run refuses | Every activation, counted; the final report lists them |
| **Deferred-Consensus Delphi** | Three panelists vote blind on the same propositions from the same evidence; none can look anything up | Votes are counted by code; a vote without a real quote weighs half, unbacked dissent in round two weighs nothing; without a quorum the run ends `INSUFFICIENT_CONSENSUS` | The evidence dossier with a SHA-256 per source, every ballot, every tally |

Three mechanisms carry this. All three are in the engine, not in a prompt.

### Isolation: `reads`

```yaml
agents:
  - id: skeptic
    reads: [dossier, researcher.claims]   # the claims, not researcher.reasoning
    max_input_tokens: 6000                # optional: never handed more than this
```

Without `reads` an agent sees everything produced so far. With it, the agent's prompt is
built from the listed nodes only: `node` (the whole output), `node.field` (one field) or
`a|b.field` (whichever of the two ran last). Nothing else reaches it. `max_input_tokens`
caps the earlier work an agent is handed, whatever its model could hold.

`choices` fixes the values a text field of the output may take:

```yaml
  - id: router
    output_schema: Routing
    choices: {expert: [code, data, systems, language, none]}
```

The list is given to the decoder as a JSON-schema `enum`, so a grammar-constrained
engine cannot produce another value, and the parsed answer is checked against it again.

### Checks: steps that are code

```yaml
checks:
  - id: filter
    kind: verify_claims
    params: {claims: researcher, audit: skeptic, evidence: dossier}
edges:
  - {from: filter, to: writer, when: "passed == true"}
  - {from: filter, to: researcher, when: "passed == false", max_loops: 1}
```

A check is a node like an agent, but no model runs in it. Its output is routed on like
any other (`passed`, `verdict`, counts). A check that fails and ends the run gives the run
the status `blocked` and its `summary` as the reason.

| Kind | What it does | What it does not prove |
|---|---|---|
| `evidence_dossier` | Before any model: turns the request and the text files of the documents folder into numbered sources (E1, E2, ...), each with its SHA-256. Fails when there is too little evidence or the request is too long | That the evidence is true |
| `verify_claims` | Keeps a claim only if its quote is in the evidence word for word (case, spacing and typographic quotes aside; `...` may skip words) and, with an auditor, only if the auditor's verdict says it holds. No quote, invented quote, no verdict: discarded | That the claim reads the quote correctly. That judgement is the auditor's |
| `tally_votes` | Counts a panel's votes per proposition with fixed weights; decides one only when a side reaches the threshold and holds a real quote | Anything statistical. The threshold (0.66) and quorum (0.5) are settings, not calibrated values: calibrating them, for example with Learn-then-Test, needs a set of questions with known answers, which Will It Run does not have |
| `verify_commits` | Recomputes each commit's SHA-256 and passes it only if approved and unchanged since the reviewer read it | That the reviewer judged well |
| `audit_trail` | Last step: every item the answer says it relies on must have been accepted; every recorded hash is recomputed; then the answer is released with the list of steps | The wording of the answer. It checks the citations the answer declares |
| `refuse` | Ends the run with a named verdict instead of an answer | |

Ids of claims and propositions are written by the engine from their content
(`c-70643c55`). Whatever id the model writes is replaced before anyone else reads it.

### The ledger

Every completed node appends one entry: the SHA-256 of its output (canonical JSON), the
SHA-256 of each earlier output that was put in its prompt, and the model that served it.
A step that read another carries the same hash that step wrote. The run's result holds
the ledger, the Sessions page shows it under *Audit trail*, and each step shows its hash.

These are hashes, not signatures: everything runs in one process on one PC and no keys
are involved. A run cannot be replayed from an arbitrary step from the interface.

### Evidence

A workflow with an `evidence_dossier` answers from what you give it: the text of the
request and, optionally, a **documents folder** chosen when the run starts
(`evidence_dir` in `POST /api/runs`). The folder is only read. Plain-text formats are
read (`.txt`, `.md`, `.csv`, `.json`, source files and similar, up to 40 files of 2 MB);
PDF and Word files are listed as skipped, not silently ignored.

Give the roles that check each other different models when you can. The checks apply
either way, but two instances of one model tend to make the same mistake.

In a live check on an RTX 4080 SUPER, the Adversarial Blackboard with Gemma 4 12B as
researcher and writer and Spark X2.5 4B as skeptic answered a question about a six-line
warranty text in about two minutes: four claims, four quotes found, four verdicts, the
answer released with its trail, three model loads. The same workflow asked for a price
and a country the text does not contain ended `blocked` with no answer.

## Tools

| Tool | Risk | Notes |
|---|---|---|
| `read_file`, `list_dir` | low | Workspace only |
| `write_file` | medium | Workspace only |
| `delete_file` | **high** | Asks first |
| `run_python`, `run_tests` | medium | Runs in the workspace with a timeout. `run_tests` runs pytest for Python tests, and `node --test` for JavaScript tests (`*.test.js`) when the project has no Python ones and Node.js is installed |

A turn the runtime cannot read as a tool call or an answer (LM Studio: *"The model produced
output that does not match the expected peg-native format"*, seen with gpt-oss) is not sent
again unchanged: the model is told what went wrong and which tools exist. After three such
turns the agent stops using tools and gives its answer with what it has found.
| `git` | by subcommand | `status/log/diff` low; `push`, `reset --hard`, `clean`, `rebase` **high** |
| `powershell` | by command | Read-only commands low; unknown medium; destructive, installing, elevating, registry, remote or infrastructure-changing commands **high** |
| `list_windows`, `active_window` | low | Window titles only |
| `read_clipboard`, `screenshot` | medium | Screenshot is saved to the workspace |
| `open_application`, `type_text`, `click` | **high** | Asks first |

| `recall`, `remember` | low | Given by the engine to every agent that has tools, when the run works in a project. They read and write the project's memory only. See [MEMORY.md](MEMORY.md) |

File tools cannot leave the run's workspace
(`%LOCALAPPDATA%\Will It Run\workspaces\<run id>`, or a folder you choose). High-risk calls
pause the run for your approval. See [SECURITY.md](SECURITY.md).

## Several models in one workflow

`models:` is a set of slots, and each agent names one. Agents on the same slot share the
loaded model; an agent on its own slot is served by its own model:

```yaml
models:
  reasoning:
    model_key: qwen3.8-27b
    context_length: 16384
    wide_model_key: qwen3.8-27b-gsq-rco   # optional: serves requests too large for the model above
    wide_context_length: 49152
  logs:
    model_key: spark-x2.5-4b
    context_length: 32768
agents:
  - {id: planner, role: Planner, model: reasoning, ...}
  - {id: logreader, role: Log Reader, model: logs, ...}
```

In the visual editor you do not handle slots. Select an agent and choose its **Model**
from the models you have downloaded; or deselect every node to see all agents with a
model picker each, and *Same model for every agent*. Agents given the same model are put
on one slot, an agent given a different one gets a slot of its own, and slots nobody uses
are removed. There is no limit: nine agents can use nine models.

*Context and memory settings* has, for each model in use, **Automatic** or **Manual**. A
model you pick in the editor starts on Automatic (`auto_tune: true` on its slot):

- The context is not a number you set. Each request says how much it needs; the model is
  loaded with that plus a quarter, at least 16384 tokens, never more than the model
  supports and never more than the free VRAM holds when it is measured at that load.
- A later request that needs more than the model has reloads it larger. It never shrinks.
- What stays in memory is decided by the gateway: a model stays while there is room and
  goes when another needs the space.
- The panel says what this PC holds for that model ("up to about 48,128 tokens entirely on
  the graphics card") and whether that is LM Studio's estimate or a measurement from an
  earlier run. A model whose weights alone exceed the usable VRAM is flagged: it will run
  partly from system RAM and slowly, whatever the context.

Manual keeps the three settings of before: context length, loading policy and the
wide-context variant.

Automatic or manual, a conversation that outgrows the context does not fail the request.
An agent that calls tools keeps every result in its conversation; before each call the
conversation is fitted to the context the model really has, shortening the oldest tool
results first. On an automatic slot the model is reloaded with more context instead, and
nothing is shortened while more fits.

In the team builder, *How many models* chooses between one model for every role, one
per kind of role, or letting Will It Run decide. The plan then lists **which model does
which job**, role by role in running order, with the model's score for that kind of work
(reasoning, coding, writing or vision), its speed and memory. In Guided and Expert mode
each role has a picker with every downloaded model and the recommended ones, scored for
that role; a model picked there (`agent_overrides` in the API) moves only that role.
Models chosen by hand are not counted against the automatic limit of three.

Only one model is normally in memory at a time, and every change of model is a reload
(about 7 to 19 seconds per swap for 4B to 27B models in the live check), so a workflow should
change model where it pays: a reviewer that is not the author, a small model with a huge
context for logs, a vision model for images. How the swap is done, and how the context
is fitted to the free VRAM, is in [LANGGRAPH.md](LANGGRAPH.md#model-loading-the-gateway).

## Loading policies

| Policy | Meaning |
|---|---|
| `keep_loaded` | The persistent model: last to be evicted, left loaded when the run ends |
| `load_on_demand` | Loaded when an agent needs it, evicted when room is needed |
| `unload_after_use` | Unloaded as soon as the agent finishes |
| `shared_instance` | (agents) uses its slot's instance together with other agents |
| `sequential` / `parallel` | Stored with the definition for the planner; LLM calls within one run are serialized on a single GPU |

## Visual editor

Workflows → open a workflow. Drag nodes, connect them by dragging between handles,
select a node or edge to edit it in the side panel, Delete to remove. Loops are drawn as
dashed arcs beside the main flow, rejections in red. The YAML button exports the
definition.

The side panel (models, or the selected node) can sit on the left or on the right: the two
buttons at its top move it. Drag its inner edge to make it wider or narrower, between 300
and 640 pixels; double-click the edge to reset. With the keyboard, focus the edge and use
the arrow keys. Both choices are remembered.

## Running

From the editor (Run), from Sessions (New), or `willitrun workflow run <id> --task "..."`.

**The prompt** is a rounded field docked at the bottom, in the editor, in a new session
and under the graph of a finished run. Enter starts, Shift+Enter makes a new line. The
"+" on its left connects a folder or a GitHub repository, which then shows as a label
above the field; the cross on the label disconnects it. This is where the team works:

| Where | What happens | API |
|---|---|---|
| Nothing chosen | A workspace made for this run, under Will It Run's data folder (`%LOCALAPPDATA%\Will It Run\Data\workspaces\<run id>`; the session's *Resources* tab shows it). The next requests of the session work in the same one. There is no button for it: it is what happens when you add neither of the two below | (nothing) |
| Folder on this PC | The agents read and write in that folder. A whole drive, the user folder itself and Windows' or programs' folders are refused | `workspace` |
| GitHub | One of your repositories, chosen from a list after signing in, or any `https://github.com/owner/repository` address; optionally a branch. Cloned (shallow) into Will It Run's data folder; the team works on the copy | `repo_url`, `repo_branch` |

**Signing in to GitHub.** Clicking GitHub when you are not signed in opens github.com in
the browser with a one-time code (already on the clipboard) to enter there; when you have
authorised, the list of your repositories appears, most recently pushed first, with a
search box. The sign-in is GitHub's own, run by the GitHub CLI (`gh auth login --web`):
you type your password on github.com, and `gh` keeps the resulting token in the Windows
credential store. If you already use `gh`, you are already signed in and the list appears
at once; *Change account* signs in with another. Without `gh` installed the app says so
and still accepts the address of a public repository.

In a folder of yours, *Ask me before a file that is already there is overwritten* is on
by default (`protect_existing`): `write_file` on a file that existed before the run pauses
for approval, exactly like deleting. Files the run itself created are rewritten freely.
Code the agents run (`run_python`, `run_tests`, PowerShell that is not recognised as
high-risk) is not covered by this: see [SECURITY.md](SECURITY.md).

A GitHub copy is reused by later runs. It is updated from GitHub only when that cannot
lose anything (no local changes, fast-forward); a copy the agents changed is left as it
is, and the run says which of the two happened. Cloning uses the Git installed on the PC
with every prompt turned off; when you are signed in through `gh`, Git is told to take the
credential from it for that one command, so your private repositories clone. Will It Run
never asks for, reads, stores or sends a GitHub password or token, and nothing is pushed
unless an agent calls `git push` and you approve it.

Under a finished run, what you write is one of two things, and the prompt says which
before you send it:

| | What happens | API |
|---|---|---|
| **A question about the work** ("how do I open the site?", "why did the tests fail?") | **One model answers**, under the result, in the folder the team worked in, told what the team was asked and delivered. The team does not run again. Further questions continue the same conversation. The model is the one that wrote the team's files | `POST /api/runs/{id}/ask`, `GET /api/runs/{id}/questions` |
| **New work** ("add a contact page") | **The whole team works**: the next request of the session, same workflow, same place, and the agents are told what the previous request was and what it delivered | `POST /api/runs` with `continue_from` |

Which of the two is chosen from the words: a message that ends with a question mark, or
starts with a question word (*come, cosa, perché, dove, quale, how, what, why…*), is a
question; anything else is work for the team. The label under the field shows the
choice; one click switches it.

"Same place" holds for a session with nothing chosen too: its next request works in the
workspace of the request before, where the files are, not in a new empty one.

Evidence workflows are the exception for new work: an earlier answer is never handed on,
because it would become evidence for the next one.

For a workflow with an evidence dossier, the chosen folder or repository is what it reads
as documents; nothing is written there.

**The project's memory.** A folder or a repository is a project, and a project has a
permanent memory. When one is connected, a label next to it says how many entries the
project holds. Each agent is then handed the few that match its task, agents with tools
can search and add to it, and when the run ends what it changed and found out is written
down. A run with nothing connected has no project and remembers nothing. See
[MEMORY.md](MEMORY.md).

From the command line: `--folder`, `--repo`, `--branch`, `--documents`.

**A session, live.** The left half is the workflow's graph as it runs: nodes that have
finished turn green, the one working is outlined and shows its tokens per second, a
node that ran more than once says which run it is on, and a check that did not pass is
orange. The arrows the work has travelled are solid green; the arrow into the working
node is made of dashes that flow towards it, faster the faster the model is writing
(one cycle every 14 / tokens-per-second seconds, between 0.2 and 2.4). *Fit* shows the
whole graph; *Follow* keeps the working step in view. With reduced motion turned on in
Windows the dashes stand still.

The right half has three tabs. *Session* is the activity: each step with its tool calls,
model loads, approvals, and, while a model is answering, what it is producing right now,
first its reasoning and then its answer, with speed and token count. *Step detail* shows
one step in full; click a node in the graph to choose it: the reasoning, the answer, the
tools it ran and its hash. *Resources* has the models in memory, GPU and RAM, and the
files written. The header counts the steps so far and how many times the run was
**short on context** (a model loaded with less context than asked, or a conversation
shortened to fit); *Copy the log* puts the whole session on the clipboard as text. A step that was given
entries of the project's memory says which, and the run's last line says how many it
wrote; *Resources* has the project and a link to its memory.

Reasoning is shown when the model reports it: LM Studio sends it apart for reasoning
models, and `<think>` blocks are recognised too. It is streamed to an open session and
saved once with the finished step (up to 24,000 characters), so a session opened later
still has it. The text streamed while a step runs is not stored.

Checks appear as steps of their own, marked as code with no model, with their verdict.

A run ends `completed`, `failed`, `rejected` (a human said no), `cancelled`,
`retry_limit` (a loop ran out), `no_route`, or `blocked` (a check did not pass).
