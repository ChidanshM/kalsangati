# Architecture

This document describes the system design, data flow, and module responsibilities for Kālsangati.

---

## Overview

Kālsangati is a local-first desktop application. All data is stored in a single SQLite database on the user's machine. There is no network layer, no authentication, and no external services.

```
┌─────────────────────────────────────────────────────────┐
│                        GUI Layer                        │
│  main_window · schedule_editor · analytics_dashboard   │
│  stopwatch · label_manager · settings                  │
└────────────────────┬────────────────────────────────────┘
                     │  calls
┌────────────────────▼────────────────────────────────────┐
│                     Core Modules                        │
│  ingest · labels · schedule · compare · analytics      │
│  notifications · tracker                               │
└────────────────────┬────────────────────────────────────┘
                     │  reads/writes
┌────────────────────▼────────────────────────────────────┐
│              SQLite Database (kalsangati.db)               │
│  kalrekha · niyam · label_mappings  │
│  label_groups · weekly_aggregates · settings           │
└─────────────────────────────────────────────────────────┘
```

---

## Database Design

### Why SQLite + JSON (hybrid)

Most of the data in Kālsangati is relational and benefits from SQL joins:

- `kalrekha` joins against `label_mappings` for name resolution
- `weekly_aggregates` joins against `label_groups` for rollup views
- `vimarsha.py` diffs two tables using standard GROUP BY queries

However, **ideal schedule versions** are document-like. Each version has a different set of activities and time blocks. A fixed relational schema would require schema migrations every time a user adds a new activity. Instead, `niyam` stores the full week layout as a JSON blob in a single column:

```json
{
  "monday": [
    {"activity": "01-02-el", "start": "09:00", "end": "11:00", "duration_h": 2.0},
    {"activity": "04-workout", "start": "06:00", "end": "07:48", "duration_h": 1.8}
  ],
  "tuesday": [...]
}
```

SQLite's built-in `json_extract` and `json_each` functions allow querying into this blob when needed for notifications and comparison, without losing the flexibility.

### Schema

```sql
-- Ideal schedule versions (document-style time_blocks)
CREATE TABLE niyam (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    is_active   INTEGER DEFAULT 0,
    time_blocks TEXT  -- JSON
);

-- Actual logged sessions from any source
CREATE TABLE kalrekha (
    id           INTEGER PRIMARY KEY,
    project      TEXT,
    task         TEXT,
    date         TEXT NOT NULL,
    start        TEXT NOT NULL,
    end          TEXT NOT NULL,
    duration_min REAL,
    tz_offset    TEXT,
    source       TEXT  -- 'csv_import' | 'manual_stopwatch' | 'tracker'
);

-- Pre-aggregated weekly totals (refreshed on ingest)
CREATE TABLE weekly_aggregates (
    id          INTEGER PRIMARY KEY,
    week_start  TEXT NOT NULL,
    activity    TEXT NOT NULL,
    total_hours REAL
);

-- Raw label → canonical name mappings
CREATE TABLE label_mappings (
    id            INTEGER PRIMARY KEY,
    raw_label     TEXT NOT NULL UNIQUE,
    canonical_label TEXT NOT NULL
);

-- Canonical label → parent group hierarchy
CREATE TABLE label_groups (
    id              INTEGER PRIMARY KEY,
    canonical_label TEXT NOT NULL UNIQUE,
    parent_group    TEXT,
    level           INTEGER
);

-- Application settings (key-value)
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

---

## Module Responsibilities

### `db.py`
Schema creation, migrations, and connection management. All other modules receive a connection via dependency injection — they do not open their own connections.

### `ingest.py`
Parses CSV exports, normalises timezones to UTC, aggregates fragmented sessions (same project, same day), applies label conversion via `labels.py`, flags unknowns, and writes to `kalrekha` and `weekly_aggregates`. Also contains the `Watcher` class (watchdog integration) for folder monitoring.

### `labels.py`
Two responsibilities: (1) the converter — maps raw strings to canonical names using `label_mappings`; (2) the grouper — resolves a canonical label to its full parent chain using `label_groups`. Also handles auto-suggest by scanning numeric prefixes.

### `niyam.py`
CRUD operations for `niyam`. Handles JSON serialisation/deserialisation of `time_blocks`. Provides helpers to get today's blocks from the active version.

### `vimarsha.py`
Takes a week start date and an optional version ID. Returns a structured diff: per-activity ideal hours, actual hours, delta, and group rollups. Used by both the GUI comparison screen and the reflection generator.

### `analytics.py`
Computes the live metrics shown on the dashboard: today's logged hours per activity, week progress percentages, pacing (hours remaining vs days remaining), streak counts, and the composite adherence score.

### `notifications.py`
Background thread that runs every 60 seconds. Reads today's blocks from the active schedule version, compares against current time and the `notify_lead_minutes` setting, and fires desktop notifications via `plyer`. Suppresses notifications if the stopwatch is already tracking the correct activity.

### `tracker.py`
Platform abstraction for active window detection (future feature). Defines a `Tracker` base class with `get_active_window() -> str`. Concrete implementations: `LinuxTracker` (ewmh + Xlib), `WindowsTracker` (pygetwindow), `MacTracker` (AppKit).

### `gui/`
PyQt5 widgets. Each screen is a self-contained widget that receives a database connection and calls core modules for data. The GUI does not contain business logic — it calls `analytics.py`, `vimarsha.py`, etc., and renders the results.

---

## Data Flow

### CSV Import

```
CSV file on disk
    → ingest.parse_csv()         # pandas read, schema validation
    → ingest.normalize_timezone() # convert to UTC
    → ingest.aggregate_sessions() # merge fragments by project+date
    → labels.convert_label()      # raw → canonical per session
    → db.write_sessions()         # INSERT into kalrekha
    → db.refresh_aggregates()     # recompute weekly_aggregates
    → analytics.refresh()         # notify GUI to re-render
