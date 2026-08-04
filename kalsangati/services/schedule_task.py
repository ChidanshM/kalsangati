"""Place a task on the weekly calendar, or remove it, logging the change.

Fourth service in the six-service plan (see ``SKILL-state.md §9``).
Follows the "atomic small service" shape of
:func:`kalsangati.services.update_task_status.update_task_status`: an
argument-validation step, an existence check, no-op detection, and a
single atomic write returning a structured result.

Scheduling is the *calendar-placement* axis: it writes the task's four
``scheduled_*`` columns (day, start minute, end minute, week start).  It
is deliberately independent of the weekly-hours axis (``week_assigned``
+ ``status`` + ``estimated_hours``) that
:func:`kalsangati.tasks.capacity_for_activity` measures — placing a task
on the grid does not move it to ``this_week`` and does not change its
status.  Status changes are ``UpdateTaskStatus`` (service #5) territory.

What :func:`schedule_task` does, in order:

1. Validate the proposed slot's bounds before any DB work (raises
   :class:`kalsangati.exceptions.InvalidTaskScheduleError`) — mirroring
   the way ``update_task_status`` validates its status argument first,
   and turning what would otherwise be a raw ``sqlite3.IntegrityError``
   from the ``tasks`` all-or-nothing CHECK into a clean domain error.
2. Validate the task exists (raises
   :class:`kalsangati.exceptions.TaskNotFoundError`).
3. Detect the no-op case — scheduling a task to the exact slot it
   already occupies is not an error and logs no event
   (``was_noop=True``, ``event=None``).
4. Choose the ``task_events`` verb: ``assigned`` for a task that had no
   slot, ``reassigned`` for one being moved.  Both verbs are Unit-2
   vocabulary, unused until this service.
5. Execute the ``tasks`` UPDATE and the ``task_events`` INSERT inside a
   single transaction, so a crash can never leave a placement without
   its history row (or the reverse).

:func:`unschedule_task` is the inverse: it clears the four
``scheduled_*`` columns and logs an ``unscheduled`` event whose snapshot
preserves the slot that was removed.

Design notes:

* Atomic write is owned here, not composed from
  :func:`kalsangati.tasks.update` and
  :func:`kalsangati.tasks.log_task_event` (which each commit
  independently).  The single-savepoint write mirrors
  :func:`kalsangati.tasks.create` and
  :func:`kalsangati.services.update_task_status.update_task_status`.

* Capacity and slot-overlap validation are intentionally *not* done
  here.  ``capacity_for_activity`` measures weekly hours and is blind
  to ``scheduled_*`` slots, so it is the wrong tool for slot placement;
  and the overlap-collision UX (reject / straddle / split) is a
  deferred decision (``SKILL-state.md §14``) that needs the calendar
  grid to exist before it can be answered.  This service ships the
  validated write; the future grid consumer layers placement policy on
  top.

* No ``AppSignals`` emission yet — a ``task_scheduled`` signal is
  deferred with the other services' signals (§14).

* The ``task_events`` snapshot columns hold day/start/end only (there is
  no ``scheduled_week_start`` column on ``task_events``); ``week_start``
  is recorded in the event ``notes`` for audit completeness.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from kalsangati.db import transaction
from kalsangati.exceptions import InvalidTaskScheduleError, TaskNotFoundError
from kalsangati.tasks import TaskEvent, get_by_id

# ── Slot validation constants ───────────────────────────────────────────

# Valid weekday values for ``scheduled_day``; mirrors the ``tasks``
# CHECK.  Defined here (as tasks.py does locally) rather than imported —
# there is no shared day-name constant yet.
_VALID_DAYS: frozenset[str] = frozenset({
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
})

_MINUTES_PER_DAY: int = 1440


def _validate_slot(
    day: str, start_min: int, end_min: int, week_start: str
) -> None:
    """Validate a proposed calendar slot against the ``tasks`` bounds.

    Mirrors the populated branch of the all-or-nothing CHECK so a bad
    slot fails at the service boundary with a domain error rather than
    a raw ``sqlite3.IntegrityError`` at write time.

    Args:
        day: Weekday name, lowercase.
        start_min: Slot start, minutes since midnight.
        end_min: Slot end, minutes since midnight.
        week_start: ISO ``YYYY-MM-DD`` week-start date.

    Raises:
        InvalidTaskScheduleError: If any bound is violated.
    """
    if day not in _VALID_DAYS:
        raise InvalidTaskScheduleError(
            f"Unknown day {day!r}. Expected one of {sorted(_VALID_DAYS)}."
        )
    if not 0 <= start_min < _MINUTES_PER_DAY:
        raise InvalidTaskScheduleError(
            f"start_min {start_min} out of range [0, {_MINUTES_PER_DAY})."
        )
    if not start_min < end_min <= _MINUTES_PER_DAY:
        raise InvalidTaskScheduleError(
            f"end_min {end_min} must satisfy "
            f"{start_min} < end_min <= {_MINUTES_PER_DAY}."
        )
    try:
        datetime.strptime(week_start, "%Y-%m-%d")
    except ValueError:
        raise InvalidTaskScheduleError(
            f"week_start {week_start!r} is not an ISO YYYY-MM-DD date."
        ) from None


# ── Result types ────────────────────────────────────────────────────────


@dataclass(slots=True)
class ScheduleResult:
    """Outcome of a :func:`schedule_task` call.

    Attributes:
        task_id: Id of the scheduled task (echoed back).
        day: The weekday the task is now placed on.
        start_min: Slot start, minutes since midnight.
        end_min: Slot end, minutes since midnight.
        week_start: ISO week-start date the slot belongs to.
        was_reschedule: ``True`` if the task already had a slot (the
            move logged a ``reassigned`` event); ``False`` for a first
            placement (``assigned``).  Meaningless when ``was_noop``.
        was_noop: ``True`` when the requested slot equalled the task's
            current slot.  No UPDATE was issued, no event logged;
            ``event`` is ``None``.
        event: The ``task_events`` row logged, or ``None`` on no-op.
    """

    task_id: int
    day: str
    start_min: int
    end_min: int
    week_start: str
    was_reschedule: bool
    was_noop: bool
    event: TaskEvent | None


@dataclass(slots=True)
class UnscheduleResult:
    """Outcome of an :func:`unschedule_task` call.

    Attributes:
        task_id: Id of the task (echoed back).
        was_noop: ``True`` when the task had no slot to clear.  No
            UPDATE was issued, no event logged; ``event`` is ``None``.
        event: The ``unscheduled`` ``task_events`` row — whose snapshot
            columns preserve the slot that was removed — or ``None`` on
            no-op.
    """

    task_id: int
    was_noop: bool
    event: TaskEvent | None


# ── Public service entry points ─────────────────────────────────────────


def schedule_task(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    day: str,
    start_min: int,
    end_min: int,
    week_start: str,
) -> ScheduleResult:
    """Place a task on the weekly calendar, logging the placement.

    Scheduling to the exact slot a task already occupies is an
    idempotent no-op (``was_noop=True``, no event), not an error.  The
    task's status is not changed — placement and status are independent
    axes.

    Args:
        conn: Database connection.
        task_id: Id of the task to place.
        day: Weekday name, lowercase (``monday`` … ``sunday``).
        start_min: Slot start, minutes since midnight
            (``0 <= start_min < 1440``).
        end_min: Slot end, minutes since midnight
            (``start_min < end_min <= 1440``).
        week_start: ISO ``YYYY-MM-DD`` date of the week the slot
            belongs to.

    Returns:
        A :class:`ScheduleResult` describing the outcome.

    Raises:
        InvalidTaskScheduleError: If the slot fails a bound check.
        TaskNotFoundError: If no task exists with ``task_id``.
    """
    # 1. Validate the slot argument before any DB work.
    _validate_slot(day, start_min, end_min, week_start)

    # 2. Validate the task exists.
    task = get_by_id(conn, task_id)
    if task is None:
        raise TaskNotFoundError(f"No task found with id {task_id}")

    # 3. No-op short-circuit: an identical slot logs nothing.
    was_scheduled = task.scheduled_day is not None
    if (
        was_scheduled
        and task.scheduled_day == day
        and task.scheduled_start_min == start_min
        and task.scheduled_end_min == end_min
        and task.scheduled_week_start == week_start
    ):
        return ScheduleResult(
            task_id=task_id,
            day=day,
            start_min=start_min,
            end_min=end_min,
            week_start=week_start,
            was_reschedule=True,
            was_noop=True,
            event=None,
        )

    # 4. Choose the event verb: first placement vs move.
    event_type = "reassigned" if was_scheduled else "assigned"

    # 5. Atomic write: slot UPDATE + task_events INSERT in one savepoint.
    #    The event snapshot records the NEW slot (day/start/end); the
    #    week_start rides in notes since task_events has no week column.
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    note = f"{day} {start_min}-{end_min} (week {week_start})"
    with transaction(conn) as cur:
        cur.execute(
            "UPDATE tasks SET "
            "scheduled_day = ?, scheduled_start_min = ?, "
            "scheduled_end_min = ?, scheduled_week_start = ? "
            "WHERE id = ?",
            (day, start_min, end_min, week_start, task_id),
        )
        cur.execute(
            "INSERT INTO task_events "
            "(task_id, event_type, event_at, "
            " scheduled_day, scheduled_start_min, scheduled_end_min, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, event_type, now, day, start_min, end_min, note),
        )
        event_id = cur.lastrowid
    assert event_id is not None  # guaranteed after a successful INSERT

    event = TaskEvent(
        id=event_id,
        task_id=task_id,
        event_type=event_type,
        event_at=now,
        scheduled_day=day,
        scheduled_start_min=start_min,
        scheduled_end_min=end_min,
        notes=note,
    )

    return ScheduleResult(
        task_id=task_id,
        day=day,
        start_min=start_min,
        end_min=end_min,
        week_start=week_start,
        was_reschedule=was_scheduled,
        was_noop=False,
        event=event,
    )


def unschedule_task(
    conn: sqlite3.Connection,
    task_id: int,
) -> UnscheduleResult:
    """Remove a task from the weekly calendar, logging the removal.

    Clearing a task that has no slot is an idempotent no-op
    (``was_noop=True``, no event), not an error.  The task's status is
    not changed.

    Args:
        conn: Database connection.
        task_id: Id of the task to clear.

    Returns:
        An :class:`UnscheduleResult`.  On success, ``event`` snapshots
        the slot that was removed.

    Raises:
        TaskNotFoundError: If no task exists with ``task_id``.
    """
    task = get_by_id(conn, task_id)
    if task is None:
        raise TaskNotFoundError(f"No task found with id {task_id}")

    # No-op: nothing to clear.
    if task.scheduled_day is None:
        return UnscheduleResult(task_id=task_id, was_noop=True, event=None)

    # Snapshot the slot being removed for the event's history columns.
    old_day = task.scheduled_day
    old_start = task.scheduled_start_min
    old_end = task.scheduled_end_min
    note = f"cleared {old_day} {old_start}-{old_end}"

    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    with transaction(conn) as cur:
        cur.execute(
            "UPDATE tasks SET "
            "scheduled_day = NULL, scheduled_start_min = NULL, "
            "scheduled_end_min = NULL, scheduled_week_start = NULL "
            "WHERE id = ?",
            (task_id,),
        )
        cur.execute(
            "INSERT INTO task_events "
            "(task_id, event_type, event_at, "
            " scheduled_day, scheduled_start_min, scheduled_end_min, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, "unscheduled", now, old_day, old_start, old_end, note),
        )
        event_id = cur.lastrowid
    assert event_id is not None  # guaranteed after a successful INSERT

    event = TaskEvent(
        id=event_id,
        task_id=task_id,
        event_type="unscheduled",
        event_at=now,
        scheduled_day=old_day,
        scheduled_start_min=old_start,
        scheduled_end_min=old_end,
        notes=note,
    )

    return UnscheduleResult(task_id=task_id, was_noop=False, event=event)
