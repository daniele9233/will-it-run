-- Arena: runs that put models through questions with a verifiable answer (see agentfit.arena).
-- A run keeps its own copy of the questions it asked, so it stays readable and reproducible after the question bank changes.
CREATE TABLE arena_run (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    suite_id      TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    suite_hash    TEXT NOT NULL,
    bank_hash     TEXT NOT NULL,
    seed          INTEGER NOT NULL,
    preset        TEXT,
    categories    TEXT NOT NULL DEFAULT '[]',
    settings      TEXT NOT NULL DEFAULT '{}',
    environment   TEXT NOT NULL DEFAULT '{}',
    total         INTEGER NOT NULL DEFAULT 0,
    done          INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_arena_run_created ON arena_run(created_at);

-- The questions of a run, with their pictures and files, in the order they were asked.
CREATE TABLE arena_question (
    run_id      TEXT NOT NULL REFERENCES arena_run(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    position    INTEGER NOT NULL,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    tier        INTEGER NOT NULL,
    difficulty  INTEGER NOT NULL,
    data        TEXT NOT NULL,
    PRIMARY KEY (run_id, question_id)
);

CREATE TABLE arena_model (
    run_id         TEXT NOT NULL REFERENCES arena_run(id) ON DELETE CASCADE,
    model_key      TEXT NOT NULL,
    position       INTEGER NOT NULL,
    label          TEXT NOT NULL,
    cloud          INTEGER NOT NULL DEFAULT 0,
    vision         INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'pending',
    started_at     TEXT,
    finished_at    TEXT,
    load_seconds   REAL,
    context_asked  INTEGER,
    context_granted INTEGER,
    error          TEXT,
    info           TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, model_key)
);

-- One answer: what the model said, how the verifier judged it, and how long it took.
CREATE TABLE arena_result (
    run_id        TEXT NOT NULL REFERENCES arena_run(id) ON DELETE CASCADE,
    model_key     TEXT NOT NULL,
    question_id   TEXT NOT NULL,
    repeat        INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    score         REAL,
    kind          TEXT,
    method        TEXT,
    verdict       TEXT NOT NULL DEFAULT '{}',
    answer        TEXT NOT NULL DEFAULT '',
    raw           TEXT NOT NULL DEFAULT '',
    reasoning     TEXT NOT NULL DEFAULT '',
    tool_calls    TEXT NOT NULL DEFAULT '[]',
    finish_reason TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    ttft_s        REAL,
    duration_s    REAL,
    tps           REAL,
    error         TEXT,
    manual        TEXT,
    manual_note   TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (run_id, model_key, question_id, repeat)
);
CREATE INDEX idx_arena_result_question ON arena_result(run_id, question_id);
