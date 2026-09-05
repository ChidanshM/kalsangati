"""Tests for the delete_task service.

Covers soft deletion of a leaf, cascade down a subtree, history
survival, the per-operation timestamp that acts as the undo key,
restoration, and the refusal to restore a task under a deleted ancestor.

The test that justifies the whole design is
``test_separately_deleted_descendant_stays_deleted``: without a
per-operation timestamp, restoring a parent would resurrect a child the
user had removed deliberately and separately.
"""

from __future__ import annotations

import sqlite3

import pytest

from kalsangati.core import tasks
from kalsangati.core.exceptions import (
    KalsangatiError,
    TaskNotFoundError,
    TaskNotRestorableError,
)
from kalsangati.services.delete_task import (
    DeleteResult,
    UndeleteResult,
    delete_task,
    undelete_task,
)


def _task(
    conn: sqlite3.Connection, title: str, *, parent_id: int | None = None
) -> tasks.Task:
    return tasks.create(conn, title, "01-02-el", parent_id=parent_id)


def _is_deleted(conn: sqlite3.Connection, task_id: int) -> bool:
    row = conn.execute(
        "SELECT deleted_at FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    assert row is not None, f"task {task_id} was removed, not marked"
    deleted_at: str | None = row["deleted_at"]
    return deleted_at is not None


def _verbs(conn: sqlite3.Connection, task_id: int) -> list[str]:
    return [e.event_type for e in tasks.get_task_events(conn, task_id)]


# ── Delete ──────────────────────────────────────────────────────────────


class TestDeleteMarksRatherThanRemoves:
    def test_row_survives(self, conn: sqlite3.Connection) -> None:
        t = _task(conn, "Gone")
        delete_task(conn, t.id)
        assert _is_deleted(conn, t.id) is True

    def test_hidden_from_reads(self, conn: sqlite3.Connection) -> None:
        t = _task(conn, "Gone")
        delete_task(conn, t.id)
        assert tasks.get_by_id(conn, t.id) is None
        assert tasks.get_by_id(conn, t.id, include_deleted=True) is not None

    def test_history_survives(self, conn: sqlite3.Connection) -> None:
        """The property this whole unit exists for.

        The old hard delete cascaded task_events through the foreign
        key, destroying the history the deleted_at column was added to
        preserve.
        """
        t = _task(conn, "Gone")
        tasks.log_task_event(conn, t.id, "assigned")
        before = len(tasks.get_task_events(conn, t.id))

        delete_task(conn, t.id)

        after = conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ?", (t.id,)
        ).fetchone()[0]
        # created + assigned survive; the new `deleted` event is added.
        assert after == before + 1

    def test_returns_a_delete_result(
        self, conn: sqlite3.Connection
    ) -> None:
        t = _task(conn, "Gone")
        result = delete_task(conn, t.id)
        assert isinstance(result, DeleteResult)
        assert result.was_noop is False
        assert result.deleted_at
        assert result.cascaded_ids == []

    def test_unknown_id_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(TaskNotFoundError):
            delete_task(conn, 99999)

    def test_already_deleted_is_a_noop(
        self, conn: sqlite3.Connection
    ) -> None:
        t = _task(conn, "Gone")
        delete_task(conn, t.id)
        before = len(tasks.get_task_events(conn, t.id))

        result = delete_task(conn, t.id)

        assert result.was_noop is True
        assert result.events == []
        assert len(tasks.get_task_events(conn, t.id)) == before


class TestDeleteCascades:
    def test_children_are_marked(self, conn: sqlite3.Connection) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)

        result = delete_task(conn, parent.id)

        assert _is_deleted(conn, child.id) is True
        assert result.cascaded_ids == [child.id]

    def test_cascade_reaches_every_depth(
        self, conn: sqlite3.Connection
    ) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        c = _task(conn, "C", parent_id=b.id)
        d = _task(conn, "D", parent_id=c.id)

        result = delete_task(conn, a.id)

        assert all(_is_deleted(conn, i) for i in (a.id, b.id, c.id, d.id))
        assert result.cascaded_ids == [b.id, c.id, d.id]

    def test_siblings_outside_the_subtree_untouched(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        _task(conn, "Child", parent_id=parent.id)
        bystander = _task(conn, "Bystander")

        delete_task(conn, parent.id)

        assert _is_deleted(conn, bystander.id) is False

    def test_every_affected_row_shares_one_timestamp(
        self, conn: sqlite3.Connection
    ) -> None:
        """The timestamp identifies the operation, not just the moment."""
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)

        result = delete_task(conn, parent.id)

        stamps = {
            r["deleted_at"]
            for r in conn.execute(
                "SELECT deleted_at FROM tasks WHERE id IN (?, ?)",
                (parent.id, child.id),
            ).fetchall()
        }
        assert stamps == {result.deleted_at}

    def test_one_event_per_affected_row(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)

        delete_task(conn, parent.id)

        assert "deleted" in _verbs(conn, parent.id)
        assert "deleted" in _verbs(conn, child.id)

    def test_notes_distinguish_root_from_cascaded(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)

        result = delete_task(conn, parent.id)

        by_task = {e.task_id: e.notes for e in result.events}
        assert by_task[parent.id] == "deleted"
        assert by_task[child.id] == f"deleted with parent {parent.id}"

    def test_two_deletes_get_different_timestamps(
        self, conn: sqlite3.Connection
    ) -> None:
        """Microsecond precision, unlike the rest of the codebase.

        Two deletions in the same second would otherwise share an undo
        key and restore each other's rows.
        """
        a = _task(conn, "A")
        b = _task(conn, "B")

        first = delete_task(conn, a.id)
        second = delete_task(conn, b.id)

        assert first.deleted_at != second.deleted_at


