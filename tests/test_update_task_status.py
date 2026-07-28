"""Tests for ``kalsangati.services.update_task_status``.

Service #5 in the six-service plan.  Atomic small service — validation
+ structured result + exception wrapping, doing the ``tasks`` UPDATE and
the ``task_events`` INSERT in one transaction on top of the core
``tasks`` module.

The service does three separable jobs, exercised below: atomic write,
no-op detection, and lifecycle-transition validation.
"""

from __future__ import annotations

import sqlite3

import pytest

from kalsangati.exceptions import (
    InvalidTaskTransitionError,
    KalsangatiError,
    TaskNotFoundError,
)
from kalsangati.services.update_task_status import (
    UpdateStatusResult,
    allowed_transitions,
    update_task_status,
)
from kalsangati.tasks import EVENT_TYPES, create, get_by_id, get_task_events

# The arrow used in the auto-generated event note (``previous→new``).
_ARROW = "\u2192"

# Every legal (previous, new) edge and the event verb it should log.
# Locks the D1 vocabulary decision, including the on-hold-exit special
# case (``resumed`` only into in_progress / this_week).
_LEGAL_EDGES: list[tuple[str, str, str]] = [
    ("backlog", "this_week", "planned"),
    ("backlog", "in_progress", "started"),
    ("backlog", "on_hold", "on_hold"),
    ("backlog", "done", "ended"),
    ("this_week", "in_progress", "started"),
    ("this_week", "on_hold", "on_hold"),
    ("this_week", "backlog", "backlogged"),
    ("this_week", "done", "ended"),
    ("in_progress", "on_hold", "on_hold"),
    ("in_progress", "done", "ended"),
    ("in_progress", "this_week", "planned"),
    ("in_progress", "backlog", "backlogged"),
    ("on_hold", "in_progress", "resumed"),
    ("on_hold", "this_week", "resumed"),
    ("on_hold", "backlog", "backlogged"),
    ("on_hold", "done", "ended"),
    ("done", "in_progress", "started"),
    ("done", "backlog", "backlogged"),
]

# The only illegal edges in the graph: ``done`` reopens to in_progress
# or backlog only, so these two are rejected.
_ILLEGAL_EDGES: list[tuple[str, str]] = [
    ("done", "on_hold"),
    ("done", "this_week"),
]


def _make_task(conn: sqlite3.Connection, status: str) -> int:
    """Create a task in a given status; return its id."""
    task = create(conn, "T", "01-02-el", status=status)
    return task.id


# ── Happy-path behaviour ────────────────────────────────────────────────


class TestUpdateTaskStatusSuccess:
    """Legal transitions mutate status and log a matching event."""

    def test_status_is_updated(self, conn: sqlite3.Connection) -> None:
        tid = _make_task(conn, "this_week")

        result = update_task_status(conn, tid, "in_progress")

        assert isinstance(result, UpdateStatusResult)
        assert result.task_id == tid
        assert result.previous_status == "this_week"
        assert result.new_status == "in_progress"
        assert result.was_noop is False

        fresh = get_by_id(conn, tid)
        assert fresh is not None
        assert fresh.status == "in_progress"

    def test_event_is_logged_with_verb_and_note(
        self, conn: sqlite3.Connection
    ) -> None:
        tid = _make_task(conn, "backlog")

        result = update_task_status(conn, tid, "in_progress")

        # The result carries the event...
        assert result.event is not None
        assert result.event.event_type == "started"
        assert result.event.notes == f"backlog{_ARROW}in_progress"

        # ...and it is the most recent row in history (after "created").
        events = get_task_events(conn, tid)
        assert len(events) == 2
        assert events[0].event_type == "created"
        assert events[-1].event_type == "started"
        assert events[-1].id == result.event.id

    def test_logged_verb_is_in_event_types_vocabulary(
        self, conn: sqlite3.Connection
    ) -> None:
        """Whatever verb the service picks must be a declared event type."""
        tid = _make_task(conn, "on_hold")

        result = update_task_status(conn, tid, "in_progress")

        assert result.event is not None
        assert result.event.event_type in EVENT_TYPES

    def test_reopen_done_task(self, conn: sqlite3.Connection) -> None:
        """``done`` may reopen to in_progress."""
        tid = _make_task(conn, "done")

        result = update_task_status(conn, tid, "in_progress")

        assert result.was_noop is False
        assert result.event is not None
        assert result.event.event_type == "started"
        assert get_by_id(conn, tid).status == "in_progress"


