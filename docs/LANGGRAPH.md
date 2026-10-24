# LangGraph execution

How a [workflow definition](WORKFLOWS.md) becomes a running LangGraph. Verified with
`langgraph 1.2.14` and `langgraph-checkpoint-sqlite 3.1.1`.

Code: `workflows/compiler.py`, `workflows/model_manager.py`, `workflows/runner.py`.

## LangGraph APIs used

```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphInterrupt, GraphRecursionError

builder = StateGraph(WorkflowState)
builder.add_node(name, fn)
builder.add_edge(START, first)                     # entry
builder.add_edge([a, b, c], target)                # join: waits for all sources
builder.add_conditional_edges(node, router, path_map)
graph = builder.compile(checkpointer=SqliteSaver(conn))
graph.stream(input, {"configurable": {"thread_id": run_id}, "recursion_limit": max_steps}, stream_mode="updates")
graph.get_state(config)                            # pending interrupts
graph.stream(Command(resume=decision), config)     # resume after human input
```

## State

```python
class WorkflowState(TypedDict, total=False):
    task: str
    outputs:     Annotated[dict, merge]     # node id -> its latest structured output
    routes:      Annotated[dict, merge]     # node id -> next node(s), read by its edge
    loop_counts: Annotated[dict, max]       # "source->target" -> times taken
    approvals:   Annotated[dict, merge]     # gate id -> {approved, comment}
    history:     Annotated[list, append]    # nodes in completion order
    ledger:      Annotated[list, append]    # per node: hash of its output and of what it read
    status:      Annotated[str, last]
    last_node:   Annotated[str, last]
```

Every key has a reducer so parallel branches can write in the same step.

## Compilation

- Each agent, each deterministic check and each human-approval gate becomes a node.
- A check node calls no model: it runs one function from `workflows/checks.py` over the
  state and routes on its output like an agent. If the function raises, the check fails;
  it never lets work through because it could not run.
