# Workflows (`backend/src/agentfit/workflows`)

A workflow is a graph of agents, human approval gates and conditional edges, written in YAML, validated, compiled to LangGraph and run by a run manager. Read `docs/WORKFLOWS.md` (the DSL, validation, templates, verification workflows, tools, several models in one workflow) and `docs/LANGGRAPH.md` (how a definition becomes a running graph); `docs/map/workflows.md` lists modules and tests. Teams (`../teams`) build workflows from roles; the two import each other (a known cycle, leave it).

## Pieces

`dsl.py` (the YAML model), `validator.py`, `conditions.py`, `compiler.py` (to LangGraph), `runner.py` (`RunManager`: threads, events, resume), `model_manager.py` (the gateway: which model is in VRAM, what to unload, the context from free VRAM), `tools.py` (file, PowerShell, git, test and desktop tools, and how each call is classified: `Risk`, `classify_powershell`, `classify_git`), `checks.py` (steps that are code), `schemas.py` (structured outputs), `sources.py`, `store.py`, `templates/`.

## Rules

- **Use only LangGraph and LM Studio behaviour that is documented**, and list new usage in `docs/LANGGRAPH.md` or `docs/LM_STUDIO.md`.
- **Models go through the gateway.** Never load or unload a model directly: the gateway keeps the GPU from being oversubscribed, picks the context, and learns the real cost of a load. The Arena uses the same gateway.
- **Human approval** pauses a run before anything high-risk (deleting or overwriting files, installing software, pushing, changing remote infrastructure, driving the desktop). Do not weaken the patterns in `tools.py` (`classify_powershell`, `classify_git`) or the table in `docs/SECURITY.md`; a chain is as risky as its riskiest part.
- **Verification workflows** read only what their `reads` list names, check with code, and leave nothing in the project memory. Keep that isolation.
- **Text sent to models is never translated** (roles, prompts, tool descriptions, workflow content). Only a template's name and description are translated in the list.
- Runs and events are persisted (`workflow_run`, `run_event`, `tool_call`, `approval`); checkpoints use a second SQLite file.

## Tests

`test_workflows.py` (conditions, DSL, validator, tools and risk, LangGraph paths on `tests/fakes.py::FakeRuntime`), `test_gateway.py` (several models on a simulated GPU), `test_verification.py`, `test_tool_loop_guards.py`, `test_model_quirks.py`, `test_streaming.py`, `test_followups.py`, `test_teams.py`.
