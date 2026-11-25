-- JalDrishti edge store-and-forward buffer (one SQLite file per node).
-- Pragmas set by edge/buffer.py: WAL, synchronous=FULL, busy_timeout=5000.

CREATE TABLE IF NOT EXISTS telemetry_buffer (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms         INTEGER NOT NULL UNIQUE,
    payload       TEXT    NOT NULL,
    breach        INTEGER NOT NULL DEFAULT 0,
    synced        INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    first_send_ms INTEGER,
    synced_ms     INTEGER,
    batch_id      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pending ON telemetry_buffer (synced, ts_ms);

CREATE TABLE IF NOT EXISTS eviction_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at_ms       INTEGER NOT NULL,
    tier        TEXT    NOT NULL,
    rows        INTEGER NOT NULL,
    oldest_ts   INTEGER,
    newest_ts   INTEGER,
    depth_after INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS node_state (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);