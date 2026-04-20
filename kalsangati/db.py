"""Database schema, migrations, and connection management.

Kālsangati stores all data in a local SQLite database with native JSON
support.  This module owns the schema, provides a connection factory, and
runs forward-only migrations on open.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

# Default DB lives next to the package in the user's data dir.
_DEFAULT_DB_PATH = Path.home() / ".kalsangati" / "kalsangati.db"

# Schema version.  Bump when a migration is added.
# v1: initial schema.
# v2: Niyam time_blocks migrated from "HH:MM" strings to minutes-since-
#     midnight integers (see _migrate_v2_time_blocks_to_minutes).
SCHEMA_VERSION = 2

# ── Schema DDL ──────────────────────────────────────────────────────────

_SCHEMA_SQL = """\
-- Blueprint schedules (document-style time_blocks).
-- Since v2 each block stores start_min / end_min as int minutes-since-midnight.
CREATE TABLE IF NOT EXISTS niyam (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    is_active   INTEGER DEFAULT 0,
    time_blocks TEXT  -- JSON: {"monday": [{"activity": "...",
                      --   "start_min": 540, "end_min": 660,
                      --   "duration_h": 2.0}, ...], ...}
);

-- Session log (actual lived time — Kālrekhā)
CREATE TABLE IF NOT EXISTS kalrekha (
    id               INTEGER PRIMARY KEY,
    project          TEXT,
    task             TEXT,
    date             TEXT NOT NULL,
    start            TEXT NOT NULL,
    "end"            TEXT NOT NULL,
    duration_min     REAL,
    tz_offset        TEXT,
    source           TEXT,         -- 'csv_import' | 'manual_stopwatch' | 'tracker'
    unplanned        BOOLEAN DEFAULT 0,
    override_reason  TEXT,
    block_classified BOOLEAN DEFAULT 0
);

-- Weekly aggregates (refreshed on ingest / session save)
CREATE TABLE IF NOT EXISTS weekly_aggregates (
    id              INTEGER PRIMARY KEY,
    week_start      TEXT NOT NULL,
    activity        TEXT NOT NULL,
    total_hours     REAL,
    planned_hours   REAL,
    unplanned_hours REAL
);

-- Label converter: raw imported label → canonical activity name
CREATE TABLE IF NOT EXISTS label_mappings (
    id              INTEGER PRIMARY KEY,
    raw_label       TEXT NOT NULL UNIQUE,
    canonical_label TEXT NOT NULL
);

-- Label group hierarchy: canonical label → parent group
CREATE TABLE IF NOT EXISTS label_groups (
    id              INTEGER PRIMARY KEY,
    canonical_label TEXT NOT NULL UNIQUE,
    parent_group    TEXT,
    level           INTEGER
);

-- Projects
CREATE TABLE IF NOT EXISTS projects (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    canonical_activity TEXT NOT NULL,
    color              TEXT,
    notes              TEXT
);

-- Tasks
CREATE TABLE IF NOT EXISTS tasks (
    id                 INTEGER PRIMARY KEY,
    title              TEXT NOT NULL,
    project_id         INTEGER REFERENCES projects(id),
    canonical_activity TEXT NOT NULL,
    estimated_hours    REAL,
    due_date           TEXT,
    status             TEXT DEFAULT 'backlog'
                       CHECK(status IN ('backlog','this_week','in_progress','done')),
    week_assigned      TEXT,
    spilled_from       TEXT,
    override_reason    TEXT,
    notes              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- App settings (key-value)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Internal migration tracker
CREATE TABLE IF NOT EXISTS _migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ── Index DDL ───────────────────────────────────────────────────────────

_INDEX_SQL = """\
CREATE INDEX IF NOT EXISTS idx_kalrekha_date
    ON kalrekha(date);
CREATE INDEX IF NOT EXISTS idx_kalrekha_project
    ON kalrekha(project);
CREATE INDEX IF NOT EXISTS idx_weekly_agg_week
    ON weekly_aggregates(week_start, activity);
CREATE INDEX IF NOT EXISTS idx_label_raw
    ON label_mappings(raw_label);
CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_week
    ON tasks(week_assigned);
CREATE INDEX IF NOT EXISTS idx_tasks_activity
    ON tasks(canonical_activity);
"""

# ── Default settings ────────────────────────────────────────────────────

_DEFAULT_SETTINGS: dict[str, str] = {
    "notify_lead_minutes": "5",
    "notifications_enabled": "true",
    "watched_folder": "",
    "refresh_interval_min": "5",
    "week_start_day": "monday",
}


# ── Connection helpers ──────────────────────────────────────────────────


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Enable WAL mode and foreign keys for performance and safety."""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")


def get_connection(
    db_path: Optional[Path] = None,
    *,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Return a configured SQLite connection.

    Args:
        db_path: Path to the database file.  Uses the default location
            (~/.kalsangati/kalsangati.db) when *None*.
        read_only: Open in read-only mode (URI flag).

    Returns:
        A sqlite3.Connection with row_factory set to sqlite3.Row,
        WAL journal mode, and foreign keys enabled.
    """
    path = db_path or _DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    if read_only:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(path))

    conn.row_factory = sqlite3.Row
    _enable_wal(conn)
    return conn


@contextmanager
def transaction(
    conn: sqlite3.Connection,
) -> Generator[sqlite3.Cursor, None, None]:
    """Context manager that wraps a block in a SAVEPOINT.

    Commits on clean exit; rolls back on exception.  Uses SAVEPOINTs
    so it can nest safely within an existing transaction.

    Args:
        conn: An active database connection.

    Yields:
        A cursor bound to the connection.
    """
    cur = conn.cursor()
    savepoint = f"sp_{id(cur)}"
    cur.execute(f"SAVEPOINT {savepoint}")
    try:
        yield cur
        cur.execute(f"RELEASE {savepoint}")
        conn.commit()
    except Exception:
        cur.execute(f"ROLLBACK TO {savepoint}")
        cur.execute(f"RELEASE {savepoint}")
        raise