# ── Event vocabulary (D1) ───────────────────────────────────────────────


class TestEventVocabulary:
    """Every legal transition logs the documented event verb."""

    @pytest.mark.parametrize(
        ("previous", "new", "expected_verb"),
        _LEGAL_EDGES,
        ids=[f"{p}->{n}" for p, n, _ in _LEGAL_EDGES],
    )
    def test_transition_logs_expected_verb(
        self,
        conn: sqlite3.Connection,
        previous: str,
        new: str,
        expected_verb: str,
    ) -> None:
        tid = _make_task(conn, previous)

        result = update_task_status(conn, tid, new)

        assert result.event is not None
        assert result.event.event_type == expected_verb
        assert result.event.event_type in EVENT_TYPES
        assert result.event.notes == f"{previous}{_ARROW}{new}"


# ── No-op (same-status) case ────────────────────────────────────────────


class TestNoOpIsNotAnError:
    """Setting the current status succeeds without mutation or event."""

    def test_result_flags_noop(self, conn: sqlite3.Connection) -> None:
        tid = _make_task(conn, "in_progress")

        result = update_task_status(conn, tid, "in_progress")

        assert result.was_noop is True
        assert result.event is None
        assert result.previous_status == "in_progress"
        assert result.new_status == "in_progress"

    def test_no_event_row_added(self, conn: sqlite3.Connection) -> None:
        tid = _make_task(conn, "backlog")
        before = len(get_task_events(conn, tid))  # just "created"

        update_task_status(conn, tid, "backlog")

        after = len(get_task_events(conn, tid))
        assert after == before

    def test_idempotent_under_repetition(
        self, conn: sqlite3.Connection
    ) -> None:
        tid = _make_task(conn, "backlog")

        r1 = update_task_status(conn, tid, "done")
        r2 = update_task_status(conn, tid, "done")
        r3 = update_task_status(conn, tid, "done")

        assert r1.was_noop is False
        assert r2.was_noop is True
        assert r3.was_noop is True
        # Exactly one transition event was logged (plus "created").
        assert len(get_task_events(conn, tid)) == 2


# ── Illegal transition (D2) ─────────────────────────────────────────────


class TestIllegalTransitionRaises:
    """Moves that are not allowed edges raise and change nothing."""

    @pytest.mark.parametrize(
        ("previous", "new"),
        _ILLEGAL_EDGES,
        ids=[f"{p}->{n}" for p, n in _ILLEGAL_EDGES],
    )
    def test_illegal_edge_raises(
        self, conn: sqlite3.Connection, previous: str, new: str
    ) -> None:
        tid = _make_task(conn, previous)

        with pytest.raises(InvalidTaskTransitionError):
            update_task_status(conn, tid, new)

    def test_db_unchanged_on_illegal(
        self, conn: sqlite3.Connection
    ) -> None:
        tid = _make_task(conn, "done")
        events_before = len(get_task_events(conn, tid))

        with pytest.raises(InvalidTaskTransitionError):
            update_task_status(conn, tid, "on_hold")

        # Status still done, no new event logged.
        assert get_by_id(conn, tid).status == "done"
        assert len(get_task_events(conn, tid)) == events_before

    def test_error_inherits_kalsangati_error(self) -> None:
        assert issubclass(InvalidTaskTransitionError, KalsangatiError)


# ── Unknown target status (D2) ──────────────────────────────────────────


class TestUnknownStatusRaises:
    """A target that is not one of the five statuses raises."""

    @pytest.mark.parametrize("bogus", ["nonsense", "in_progres", "", "DONE"])
    def test_unknown_status_raises(
        self, conn: sqlite3.Connection, bogus: str
    ) -> None:
        tid = _make_task(conn, "backlog")

        with pytest.raises(InvalidTaskTransitionError):
            update_task_status(conn, tid, bogus)

    def test_db_unchanged_on_unknown(
        self, conn: sqlite3.Connection
    ) -> None:
        tid = _make_task(conn, "backlog")

        with pytest.raises(InvalidTaskTransitionError):
            update_task_status(conn, tid, "nonsense")

        assert get_by_id(conn, tid).status == "backlog"
        assert len(get_task_events(conn, tid)) == 1  # only "created"


