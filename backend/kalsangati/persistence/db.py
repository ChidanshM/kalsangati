"""Database schema, migrations, and connection management.

Kālsangati stores all data in a local SQLite database with native JSON
support.  This module owns the schema, provides a connection factory, and
runs forward-only migrations on open.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

# Default DB lives next to the package in the user's data dir.
_DEFAULT_DB_PATH = Path.home() / ".kalsangati" / "kalsangati.db"

# Schema version.  Bump when a migration is added.
# v1: initial schema.
# v2: Niyam time_blocks migrated from "HH:MM" strings to minutes-since-
#     midnight integers (see _migrate_v2_time_blocks_to_minutes).
# v3: tasks gained scheduled_day / scheduled_start_min / scheduled_end_min /
#     scheduled_week_start (all NULL for backlog tasks, all populated for
#     scheduled tasks — enforced via CHECK).  Status enum gained 'on_hold'.
#     New task_events history table (see _migrate_v3_task_schedule_and_events).
# v4: tasks gained parent_id / slug / notes_path / deleted_at / sort_order,
#     and the status enum gained 'dropped'.  A trigger rejects any
#     parent_id change that would make a task its own ancestor (see
#     _migrate_v4_task_hierarchy).  Every new column is dormant at
#     introduction — no service writes them yet.  The legacy `notes`
#     column is retained: notes_path supersedes it, but core/tasks.py
#     still writes it, so removing it belongs with the unit that moves
#     task bodies to disk.
SCHEMA_VERSION = 4

# Maximum length of a generated slug.  Slugs are a filename readability
# hint, not an identifier — the row id is the identifier — so truncation
# is safe.
_SLUG_MAX_LEN = 42

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
-- v3: added scheduled_day / scheduled_start_min / scheduled_end_min /
--     scheduled_week_start (all NULL = backlog, all populated = scheduled);
--     status enum gained 'on_hold'.
-- v4: added parent_id (self-referential, NULL = root), slug (filename hint,
--     fixed at creation), notes_path (override; NULL = derive), deleted_at
--     (soft delete; NULL = live), sort_order (manual sibling ordering);
--     status enum gained 'dropped'.  `notes` is retained for now —
--     notes_path supersedes it, but core/tasks.py still writes `notes`,
--     so dropping it is a behaviour change and belongs elsewhere.
CREATE TABLE IF NOT EXISTS tasks (
    id                   INTEGER PRIMARY KEY,
    title                TEXT NOT NULL,
    project_id           INTEGER REFERENCES projects(id),
    canonical_activity   TEXT NOT NULL,
    estimated_hours      REAL,
    due_date             TEXT,
    status               TEXT DEFAULT 'backlog'
                         CHECK(status IN ('backlog','this_week',
                                          'in_progress','on_hold',
                                          'done','dropped')),
    week_assigned        TEXT,
    spilled_from         TEXT,
    override_reason      TEXT,
    notes                TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    scheduled_day        TEXT,
    scheduled_start_min  INTEGER,
    scheduled_end_min    INTEGER,
    scheduled_week_start TEXT,
    parent_id            INTEGER REFERENCES tasks(id),
    slug                 TEXT,
    notes_path           TEXT,
    deleted_at           TEXT,
    sort_order           REAL NOT NULL DEFAULT 0,
    CHECK (
        (scheduled_day IS NULL
         AND scheduled_start_min IS NULL
         AND scheduled_end_min IS NULL
         AND scheduled_week_start IS NULL)
        OR
        (scheduled_day IN ('monday','tuesday','wednesday','thursday',
                           'friday','saturday','sunday')
         AND scheduled_start_min IS NOT NULL
         AND scheduled_start_min >= 0
         AND scheduled_start_min < 1440
         AND scheduled_end_min IS NOT NULL
         AND scheduled_end_min > scheduled_start_min
         AND scheduled_end_min <= 1440
         AND scheduled_week_start IS NOT NULL)
    )
);

-- Task event history (v3).  Append-only audit trail of task lifecycle
-- events: created, assigned, reassigned, unscheduled, on_hold, resumed,
-- ended, spilled.  scheduled_* columns are a snapshot of the task's
-- schedule at the time of the event.  ON DELETE CASCADE: events vanish
-- with their task.
CREATE TABLE IF NOT EXISTS task_events (
    id                  INTEGER PRIMARY KEY,
    task_id             INTEGER NOT NULL
                        REFERENCES tasks(id) ON DELETE CASCADE,
    event_type          TEXT NOT NULL,
    event_at            TEXT NOT NULL,
    scheduled_day       TEXT,
    scheduled_start_min INTEGER,
    scheduled_end_min   INTEGER,
    notes               TEXT
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
CREATE INDEX IF NOT EXISTS idx_task_events_task_id
    ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_task_events_event_at
    ON task_events(event_at);
CREATE INDEX IF NOT EXISTS idx_tasks_parent
    ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_deleted
    ON tasks(deleted_at);
"""

