-- A human-readable remark on a finished download, e.g. how the file was linked into
-- LM Studio's models folder when it was saved somewhere else.
ALTER TABLE download ADD COLUMN note TEXT;
