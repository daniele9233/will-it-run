-- Image studio: one row per picture generated through ComfyUI.
-- The file itself lives under the app's own data folder, so clearing ComfyUI's output
-- does not empty the gallery, and every picture keeps the settings that made it.

CREATE TABLE IF NOT EXISTS image_generation (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    model_key       TEXT NOT NULL,
    template        TEXT NOT NULL,
    prompt          TEXT NOT NULL DEFAULT '',
    negative        TEXT NOT NULL DEFAULT '',
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    seed            INTEGER,
    steps           INTEGER,
    cfg             REAL,
    sampler         TEXT,
    scheduler       TEXT,
    loras           TEXT NOT NULL DEFAULT '[]',
    file_name       TEXT NOT NULL,
    bytes           INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL,
    prompt_id       TEXT,
    status          TEXT NOT NULL DEFAULT 'done'
);

CREATE INDEX IF NOT EXISTS idx_image_generation_created ON image_generation(created_at DESC);
