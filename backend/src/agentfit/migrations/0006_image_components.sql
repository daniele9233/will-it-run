-- Image components: one row per file (or custom node) a model needs.
-- The catalog says what a model is made of. This table says what is actually on disk,
-- where it came from and what it was verified against. Files are matched on disk by
-- name first, so a row that is wrong is corrected rather than trusted.

CREATE TABLE IF NOT EXISTS image_component (
    id           TEXT PRIMARY KEY,
    role         TEXT NOT NULL DEFAULT '',
    file_name    TEXT NOT NULL DEFAULT '',
    folder       TEXT NOT NULL DEFAULT '',
    path         TEXT NOT NULL DEFAULT '',
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    sha256       TEXT,
    status       TEXT NOT NULL DEFAULT 'missing',
    source_json  TEXT NOT NULL DEFAULT '{}',
    licence      TEXT NOT NULL DEFAULT '',
    installed_at TEXT,
    verified_at  TEXT,
    error        TEXT
);