- Every agent and check node appends a ledger entry: SHA-256 of its output and of each
  output that was put in front of it (`visible_outputs`, which applies the agent's `reads`).
- A node computes where to go next (conditions, loop bounds) and writes it to
  `routes[node]`. A conditional edge with an explicit path map reads it. This is how
  conditional edges, bounded loops, `on_exhausted` and fan-out are all expressed.
- `from: [a, b, c]` becomes a LangGraph join edge.
- `workflow.max_steps` is the graph's `recursion_limit`. Loop bounds are the first line
  of defence, the recursion limit the second: no workflow can run forever.

The graph is compiled per run, closing over a `RunContext` (runtime, model manager,
event emitter, tool workspace), so nothing unserializable is put into the checkpointed
state.

## An agent node

1. Emit `node.started`.
2. Ask the model manager for the slot's instance (may load or switch models).
3. Build messages: system prompt (role, goal, responsibilities, rules, output contract)
   and a user message with the task, the work so far, and on a loop the agent's previous
   output plus the feedback that sent it back.
4. If the agent has tools: call the model with tool schemas, execute the calls, feed
   results back, up to `max_tool_rounds`. A call's risk comes from its arguments and from
   where the run works (`tools.classify`): in a folder of the user's, overwriting a file
   that was there before the run is high risk and waits for approval.
5. Get the structured answer with a JSON-schema response format, validate it, retry
   with the validation error up to `max_retries`.
6. Emit `node.completed` with tokens, reasoning tokens, tok/s, duration, the context the
   model ran with and the reasoning it reported.

**Permissions of a run.** `RunManager.start(..., approval=)` takes "ask" or "bypass", chosen
under the prompt (Shift+Tab switches, as in Claude Code). With "ask", plan gates and
high-risk tool calls wait for the user. With "bypass" nothing waits: `request_approval`
records the request and an approval marked `automatic`, and no tool call asks, deletions
and overwriting the user's own files included. The choice is in `run.started` and shown
on the session.

**Stop.** `cancel` sets a flag that is read between steps and, since 2026-10-13, as each
piece of a streamed answer arrives (`on_delta` in `_complete`): the stream is closed and
the model stops generating. Before, a stopped run first finished the answer it was on.

**A request written while a run works.** `POST /api/runs` with `continue_from` and
`queue: true` does not start a run while that one is active: `RunManager.queue_after`
holds it (one per run, in memory) and starts it when the run ends, however it ends, with
what the run did as context. `RunRecord.queued` shows it, `DELETE /api/runs/{id}/queued`
takes it back, and a `run.followed` event takes whoever is watching to the new run.

**Chat.** A chat (`agentfit/chat.py`) is not a second engine: each message is a run of a
one-agent workflow built on the spot for the model the user picked, so it has the
gateway, the tools, approvals or ignored permissions, live text, Stop and the waiting
request of any other run. Two things make it a conversation. The agent's output is
`Text`, the one schema the engine does not enforce through the decoder: the last message
is taken as it is (`_final_text`), so the answer is prose and arrives as it is written.
And every message is given the turns before it (`ChatService.history`, the oldest left
out when they do not fit). A chat is one entry in the key-value table; its turns are the
runs of project `chat:<id>`, kept out of the list of sessions. With no folder or
repository it works in one workspace of its own for all its turns, and the model stays
in memory between messages (`keep_models`), until the idle timeout or another run needs
the card.

**The tool budget.** `max_tool_rounds` is 30 by default. A round is one answer of the model
and may hold several calls. Until 2026-10-13 the value was 6, written into every saved
workflow and shown on no screen. It was too few: in a real run a Developer wrote three
files and was out of rounds before it could test or fix them, four visits in a row, and
the run ended at its retry limit. A saved 6 is therefore read as the default
(`dsl.LEGACY_TOOL_ROUNDS`). The agent is told its budget in the system prompt (`## Tools`)
and again when 5 and 2 rounds are left, on the tool result it reads next; before, it
learned of the budget when it was gone.

**The verdict of a test report.** When an agent whose output is a `TestReport` ran
`run_tests`, `passed` is set by the engine from the exit code of its last run
(`checks.bind_test_verdict`) and `evidence` records the run and whether the agent had
answered otherwise. The agent can be wrong in both directions: in the same real run it
answered `passed=false` over 33 green tests because the reviewer had asked for more of
them. What else it found stays in `failures` and `unverified` for the reviewer. Without a
`run_tests` call the agent's own verdict stands.

**What the team cannot check.** No agent can open a browser. `TaskBrief`, `TestReport` and
`CodeReview` say so in the descriptions of their fields, which are put in the prompt at
run time and therefore reach saved workflows too: acceptance criteria are to be checkable
by reading files and running code, and what cannot be checked goes under `unverified`,
which does not fail the tests and does not block approval.

**The workspace stays clean.** `run_python` writes its snippet in a temporary folder and
runs it with the workspace as working directory; `run_tests` runs pytest without its cache
and without bytecode files. An agent that listed the workspace used to find
`_agentfit_snippet.py` there and spend rounds on it.

Every model call is streamed when the runtime can (`supports_streaming`). `LiveText`
forwards what arrives, reasoning and answer apart, about four times a second as
`node.stream` events with the token count and the speed so far. Those events go to the
event stream only (`RunManager._emit_live`): they are not written to `run_event` and not
kept in the bus backlog. The response the rest of the engine sees is assembled from the
stream and is the same a non-streamed call returns.

**The schema sent to the model.** `schemas.response_format` inlines `$ref`s and removes the
annotations a decoder does not need (`title`, `default`, `$defs`). Those words are schema
keywords only where a schema is expected: under `properties` they are names of fields. An
earlier version removed them there too, so a field called `title` (the steps of a `Plan`,
a `Documentation`, a `Draft`) vanished from the schema while staying in `required`, the
decoder could not be made to produce it, and the answer failed validation on every
retry. Each object now also carries `additionalProperties: false`. A test walks every
schema and fails if a required field is not in its `properties`.
7. Decide the route and return the state update.

If the turn that ends the tool loop is already a valid answer, it is used directly and
the extra model call is skipped.

### Failure and escalation

A failed model call, an invalid answer or a truncated answer consumes one retry. When
retries run out, the agent's `escalation` policy applies:

| Policy | Effect |
|---|---|
| `fail` | The run ends `failed` with the agent's error |
| `human` | The run pauses; approve to continue without the step, reject to stop |
| `continue` | The agent's output becomes an error note and the run goes on |

A model that cannot be loaded fails the agent the same way.

## Human in the loop

Two places use `interrupt()`:

- **Approval gates** (`human_approval` in the definition): the run pauses with the
  shown agent's output. Approve → continue; reject → `on_reject` (bounded by
  `max_rejections`) or end as `rejected`. Your comment is passed to the agents as
  "Human feedback".
