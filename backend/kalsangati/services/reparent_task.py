"""Move a task to a different parent, or promote it to a root.

Service #7 in the layer.  Follows the shape established across Units 3
to 9 and mirrored most closely by
:mod:`kalsangati.services.schedule_task`: check existence, short-circuit
the no-op, validate the invariant, then a single atomic write returning
a structured result.

``parent_id`` has existed since the v4 migration and been readable since
the task module gained its hierarchy fields, but nothing could write it.
:func:`kalsangati.core.tasks.update` deliberately excludes it, because a
move needs a cycle check and an audit event and a general-purpose field
setter provides neither.  This service is that owner.

Design notes:

* **The cycle check duplicates a database trigger, on purpose.**
  ``trg_tasks_no_cycle`` rejects any ``parent_id`` update that would
  make a task its own ancestor, and raises ``sqlite3.IntegrityError`` —
  a persistence-layer error surfacing in a GUI handler that catches
  :class:`kalsangati.core.exceptions.KalsangatiError`.  Checking here
  first gives the caller
  :class:`kalsangati.core.exceptions.TaskCycleError` instead.  The
  trigger stays as the backstop for every path that is not this
  service: a future API endpoint, a manual ``sqlite3`` session, a bug.
  Same reasoning as ``schedule_task`` validating slot bounds ahead of
  the ``tasks`` CHECK.

* **``sort_order`` is recomputed.**  A moved task joins a different
  sibling group, where its old position means nothing — it could
  collide with an existing sibling or sort into the middle arbitrarily.
  The task lands last among its new siblings.

* **``project_id`` is untouched.**  A task is filed once, deliberately
  or by default, and moving its parent must not silently change where
  it lives.

* **Children are not touched either.**  They keep pointing at the moved
  task, so the whole subtree follows implicitly.  Nothing cascades.

* **Notes files are not moved.**  A task's Markdown path will be derived
  from its ancestry, so reparenting will eventually relocate a subtree
  on disk.  Nothing derives a path yet.

* No ``AppSignals`` emission — a ``task_reparented`` signal is deferred
  with the other services' signals (``SKILL-state.md §14``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from kalsangati.core.exceptions import TaskCycleError, TaskNotFoundError
from kalsangati.core.tasks import TaskEvent, get_by_id
from kalsangati.persistence.db import transaction

# ── Result type ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class ReparentResult:
    """Outcome of a :func:`reparent_task` call.

    Attributes:
        task_id: Id of the moved task (echoed back).
        previous_parent_id: The parent before the move; ``None`` if the
            task was a root.
        new_parent_id: The parent after the move; ``None`` if the task
            was promoted to a root.
        new_sort_order: The task's position among its new siblings.
            Unchanged from its current value when ``was_noop``.
        was_noop: ``True`` when the requested parent equalled the
            task's current parent.  No UPDATE was issued, no event
            logged; ``event`` is ``None``.
        event: The ``task_events`` row logged, or ``None`` on no-op.
    """

    task_id: int
    previous_parent_id: int | None
    new_parent_id: int | None
    new_sort_order: float
    was_noop: bool
    event: TaskEvent | None


# ── Internals ───────────────────────────────────────────────────────────


def _would_create_cycle(
    conn: sqlite3.Connection, task_id: int, new_parent_id: int
) -> bool:
    """Walk up from ``new_parent_id`` looking for ``task_id``.

    If the task appears in its proposed parent's ancestor chain, the
    move would make the task its own ancestor.

    The self-parent case needs no separate branch: when
    ``new_parent_id == task_id`` the first iteration matches.

    Args:
        conn: Database connection.
        task_id: The task being moved.
        new_parent_id: The proposed parent.

    Returns:
        ``True`` if the move would close a cycle.
    """
    seen: set[int] = set()
    current: int | None = new_parent_id
    while current is not None:
        if current == task_id:
            return True
        if current in seen:
            # Unreachable while the trigger holds.  Guarding anyway: an
            # unbounded walk over data merely *assumed* acyclic is how a
            # guard becomes the hang it was written to prevent — the
            # same reason the trigger's CTE uses UNION, not UNION ALL.
            break
        seen.add(current)
        row = conn.execute(
            "SELECT parent_id FROM tasks WHERE id = ?", (current,)
        ).fetchone()
        if row is None:
            break
        parent: int | None = row["parent_id"]
        current = parent
    return False


def _next_sibling_order(
    conn: sqlite3.Connection, parent_id: int | None, moving_task_id: int
) -> float:
    """Position for a task joining ``parent_id``'s children: last.

    ``IS``, not ``=``: comparison against NULL is never true in SQL, so
    ``parent_id = NULL`` matches no rows and every task promoted to a
    root would collapse onto the same value.

    The moving task is excluded from the maximum.  Without that, a
    root-to-root move would count the task's own current position and
    push it one place further out each time.

    Args:
        conn: Database connection.
        parent_id: The new parent, or ``None`` for the root group.
        moving_task_id: Excluded from the maximum.

    Returns:
        One more than the highest ``sort_order`` among the new
        siblings; ``1.0`` for an empty group.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order "
        "FROM tasks WHERE parent_id IS ? AND id != ?",
        (parent_id, moving_task_id),
    ).fetchone()
    next_order: float = row["next_order"]
    return next_order


