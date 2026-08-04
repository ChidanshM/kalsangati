"""Tests for the schedule_task service (Unit 9, service #4).

Covers first placement (``assigned``), moves (``reassigned``),
un-scheduling (``unscheduled``), idempotent no-ops, slot-bound
validation, and the argument-before-existence ordering.  All tests use
the shared ``conn`` fixture (a temporary on-disk SQLite database from
``conftest``); no display required.
"""

from __future__ import annotations

import sqlite3

import pytest

from kalsangati import tasks
from kalsangati.exceptions import InvalidTaskScheduleError, TaskNotFoundError
from kalsangati.services.schedule_task import (
    ScheduleResult,
    UnscheduleResult,
    schedule_task,
    unschedule_task,
)

WEEK = "2026-04-13"


def _backlog_task(conn: sqlite3.Connection) -> tasks.Task:
    """Create an unscheduled backlog task for scheduling tests."""
    return tasks.create(conn, "Write report", "01-02-el")


class TestScheduleTask:
    def test_schedule_backlog_task_assigns(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        result = schedule_task(
            conn, task.id, day="monday",
            start_min=540, end_min=600, week_start=WEEK,
        )
        assert isinstance(result, ScheduleResult)
        assert result.was_noop is False
        assert result.was_reschedule is False
        assert result.event is not None
        assert result.event.event_type == "assigned"

        row = tasks.get_by_id(conn, task.id)
        assert row is not None
        assert row.scheduled_day == "monday"
        assert row.scheduled_start_min == 540
        assert row.scheduled_end_min == 600
        assert row.scheduled_week_start == WEEK

    def test_schedule_persists_event_snapshot(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        schedule_task(
            conn, task.id, day="tuesday",
            start_min=60, end_min=120, week_start=WEEK,
        )
        events = tasks.get_task_events(conn, task.id)
        assert [e.event_type for e in events] == ["created", "assigned"]
        assigned = events[-1]
        assert assigned.scheduled_day == "tuesday"
        assert assigned.scheduled_start_min == 60
        assert assigned.scheduled_end_min == 120

    def test_reschedule_moves_slot_and_logs_reassigned(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        schedule_task(
            conn, task.id, day="monday",
            start_min=540, end_min=600, week_start=WEEK,
        )
        result = schedule_task(
            conn, task.id, day="wednesday",
            start_min=780, end_min=900, week_start=WEEK,
        )
        assert result.was_reschedule is True
        assert result.was_noop is False
        assert result.event is not None
        assert result.event.event_type == "reassigned"

        row = tasks.get_by_id(conn, task.id)
        assert row is not None
        assert row.scheduled_day == "wednesday"
        assert row.scheduled_start_min == 780
        assert row.scheduled_end_min == 900

    def test_identical_slot_is_noop(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        schedule_task(
            conn, task.id, day="monday",
            start_min=540, end_min=600, week_start=WEEK,
        )
        result = schedule_task(
            conn, task.id, day="monday",
            start_min=540, end_min=600, week_start=WEEK,
        )
        assert result.was_noop is True
        assert result.event is None
        events = tasks.get_task_events(conn, task.id)
        assert [e.event_type for e in events] == ["created", "assigned"]

    def test_schedule_does_not_change_status(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        assert task.status == "backlog"
        schedule_task(
            conn, task.id, day="monday",
            start_min=540, end_min=600, week_start=WEEK,
        )
        row = tasks.get_by_id(conn, task.id)
        assert row is not None
        assert row.status == "backlog"

    def test_missing_task_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(TaskNotFoundError):
            schedule_task(
                conn, 9999, day="monday",
                start_min=540, end_min=600, week_start=WEEK,
            )

    def test_boundary_start_zero_end_max_ok(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        result = schedule_task(
            conn, task.id, day="sunday",
            start_min=0, end_min=1440, week_start=WEEK,
        )
        assert result.was_noop is False
        row = tasks.get_by_id(conn, task.id)
        assert row is not None
        assert row.scheduled_start_min == 0
        assert row.scheduled_end_min == 1440


class TestScheduleValidation:
    def test_invalid_day_raises(self, conn: sqlite3.Connection) -> None:
        task = _backlog_task(conn)
        with pytest.raises(InvalidTaskScheduleError):
            schedule_task(
                conn, task.id, day="funday",
                start_min=540, end_min=600, week_start=WEEK,
            )

    def test_negative_start_raises(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        with pytest.raises(InvalidTaskScheduleError):
            schedule_task(
                conn, task.id, day="monday",
                start_min=-1, end_min=600, week_start=WEEK,
            )

    def test_start_at_midnight_boundary_raises(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        with pytest.raises(InvalidTaskScheduleError):
            schedule_task(
                conn, task.id, day="monday",
                start_min=1440, end_min=1441, week_start=WEEK,
            )

    def test_end_not_after_start_raises(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        with pytest.raises(InvalidTaskScheduleError):
            schedule_task(
                conn, task.id, day="monday",
                start_min=600, end_min=600, week_start=WEEK,
            )

    def test_end_past_max_raises(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        with pytest.raises(InvalidTaskScheduleError):
            schedule_task(
                conn, task.id, day="monday",
                start_min=600, end_min=1441, week_start=WEEK,
            )

    def test_invalid_week_start_raises(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        with pytest.raises(InvalidTaskScheduleError):
            schedule_task(
                conn, task.id, day="monday",
                start_min=540, end_min=600, week_start="13-04-2026",
            )

    def test_validation_runs_before_write(
        self, conn: sqlite3.Connection
    ) -> None:
        # A bad slot must not mutate the task or log an event.
        task = _backlog_task(conn)
        with pytest.raises(InvalidTaskScheduleError):
            schedule_task(
                conn, task.id, day="monday",
                start_min=600, end_min=500, week_start=WEEK,
            )
        row = tasks.get_by_id(conn, task.id)
        assert row is not None
        assert row.scheduled_day is None
        events = tasks.get_task_events(conn, task.id)
        assert [e.event_type for e in events] == ["created"]

    def test_slot_validated_before_existence(
        self, conn: sqlite3.Connection
    ) -> None:
        # The slot argument is checked before task existence, so a bad
        # slot on a missing id raises InvalidTaskScheduleError (mirrors
        # update_task_status checking its status argument first).
        with pytest.raises(InvalidTaskScheduleError):
            schedule_task(
                conn, 9999, day="funday",
                start_min=-1, end_min=-2, week_start="nope",
            )


class TestUnscheduleTask:
    def test_unschedule_clears_slot(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        schedule_task(
            conn, task.id, day="monday",
            start_min=540, end_min=600, week_start=WEEK,
        )
        result = unschedule_task(conn, task.id)
        assert isinstance(result, UnscheduleResult)
        assert result.was_noop is False
        assert result.event is not None
        assert result.event.event_type == "unscheduled"
        # Snapshot preserves the removed slot.
        assert result.event.scheduled_day == "monday"
        assert result.event.scheduled_start_min == 540
        assert result.event.scheduled_end_min == 600

        row = tasks.get_by_id(conn, task.id)
        assert row is not None
        assert row.scheduled_day is None
        assert row.scheduled_start_min is None
        assert row.scheduled_end_min is None
        assert row.scheduled_week_start is None

    def test_unschedule_unscheduled_task_is_noop(
        self, conn: sqlite3.Connection
    ) -> None:
        task = _backlog_task(conn)
        result = unschedule_task(conn, task.id)
        assert result.was_noop is True
        assert result.event is None
        events = tasks.get_task_events(conn, task.id)
        assert [e.event_type for e in events] == ["created"]

    def test_unschedule_missing_task_raises(
        self, conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(TaskNotFoundError):
            unschedule_task(conn, 9999)

    def test_reschedule_after_unschedule_is_assigned(
        self, conn: sqlite3.Connection
    ) -> None:
        # After clearing, the next placement is a fresh "assigned",
        # not a "reassigned".
        task = _backlog_task(conn)
        schedule_task(
            conn, task.id, day="monday",
            start_min=540, end_min=600, week_start=WEEK,
        )
        unschedule_task(conn, task.id)
        result = schedule_task(
            conn, task.id, day="friday",
            start_min=60, end_min=120, week_start=WEEK,
        )
        assert result.was_reschedule is False
        assert result.event is not None
        assert result.event.event_type == "assigned"
