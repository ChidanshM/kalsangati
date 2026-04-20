"""Tests for kalsangati.tasks — CRUD, capacity, spillover."""

from __future__ import annotations

import sqlite3

from kalsangati.niyam import TimeBlock, create as create_niyam
from kalsangati.tasks import (
    capacity_for_activity,
    create,
    delete,
    get_all,
    get_by_id,
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