# ── Undelete ────────────────────────────────────────────────────────────


class TestUndelete:
    def test_restores_the_task(self, conn: sqlite3.Connection) -> None:
        t = _task(conn, "Back")
        delete_task(conn, t.id)

        result = undelete_task(conn, t.id)

        assert isinstance(result, UndeleteResult)
        assert result.was_noop is False
        assert tasks.get_by_id(conn, t.id) is not None

    def test_restores_the_cascaded_subtree(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)
        delete_task(conn, parent.id)

        result = undelete_task(conn, parent.id)

        assert _is_deleted(conn, child.id) is False
        assert result.restored_ids == [child.id]

    def test_already_live_is_a_noop(
        self, conn: sqlite3.Connection
    ) -> None:
        t = _task(conn, "Live")
        result = undelete_task(conn, t.id)
        assert result.was_noop is True
        assert result.events == []

    def test_unknown_id_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(TaskNotFoundError):
            undelete_task(conn, 99999)

    def test_one_event_per_restored_row(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)
        delete_task(conn, parent.id)

        undelete_task(conn, parent.id)

        assert "undeleted" in _verbs(conn, parent.id)
        assert "undeleted" in _verbs(conn, child.id)

    def test_separately_deleted_descendant_stays_deleted(
        self, conn: sqlite3.Connection
    ) -> None:
        """The test that justifies the per-operation timestamp.

        Delete the child, then the parent, then restore the parent.  The
        child was removed deliberately in its own operation and must not
        come back.  Without the timestamp key, a "restore the subtree"
        implementation would resurrect it.
        """
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)

        delete_task(conn, child.id)
        delete_task(conn, parent.id)
        result = undelete_task(conn, parent.id)

        assert _is_deleted(conn, parent.id) is False
        assert _is_deleted(conn, child.id) is True
        assert result.restored_ids == []


class TestUndeleteUnderDeletedAncestor:
    def test_refused(self, conn: sqlite3.Connection) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)
        delete_task(conn, parent.id)

        with pytest.raises(TaskNotRestorableError):
            undelete_task(conn, child.id)

    def test_message_names_the_ancestor(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)
        delete_task(conn, parent.id)

        with pytest.raises(TaskNotRestorableError) as exc:
            undelete_task(conn, child.id)
        assert str(parent.id) in str(exc.value)

    def test_is_a_domain_error(self, conn: sqlite3.Connection) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)
        delete_task(conn, parent.id)

        with pytest.raises(KalsangatiError):
            undelete_task(conn, child.id)

    def test_refusal_writes_nothing(
        self, conn: sqlite3.Connection
    ) -> None:
        parent = _task(conn, "Parent")
        child = _task(conn, "Child", parent_id=parent.id)
        delete_task(conn, parent.id)
        before = len(tasks.get_task_events(conn, child.id))

        with pytest.raises(TaskNotRestorableError):
            undelete_task(conn, child.id)

        assert _is_deleted(conn, child.id) is True
        assert len(tasks.get_task_events(conn, child.id)) == before

    def test_distant_ancestor_also_blocks(
        self, conn: sqlite3.Connection
    ) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        c = _task(conn, "C", parent_id=b.id)
        delete_task(conn, a.id)

        with pytest.raises(TaskNotRestorableError):
            undelete_task(conn, c.id)


# ── Interaction with the rest of the module ─────────────────────────────


class TestDeletedTasksAreInert:
    def test_consume_no_capacity(self, conn: sqlite3.Connection) -> None:
        t = tasks.create(
            conn, "Gone", "act",
            estimated_hours=5.0, status="this_week",
            week_assigned="2026-09-07",
        )
        assert tasks.capacity_for_activity(
            conn, "act", "2026-09-07"
        ).assigned_hours == 5.0

        delete_task(conn, t.id)

        assert tasks.capacity_for_activity(
            conn, "act", "2026-09-07"
        ).assigned_hours == 0.0

    def test_do_not_spill(self, conn: sqlite3.Connection) -> None:
        t = tasks.create(
            conn, "Gone", "act",
            status="this_week", week_assigned="2026-09-07",
        )
        delete_task(conn, t.id)
        assert tasks.process_spillover(conn, "2026-09-07") == 0

    def test_cannot_be_reparented(
        self, conn: sqlite3.Connection
    ) -> None:
        """Asserted so it is a decision rather than an accident.

        ``reparent_task`` looks the task up with ``get_by_id``, which
        hides deleted rows, so a deleted task raises TaskNotFoundError.
        Restore it first.
        """
        from kalsangati.services.reparent_task import reparent_task

        parent = _task(conn, "Parent")
        t = _task(conn, "Gone")
        delete_task(conn, t.id)

        with pytest.raises(TaskNotFoundError):
            reparent_task(conn, t.id, new_parent_id=parent.id)


class TestEventVocabulary:
    def test_verbs_are_registered(self) -> None:
        assert "deleted" in tasks.EVENT_TYPES
        assert "undeleted" in tasks.EVENT_TYPES
