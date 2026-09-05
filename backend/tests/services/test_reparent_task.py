"""Tests for the reparent_task service.

Covers first placement under a parent, moves between parents, promotion
to root, idempotent no-ops, both not-found cases, the cycle check at
every depth, and the atomicity of a rejected move.

One test deliberately reaches past the service to raw SQL: the database
trigger must keep rejecting cycles independently, or the service check
silently becomes the only defence.
"""

from __future__ import annotations

import sqlite3

import pytest

from kalsangati.core import tasks
from kalsangati.core.exceptions import (
    KalsangatiError,
    TaskCycleError,
    TaskNotFoundError,
)
from kalsangati.services.reparent_task import ReparentResult, reparent_task


def _task(
    conn: sqlite3.Connection, title: str, *, parent_id: int | None = None
) -> tasks.Task:
    """Create a task for reparenting tests."""
    return tasks.create(conn, title, "01-02-el", parent_id=parent_id)


def _reparent_events(
    conn: sqlite3.Connection, task_id: int
) -> list[tasks.TaskEvent]:
    return [
        e
        for e in tasks.get_task_events(conn, task_id)
        if e.event_type == "reparented"
    ]


# ── Happy path ──────────────────────────────────────────────────────────


class TestReparentSuccess:
    def test_root_gains_a_parent(self, conn: sqlite3.Connection) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child")

        result = reparent_task(conn, child.id, new_parent_id=parent.id)

        assert isinstance(result, ReparentResult)
        assert result.previous_parent_id is None
        assert result.new_parent_id == parent.id
        assert result.was_noop is False
        fetched = tasks.get_by_id(conn, child.id)
        assert fetched is not None
        assert fetched.parent_id == parent.id

    def test_child_moves_between_parents(
        self, conn: sqlite3.Connection
    ) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B")
        child = _task(conn, "Child", parent_id=a.id)

        result = reparent_task(conn, child.id, new_parent_id=b.id)

        assert result.previous_parent_id == a.id
        assert result.new_parent_id == b.id
        fetched = tasks.get_by_id(conn, child.id)
        assert fetched is not None
        assert fetched.parent_id == b.id

    def test_child_promoted_to_root(self, conn: sqlite3.Connection) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)

        result = reparent_task(conn, child.id, new_parent_id=None)

        assert result.previous_parent_id == parent.id
        assert result.new_parent_id is None
        fetched = tasks.get_by_id(conn, child.id)
        assert fetched is not None
        assert fetched.parent_id is None

    def test_subtree_follows_implicitly(
        self, conn: sqlite3.Connection
    ) -> None:
        """Children keep pointing at the moved task; nothing cascades."""
        old_home = _task(conn, "Old home")
        new_home = _task(conn, "New home")
        mid = _task(conn, "Mid", parent_id=old_home.id)
        leaf = _task(conn, "Leaf", parent_id=mid.id)

        reparent_task(conn, mid.id, new_parent_id=new_home.id)

        fetched_leaf = tasks.get_by_id(conn, leaf.id)
        assert fetched_leaf is not None
        assert fetched_leaf.parent_id == mid.id

    def test_logs_a_reparented_event(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child")

        result = reparent_task(conn, child.id, new_parent_id=parent.id)

        assert result.event is not None
        assert result.event.event_type == "reparented"
        assert len(_reparent_events(conn, child.id)) == 1

    def test_project_is_left_alone(self, conn: sqlite3.Connection) -> None:
        """A task is filed once; moving its parent must not refile it."""
        conn.execute(
            "INSERT INTO projects (id, name, canonical_activity) "
            "VALUES (1, 'Proj', '01-02-el')"
        )
        conn.commit()
        parent = _task(conn, "Parent")
        child = tasks.create(
            conn, "Child", "01-02-el", project_id=1
        )

        reparent_task(conn, child.id, new_parent_id=parent.id)

        fetched = tasks.get_by_id(conn, child.id)
        assert fetched is not None
        assert fetched.project_id == 1


# ── No-op ───────────────────────────────────────────────────────────────


class TestReparentNoop:
    def test_same_parent_is_a_noop(self, conn: sqlite3.Connection) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)

        result = reparent_task(conn, child.id, new_parent_id=parent.id)

        assert result.was_noop is True
        assert result.event is None
        assert _reparent_events(conn, child.id) == []

    def test_root_to_root_is_a_noop(self, conn: sqlite3.Connection) -> None:
        root = _task(conn, "Root")

        result = reparent_task(conn, root.id, new_parent_id=None)

        assert result.was_noop is True
        assert result.event is None

    def test_noop_preserves_sort_order(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        first = _task(conn, "First", parent_id=parent.id)
        second = _task(conn, "Second", parent_id=parent.id)

        result = reparent_task(conn, first.id, new_parent_id=parent.id)

        assert result.new_sort_order == first.sort_order
        fetched = tasks.get_by_id(conn, first.id)
        assert fetched is not None
        assert fetched.sort_order == first.sort_order
        assert second.sort_order == 2.0


# ── Errors ──────────────────────────────────────────────────────────────


class TestReparentErrors:
    def test_unknown_task_raises(self, conn: sqlite3.Connection) -> None:
        parent = _task(conn, "Parent")
        with pytest.raises(TaskNotFoundError):
            reparent_task(conn, 99999, new_parent_id=parent.id)

    def test_unknown_parent_raises(self, conn: sqlite3.Connection) -> None:
        child = _task(conn, "Child")
        with pytest.raises(TaskNotFoundError) as exc:
            reparent_task(conn, child.id, new_parent_id=99999)
        # The message must name the parent, not the task, or a log
        # cannot tell the two cases apart.
        assert "99999" in str(exc.value)
        assert "parent" in str(exc.value).lower()

    def test_errors_are_domain_errors(
        self, conn: sqlite3.Connection
    ) -> None:
        """The presentation layer catches KalsangatiError."""
        child = _task(conn, "Child")
        with pytest.raises(KalsangatiError):
            reparent_task(conn, child.id, new_parent_id=99999)


# ── Cycles ──────────────────────────────────────────────────────────────


class TestReparentCycles:
    def test_self_parent_rejected(self, conn: sqlite3.Connection) -> None:
        t = _task(conn, "Alone")
        with pytest.raises(TaskCycleError):
            reparent_task(conn, t.id, new_parent_id=t.id)

    def test_direct_cycle_rejected(self, conn: sqlite3.Connection) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        with pytest.raises(TaskCycleError):
            reparent_task(conn, a.id, new_parent_id=b.id)

    def test_deep_cycle_rejected(self, conn: sqlite3.Connection) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        c = _task(conn, "C", parent_id=b.id)
        with pytest.raises(TaskCycleError):
            reparent_task(conn, a.id, new_parent_id=c.id)

    def test_cycle_error_is_a_domain_error(
        self, conn: sqlite3.Connection
    ) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        with pytest.raises(KalsangatiError):
            reparent_task(conn, a.id, new_parent_id=b.id)

    def test_sibling_move_is_not_a_cycle(
        self, conn: sqlite3.Connection
    ) -> None:
        """An unrelated subtree is a legitimate destination."""
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        other = _task(conn, "Other")

        reparent_task(conn, other.id, new_parent_id=b.id)

        fetched = tasks.get_by_id(conn, other.id)
        assert fetched is not None
        assert fetched.parent_id == b.id

    def test_rejected_move_writes_nothing(
        self, conn: sqlite3.Connection
    ) -> None:
        """A raise must leave neither the parent nor an event behind."""
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)

        with pytest.raises(TaskCycleError):
            reparent_task(conn, a.id, new_parent_id=b.id)

        fetched = tasks.get_by_id(conn, a.id)
        assert fetched is not None
        assert fetched.parent_id is None
        assert _reparent_events(conn, a.id) == []