# ── Migrations ──────────────────────────────────────────────────────────


def _time_str_to_minutes(time_str: str) -> int:
    """Local HH:MM → minutes helper (avoids circular import with niyam.py)."""
    s = time_str.strip()
    parts = s.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid time string: {time_str!r}")
    hours = int(parts[0])
    minutes = int(parts[1])
    if not (0 <= hours <= 24) or not (0 <= minutes < 60):
        raise ValueError(f"Time out of range: {time_str!r}")
    total = hours * 60 + minutes
    if total > 24 * 60:
        raise ValueError(f"Time out of range: {time_str!r}")
    return total


def _migrate_v2_time_blocks_to_minutes(conn: sqlite3.Connection) -> None:
    """Rewrite every niyam.time_blocks JSON to use minutes-since-midnight.

    Old format (v1):
        {"monday": [{"activity": "...", "start": "09:00",
                     "end": "11:00", "duration_h": 2.0}, ...]}

    New format (v2):
        {"monday": [{"activity": "...", "start_min": 540,
                     "end_min": 660, "duration_h": 2.0}, ...]}

    Rows that already look v2 (contain ``start_min``) are left alone — this
    makes the migration idempotent and safe to replay.
    """
    rows = conn.execute("SELECT id, time_blocks FROM niyam").fetchall()
    for row in rows:
        raw = row["time_blocks"]
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue

        changed = False
        for day, block_list in data.items():
            if not isinstance(block_list, list):
                continue
            for block in block_list:
                if not isinstance(block, dict):
                    continue
                if "start_min" in block and "end_min" in block:
                    continue  # already migrated
                if "start" not in block or "end" not in block:
                    continue  # malformed, skip
                try:
                    block["start_min"] = _time_str_to_minutes(block["start"])
                    block["end_min"] = _time_str_to_minutes(block["end"])
                except (ValueError, TypeError):
                    # Skip malformed block but keep going on the rest.
                    continue
                # Remove legacy fields so the stored JSON is clean v2.
                block.pop("start", None)
                block.pop("end", None)
                changed = True

        if changed:
            new_raw = json.dumps(data, separators=(",", ":"))
            conn.execute(
                "UPDATE niyam SET time_blocks = ? WHERE id = ?",
                (new_raw, row["id"]),
            )


# Registry of version → callable.  Callables take the connection and
# perform the migration under the shared transaction handler.
_MIGRATION_FUNCS: dict[int, Any] = {
    # Version 1 is the initial schema; no migration function needed.
    2: _migrate_v2_time_blocks_to_minutes,
}


# ── Initialization & migration ──────────────────────────────────────────


def _current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0."""
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM _migrations"
        ).fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Run any unapplied migrations in order, up to SCHEMA_VERSION."""
    current = _current_version(conn)

    # v1 baseline: if nothing is recorded, mark schema v1 as applied
    # (tables were just created by _SCHEMA_SQL).
    if current < 1:
        with transaction(conn) as cur:
            cur.execute(
                "INSERT INTO _migrations (version) VALUES (?)", (1,)
            )
        current = 1

    for version in sorted(_MIGRATION_FUNCS):
        if version <= current:
            continue
        fn = _MIGRATION_FUNCS[version]
        with transaction(conn) as cur:
            fn(conn)
            cur.execute(
                "INSERT INTO _migrations (version) VALUES (?)", (version,)
            )


def _seed_defaults(conn: sqlite3.Connection) -> None:
    """Insert default settings if the settings table is empty."""
    existing = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    if existing == 0:
        with transaction(conn) as cur:
            for key, value in _DEFAULT_SETTINGS.items():
                cur.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )


def init_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Create or open the database, apply schema + migrations, seed defaults.

    This is the main entry point for all other modules.  Call once at
    application startup.

    Args:
        db_path: Override the default database location.

    Returns:
        A fully initialized sqlite3.Connection.
    """
    conn = get_connection(db_path)
    conn.executescript(_SCHEMA_SQL)
    conn.executescript(_INDEX_SQL)
    _apply_migrations(conn)
    _seed_defaults(conn)
    return conn


# ── Settings helpers ────────────────────────────────────────────────────


def get_setting(conn: sqlite3.Connection, key: str) -> Optional[str]:
    """Read a single setting value by key.

    Args:
        conn: Database connection.
        key: Setting key name.

    Returns:
        The string value, or None if not found.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a setting.

    Args:
        conn: Database connection.
        key: Setting key name.
        value: New string value.
    """
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ── JSON helpers ────────────────────────────────────────────────────────


def parse_time_blocks(raw: Optional[str]) -> dict[str, list[dict[str, Any]]]:
    """Parse a Niyam time_blocks JSON column into a Python dict.

    Args:
        raw: The JSON string from the time_blocks column, or None.

    Returns:
        A dict mapping day names to lists of block dicts.  Returns an
        empty dict when *raw* is None or empty.
    """
    if not raw:
        return {}
    return json.loads(raw)


def serialize_time_blocks(blocks: dict[str, list[dict[str, Any]]]) -> str:
    """Serialize a time_blocks dict to compact JSON for storage.

    Args:
        blocks: Day-name → list-of-block-dicts mapping.

    Returns:
        A compact JSON string.
    """
    return json.dumps(blocks, separators=(",", ":"))