- **High-risk tool calls**: the run pauses *inside* the agent, showing the exact tool and
  arguments. Deny → the tool result becomes "DENIED by the user" and the agent carries on
  without it.

### Resuming exactly where it stopped

LangGraph re-runs a node from its start when it resumes. An agent that had already
written three files before asking to delete one must not write them again. So:

- the agent's progress (messages, pending tool calls) is kept for the duration of the
  node and reused on resume, so earlier tool calls and model calls are not repeated;
- every approval request carries a fingerprint (run, node, sequence, tool, arguments),
  and a resume value is only applied to the request with the same fingerprint. A decision
  can never be applied to a different call, including after a restart.

`tests/test_workflows.py::test_work_before_an_approval_is_not_repeated_on_resume`
covers this.

## Model loading: the gateway

A workflow can use several models, one per slot. They rarely fit in graphics memory
together, so every model call goes through one gateway (`workflows/model_manager.py`)
that swaps them, and that measures instead of assuming. Before each request:

1. **Ask LM Studio what is loaded.** The right model with enough context for this
   request: use it, nothing is reloaded. LM Studio is the truth here; an idle timeout
   or the user can unload a model behind Will It Run's back.
2. **Free memory.** Other models stay only if the incoming one measurably fits beside
   them at its full context. Otherwise they are unloaded, `load_on_demand` ones first
   (least recently used), `keep_loaded` ones last. A context is never shrunk to keep a
   neighbour in memory.
3. **Measure free VRAM** with `nvidia-smi`. Taken at this point, the figure includes
   what the desktop and the browser hold right now.
4. **Choose the context**: the largest, up to what the slot asks for, that fits the
   model's fixed cost plus its cost per 1000 tokens, leaving a margin (600 MiB by
   default, Settings → Workflow).
5. **Load** with that context and an idle timeout (10 minutes by default), so an
   abandoned run cannot keep a model in memory.
6. **Measure again.** Less than 480 MiB free means the model is spilling into system
   RAM: Windows does not fail, the driver moves the overflow and the model keeps
   answering several times slower. That counts as a failed load: unload, first evict
   any neighbour that was kept, then retry with three quarters of the context, up to
   three attempts. If nothing helps the run continues and the session says so.

The acquire and the request happen under the run's LLM lock, so one request is served
at a time and a parallel branch cannot swap the model out from under another agent.

### What a model costs

