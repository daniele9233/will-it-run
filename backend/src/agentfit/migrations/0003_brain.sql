-- Project Brain: the permanent memory of each project (see agentfit.brain).
-- A project is a folder of the user's or a GitHub repository.
CREATE TABLE brain_project (
    id         TEXT PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    path       TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One thing the project knows. A memory that was replaced is kept, with the id of what replaced it.
CREATE TABLE brain_memory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL REFERENCES brain_project(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL,
    files         TEXT NOT NULL DEFAULT '[]',
    tags          TEXT NOT NULL DEFAULT '[]',
    source        TEXT NOT NULL,
    agent         TEXT NOT NULL DEFAULT '',
    run_id        TEXT,
    slot          TEXT,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    superseded_by INTEGER,
    uses          INTEGER NOT NULL DEFAULT 0,
    embedding     BLOB,
    embed_model   TEXT
);
CREATE INDEX idx_brain_memory_project ON brain_memory(project_id, kind, updated_at);
CREATE INDEX idx_brain_memory_hash ON brain_memory(project_id, content_hash);
CREATE UNIQUE INDEX idx_brain_memory_slot ON brain_memory(project_id, slot) WHERE slot IS NOT NULL;

-- What memories are about: files and concepts.
CREATE TABLE brain_entity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES brain_project(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    UNIQUE (project_id, kind, name)
);

-- The knowledge graph. A node is "m:<memory id>" or "e:<entity id>".
CREATE TABLE brain_edge (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES brain_project(id) ON DELETE CASCADE,
    source     TEXT NOT NULL,
    target     TEXT NOT NULL,
    relation   TEXT NOT NULL,
    origin     TEXT NOT NULL DEFAULT 'memory',
    UNIQUE (project_id, source, target, relation)
);
CREATE INDEX idx_brain_edge_source ON brain_edge(project_id, source);
CREATE INDEX idx_brain_edge_target ON brain_edge(project_id, target);
