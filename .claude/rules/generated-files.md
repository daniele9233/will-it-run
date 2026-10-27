---
paths:
  - "docs/map/**"
  - "desktop/src/locales/it.ts"
  - ".codegraph/**"
---

# Generated files: do not edit them

| File | Made by | Source of truth |
|---|---|---|
| `docs/map/*.md` | `python -X utf8 scripts/build_context.py` | the code itself (CI runs it with `--check`) |
| `desktop/src/locales/it.ts` | `python -X utf8 scripts/build_locale.py` | `desktop/src/locales/it.txt` |
| `.codegraph/` | `codegraph sync` | the code; not in git |

Change the source and run the generator. A hand edit is overwritten by the next run and fails CI. A Claude Code hook refuses the edit.

The map lists the files git tracks or has staged, so a brand new file shows up after `git add <file>` and a regeneration. The git pre-commit hook regenerates the map and adds it to the commit; the Claude Code session start regenerates it too.
