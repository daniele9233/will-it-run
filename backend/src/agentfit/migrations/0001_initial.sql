-- Generic JSON document cache (hardware profile, catalog metadata, UI state).
CREATE TABLE kv (
    namespace  TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);

CREATE TABLE hardware_profile (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    profile     TEXT NOT NULL
);

CREATE TABLE model_metadata (
    repo        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    publisher   TEXT NOT NULL,
    alignment   TEXT NOT NULL,
    params_b    REAL,
    source      TEXT NOT NULL,
    data        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);
CREATE INDEX idx_model_metadata_alignment ON model_metadata(alignment);

CREATE TABLE installed_model (
    model_key   TEXT PRIMARY KEY,
    repo        TEXT,
    filename    TEXT,
    path        TEXT,
    quant       TEXT,
    size_bytes  INTEGER,
    data        TEXT NOT NULL,
    seen_at     TEXT NOT NULL
);

CREATE TABLE benchmark (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key           TEXT NOT NULL,
    quant               TEXT,
    context_length      INTEGER,
    load_time_s         REAL,
    ttft_ms             REAL,
    prompt_tps          REAL,
    generation_tps      REAL,
    vram_used_mb        REAL,
    ram_used_mb         REAL,
    gpu_util_percent    REAL,
    output_tokens       INTEGER,
    hardware_fingerprint TEXT,
    details             TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX idx_benchmark_model ON benchmark(model_key, created_at);

CREATE TABLE download (
    id            TEXT PRIMARY KEY,
    repo          TEXT NOT NULL,
    filename      TEXT NOT NULL,
    quant         TEXT,
    revision      TEXT,
    sha256        TEXT,
    size_bytes    INTEGER,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    backend       TEXT NOT NULL,
    target_dir    TEXT NOT NULL,
    status        TEXT NOT NULL,
    error         TEXT,
    external_job_id TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE workflow (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    source      TEXT NOT NULL,
    yaml        TEXT NOT NULL,
    layout      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE team (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    workflow_id TEXT,
    plan        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE workflow_run (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    project     TEXT NOT NULL DEFAULT 'Default',
    title       TEXT NOT NULL,
    task        TEXT NOT NULL,
    status      TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX idx_run_started ON workflow_run(started_at);

CREATE TABLE run_event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    seq       INTEGER NOT NULL,
    type      TEXT NOT NULL,
    payload   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_run_event ON run_event(run_id, seq);

CREATE TABLE model_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    agent_id    TEXT NOT NULL,
    model_key   TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    tps         REAL,
    duration_s  REAL,
    created_at  TEXT NOT NULL
);

CREATE TABLE tool_call (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    agent_id    TEXT NOT NULL,
    tool        TEXT NOT NULL,
    arguments   TEXT NOT NULL,
    status      TEXT NOT NULL,
    output      TEXT,
    risk        TEXT NOT NULL,
    duration_s  REAL,
    created_at  TEXT NOT NULL
);

CREATE TABLE approval (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    node_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    payload     TEXT,
    decision    TEXT,
    comment     TEXT,
    requested_at TEXT NOT NULL,
    decided_at  TEXT
);
