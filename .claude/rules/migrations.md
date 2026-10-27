---
paths:
  - "backend/src/agentfit/migrations/**"
---

# Database migrations

- Forward-only and numbered: `NNNN_name.sql`, applied in order inside a transaction and recorded in `schema_migrations` (`backend/src/agentfit/db.py`). The next number is the highest one here plus one.
- **A migration that was committed is never edited**: a database somewhere has already run it. Fix or extend with a new file. A Claude Code hook asks before it lets an edit through.
- Write `CREATE TABLE IF NOT EXISTS`; add columns with `ALTER TABLE` and give every new column a default, because rows already exist.
- Put a `--` comment directly above each `CREATE TABLE` saying what it is for: the map copies it into `docs/map/db.md`.
- After adding one: run `python -X utf8 scripts/build_context.py`, add the table to "Data model" in `docs/ARCHITECTURE.md`, and add a test (tests use `Database(":memory:")`, which applies every migration).
