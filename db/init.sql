CREATE TABLE IF NOT EXISTS scans (
    id                  TEXT PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    hostname            TEXT,
    macos               TEXT,
    disk_total          BIGINT,
    disk_used           BIGINT,
    disk_free           BIGINT,
    system_data_bytes   BIGINT,
    cleanable_bytes     BIGINT,
    trigger             TEXT NOT NULL DEFAULT 'manual',
    report              JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS cleanup_runs (
    id                  TEXT PRIMARY KEY,
    scan_id             TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    dry_run             BOOLEAN NOT NULL DEFAULT false,
    requested_bytes     BIGINT NOT NULL DEFAULT 0,
    freed_bytes         BIGINT NOT NULL DEFAULT 0,
    items               JSONB NOT NULL DEFAULT '[]'::jsonb,
    errors              JSONB NOT NULL DEFAULT '[]'::jsonb,
    trigger             TEXT NOT NULL DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cleanup_runs_created_at ON cleanup_runs (created_at DESC);
ALTER TABLE scans ADD COLUMN IF NOT EXISTS trigger TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE cleanup_runs ADD COLUMN IF NOT EXISTS trigger TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE cleanup_runs DROP CONSTRAINT IF EXISTS cleanup_runs_scan_id_fkey;

COMMENT ON TABLE scans IS 'DS Mac Cleaner read-only scan history';
COMMENT ON TABLE cleanup_runs IS 'Explicitly confirmed cache cleanup runs';