# ── Task not found (D3) ─────────────────────────────────────────────────


class TestTaskNotFound:
    """A missing task id raises TaskNotFoundError before any work."""

    def test_bogus_id_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(TaskNotFoundError):
            update_task_status(conn, 999, "done")

    def test_empty_db_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(TaskNotFoundError):
            update_task_status(conn, 1, "done")

    def test_deleted_task_raises(self, conn: sqlite3.Connection) -> None:
        tid = _make_task(conn, "backlog")
        conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        conn.commit()

        with pytest.raises(TaskNotFoundError):
            update_task_status(conn, tid, "done")

    def test_error_inherits_kalsangati_error(self) -> None:
        assert issubclass(TaskNotFoundError, KalsangatiError)

    def test_unknown_status_beats_not_found(
        self, conn: sqlite3.Connection
    ) -> None:
        """Target-status validation runs before the existence check, so
        a bad status on a missing task raises the transition error."""
        with pytest.raises(InvalidTaskTransitionError):
            update_task_status(conn, 999, "nonsense")


# ── Schedule snapshot in the event row ──────────────────────────────────


class TestScheduleSnapshot:
    """The event row snapshots the task's schedule at event time."""

    def test_scheduled_fields_are_snapshot(
        self, conn: sqlite3.Connection
    ) -> None:
        from kalsangati import tasks

        tid = _make_task(conn, "this_week")
        # Populate all four scheduled_* fields together (fat CHECK).
        tasks.update(
            conn,
            tid,
            scheduled_day="monday",
            scheduled_start_min=540,
            scheduled_end_min=600,
            scheduled_week_start="2026-04-13",
        )

        result = update_task_status(conn, tid, "in_progress")

        assert result.event is not None
        assert result.event.scheduled_day == "monday"
        assert result.event.scheduled_start_min == 540
        assert result.event.scheduled_end_min == 600

    def test_backlog_task_snapshots_nulls(
        self, conn: sqlite3.Connection
    ) -> None:
        tid = _make_task(conn, "backlog")

        result = update_task_status(conn, tid, "in_progress")

        assert result.event is not None
        assert result.event.scheduled_day is None
        assert result.event.scheduled_start_min is None
        assert result.event.scheduled_end_min is None


# ── Result type invariants ──────────────────────────────────────────────


def test_result_is_dataclass_with_slots(
    conn: sqlite3.Connection,
) -> None:
    """Sanity check on the UpdateStatusResult shape."""
    tid = _make_task(conn, "backlog")
    result = update_task_status(conn, tid, "done")

    # slots=True means no __dict__.
    assert not hasattr(result, "__dict__")
    # Five documented fields, in the documented order.
    assert result.__slots__ == (
        "task_id",
        "previous_status",
        "new_status",
        "was_noop",
        "event",
    )


# ── allowed_transitions helper (Unit 8) ─────────────────────────


class TestAllowedTransitions:
    """allowed_transitions exposes the legal targets for a status."""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("backlog", {"this_week", "in_progress", "on_hold", "done"}),
            ("this_week", {"in_progress", "on_hold", "backlog", "done"}),
            ("in_progress", {"on_hold", "done", "this_week", "backlog"}),
            ("on_hold", {"in_progress", "this_week", "backlog", "done"}),
            ("done", {"in_progress", "backlog"}),
        ],
        ids=["backlog", "this_week", "in_progress", "on_hold", "done"],
    )
    def test_returns_legal_targets(
        self, status: str, expected: set[str]
    ) -> None:
        assert allowed_transitions(status) == expected

    def test_excludes_current_status(self) -> None:
        for status in ("backlog", "this_week", "in_progress", "on_hold", "done"):
            assert status not in allowed_transitions(status)

    def test_unknown_status_returns_empty(self) -> None:
        assert allowed_transitions("nonsense") == frozenset()
        assert allowed_transitions("") == frozenset()

    def test_offered_targets_never_raise(
        self, conn: sqlite3.Connection
    ) -> None:
        """Every target the helper offers is an accepted, real move."""
        for status in ("backlog", "this_week", "in_progress", "on_hold", "done"):
            for target in allowed_transitions(status):
                tid = _make_task(conn, status)
                result = update_task_status(conn, tid, target)
                assert result.was_noop is False