# ── Trigger DDL ────────────────────────────────────────────────

# Applied AFTER migrations, never before — see init_db.  A trigger body is
# validated against the table as it exists at CREATE time, so installing
# this against a pre-v4 `tasks` would fail on NEW.parent_id.  Dropping a
# table also drops its triggers, so the v4 rebuild would destroy it anyway.
#
# Why a trigger rather than a service check alone: a cycle cannot be seen
# from any single row (every parent/child pair stays locally consistent),
# and any query that walks the tree hangs rather than fails.  Enforcement
# no code path can bypass is worth the invisibility.
#
# UNION, not UNION ALL: UNION deduplicates, so the walk terminates even if
# a cycle somehow already exists.  With UNION ALL the guard itself would
# hang — exactly the failure it is here to prevent.
#
# RAISE(ABORT) undoes the statement and returns an error while leaving the
# transaction open, so transaction()'s ROLLBACK TO SAVEPOINT still behaves
# normally.  RAISE(ROLLBACK) would tear down the transaction underneath
# the context manager.
#
# BEFORE UPDATE OF parent_id only, not INSERT: a new row cannot be its own
# ancestor, because its id does not yet appear in any chain.
_TRIGGER_SQL = """\
CREATE TRIGGER IF NOT EXISTS trg_tasks_no_cycle
BEFORE UPDATE OF parent_id ON tasks
WHEN NEW.parent_id IS NOT NULL AND EXISTS (
    WITH RECURSIVE up(id) AS (
        SELECT NEW.parent_id
        UNION
        SELECT t.parent_id FROM tasks t JOIN up ON t.id = up.id
        WHERE t.parent_id IS NOT NULL
    )
    SELECT 1 FROM up WHERE id = NEW.id
)
BEGIN
    SELECT RAISE(ABORT, 'task hierarchy cycle');
END;
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
    db_path: Path | None = None,
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
    *,
    immediate: bool = True,
) -> Generator[sqlite3.Cursor, None, None]:
    """Wrap a block in a transaction, taking the write lock upfront.

    Commits on clean exit; rolls back on exception.  Nests safely.

    **Why ``immediate`` defaults to True.**  Every service in this
    codebase reads before it writes — an existence check, a no-op
    short-circuit — and in WAL mode a transaction's view of the database
    is fixed at its *first read*.  A deferred transaction therefore
    validates against a snapshot, asks for the write lock later, and
    discovers at that point that another connection has committed in
    between.  SQLite refuses the write (``SQLITE_BUSY_SNAPSHOT``) rather
    than silently discarding the other change.

    ``PRAGMA busy_timeout`` does not help.  It answers "the lock is held
    right now, wait"; this is "your view expired", and waiting cannot
    un-expire it — the transaction has to roll back and start over.
    Taking the write lock *before* the first read means nothing else can
    commit underneath, so the snapshot cannot go stale.

    The cost is that writers serialise and wait on each other.  For a
    local application with at most a desktop process and an embedded API
    daemon, that is the correct trade.

    Defaulting to True is deliberate: a caller opts *out*.  A new service
    that forgot to opt in would be silently exposed, and the exposure is
    invisible until a second writer exists — which is exactly when nobody
    is looking for it.  Pass ``immediate=False`` for a read-only block
    that should not hold the write lock.

    **Why the outermost / nested split.**  There is no
    ``SAVEPOINT IMMEDIATE``; lock mode is a property of ``BEGIN``.  So
    the outermost transaction issues ``BEGIN IMMEDIATE`` and the nested
    ones issue ``SAVEPOINT``.  Nesting needs no lock of its own — the
    outer transaction already holds it — so ``immediate`` is ignored
    there rather than being an error.

    **A note on the driver.**  This connection uses Python's default
    ``isolation_level``, so ``sqlite3`` inserts its own ``BEGIN`` before
    DML when no transaction is open.  ``conn.in_transaction`` is what
    distinguishes outermost from nested, and issuing ``BEGIN IMMEDIATE``
    ourselves turns autocommit off, after which the driver adds nothing.
    That interaction is load-bearing and was previously undocumented.

    Args:
        conn: An active database connection.
        immediate: Take the write lock before the first read.  Leave at
            the default for anything that writes.

    Yields:
        A cursor bound to the connection.
    """
    cur = conn.cursor()

    if immediate and not conn.in_transaction:
        cur.execute("BEGIN IMMEDIATE")
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return

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
        for _day, block_list in data.items():
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


def _migrate_v3_task_schedule_and_events(conn: sqlite3.Connection) -> None:
    """Add scheduled_* columns + expanded status enum to tasks.

    SQLite cannot ``ALTER TABLE ... ADD CHECK`` or change a column list
    that participates in a CHECK constraint — so we do the standard
    12-step table rebuild:

    1. Create ``tasks_new`` with the v3 schema (new columns + new CHECK +
       expanded status enum).
    2. Copy all rows from ``tasks`` to ``tasks_new`` (legacy columns
       only; new scheduled_* columns default to NULL).
    3. Drop old ``tasks``.
    4. Rename ``tasks_new`` → ``tasks``.
    5. Recreate indexes (``idx_tasks_status`` / ``idx_tasks_week`` /
       ``idx_tasks_activity``).

    ``task_events`` is created by the top-level ``_SCHEMA_SQL`` via
    ``CREATE TABLE IF NOT EXISTS``, so this migration does nothing for
    it directly.

    Idempotency: if ``tasks`` already has the ``scheduled_day`` column,
    the rebuild is skipped.

    FK safety: ``PRAGMA foreign_keys`` must be OFF during the rebuild —
    otherwise any FK referencing ``tasks(id)`` (notably ``task_events``)
    would fail on DROP TABLE.  The pragma toggle is handled one level up
    in ``_apply_migrations`` because ``PRAGMA foreign_keys`` is a no-op
    inside a transaction.  Row ids are preserved through the rebuild so
    existing FK references remain valid once FK enforcement is restored.
    """
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "scheduled_day" in cols:
        return  # already v3

    # IMPORTANT: use individual conn.execute() calls rather than
    # conn.executescript().  executescript() issues an implicit COMMIT
    # before running its body — which would dissolve the outer SAVEPOINT
    # that _apply_migrations has opened around this migration.
    # execute() with DDL under the default isolation_level leaves the
    # savepoint intact.

    conn.execute(
        """
        CREATE TABLE tasks_new (
            id                   INTEGER PRIMARY KEY,
            title                TEXT NOT NULL,
            project_id           INTEGER REFERENCES projects(id),
            canonical_activity   TEXT NOT NULL,
            estimated_hours      REAL,
            due_date             TEXT,
            status               TEXT DEFAULT 'backlog'
                                 CHECK(status IN ('backlog','this_week',
                                                  'in_progress','on_hold',
                                                  'done')),
            week_assigned        TEXT,
            spilled_from         TEXT,
            override_reason      TEXT,
            notes                TEXT,
            created_at           TEXT NOT NULL DEFAULT (datetime('now')),
            scheduled_day        TEXT,
            scheduled_start_min  INTEGER,
            scheduled_end_min    INTEGER,
            scheduled_week_start TEXT,
            CHECK (
                (scheduled_day IS NULL
                 AND scheduled_start_min IS NULL
                 AND scheduled_end_min IS NULL
                 AND scheduled_week_start IS NULL)
                OR
                (scheduled_day IN ('monday','tuesday','wednesday','thursday',
                                   'friday','saturday','sunday')
                 AND scheduled_start_min IS NOT NULL
                 AND scheduled_start_min >= 0
                 AND scheduled_start_min < 1440
                 AND scheduled_end_min IS NOT NULL
                 AND scheduled_end_min > scheduled_start_min
                 AND scheduled_end_min <= 1440
                 AND scheduled_week_start IS NOT NULL)
            )
        )
        """
    )

    conn.execute(
        """
        INSERT INTO tasks_new (
            id, title, project_id, canonical_activity, estimated_hours,
            due_date, status, week_assigned, spilled_from,
            override_reason, notes, created_at
        )
        SELECT
            id, title, project_id, canonical_activity, estimated_hours,
            due_date, status, week_assigned, spilled_from,
            override_reason, notes, created_at
        FROM tasks
        """
    )

    conn.execute("DROP TABLE tasks")
    conn.execute("ALTER TABLE tasks_new RENAME TO tasks")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_week ON tasks(week_assigned)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_activity "
        "ON tasks(canonical_activity)"
    )


def _slugify(title: str, task_id: int) -> str:
    """Local title → filename-safe slug helper.

    **Deliberately duplicates** the equivalent helper that will live in
    ``core/tasks.py``.  ``persistence/`` imports nothing internal — that
    leaf invariant is what the layer split rests on — and the same
    reasoning already governs ``_time_str_to_minutes`` above.

    Divergence between the two is acceptable and expected: a migration is
    frozen history, so it must keep producing what it produced on the day
    it ran, while the core helper is free to evolve.

    Non-ASCII characters are dropped rather than transliterated, so a
    title with no ASCII at all yields an empty slug and falls back to
    ``task-{id}``.  The slug is a readability hint for filenames; the row
    id is the identifier, and the real title lives in the ``title``
    column.

    Args:
        title: The task title, any content.
        task_id: Row id, used only for the fallback.

    Returns:
        A lowercase hyphenated slug of at most ``_SLUG_MAX_LEN``
        characters, never empty.
    """
    ascii_only = title.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_only).strip("-").lower()
    # rstrip again: truncation can land on a hyphen.
    slug = slug[:_SLUG_MAX_LEN].rstrip("-")
    return slug or f"task-{task_id}"


def _migrate_v4_task_hierarchy(conn: sqlite3.Connection) -> None:
    """Add hierarchy, slug, notes-path, soft-delete and ordering columns.

    Same 12-step rebuild as v3, and for the same reason: SQLite cannot
    change a CHECK constraint in place, and the status enum gains
    ``'dropped'`` here.

    Changes:

    * ``parent_id`` — self-referential FK; NULL means the task is a root.
    * ``slug`` — nullable; backfilled for existing rows from their titles.
      Nullable on purpose: every column here is dormant, and
      ``core/tasks.py::create`` does not yet supply one, so NOT NULL would
      break task creation — a behaviour change this unit must not make.
      It tightens to NOT NULL when the creating service populates it.
    * ``notes_path`` — override for the derived Markdown path.
    * ``deleted_at`` — soft deletion; NULL means live.
    * ``sort_order`` — REAL, seeded from the row id so existing rows come
      out oldest-first.
    * status CHECK gains ``'dropped'``.

    ``notes`` is **retained**.  ``notes_path`` supersedes it, but
    ``core/tasks.py`` still inserts and updates ``notes``, so removing the
    column would change behaviour and break existing tests — which is
    exactly what a migration-only unit must not do.  It goes when task
    bodies move to disk and that module changes anyway.

    ``sort_order`` is declared ``DEFAULT 0`` rather than defaulting to the
    row id: SQLite requires DEFAULT to be a constant expression, so it
    cannot reference another column.  The seeding happens in a follow-up
    UPDATE below.  New rows are the creating service's responsibility.

    Idempotency: if ``tasks`` already has ``parent_id``, the rebuild is
    skipped.

    FK safety: ``PRAGMA foreign_keys`` must be OFF during the rebuild, so
    that ``task_events``' reference to ``tasks(id)`` survives the DROP.
    Handled a level up in ``_apply_migrations`` via
    ``_MIGRATIONS_NEEDING_FK_OFF``, because the pragma is a no-op inside
    a transaction.  Row ids are preserved, so those references remain
    valid once enforcement is restored.

    The cycle trigger is **not** created here.  Dropping ``tasks`` drops
    its triggers, and a fresh database skips this function entirely, so
    the trigger is installed from ``_TRIGGER_SQL`` after migrations run.
    """
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "parent_id" in cols:
        return  # already v4

    # Individual execute() calls, never executescript() — see the note in
    # _migrate_v3_task_schedule_and_events.
    conn.execute(
        """
        CREATE TABLE tasks_new (
            id                   INTEGER PRIMARY KEY,
            title                TEXT NOT NULL,
            project_id           INTEGER REFERENCES projects(id),
            canonical_activity   TEXT NOT NULL,
            estimated_hours      REAL,
            due_date             TEXT,
            status               TEXT DEFAULT 'backlog'
                                 CHECK(status IN ('backlog','this_week',
                                                  'in_progress','on_hold',
                                                  'done','dropped')),
            week_assigned        TEXT,
            spilled_from         TEXT,
            override_reason      TEXT,
            notes                TEXT,
            created_at           TEXT NOT NULL DEFAULT (datetime('now')),
            scheduled_day        TEXT,
            scheduled_start_min  INTEGER,
            scheduled_end_min    INTEGER,
            scheduled_week_start TEXT,
            parent_id            INTEGER REFERENCES tasks(id),
            slug                 TEXT,
            notes_path           TEXT,
            deleted_at           TEXT,
            sort_order           REAL NOT NULL DEFAULT 0,
            CHECK (
                (scheduled_day IS NULL
                 AND scheduled_start_min IS NULL
                 AND scheduled_end_min IS NULL
                 AND scheduled_week_start IS NULL)
                OR
                (scheduled_day IN ('monday','tuesday','wednesday','thursday',
                                   'friday','saturday','sunday')
                 AND scheduled_start_min IS NOT NULL
                 AND scheduled_start_min >= 0
                 AND scheduled_start_min < 1440
                 AND scheduled_end_min IS NOT NULL
                 AND scheduled_end_min > scheduled_start_min
                 AND scheduled_end_min <= 1440
                 AND scheduled_week_start IS NOT NULL)
            )
        )
        """
    )

    # sort_order takes the row id so existing rows keep creation order.
    # slug is left NULL by the INSERT and backfilled immediately below.
    conn.execute(
        """
        INSERT INTO tasks_new (
            id, title, project_id, canonical_activity, estimated_hours,
            due_date, status, week_assigned, spilled_from,
            override_reason, notes, created_at, scheduled_day,
            scheduled_start_min, scheduled_end_min, scheduled_week_start,
            sort_order
        )
        SELECT
            id, title, project_id, canonical_activity, estimated_hours,
            due_date, status, week_assigned, spilled_from,
            override_reason, notes, created_at, scheduled_day,
            scheduled_start_min, scheduled_end_min, scheduled_week_start,
            id
        FROM tasks
        """
    )

    for row in conn.execute("SELECT id, title FROM tasks_new").fetchall():
        conn.execute(
            "UPDATE tasks_new SET slug = ? WHERE id = ?",
            (_slugify(row["title"], row["id"]), row["id"]),
        )

    conn.execute("DROP TABLE tasks")
    conn.execute("ALTER TABLE tasks_new RENAME TO tasks")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_week ON tasks(week_assigned)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_activity "
        "ON tasks(canonical_activity)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_deleted ON tasks(deleted_at)"
    )


# Registry of version → callable.  Callables take the connection and
# perform the migration under the shared transaction handler.
_MIGRATION_FUNCS: dict[int, Any] = {
    # Version 1 is the initial schema; no migration function needed.
    2: _migrate_v2_time_blocks_to_minutes,
    3: _migrate_v3_task_schedule_and_events,
    4: _migrate_v4_task_hierarchy,
}

# Migrations that require ``PRAGMA foreign_keys = OFF`` during execution
# (typically ones that rebuild a FK-referenced table).  ``PRAGMA
# foreign_keys`` is a no-op inside an open transaction, so the pragma
# must be toggled at the connection level, outside the savepoint the
# migration runs under.
_MIGRATIONS_NEEDING_FK_OFF: frozenset[int] = frozenset({3, 4})


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
        needs_fk_off = version in _MIGRATIONS_NEEDING_FK_OFF
        # ``PRAGMA foreign_keys`` has no effect inside an open
        # transaction, so toggle it here — outside the savepoint the
        # migration will run under — and restore on exit whether the
        # migration succeeds or raises.
        if needs_fk_off:
            conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with transaction(conn) as cur:
                fn(conn)
                cur.execute(
                    "INSERT INTO _migrations (version) VALUES (?)", (version,)
                )
        finally:
            if needs_fk_off:
                conn.execute("PRAGMA foreign_keys=ON")


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


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Create or open the database, apply schema + migrations, seed defaults.

    This is the main entry point for all other modules.  Call once at
    application startup.

    Order matters.  ``_SCHEMA_SQL`` runs first so a fresh database has
    every table.  Migrations run next.  Indexes and the cycle trigger run
    **after** migrations, because both reference columns that only exist
    at v4: creating them earlier would fail on a pre-v4 database, and
    creating the trigger inside the v4 migration would not help a fresh
    database (which skips that migration) or survive the rebuild's DROP.

    Args:
        db_path: Override the default database location.

    Returns:
        A fully initialized sqlite3.Connection.
    """
    conn = get_connection(db_path)
    conn.executescript(_SCHEMA_SQL)
    _apply_migrations(conn)
    conn.executescript(_INDEX_SQL)
    conn.executescript(_TRIGGER_SQL)
    _seed_defaults(conn)
    return conn


# ── Settings helpers ────────────────────────────────────────────────────


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
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


def parse_time_blocks(raw: str | None) -> dict[str, list[dict[str, Any]]]:
    """Parse a Niyam time_blocks JSON column into a Python dict.

    Args:
        raw: The JSON string from the time_blocks column, or None.

    Returns:
        A dict mapping day names to lists of block dicts.  Returns an
        empty dict when *raw* is None or empty.
    """
    if not raw:
        return {}
    return cast(dict[str, list[dict[str, Any]]], json.loads(raw))


def serialize_time_blocks(blocks: dict[str, list[dict[str, Any]]]) -> str:
    """Serialize a time_blocks dict to compact JSON for storage.

    Args:
        blocks: Day-name → list-of-block-dicts mapping.

    Returns:
        A compact JSON string.
    """
    return json.dumps(blocks, separators=(",", ":"))
