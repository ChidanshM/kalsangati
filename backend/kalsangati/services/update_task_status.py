"""Change a task's lifecycle status, logging the transition.

Fifth service in the six-service plan (see ``SKILL-state.md §9``).
Follows the "atomic small service" shape established by
:func:`kalsangati.services.set_active_niyam.set_active_niyam`: a
validation step, a domain operation, and a structured result.

What the service does, in order:

1. Reject an unrecognised target status before touching the DB
   (raises :class:`kalsangati.core.exceptions.InvalidTaskTransitionError`).
2. Validate that the task exists (raises
   :class:`kalsangati.core.exceptions.TaskNotFoundError` if not).
3. Detect the no-op case — setting a task to the status it already
   holds is not an error and logs no event.  The call succeeds and
   the result carries ``was_noop=True`` with ``event=None``.
4. Reject illegal lifecycle moves per ``_LEGAL_TRANSITIONS`` (raises
   :class:`kalsangati.core.exceptions.InvalidTaskTransitionError`).
5. Choose the ``task_events`` verb for the transition
   (see :func:`_event_type_for`).
6. Execute the ``tasks`` UPDATE and the ``task_events`` INSERT inside
   a single transaction, so a crash can never leave a status change
   without its matching history row (or the reverse).

Design notes:

* Atomic write is owned here, not composed from
  :func:`kalsangati.core.tasks.set_status` and
  :func:`kalsangati.core.tasks.log_task_event`.  Those two helpers each
  commit independently, so calling them in sequence would be two
  commits — not atomic.  The single-savepoint write below mirrors the
  pattern already used by :func:`kalsangati.core.tasks.create`, which
  inserts the task row and its ``created`` event together.

* Transition validation (D2) is separable from the rest of the
  service.  ``_LEGAL_TRANSITIONS`` is a single module-level table; a
  fully-permissive policy is one deletion away and the atomic write
  and no-op detection stand on their own without it.

* Event vocabulary (D1) reuses the Unit-2 verbs where they fit
  (``on_hold`` / ``resumed`` / ``ended``) and adds ``started`` /
  ``planned`` / ``backlogged`` for the transitions Unit 2 did not
  anticipate.  The full ``previous→new`` pair is always recorded in
  the event's ``notes`` for precise auditing regardless of verb.

* No ``AppSignals`` emission yet.  A future ``task_status_changed``
  signal is deferred until the signal-consumer count justifies the
  abstraction (§14); a GUI consumer would emit its own signal after a
  successful call, as the niyam editor does today.

* ``scheduled_*`` fields are snapshot into the event row from the task
  as it stands, matching :func:`kalsangati.core.tasks.log_task_event`'s
  history-preserving contract.  This service does not itself touch the
  schedule — status is its only responsibility; slot assignment is
  ``ScheduleTask`` (service #4) territory.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from kalsangati.core.exceptions import InvalidTaskTransitionError, TaskNotFoundError
from kalsangati.core.tasks import TaskEvent, get_by_id
from kalsangati.persistence.db import transaction

# ── Lifecycle policy ────────────────────────────────────────────────────

# Legal status moves, keyed by the current status.  Self-edges are
# intentionally absent: a same-status call is handled as a no-op before
# this table is consulted, so it never needs a self-loop.  ``done`` is
# reopenable to ``in_progress`` or ``backlog`` only.
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "backlog": frozenset({"this_week", "in_progress", "on_hold", "done"}),
    "this_week": frozenset({"in_progress", "on_hold", "backlog", "done"}),
    "in_progress": frozenset({"on_hold", "done", "this_week", "backlog"}),
    "on_hold": frozenset({"in_progress", "this_week", "backlog", "done"}),
    "done": frozenset({"in_progress", "backlog"}),
}

# All recognised status values, derived from the transition table so the
# two never drift.
_VALID_STATUSES: frozenset[str] = frozenset(_LEGAL_TRANSITIONS)

# Destination-based ``task_events`` verb for each target status.  The
# on-hold exit is special-cased in :func:`_event_type_for`.
_TARGET_EVENT_TYPE: dict[str, str] = {
    "in_progress": "started",
    "this_week": "planned",
    "backlog": "backlogged",
    "on_hold": "on_hold",
    "done": "ended",
}


def _event_type_for(previous: str, new: str) -> str:
    """Return the ``task_events.event_type`` verb for a transition.

    Leaving ``on_hold`` for an active planning/working state
    (``in_progress`` or ``this_week``) is a ``resumed`` event — the
    Unit-2 verb for un-pausing — rather than a plain destination verb.
    ``on_hold → backlog`` and ``on_hold → done`` keep their
    destination verbs (``backlogged`` / ``ended``), since neither is a
    "carry on where you left off" move.

    Args:
        previous: The status the task is moving from.
        new: The status the task is moving to.

    Returns:
        A verb guaranteed to be a member of
        :data:`kalsangati.core.tasks.EVENT_TYPES`.
    """
    if previous == "on_hold" and new in {"in_progress", "this_week"}:
        return "resumed"
    return _TARGET_EVENT_TYPE[new]


# ── Result type ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class UpdateStatusResult:
    """Outcome of an :func:`update_task_status` call.

    Attributes:
        task_id: Id of the task whose status was addressed (echoed back
            for consumer convenience).
        previous_status: The status the task held before the call.  On
            a no-op this equals ``new_status``.
        new_status: The status requested (and now in effect, unless the
            call was a no-op — in which case it was already in effect).
        was_noop: ``True`` when the requested status equalled the
            current status.  No UPDATE was issued and no event was
            logged; ``event`` is ``None``.  Consumers that want to
            suppress user-facing feedback on redundant calls branch on
            this flag.
        event: The :class:`kalsangati.core.tasks.TaskEvent` logged for the
            transition, or ``None`` on the no-op path.
    """

    task_id: int
    previous_status: str
    new_status: str
    was_noop: bool
    event: TaskEvent | None


# ── Public service entry point ──────────────────────────────────────────


def update_task_status(
    conn: sqlite3.Connection,
    task_id: int,
    new_status: str,
) -> UpdateStatusResult:
    """Change a task's status, logging the transition atomically.

    Re-setting a task to its current status is an idempotent no-op
    (``was_noop=True``, no event logged), not an error.  Illegal moves
    and unrecognised target statuses raise.

    Args:
        conn: Database connection.
        task_id: Id of the task to transition.
        new_status: Target status — one of ``backlog``, ``this_week``,
            ``in_progress``, ``on_hold``, ``done``.

    Returns:
        An :class:`UpdateStatusResult` describing the outcome.

    Raises:
        InvalidTaskTransitionError: If ``new_status`` is not a
            recognised status, or the move from the current status to
            ``new_status`` is not an allowed edge.
        TaskNotFoundError: If no task exists with ``task_id``.
    """
    # 1. Reject an unrecognised target before any DB work.
    if new_status not in _VALID_STATUSES:
        raise InvalidTaskTransitionError(
            f"Unknown status {new_status!r}. "
            f"Expected one of {sorted(_VALID_STATUSES)}."
        )

    # 2. Validate the task exists.
    task = get_by_id(conn, task_id)
    if task is None:
        raise TaskNotFoundError(f"No task found with id {task_id}")

    previous_status = task.status

    # 3. No-op short-circuit: setting the current status logs nothing.
    if previous_status == new_status:
        return UpdateStatusResult(
            task_id=task_id,
            previous_status=previous_status,
            new_status=new_status,
            was_noop=True,
            event=None,
        )

    # 4. Reject illegal lifecycle moves.
    if new_status not in _LEGAL_TRANSITIONS[previous_status]:
        raise InvalidTaskTransitionError(
            f"Cannot move task {task_id} from {previous_status!r} "
            f"to {new_status!r}."
        )

    # 5. Choose the event verb for this transition.
    event_type = _event_type_for(previous_status, new_status)

    # 6. Atomic write: status UPDATE + task_events INSERT in one
    #    savepoint.  scheduled_* are snapshot from the task as it
    #    stands (history-preserving, per log_task_event's contract).
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    note = f"{previous_status}\u2192{new_status}"
    with transaction(conn) as cur:
        cur.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (new_status, task_id),
        )
        cur.execute(
            "INSERT INTO task_events "
            "(task_id, event_type, event_at, "
            " scheduled_day, scheduled_start_min, scheduled_end_min, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, event_type, now,
             task.scheduled_day, task.scheduled_start_min,
             task.scheduled_end_min, note),
        )
        event_id = cur.lastrowid
    assert event_id is not None  # guaranteed after a successful INSERT

    event = TaskEvent(
        id=event_id,
        task_id=task_id,
        event_type=event_type,
        event_at=now,
        scheduled_day=task.scheduled_day,
        scheduled_start_min=task.scheduled_start_min,
        scheduled_end_min=task.scheduled_end_min,
        notes=note,
    )

    return UpdateStatusResult(
        task_id=task_id,
        previous_status=previous_status,
        new_status=new_status,
        was_noop=False,
        event=event,
    )


def allowed_transitions(current_status: str) -> frozenset[str]:
    """Return the statuses ``current_status`` may legally move to.

    Excludes the current status itself (a same-status call is a no-op,
    not a transition).  Returns an empty frozenset for an unrecognised
    status rather than raising, so a caller populating UI controls can
    fail soft.

    Args:
        current_status: The status to look up.

    Returns:
        The legal target statuses, or an empty frozenset if
        ``current_status`` is not a recognised status.
    """
    return _LEGAL_TRANSITIONS.get(current_status, frozenset())