The two numbers in step 4 start from LM Studio's own estimator (`lms load
--estimate-only` at two context lengths) and are then replaced by what real loads take:
every unsaturated load adds a point (context, MiB used), and two points at different
contexts give the true line, kept in the database for later runs.

The estimator is cautious. Measured on the development PC (RTX 4080 SUPER, 16 GB):

| Model | Estimator at 4096 tokens | Real load at 4096 | Estimator per 1000 tokens | Real per 1000 tokens |
|---|---|---|---|---|
| Qwen3.8 27B (13.1 GB file) | 15258 MiB | 14422 MiB | 100 MiB | 40 MiB |
| Qwen3.8 27B GSQ RCO (11.8 GB) | 13773 MiB | 12663 MiB | 93 MiB | 36 MiB |

So the first load of a big model comes out with less context than the card allows.
When the first real measurement shows room for at least half as much context again,
the gateway reloads once at the better context (`retuning` in the session); otherwise
the profile improves over the next loads. In the live check the 27B was given 4096
tokens on first use, then 5120, 10240, 12288 and 14336 over the following loads, as
real points replaced the estimate.

Readings are taken on a desktop in use, so they carry noise (other programs took or
released about 200 MiB between loads in that check). A load that took more than 30%
above or 50% below what was expected, or a pair of readings that implies a negative or
absurd cost per token, is not learned.

### Past the plan, judged by speed

A model that nearly fills the card by itself breaks step 4. Qwen3.8 27B Q3_K_XL takes
about 14.4 GB of the 16 GB; with a browser open, not even 4096 tokens leave the margin
free on paper, so the plan is 4096, the minimum. That is too little for an agent that
calls tools: a Developer could not write one HTML file within it. Measured on the same
PC, with nothing else changed (`lms load`, 220 generated tokens):

| Context | Free VRAM after the load | Speed |
|---|---|---|
| 4096 | 670 MiB | 63 tok/s |
| 16384 | 649 MiB | 54 tok/s |
| 49152 | 591 MiB | 61 tok/s |
| 65536 | 557 MiB | 51 tok/s |
| 98304 | 560 MiB | 12 tok/s |

Two things follow. Windows hands the model what idle desktop programs hold (their share
went from about 1300 MiB to about 600), so far more context fits than the plan says. And
the free-VRAM figure of step 6 cannot see the overflow when it does come: it reads the
same 560 MiB at 61 tokens per second and at 12. Only the speed tells them apart.

So when a request needs more context than the plan allows, or an earlier load already
proved more, the gateway loads past the plan and keeps the result only if the model is
measured to answer at its usual speed:

- **How far**: up to the context that is within `stretch_mib` (1024 MiB) of fitting on
  paper, never more than the slot asks for (`_reach`).
- **Usual speed**: a 48-token generation right after a load the plan vouches for
  (`_speed`, through the runtime's own statistics), kept in the model's profile as
  `tokens_per_second`. It is measured only for models where going past the plan is
  possible, so an ordinary load costs nothing more.
- **The check**: a load past the plan that answers at less than half the usual speed, or
  whose speed cannot be measured, is undone like a saturated one. Past the plan the speed
  is the whole verdict: the 480 MiB rule of step 6 is not applied there, because it was
  seen to refuse a load that answered at full speed (457 MiB free, 54 of 67 tok/s). Next comes a context
  that has worked before (`verified_context` in the profile) or, without one, halfway
  back; the last attempt is always the plan itself.
- **What is remembered**: a context that held its speed becomes `verified_context`, and
  the next run loads it straight away. One that was refused is not tried again in that
  run (`_too_much`), and a verified context that fails later is forgotten. A load made
  with the card full adds no point to the memory profile: its reading is not what the
  model costs.
- **Without a usual speed** the plan is loaded first to measure one, then the larger
  context: two loads, once per model.

**The overflow itself.** Speed at load time is not the whole story either. In a real run
the same model, loaded with 37888 tokens at 60 tok/s and 530 MiB free, was answering at
8 tok/s an hour later: the desktop compositor had grown from about 0.5 to 2 GB, and 858
MiB of the model were in system RAM. Windows counts that per process (`\GPU Process
Memory(*)\Shared Usage`), and `hardware.windows.model_spill_mib` reads it for the
processes that run models for LM Studio (about 1.5 s through PowerShell). The gateway
uses it twice: after every load, where more than `spill_mib` (300) refuses the load
whatever its speed, and while the model works (`_watch_spill`, every 45 s at most). An
overflow seen at two looks in a row makes the next request reload the model with half
the context, remembered as the most to try in that run; at the smallest context it is
reported once, with the advice to close programs or use a smaller quantization. What
this cannot do is make a model fit that does not: with 2 GB taken by the desktop, a
14.4 GB model overflows a 16 GB card at any context.

An ordinary request on such a model still gets the plan's context; `_ceiling` then holds
what could be reached, so a later request that needs more triggers the larger load. An
agent that calls tools counts as needing the base context from its first request
(`AUTO_WORKING_NEED`, automatic slots only): it carries every call and result with it,
and with the plan's 7168 tokens a Developer had its own first file cut out of its
conversation and spent rounds reading it back.

### Requests too large for the model

A slot can name a **wide-context variant** (`wide_model_key`, `wide_context_length`):
usually the same model at a smaller quantization, whose saved memory becomes context.
Each agent tells the gateway how much context its whole input needs before trimming.
When that is more than the slot's model can get, the variant serves the request; an
ordinary request goes back to the main model. Without a variant the main model serves
and the prompt builder trims the earlier outputs to fit, as before.

The prompt is always built for the context the model was really loaded with, which can
be less than the slot asked for.

### Automatic slots

A slot with `auto_tune` has no fixed context. `_target(slot, need)` is
`max(16384, need x 1.25)` rounded up to a step, capped by what the model supports
(`max_context_length` from the runtime) and then by step 4 like any other load. The model
is reused whenever it already has what the request needs, and reloaded larger when it does
not; `_ceiling` remembers a limit set by VRAM so a request that cannot be satisfied does
not reload the model on every call. Its loading policy is ignored: the gateway treats it as
load-on-demand.

### Conversations that outgrow the context

Before every model call (`_complete`) the conversation is measured. On an automatic slot
its size is passed to `acquire` as the need, so the model is reloaded with more context
when tool results have made the conversation grow. Then `fit_messages` shortens whatever
still does not fit and emits `node.trimmed`: first the text earlier tool calls carried (a
file that was written is on disk), then the oldest tool results, the system prompt never.
A fixed slot goes straight to the second step. The room the tool descriptions take is
counted in both steps and in the output budget.

### Turns that do not fit the output budget

A model can write `context - prompt` tokens in one turn, thinking included. When a turn
stops at that limit (`finish_reason: length`):

- **In the middle of a tool call**, the call's arguments are JSON without an end. They
  must never go back into the conversation: LM Studio parses the arguments of every
  earlier call again on each request and answers HTTP 500 "Internal Server Error" for
  ever after. `checked_calls` therefore lets only calls with an id and a JSON object as
  arguments into the conversation. A call that cannot be read is not run; its place is
  taken by `{}` and an error result that says what happened and what to do (for
  `write_file`: write the file in parts, the first with `write_file`, the others with
  `append=true`). It is shown in the session as a failed tool call, and up to three such
  rounds are not charged to the agent's tool budget.
- **While reasoning**, an agent with tools is told to think briefly and goes on with its
  tools while it has retries left; an agent without tools is asked again for its answer.
- **In both cases** the agent's next request asks for half as much context again. An
  automatic slot grants it (through the gateway, so only if it fits); a fixed slot keeps
  the context the user set.

### Models larger than the card

A model whose estimate exceeds the whole card even at the smallest context is loaded as
planned: LM Studio keeps part of it in system RAM by design, everything else is
unloaded first, and no context is tried and retried.

### Without measurements

No NVIDIA GPU means no VRAM reading, and no `lms` CLI means no estimate. The gateway
then falls back to the sizes the team builder planned (`vram_gb` per slot against the
VRAM budget), evicting in the same order.

### End of run and events

Models this run loaded are unloaded at the end, except `keep_loaded` ones; models that
were already in memory are left as they were. Each swap emits `model.switch` events
(`start`, `loading`, `retuning`, `saturated`, `ready`, `failed`) with the context
applied, the context asked for, the VRAM left free and the unload, load and total
times, shown in the session view.

The team builder's resource plan simulates the eviction order ahead of time, and a test
checks that the predicted number of model loads matches what the run does.

## Run lifecycle

`RunManager` executes each run on its own thread.

| Status | Meaning |
|---|---|
| `running` | Executing |
| `waiting_approval` | Paused on an interrupt |
| `completed` | Reached END normally |
| `rejected` | A gate was rejected with no way back |
| `retry_limit` | A loop hit its bound while still failing |
| `failed` | An agent failed, or `max_steps` was reached |
| `cancelled` | Stopped by the user |
| `no_route` | A node had no matching outgoing edge |

Events (`run.started`, `node.started`, `model.switch`, `tool.call`, `node.retry`,
`approval.requested`, `approval.decided`, `node.completed`, `node.failed`,
`run.waiting`, `run.completed`) are stored in `run_event` and published on the event
stream, so a session can be watched live and replayed later. Tool calls and per-agent
model usage are also stored in their own tables.

Graph state is checkpointed to SQLite through `SqliteSaver`. A run that was in progress
when Will It Run closed is marked failed on the next start rather than left "running".

## Tested behaviours

`backend/tests/test_workflows.py` runs real LangGraph graphs against a scripted runtime:
normal completion, agent failure, retry, agent retry limit, loop retry limit, human
approval, rejection (with and without a return path), tool failure, denied and approved
high-risk tools, model load failure, model switching, shared-model execution, parallel
branches joining, step limit, cancellation, truncated reasoning output.

Verified end to end on real hardware: the Documentation template on a 4B model in LM
Studio, where the Technical Reviewer rejected the first draft, the Documentation
Specialist revised it, and the second review approved.
