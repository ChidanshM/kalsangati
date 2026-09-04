"""Tests for kalsangati.persistence.db — schema, settings, helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kalsangati.persistence.db import (
    SCHEMA_VERSION,
    _slugify,
    get_setting,
    init_db,
    parse_time_blocks,
    serialize_time_blocks,
    set_setting,
)


class TestInitDb:
    def test_creates_all_tables(self, conn: sqlite3.Connection) -> None:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "niyam", "kalrekha", "weekly_aggregates",
            "label_mappings", "label_groups", "projects", "tasks",
            "settings", "_migrations",
        }
        assert expected.issubset(tables)

    def test_default_settings_seeded(self, conn: sqlite3.Connection) -> None:
        val = get_setting(conn, "notify_lead_minutes")
        assert val == "5"

    def test_wal_mode(self, conn: sqlite3.Connection) -> None:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_foreign_keys_on(self, conn: sqlite3.Connection) -> None:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


class TestSettings:
    def test_get_set(self, conn: sqlite3.Connection) -> None:
        set_setting(conn, "test_key", "test_value")
        assert get_setting(conn, "test_key") == "test_value"

    def test_upsert(self, conn: sqlite3.Connection) -> None:
        set_setting(conn, "test_key", "v1")
        set_setting(conn, "test_key", "v2")
        assert get_setting(conn, "test_key") == "v2"

    def test_missing_key(self, conn: sqlite3.Connection) -> None:
        assert get_setting(conn, "nonexistent") is None


class TestTimeBlocksJson:
    def test_roundtrip(self) -> None:
        blocks = {
            "monday": [
                {
                    "activity": "study", "start": "09:00",
                    "end": "11:00", "duration_h": 2.0,
                }
            ]
        }
        serialized = serialize_time_blocks(blocks)
        parsed = parse_time_blocks(serialized)
        assert parsed == blocks

    def test_empty(self) -> None:
        assert parse_time_blocks(None) == {}
        assert parse_time_blocks("") == {}


# ── Schema v4 ────────────────────────────────────────────────────

# v3 shape of `tasks`, reproduced here so the migration can be exercised
# against a database that predates v4.  Deliberately a literal copy rather
# than an import: the point is to pin what the migration must accept, and
# it must keep working even after db.py's own DDL moves on.
_V3_TASKS_DDL = """
CREATE TABLE tasks (
    id                   INTEGER PRIMARY KEY,
    title                TEXT NOT NULL,
    project_id           INTEGER REFERENCES projects(id),
    canonical_activity   TEXT NOT NULL,
    estimated_hours      REAL,
    due_date             TEXT,
    status               TEXT DEFAULT 'backlog'
                         CHECK(status IN ('backlog','this_week',
                                          'in_progress','on_hold','done')),
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
);
CREATE TABLE task_events (
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
CREATE TABLE _migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO _migrations (version) VALUES (1), (2), (3);
"""


def _build_v3_db(path: Path) -> None:
    """Write a v3-shaped database with representative rows."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_V3_TASKS_DDL)
    conn.executescript(
        """
        INSERT INTO tasks (id, title, canonical_activity, status, notes)
        VALUES
            (1, 'Write the report', '01-02-el', 'backlog', 'draft prose'),
            (2, 'CIS731: problem set #4!', '01-02-el', 'in_progress', NULL),
            (3, 'कालसंगति', '00-sadhana', 'done', NULL);
        INSERT INTO task_events (id, task_id, event_type, event_at)
        VALUES (1, 2, 'created', '2026-09-01T09:00:00');
        """
    )
    conn.commit()
    conn.close()


def _insert_task(
    conn: sqlite3.Connection,
    task_id: int,
    title: str,
    *,
    parent_id: int | None = None,
) -> None:
    """Insert a v4 task with raw SQL.

    Raw SQL rather than ``core.tasks.create`` on purpose: ``persistence``
    is a leaf and its tests should not reach up a layer to exercise it.
    """
    conn.execute(
        "INSERT INTO tasks (id, title, canonical_activity, slug, parent_id) "
        "VALUES (?, ?, '01-02-el', ?, ?)",
        (task_id, title, f"t{task_id}", parent_id),
    )
    conn.commit()


class TestSchemaV4:
    """A freshly created database is v4 without running the migration."""

    def test_schema_version_is_4(self) -> None:
        assert SCHEMA_VERSION == 4

    def test_new_columns_exist(self, conn: sqlite3.Connection) -> None:
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        assert {
            "parent_id", "slug", "notes_path", "deleted_at", "sort_order",
        } <= cols

    def test_notes_column_retained(self, conn: sqlite3.Connection) -> None:
        # notes_path supersedes it, but core/tasks.py still writes `notes`.
        # Dropping it is a behaviour change and belongs to a later unit.
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        assert "notes" in cols

    def test_slug_is_nullable(self, conn: sqlite3.Connection) -> None:
        # Dormant column: core/tasks.py::create does not supply a slug yet,
        # so NOT NULL here would break task creation.  It tightens when the
        # creating service populates it.
        conn.execute(
            "INSERT INTO tasks (id, title, canonical_activity) "
            "VALUES (1, 'no slug yet', '01-02-el')"
        )
        row = conn.execute(
            "SELECT slug FROM tasks WHERE id = 1"
        ).fetchone()
        assert row["slug"] is None

    def test_dropped_status_accepted(
        self, conn: sqlite3.Connection
    ) -> None:
        _insert_task(conn, 1, "abandon me")
        conn.execute("UPDATE tasks SET status = 'dropped' WHERE id = 1")
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = 1"
        ).fetchone()
        assert row["status"] == "dropped"

    def test_unknown_status_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        _insert_task(conn, 1, "a task")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET status = 'bananas' WHERE id = 1")

    def test_cycle_trigger_installed(
        self, conn: sqlite3.Connection
    ) -> None:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert "trg_tasks_no_cycle" in names

    def test_new_indexes_exist(self, conn: sqlite3.Connection) -> None:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert {"idx_tasks_parent", "idx_tasks_deleted"} <= names