class TestTriggerRemainsTheBackstop:
    """The service check does not make the database trigger redundant.

    If this fails, every path that is not ``reparent_task`` — a future
    API endpoint, a manual sqlite3 session, a bug — can write a cycle.
    """

    def test_raw_sql_cycle_still_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE tasks SET parent_id = ? WHERE id = ?", (b.id, a.id)
            )

    def test_raw_sql_self_parent_still_rejected(
        self, conn: sqlite3.Connection
    ) -> None:
        t = _task(conn, "Alone")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE tasks SET parent_id = id WHERE id = ?", (t.id,)
            )


# ── sort_order on the move ──────────────────────────────────────────────


class TestReparentSortOrder:
    def test_lands_last_among_new_siblings(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        _task(conn, "First", parent_id=parent.id)
        _task(conn, "Second", parent_id=parent.id)
        incomer = _task(conn, "Incomer")

        result = reparent_task(conn, incomer.id, new_parent_id=parent.id)

        assert result.new_sort_order == 3.0

    def test_empty_group_starts_at_one(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        incomer = _task(conn, "Incomer")

        result = reparent_task(conn, incomer.id, new_parent_id=parent.id)

        assert result.new_sort_order == 1.0

    def test_promoted_task_lands_last_among_roots(
        self, conn: sqlite3.Connection
    ) -> None:
        """Catches ``parent_id = ?`` instead of ``IS ?``.

        With ``=``, comparing against NULL is never true, the root group
        comes back empty, and every promoted task collapses onto 1.0.
        Here there are already two roots, so the promoted task must be
        third.
        """
        root_a = _task(conn, "Root A")
        _task(conn, "Root B")
        child = _task(conn, "Child", parent_id=root_a.id)

        result = reparent_task(conn, child.id, new_parent_id=None)

        assert result.new_sort_order == 3.0

    def test_moving_task_excluded_from_its_own_maximum(
        self, conn: sqlite3.Connection
    ) -> None:
        """A root moving into a group must not count itself.

        Three roots exist; the third moves under the first.  Its new
        position is computed among that parent's children, which is
        empty, so it is 1.0 — not 4.0, which is what counting the root
        group would give.
        """
        parent = _task(conn, "Parent")
        _task(conn, "Other root")
        mover = _task(conn, "Mover")

        result = reparent_task(conn, mover.id, new_parent_id=parent.id)

        assert result.new_sort_order == 1.0


# ── Event content ───────────────────────────────────────────────────────


class TestReparentEvent:
    def test_notes_record_both_ids(self, conn: sqlite3.Connection) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B")
        child = _task(conn, "Child", parent_id=a.id)

        result = reparent_task(conn, child.id, new_parent_id=b.id)

        assert result.event is not None
        assert result.event.notes == f"parent {a.id} -> {b.id}"

    def test_notes_render_root_as_none(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child")

        first = reparent_task(conn, child.id, new_parent_id=parent.id)
        assert first.event is not None
        assert first.event.notes == f"parent none -> {parent.id}"

        back = reparent_task(conn, child.id, new_parent_id=None)
        assert back.event is not None
        assert back.event.notes == f"parent {parent.id} -> none"

    def test_schedule_snapshot_columns_are_null(
        self, conn: sqlite3.Connection
    ) -> None:
        """A move says nothing about a task's calendar slot."""
        parent = _task(conn, "Parent")
        child = _task(conn, "Child")

        result = reparent_task(conn, child.id, new_parent_id=parent.id)

        assert result.event is not None
        assert result.event.scheduled_day is None
        assert result.event.scheduled_start_min is None
        assert result.event.scheduled_end_min is None

    def test_event_type_is_in_the_vocabulary(self) -> None:
        assert "reparented" in tasks.EVENT_TYPES
