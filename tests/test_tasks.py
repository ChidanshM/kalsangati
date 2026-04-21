"""Tests for kalsangati.tasks — CRUD, capacity, spillover, v3 schema.

The v3 tests exercise the schedule columns added to ``tasks``, the new
``task_events`` history table, and the v3 migration (including the
explicit FK-preservation case where ``task_events`` rows already exist
when the tasks rebuild runs).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kalsangati.db import SCHEMA_VERSION, init_db
from kalsangati.tasks import (
    EVENT_TYPES,
    create,
    delete,
    get_all,
    get_by_id,
    get_task_events,
    log_task_event,
    process_spillover,
    set_status,
    update,
)


class TestTaskCrud:
    def test_create_and_get(self, conn: sqlite3.Connection) -> None:
        t = create(conn, "Write report", "01-02-el", estimated_hours=2.0)
        assert t.title == "Write report"
        assert t.status == "backlog"
        assert t.estimated_hours == 2.0

    def test_get_by_id(self, conn: sqlite3.Connection) -> None:
        t = create(conn, "Test task", "activity-1")
        fetched = get_by_id(conn, t.id)
        assert fetched is not None
        assert fetched.title == "Test task"

    def test_update(self, conn: sqlite3.Connection) -> None:
        t = create(conn, "Old title", "act")
        update(conn, t.id, title="New title", estimated_hours=5.0)
        updated = get_by_id(conn, t.id)
        assert updated.title == "New title"
        assert updated.estimated_hours == 5.0

    def test_delete(self, conn: sqlite3.Connection) -> None:
        t = create(conn, "Delete me", "act")
        delete(conn, t.id)
        assert get_by_id(conn, t.id) is None

    def test_set_status(self, conn: sqlite3.Connection) -> None:
        t = create(conn, "Progress", "act")
        set_status(conn, t.id, "in_progress")
        assert get_by_id(conn, t.id).status == "in_progress"

    def test_filter_by_status(self, conn: sqlite3.Connection) -> None:
        create(conn, "A", "act", status="backlog")
        create(conn, "B", "act", status="this_week", week_assigned="2025-03-10")
        backlog = get_all(conn, status="backlog")
        assert len(backlog) == 1
        assert backlog[0].title == "A"

    def test_filter_by_activity(self, conn: sqlite3.Connection) -> None:
        create(conn, "T1", "alpha")
        create(conn, "T2", "beta")
        result = get_all(conn, activity="alpha")
        assert len(result) == 1


class TestSpillover:
    def test_spillover_moves_to_backlog(self, conn: sqlite3.Connection) -> None:
        t = create(
            conn, "Spill me", "act",
            status="this_week", week_assigned="2025-03-10",
        )
        count = process_spillover(conn, "2025-03-10")
        assert count == 1
        refreshed = get_by_id(conn, t.id)
        assert refreshed.status == "backlog"
        assert refreshed.spilled_from == "2025-03-10"
        assert refreshed.week_assigned is None

    def test_done_not_spilled(self, conn: sqlite3.Connection) -> None:
        t = create(
            conn, "Done task", "act",
            status="done", week_assigned="2025-03-10",
        )
        count = process_spillover(conn, "2025-03-10")
        assert count == 0
        assert get_by_id(conn, t.id).status == "done"

# ── v3: schema shape ────────────────────────────────────────────────────


class TestV3Schema:
    """Sanity-checks on the schema introduced in schema v3."""

    def test_schema_version_is_three(self, conn: sqlite3.Connection) -> None:
        assert SCHEMA_VERSION == 3
        applied = conn.execute(
            "SELECT MAX(version) FROM _migrations"
        ).fetchone()[0]
        assert applied == 3

    def test_tasks_has_scheduled_columns(
        self, conn: sqlite3.Connection
    ) -> None:
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        for expected in (
            "scheduled_day",
            "scheduled_start_min",
            "scheduled_end_min",
            "scheduled_week_start",
        ):
            assert expected in cols, f"missing column: {expected}"

    def test_task_events_table_exists(
        self, conn: sqlite3.Connection
    ) -> None:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "task_events" in tables


# ── v3: CHECK constraint on scheduled_* fields ─────────────────────────


class TestScheduleCheckConstraint:
    """The fat CHECK: all four fields NULL together, or all populated
    with bounds — ``scheduled_start_min >= 0``, ``end > start``,
    ``end <= 1440``, day in the canonical 7-name set.
    """

    def test_all_null_accepted(self, conn: sqlite3.Connection) -> None:
        t = create(conn, "backlog task", "act")
        # create() already leaves scheduled_* as NULL; re-asserting via
        # an explicit update that re-writes all four to NULL.
        update(
            conn, t.id,
            scheduled_day=None,
            scheduled_start_min=None,
            scheduled_end_min=None,
            scheduled_week_start=None,
        )
        fetched = get_by_id(conn, t.id)
        assert fetched is not None
        assert fetched.scheduled_day is None
        assert fetched.scheduled_start_min is None
        assert fetched.scheduled_end_min is None
        assert fetched.scheduled_week_start is None

    def test_all_populated_accepted(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "to schedule", "act")
        update(
            conn, t.id,
            scheduled_day="tuesday",
            scheduled_start_min=540,
            scheduled_end_min=660,
            scheduled_week_start="2026-04-13",
        )
        fetched = get_by_id(conn, t.id)
        assert fetched is not None
        assert fetched.scheduled_day == "tuesday"
        assert fetched.scheduled_start_min == 540
        assert fetched.scheduled_end_min == 660
        assert fetched.scheduled_week_start == "2026-04-13"

    def test_boundary_full_day_accepted(
        self, conn: sqlite3.Connection
    ) -> None:
        """[0, 1440] end-inclusive is the maximum valid span."""
        t = create(conn, "full-day", "act")
        update(
            conn, t.id,
            scheduled_day="friday",
            scheduled_start_min=0,
            scheduled_end_min=1440,
            scheduled_week_start="2026-04-13",
        )
        fetched = get_by_id(conn, t.id)
        assert fetched is not None
        assert fetched.scheduled_start_min == 0
        assert fetched.scheduled_end_min == 1440

    def test_partial_schedule_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        """Three of four populated → IntegrityError."""
        t = create(conn, "partial", "act")
        with pytest.raises(sqlite3.IntegrityError):
            update(
                conn, t.id,
                scheduled_day="monday",
                scheduled_start_min=540,
                scheduled_end_min=600,
                scheduled_week_start=None,  # the missing one
            )

    def test_invalid_day_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "bad day", "act")
        with pytest.raises(sqlite3.IntegrityError):
            update(
                conn, t.id,
                scheduled_day="notaday",
                scheduled_start_min=540,
                scheduled_end_min=600,
                scheduled_week_start="2026-04-13",
            )

    def test_negative_start_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "neg start", "act")
        with pytest.raises(sqlite3.IntegrityError):
            update(
                conn, t.id,
                scheduled_day="monday",
                scheduled_start_min=-1,
                scheduled_end_min=600,
                scheduled_week_start="2026-04-13",
            )

    def test_end_beyond_day_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "overflow", "act")
        with pytest.raises(sqlite3.IntegrityError):
            update(
                conn, t.id,
                scheduled_day="monday",
                scheduled_start_min=540,
                scheduled_end_min=1441,
                scheduled_week_start="2026-04-13",
            )

    def test_end_not_after_start_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "zero-span", "act")
        with pytest.raises(sqlite3.IntegrityError):
            update(
                conn, t.id,
                scheduled_day="monday",
                scheduled_start_min=600,
                scheduled_end_min=600,
                scheduled_week_start="2026-04-13",
            )


# ── v3: status enum expansion ──────────────────────────────────────────


class TestStatusOnHold:
    """``on_hold`` was added to the status CHECK enum in v3."""

    def test_on_hold_accepted(self, conn: sqlite3.Connection) -> None:
        t = create(conn, "holdable", "act")
        set_status(conn, t.id, "on_hold")
        fetched = get_by_id(conn, t.id)
        assert fetched is not None
        assert fetched.status == "on_hold"

    def test_gibberish_status_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "gibberish", "act")
        with pytest.raises(sqlite3.IntegrityError):
            update(conn, t.id, status="not_a_real_status")


# ── v3: event_type set ─────────────────────────────────────────────────


class TestEventTypes:
    def test_event_types_contains_locked_set(self) -> None:
        expected = {
            "created", "assigned", "reassigned", "unscheduled",
            "on_hold", "resumed", "ended", "spilled",
        }
        assert expected == EVENT_TYPES


# ── v3: event logging ──────────────────────────────────────────────────


class TestEventLogging:
    def test_create_auto_logs_created_event(
        self, conn: sqlite3.Connection
    ) -> None:
        """``create()`` writes one ``created`` event atomically."""
        t = create(conn, "freshly made", "act")
        events = get_task_events(conn, t.id)
        assert len(events) == 1
        assert events[0].event_type == "created"
        assert events[0].task_id == t.id
        # A freshly created task has no schedule → snapshot is all None.
        assert events[0].scheduled_day is None
        assert events[0].scheduled_start_min is None
        assert events[0].scheduled_end_min is None
        assert events[0].notes is None

    def test_log_task_event_snapshots_schedule(
        self, conn: sqlite3.Connection
    ) -> None:
        """``assigned`` event carries the schedule at log time."""
        t = create(conn, "for assign", "act")
        update(
            conn, t.id,
            scheduled_day="wednesday",
            scheduled_start_min=600,
            scheduled_end_min=720,
            scheduled_week_start="2026-04-13",
        )
        ev = log_task_event(
            conn, t.id, "assigned", notes="dropped by user"
        )
        assert ev.event_type == "assigned"
        assert ev.scheduled_day == "wednesday"
        assert ev.scheduled_start_min == 600
        assert ev.scheduled_end_min == 720
        assert ev.notes == "dropped by user"

    def test_log_task_event_snapshot_survives_later_unschedule(
        self, conn: sqlite3.Connection
    ) -> None:
        """After unscheduling, the old ``assigned`` event still shows
        the schedule it was logged against.
        """
        t = create(conn, "travelling", "act")
        update(
            conn, t.id,
            scheduled_day="monday",
            scheduled_start_min=540,
            scheduled_end_min=660,
            scheduled_week_start="2026-04-13",
        )
        log_task_event(conn, t.id, "assigned")
        # Move back to backlog (all four fields NULL)
        update(
            conn, t.id,
            scheduled_day=None,
            scheduled_start_min=None,
            scheduled_end_min=None,
            scheduled_week_start=None,
        )
        log_task_event(conn, t.id, "unscheduled")
        events = get_task_events(conn, t.id)
        # created (no schedule), assigned (snapshot), unscheduled (no schedule)
        assert [e.event_type for e in events] == [
            "created", "assigned", "unscheduled",
        ]
        assert events[1].scheduled_day == "monday"
        assert events[1].scheduled_start_min == 540
        assert events[2].scheduled_day is None

    def test_log_task_event_rejects_unknown_type(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "reject", "act")
        with pytest.raises(ValueError):
            log_task_event(conn, t.id, "gibberish")

    def test_log_task_event_bad_task_id_raises_integrity(
        self, conn: sqlite3.Connection
    ) -> None:
        """FK enforcement: non-existent task_id is rejected at insert."""
        with pytest.raises(sqlite3.IntegrityError):
            log_task_event(conn, 99999, "assigned")

    def test_get_task_events_chronological(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "ordered", "act")
        log_task_event(conn, t.id, "assigned", notes="first")
        log_task_event(conn, t.id, "reassigned", notes="second")
        log_task_event(conn, t.id, "ended", notes="third")
        events = get_task_events(conn, t.id)
        # ``created`` is auto-logged by create(), so the full chain is:
        assert [e.event_type for e in events] == [
            "created", "assigned", "reassigned", "ended",
        ]
        assert [e.notes for e in events] == [
            None, "first", "second", "third",
        ]

    def test_delete_cascades_to_events(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "to delete", "act")
        log_task_event(conn, t.id, "assigned")
        log_task_event(conn, t.id, "ended")
        # sanity: 3 events exist (1 auto-created + 2 manual)
        assert len(get_task_events(conn, t.id)) == 3
        delete(conn, t.id)
        # All gone: FK ON DELETE CASCADE.
        remaining = conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
            (t.id,),
        ).fetchone()[0]
        assert remaining == 0


# ── v3: update() allows the new scheduled_* fields ─────────────────────


class TestUpdateAllowsScheduledFields:
    def test_update_allows_scheduled_day(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "via update", "act")
        update(
            conn, t.id,
            scheduled_day="thursday",
            scheduled_start_min=720,
            scheduled_end_min=780,
            scheduled_week_start="2026-04-13",
        )
        fetched = get_by_id(conn, t.id)
        assert fetched is not None
        assert fetched.scheduled_day == "thursday"


# ── v3: migration — idempotency and FK survival ────────────────────────


def _build_legacy_v2_db(db_path: Path) -> None:
    """Construct a v2-shape DB by hand.

    Simulates the state of a project on disk before the v3 migration
    has ever run: ``tasks`` without the scheduled_* columns and with
    the older status enum; ``task_events`` present and populated (this
    is the worst case — FK-referenced rows already exist when the
    tasks rebuild runs).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE projects (
            id                 INTEGER PRIMARY KEY,
            name               TEXT NOT NULL,
            canonical_activity TEXT NOT NULL,
            color              TEXT,
            notes              TEXT
        );
        CREATE TABLE tasks (
            id                 INTEGER PRIMARY KEY,
            title              TEXT NOT NULL,
            project_id         INTEGER REFERENCES projects(id),
            canonical_activity TEXT NOT NULL,
            estimated_hours    REAL,
            due_date           TEXT,
            status             TEXT DEFAULT 'backlog'
                               CHECK(status IN ('backlog','this_week',
                                                'in_progress','done')),
            week_assigned      TEXT,
            spilled_from       TEXT,
            override_reason    TEXT,
            notes              TEXT,
            created_at         TEXT NOT NULL DEFAULT (datetime('now'))
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
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO _migrations (version) VALUES (1);
        INSERT INTO _migrations (version) VALUES (2);
        """
    )
    conn.execute(
        "INSERT INTO projects (id, name, canonical_activity) "
        "VALUES (?, ?, ?)",
        (7, "Research", "01-03-el"),
    )
    conn.execute(
        "INSERT INTO tasks "
        "(id, title, project_id, canonical_activity, status, "
        " week_assigned, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (42, "Legacy task", 7, "01-03-el", "this_week",
         "2026-04-13", "pre-existing"),
    )
    conn.execute(
        "INSERT INTO tasks "
        "(id, title, canonical_activity, notes) "
        "VALUES (?, ?, ?, ?)",
        (43, "Second legacy", "act", None),
    )
    conn.execute(
        "INSERT INTO task_events "
        "(task_id, event_type, event_at, notes) "
        "VALUES (?, ?, ?, ?)",
        (42, "created", "2025-01-01 10:00:00", "legacy event 1"),
    )
    conn.execute(
        "INSERT INTO task_events "
        "(task_id, event_type, event_at, notes) "
        "VALUES (?, ?, ?, ?)",
        (42, "assigned", "2025-01-02 11:00:00", "legacy event 2"),
    )
    conn.execute(
        "INSERT INTO task_events "
        "(task_id, event_type, event_at, notes) "
        "VALUES (?, ?, ?, ?)",
        (43, "created", "2025-01-03 09:00:00", None),
    )
    conn.commit()
    conn.close()


class TestV3Migration:
    def test_migration_idempotent_on_reopen(
        self, tmp_path: Path
    ) -> None:
        """Running init_db twice on the same DB must apply v3 exactly
        once, and leave existing data untouched.
        """
        db_path = tmp_path / "idem.db"

        conn1 = init_db(db_path)
        t = create(conn1, "Idem", "act")
        log_task_event(conn1, t.id, "assigned")
        conn1.close()

        # Re-open; migration loop should find v3 already applied and
        # be a no-op.
        conn2 = init_db(db_path)
        applied = conn2.execute(
            "SELECT MAX(version) FROM _migrations"
        ).fetchone()[0]
        v3_rows = conn2.execute(
            "SELECT COUNT(*) FROM _migrations WHERE version = 3"
        ).fetchone()[0]
        assert applied == 3
        assert v3_rows == 1

        # Data survived intact.
        fetched = get_by_id(conn2, t.id)
        assert fetched is not None
        assert fetched.title == "Idem"
        events = get_task_events(conn2, t.id)
        assert [e.event_type for e in events] == ["created", "assigned"]
        conn2.close()

    def test_migration_preserves_fk_referenced_events(
        self, tmp_path: Path
    ) -> None:
        """The tasks table rebuild must preserve task_events rows
        that already reference tasks(id).

        This guards the FK-survival property: row ids are preserved
        through the rebuild, ``PRAGMA foreign_keys`` is OFF during the
        rebuild (so the DROP TABLE does not cascade-delete events),
        and enforcement is re-enabled cleanly afterward.
        """
        db_path = tmp_path / "legacy.db"
        _build_legacy_v2_db(db_path)

        # Open with init_db — triggers the v3 migration.
        conn = init_db(db_path)

        # v3 recorded
        applied = conn.execute(
            "SELECT MAX(version) FROM _migrations"
        ).fetchone()[0]
        assert applied == 3

        # Tasks survived with their ids.
        t42 = get_by_id(conn, 42)
        assert t42 is not None
        assert t42.title == "Legacy task"
        assert t42.project_id == 7
        assert t42.status == "this_week"
        assert t42.week_assigned == "2026-04-13"
        assert t42.notes == "pre-existing"
        # The four new columns default to NULL on migrated rows.
        assert t42.scheduled_day is None
        assert t42.scheduled_start_min is None
        assert t42.scheduled_end_min is None
        assert t42.scheduled_week_start is None

        t43 = get_by_id(conn, 43)
        assert t43 is not None
        assert t43.title == "Second legacy"

        # Events survived with intact task_id references and order.
        events_42 = get_task_events(conn, 42)
        assert [e.event_type for e in events_42] == ["created", "assigned"]
        assert events_42[0].notes == "legacy event 1"
        assert events_42[1].notes == "legacy event 2"

        events_43 = get_task_events(conn, 43)
        assert len(events_43) == 1
        assert events_43[0].event_type == "created"

        # Total event count unchanged by migration.
        total = conn.execute(
            "SELECT COUNT(*) FROM task_events"
        ).fetchone()[0]
        assert total == 3

        conn.close()

    def test_fk_enforcement_active_after_migration(
        self, tmp_path: Path
    ) -> None:
        """``PRAGMA foreign_keys`` is OFF during the rebuild; this test
        confirms it's back ON afterward so new bad inserts are rejected.
        """
        db_path = tmp_path / "fkcheck.db"
        _build_legacy_v2_db(db_path)
        conn = init_db(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_events "
                "(task_id, event_type, event_at) VALUES (?, ?, ?)",
                (999, "created", "2026-01-01"),
            )
            conn.commit()
        conn.rollback()
        conn.close()

    def test_check_active_after_migration(
        self, tmp_path: Path
    ) -> None:
        """The scheduled_* CHECK must be enforced on the rebuilt table."""
        db_path = tmp_path / "checkcheck.db"
        _build_legacy_v2_db(db_path)
        conn = init_db(db_path)
        # t43 existed before migration with all scheduled_* NULL.
        # Attempting partial population is rejected.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE tasks SET scheduled_day = ? WHERE id = ?",
                ("monday", 43),
            )
            conn.commit()
        conn.rollback()
        conn.close()

    def test_status_on_hold_active_after_migration(
        self, tmp_path: Path
    ) -> None:
        """The expanded status enum must be in force after migration."""
        db_path = tmp_path / "statuscheck.db"
        _build_legacy_v2_db(db_path)
        conn = init_db(db_path)
        conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ?", ("on_hold", 43)
        )
        conn.commit()
        fetched = get_by_id(conn, 43)
        assert fetched is not None
        assert fetched.status == "on_hold"
        conn.close()
