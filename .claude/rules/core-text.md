---
paths:
  - "backend/src/agentfit/**/*.py"
---

# Text in the Python core

- Text the owner reads (reasons, verdicts, errors shown in the UI) is `tr("English text {n}", n=3)`, with the pair added to `backend/src/agentfit/locales/it.py`; format specs such as `{gb:.1f}` must be identical on both sides. `python scripts/list_core_strings.py` lists what is untranslated and `tests/test_i18n.py` fails on it. The Arena writes reasons as `why("...")` and `tl("...")` with the Italian in `locales/it_arena.py`.
- Text sent to a model (roles, prompts, tool descriptions, workflow content), logs and developer-facing exceptions stay English and are not passed through `tr()`. Log with `logging_setup.log_event`, and never log a key or a token.
- An unknown value is `None`, not a default that looks like data. A choice made for the person returns its reasons.
- Type hints on everything, Pydantic models at boundaries, `ruff` clean (run it from `backend/`).