```

### Notification Loop

```
notifications.py (background thread, runs every 60s)
    → schedule.get_today_blocks()     # read active version JSON
    → settings.get(notify_lead_min)   # user preference
    → for each block:
        if now >= block.start - lead_time
        and block not already fired today
        and stopwatch.current_activity != block.activity:
            plyer.notification.notify(title, message)
            mark block as fired for today
```

### Comparison Query

```
compare.get_week_diff(week_start, version_id)
    → SELECT actual from weekly_aggregates WHERE week_start = ?
    → SELECT ideal from niyam.time_blocks (json_each)
    → JOIN on canonical activity name (via label_groups for rollup)
    → return {activity: {ideal, actual, delta}, groups: {...}}
```

---

## Threading Model

Kālsangati runs three threads:

1. **Main thread** — PyQt5 event loop (GUI)
2. **Watcher thread** — watchdog observer for CSV folder monitoring
3. **Notification thread** — 60-second polling loop

Both background threads communicate with the GUI via Qt signals. They never write to the GUI directly. Database writes from background threads use a separate connection with WAL mode enabled to avoid locking the main thread.

---

## Task System

### `tasks.py`
CRUD for the `tasks` table. Exposes capacity calculation logic: given a `canonical_activity` and `week_assigned`, returns `(ideal_hours, logged_hours, assigned_hours, slack)`. Also handles spillover detection at week boundary and bulk re-scheduling.

### DB addition

```sql
CREATE TABLE tasks (
    id                 INTEGER PRIMARY KEY,
    title              TEXT NOT NULL,
    project            TEXT,
    canonical_activity TEXT NOT NULL,
    estimated_hours    REAL,
    due_date           TEXT,
    status             TEXT DEFAULT 'backlog',
    week_assigned      TEXT,
    spilled_from       TEXT,
    notes              TEXT,
    created_at         TEXT NOT NULL
);
```

### Capacity Query Pattern

```sql
-- available capacity for an activity this week
SELECT
    s.ideal_h,
    COALESCE(a.logged_h, 0)   AS logged_h,
    COALESCE(t.assigned_h, 0) AS assigned_h,
    s.ideal_h
        - COALESCE(a.logged_h, 0)
        - COALESCE(t.assigned_h, 0) AS slack
FROM (
    SELECT json_each.value ->> 'duration_h' AS ideal_h
    FROM niyam, json_each(time_blocks, '$.' || :day_name)
    WHERE is_active = 1
      AND json_each.value ->> 'activity' = :activity
) s
LEFT JOIN (
    SELECT SUM(duration_min) / 60.0 AS logged_h
    FROM kalrekha
    WHERE date BETWEEN :week_start AND :week_end
      AND project = :activity
) a ON 1=1
LEFT JOIN (
    SELECT SUM(estimated_hours) AS assigned_h
    FROM tasks
    WHERE canonical_activity = :activity
      AND week_assigned = :iso_week
      AND status != 'done'
) t ON 1=1;
```

### GUI addition: `gui/task_planner.py`
Two-column PyQt5 widget. Left panel: `QListWidget` per activity group (backlog). Right panel: per-activity capacity bar (`QProgressBar`) with task list below. Drag-and-drop uses Qt's built-in `DragDropMode`. Signals emitted on task status change so the stopwatch widget and dashboard refresh without a full reload.

---

## Three-Layer Analytics & Override System

### Session classification

Every session written to `kalrekha` carries two new fields:

```sql
unplanned        BOOLEAN DEFAULT 0
override_reason  TEXT              -- nullable
block_classified BOOLEAN DEFAULT 0
```

Classification logic (run at write time for stopwatch sessions, retroactively
for CSV imports):

```python
def classify_session(
    session_start: str,
    session_end: str,
    activity: str,
    day: str,
    active_version_id: int,
    db: Connection
) -> bool:
    """Return True if session falls within a scheduled block."""
    blocks = schedule.get_day_blocks(active_version_id, day, db)
    for block in blocks:
        if (block.activity == activity
                and block.start <= session_start
                and session_end <= block.end):
            return True  # planned
    return False  # unplanned
```

### `projects.py`

CRUD for the `projects` table. Provides `get_by_activity(canonical_activity)`
for the stopwatch to look up which projects are valid for the current block.

### `gui/override_dialog.py`

A `QDialog` subclass. Presents three buttons (Continue / Wait / Switch) and
an optional reason `QLineEdit`. Returns a named result:
`OverrideResult.CONTINUE | SNOOZE | SWITCH`. The stopwatch widget checks this
result before opening the session.

### `weekly_aggregates` — extended

```sql
CREATE TABLE weekly_aggregates (
    id              INTEGER PRIMARY KEY,
    week_start      TEXT NOT NULL,
    activity        TEXT NOT NULL,
    total_hours     REAL,
    planned_hours   REAL,  -- within-block sessions
    unplanned_hours REAL   -- override sessions
);
```

The three-layer view in the analytics dashboard reads directly from
`weekly_aggregates` — no join required at render time.

### Reflection query — chronic override detection

```sql
SELECT
    activity,
    COUNT(DISTINCT strftime('%Y-W%W', date)) AS override_weeks
FROM kalrekha
WHERE unplanned = 1
  AND date >= date('now', '-8 weeks')
GROUP BY activity
HAVING override_weeks >= 3
ORDER BY override_weeks DESC;
```

This query powers the "Chronic override" flag in the Vimarśa panel.
