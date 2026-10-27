# Arena (`backend/src/agentfit/arena`)

Reliability tests for models, checked by code. Read `docs/ARENA.md` (what it asks, how scores are made, how a run works) and `docs/map/arena.md` (modules, symbols, tests). The page is `desktop/src/pages/Arena.tsx` and `desktop/src/pages/arena/`.

## Rules

- **No model judges.** Every question has a verifier (`verifiers/`), a reference answer and wrong answers that must fail. What no code can judge is "not evaluated" and never enters a score. The one heuristic, recognising «I don't know» in the *impossible* category, shows which phrase it took.
- **The same messages, byte for byte, and the same sampling** for every model. Cloud providers decide their own sampling and the report says so. Cloud models are never members of a quantization group.
- **One run at a time**; models go one at a time through the model gateway that workflows use (it measures free VRAM, loads with the same fixed context, unloads before the next). Models LM Studio already holds, and any cloud model, are used only if the person agrees.
- **A run stores its own questions** (pictures and PDFs included), so it can be read and exported after the bank changed.
- **A reason is a template.** `why("English template {value}", value=...)`, translated when read; the Italian goes in `locales/it_arena.py`. A word inside a value is `tl("Words")`. Names of categories, areas, verdicts: `labels.py`.
- **Exports are inert.** HTML reports contain no `<script` and no external address; text a model wrote is escaped (`export.py`, `answers.py`, tested in `test_arena_api.py`).
- **Model code runs in a separate process** with a time limit (`sandbox.py`); it is not a security boundary. A tool that is missing (Terraform, `psql`, ansible-lint) is reported in the verdict's `method`, never faked.
- Scores carry a 95% bootstrap interval with a fixed seed; the paired comparison of quantizations uses the sign test with Holm's correction (`paired.py`).

## To add or change a question

1. Write it in its category module in `bank/` with a reference answer and wrong answers (`bank/kit.py` has what they share).
2. `cd backend; .venv\Scripts\python.exe -m pytest tests/test_arena_bank.py`. Every question must pass its reference and fail its wrong answers for the canonical seed (42) and five others.
3. Bump `SUITE_VERSION` in `arena/__init__.py` and pin the new hash the failing test prints. Results of two versions are not comparable.
4. Add the Italian of every new `why(...)` to `locales/it_arena.py`; `python scripts/list_core_strings.py` lists what is missing.

## To add a category

1. Write `bank/<name>.py` with `build(seed)` returning every question of the category (model it on `bank/general.py`; `bank/kit.py` has the helpers).
2. Register it in `bank/__init__.py`: import it and add it to `MODULES` (modules are imported by name so that a frozen build finds them).
3. Add a `CategoryInfo(id, group, area, (quick, standard, full))` to `_ALL` in `categories.py` (`scored=False` for a category nobody scores, `needs=("vision",)` for one a model may be unable to take). A new radar `area` also goes in `AREAS`.
4. Name it: the English in `labels.py` (`CATEGORY_LABELS`), the Italian in `locales/it_arena.py`.
5. Bump `SUITE_VERSION`, then `tests/test_arena_bank.py` pins the pack totals (48 / 115 / 192) and the hash: update both. `tests/test_arena_api.py` also counts the categories (18) and the version (lines 73 and 103).
6. The interface: `CATEGORY_LABEL` in `desktop/src/lib/arena.ts` (`geography: t("Geography")`), the line `Geography ||| Geografia` in `desktop/src/locales/it.txt` (then `python -X utf8 scripts/build_locale.py`), and the list of ids in `desktop/src/lib/arena.test.ts` ("every category of the bank has a name"): that test does not fail if you forget it, and the label falls back to the raw id.
7. `docs/ARENA.md`: "Eighteen categories" and the per-category table.

## Tests

`test_arena_bank.py` (the bank), `test_arena_basics.py`, `test_arena_scoring.py`, `test_arena_verifiers_text.py` and `_data.py`, `test_arena_exec.py`, `test_arena_sql.py`, `test_arena_iac.py`, `test_arena_assets.py`, `test_arena_quants.py`, `test_arena_runner.py` (a run on a fake runtime, `tests/arena_fakes.py`), `test_arena_api.py` (HTTP and the exports).