class TestMigrationV4:
    """An existing v3 database migrates without losing anything."""

    def test_rows_and_ids_preserved(self, tmp_path: Path) -> None:
        path = tmp_path / "v3.db"
        _build_v3_db(path)
        conn = init_db(path)
        rows = conn.execute(
            "SELECT id, title, status FROM tasks ORDER BY id"
        ).fetchall()
        assert [r["id"] for r in rows] == [1, 2, 3]
        assert rows[0]["title"] == "Write the report"
        assert rows[1]["status"] == "in_progress"

    def test_notes_data_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "v3.db"
        _build_v3_db(path)
        conn = init_db(path)
        row = conn.execute(
            "SELECT notes FROM tasks WHERE id = 1"
        ).fetchone()
        assert row["notes"] == "draft prose"

    def test_slug_backfilled(self, tmp_path: Path) -> None:
        path = tmp_path / "v3.db"
        _build_v3_db(path)
        conn = init_db(path)
        slugs = {
            r["id"]: r["slug"]
            for r in conn.execute("SELECT id, slug FROM tasks").fetchall()
        }
        assert slugs[1] == "write-the-report"
        assert slugs[2] == "cis731-problem-set-4"
        # Devanagari title leaves no ASCII behind, so the id fallback runs.
        assert slugs[3] == "task-3"
        assert all(s for s in slugs.values())

    def test_sort_order_seeded_from_id(self, tmp_path: Path) -> None:
        path = tmp_path / "v3.db"
        _build_v3_db(path)
        conn = init_db(path)
        rows = conn.execute(
            "SELECT id, sort_order FROM tasks ORDER BY sort_order"
        ).fetchall()
        assert [r["id"] for r in rows] == [1, 2, 3]
        assert rows[0]["sort_order"] == 1.0

    def test_new_columns_default_null(self, tmp_path: Path) -> None:
        path = tmp_path / "v3.db"
        _build_v3_db(path)
        conn = init_db(path)
        row = conn.execute(
            "SELECT parent_id, notes_path, deleted_at FROM tasks WHERE id = 1"
        ).fetchone()
        assert row["parent_id"] is None
        assert row["notes_path"] is None
        assert row["deleted_at"] is None

    def test_task_events_reference_survives(self, tmp_path: Path) -> None:
        # The rebuild drops `tasks` with foreign keys off; row ids are
        # preserved so this reference is still valid afterwards.
        path = tmp_path / "v3.db"
        _build_v3_db(path)
        conn = init_db(path)
        row = conn.execute(
            "SELECT task_id, event_type FROM task_events WHERE id = 1"
        ).fetchone()
        assert row["task_id"] == 2
        assert row["event_type"] == "created"
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE id = 2"
        ).fetchone()[0] == 1

    def test_trigger_installed_after_migration(
        self, tmp_path: Path
    ) -> None:
        # DROP TABLE removes a table's triggers, so this only passes if the
        # trigger is created after migrations rather than inside one.
        path = tmp_path / "v3.db"
        _build_v3_db(path)
        conn = init_db(path)
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert "trg_tasks_no_cycle" in names

    def test_replay_is_a_noop(self, tmp_path: Path) -> None:
        path = tmp_path / "v3.db"
        _build_v3_db(path)
        init_db(path).close()
        conn = init_db(path)  # second open re-runs _apply_migrations
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 3
        assert conn.execute(
            "SELECT notes FROM tasks WHERE id = 1"
        ).fetchone()["notes"] == "draft prose"
        assert conn.execute(
            "SELECT MAX(version) FROM _migrations"
        ).fetchone()[0] == 4


