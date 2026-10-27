---
paths:
  - "backend/tests/**"
---

# Backend tests

- A test never touches LM Studio, the network, a GPU, ComfyUI or the owner's data. `conftest.py` points `AGENTFIT_HOME` at a temp folder; hardware comes from `tests/fixtures`, LM Studio from `httpx.MockTransport`, a runtime from `tests/fakes.py` (`FakeRuntime`), the Arena from `tests/arena_fakes.py`. A test that needs `git` makes its own repository in `tmp_path`.
- Name a test as a sentence that says what is true: `test_a_failed_check_that_later_passes_is_an_error_with_its_fix`. One behaviour per test.
- Run one file with `cd backend; .venv\Scripts\python.exe -m pytest tests/test_x.py`; `python scripts/check.py` picks the files that import what you changed.
- A test for code in `scripts/` loads the script by path (see `test_agent_hooks.py`, `test_context_map.py`); `scripts/` is not a package.
- Do not make a test pass by loosening what it checks. If a test disagrees with the code, decide which one is wrong and say why.