def _describe(previous: int | None, new: int | None) -> str:
    """Render the transition for the event's ``notes`` column."""
    return f"parent {previous if previous is not None else 'none'} -> " \
           f"{new if new is not None else 'none'}"


# ── Public service entry point ──────────────────────────────────────────


def reparent_task(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    new_parent_id: int | None,
) -> ReparentResult:
    """Move a task under a different parent, or promote it to a root.

    Moving a task to the parent it already has is an idempotent no-op
    (``was_noop=True``, no event), not an error.  Passing
    ``new_parent_id=None`` promotes the task to the top level.

    ``new_parent_id`` is keyword-only so a call site cannot silently
    swap the two ids.

    Args:
        conn: Database connection.
        task_id: Id of the task to move.
        new_parent_id: Id of the new parent, or ``None`` for a root.

    Returns:
        A :class:`ReparentResult` describing the outcome.

    Raises:
        TaskNotFoundError: If ``task_id`` names no task, or if
            ``new_parent_id`` is not ``None`` and names no task.
        TaskCycleError: If the move would make the task its own
            ancestor, including the self-parent case.
    """
    # 1. The task exists.
    task = get_by_id(conn, task_id)
    if task is None:
        raise TaskNotFoundError(f"No task found with id {task_id}")

    # 2. The proposed parent exists.  A separate message from the one
    #    above, so a log distinguishes which id was bad.
    if new_parent_id is not None:
        parent = get_by_id(conn, new_parent_id)
        if parent is None:
            raise TaskNotFoundError(
                f"No task found with id {new_parent_id} to use as parent"
            )

    # 3. No-op short-circuit.  Covers root -> root, where both are None.
    previous_parent_id = task.parent_id
    if previous_parent_id == new_parent_id:
        return ReparentResult(
            task_id=task_id,
            previous_parent_id=previous_parent_id,
            new_parent_id=new_parent_id,
            new_sort_order=task.sort_order,
            was_noop=True,
            event=None,
        )

    # 4. Reject a move that would close a cycle, before any write, so
    #    the caller sees a domain error rather than the trigger's
    #    IntegrityError.
    if new_parent_id is not None and _would_create_cycle(
        conn, task_id, new_parent_id
    ):
        raise TaskCycleError(
            f"Moving task {task_id} under task {new_parent_id} would make "
            f"it its own ancestor."
        )

    # 5. Atomic write: parent and position UPDATE + task_events INSERT
    #    in one savepoint, so a failure leaves neither behind.
    new_sort_order = _next_sibling_order(conn, new_parent_id, task_id)
    note = _describe(previous_parent_id, new_parent_id)
    now = datetime.now().isoformat(sep=" ", timespec="seconds")

    with transaction(conn) as cur:
        cur.execute(
            "UPDATE tasks SET parent_id = ?, sort_order = ? WHERE id = ?",
            (new_parent_id, new_sort_order, task_id),
        )
        # The scheduled_* snapshot columns are irrelevant to a move, so
        # they stay NULL; the transition itself rides in notes.
        cur.execute(
            "INSERT INTO task_events "
            "(task_id, event_type, event_at, notes) "
            "VALUES (?, ?, ?, ?)",
            (task_id, "reparented", now, note),
        )
        event_id = cur.lastrowid
    assert event_id is not None  # guaranteed after a successful INSERT

    event = TaskEvent(
        id=event_id,
        task_id=task_id,
        event_type="reparented",
        event_at=now,
        scheduled_day=None,
        scheduled_start_min=None,
        scheduled_end_min=None,
        notes=note,
    )

    return ReparentResult(
        task_id=task_id,
        previous_parent_id=previous_parent_id,
        new_parent_id=new_parent_id,
        new_sort_order=new_sort_order,
        was_noop=False,
        event=event,
    )