class TestCycleTrigger:
    """A task may never become its own ancestor."""

    def test_self_parent_rejected(self, conn: sqlite3.Connection) -> None:
        _insert_task(conn, 1, "root")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET parent_id = 1 WHERE id = 1")

    def test_direct_cycle_rejected(self, conn: sqlite3.Connection) -> None:
        _insert_task(conn, 1, "parent")
        _insert_task(conn, 2, "child", parent_id=1)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET parent_id = 2 WHERE id = 1")

    def test_deep_cycle_rejected(self, conn: sqlite3.Connection) -> None:
        _insert_task(conn, 1, "a")
        _insert_task(conn, 2, "b", parent_id=1)
        _insert_task(conn, 3, "c", parent_id=2)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET parent_id = 3 WHERE id = 1")

    def test_legitimate_reparent_allowed(
        self, conn: sqlite3.Connection
    ) -> None:
        _insert_task(conn, 1, "a")
        _insert_task(conn, 2, "b", parent_id=1)
        _insert_task(conn, 3, "unrelated")
        conn.execute("UPDATE tasks SET parent_id = 1 WHERE id = 3")
        row = conn.execute(
            "SELECT parent_id FROM tasks WHERE id = 3"
        ).fetchone()
        assert row["parent_id"] == 1

    def test_detaching_to_root_allowed(
        self, conn: sqlite3.Connection
    ) -> None:
        # WHEN NEW.parent_id IS NOT NULL, so clearing never fires the guard.
        _insert_task(conn, 1, "a")
        _insert_task(conn, 2, "b", parent_id=1)
        conn.execute("UPDATE tasks SET parent_id = NULL WHERE id = 2")
        row = conn.execute(
            "SELECT parent_id FROM tasks WHERE id = 2"
        ).fetchone()
        assert row["parent_id"] is None


class TestSlugify:
    def test_ordinary_title(self) -> None:
        assert _slugify("Write the report", 1) == "write-the-report"

    def test_punctuation_collapses(self) -> None:
        assert _slugify("CIS731: problem set #4!", 1) == (
            "cis731-problem-set-4"
        )

    def test_truncated_to_limit(self) -> None:
        slug = _slugify("x" * 100, 1)
        assert len(slug) == 42

    def test_truncation_does_not_leave_trailing_hyphen(self) -> None:
        # 42 chars would land exactly on the separator here.
        slug = _slugify("a" * 42 + " tail", 1)
        assert not slug.endswith("-")

    def test_non_ascii_falls_back_to_id(self) -> None:
        assert _slugify("कालसंगति", 7) == "task-7"

    def test_punctuation_only_falls_back_to_id(self) -> None:
        assert _slugify("!!! ???", 9) == "task-9"

    def test_empty_title_falls_back_to_id(self) -> None:
        assert _slugify("", 3) == "task-3"

    def test_mixed_script_keeps_ascii(self) -> None:
        assert _slugify("Read Vimarśa chapter", 1) == "read-vimara-chapter"
