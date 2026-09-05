"""Soft-delete a task and its subtree, and restore them.

Service #8 in the layer.  Follows the shape established across Units 3
to 9: existence check, no-op short-circuit, invariant check, then one
atomic write returning a structured result.

``deleted_at`` has existed since the v4 migration and every read in
:mod:`kalsangati.core.tasks` honours it, but nothing wrote it, and
``core.tasks.delete`` removed the row outright — cascading
``task_events`` through the foreign key and destroying the history the
column was added to preserve.  This service is the owner that makes
deletion reversible.

Design notes:

* **Deletion cascades down the subtree.**  Leaving children live under a
  deleted parent makes them unreachable in any tree view while they
  still appear in flat lists and still consume weekly capacity.

* **One timestamp per operation, and it is the undo key.**  Every row
  marked by a single :func:`delete_task` call carries the *same*
  ``deleted_at``; :func:`undelete_task` restores exactly the rows in the
  subtree carrying that value.

  Without that, consider deleting child C on Monday, deleting parent P
  on Tuesday, then restoring P.  A naive "restore the whole subtree"
  would resurrect C, which was removed deliberately in a separate
  operation.  Matching on the timestamp restores only what Tuesday took.

* **Microsecond precision, unlike the rest of the codebase.**  Other
  modules stamp with ``timespec="seconds"``.  Here that is not safe: two
  deletions in the same second would share an undo key and restore each
  other's rows.

* **Restoring under a deleted ancestor is refused**, not silently
  widened.  Restoring the ancestors too would undo a separate deletion
  the user performed on purpose — the same mistake the per-operation
  timestamp exists to prevent.

* **One event per affected row**, not one per operation.  The audit
  trail is per-task; a cascade logged only against the root leaves every
  descendant with no record of why it vanished.  Direct and cascaded
  deletions are distinguished in ``notes`` rather than by a separate
  verb, the way ``spilled`` distinguishes its two cases.

* Nothing touches Markdown notes files.  Soft deletion must not disturb
  them (spec §7.2), and nothing writes them yet in any case.

* No ``AppSignals`` emission — deferred with the other services' signals
  (``SKILL-state.md §14``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from kalsangati.core.exceptions import (
    TaskNotFoundError,
    TaskNotRestorableError,
)
from kalsangati.core.tasks import TaskEvent, get_by_id
from kalsangati.persistence.db import transaction

# Recursive walk downward from a task to every descendant, inclusive.
#
# UNION, not UNION ALL: UNION deduplicates, so the walk terminates even
# if the data somehow held a cycle.  The trg_tasks_no_cycle trigger makes
# that near-impossible, which is exactly why it would go unnoticed here —
# with UNION ALL this query would hang rather than fail.
_SUBTREE_CTE = """
WITH RECURSIVE subtree(id) AS (
    SELECT ?
    UNION
    SELECT t.id FROM tasks t JOIN subtree s ON t.parent_id = s.id
)
"""


# ── Result types ────────────────────────────────────────────────────────


@dataclass(slots=True)
class DeleteResult:
    """Outcome of a :func:`delete_task` call.

    Attributes:
        task_id: Id of the task deleted (echoed back).
        deleted_at: The timestamp written to every affected row.  This
            is the undo key :func:`undelete_task` matches on.  Empty
            string when ``was_noop``.
        cascaded_ids: Descendants marked, excluding the task itself,
            in id order.
        was_noop: ``True`` when the task was already deleted.  No write
            was issued and no events logged.
        events: One ``deleted`` event per affected row; empty on no-op.
    """

    task_id: int
    deleted_at: str
    cascaded_ids: list[int]
    was_noop: bool
    events: list[TaskEvent]


@dataclass(slots=True)
class UndeleteResult:
    """Outcome of an :func:`undelete_task` call.

    Attributes:
        task_id: Id of the task restored (echoed back).
        restored_ids: Descendants restored, excluding the task itself,
            in id order.  Descendants deleted in a *different* operation
            keep their own timestamp and are not restored.
        was_noop: ``True`` when the task was already live.
        events: One ``undeleted`` event per restored row; empty on
            no-op.
    """

    task_id: int
    restored_ids: list[int]
    was_noop: bool
    events: list[TaskEvent]


# ── Internals ───────────────────────────────────────────────────────────


def _subtree_ids(conn: sqlite3.Connection, task_id: int) -> list[int]:
    """Every id at or below ``task_id``, deleted or not."""
    rows = conn.execute(
        _SUBTREE_CTE + "SELECT id FROM subtree ORDER BY id", (task_id,)
    ).fetchall()
    return [r["id"] for r in rows]


def _deleted_ancestor(
    conn: sqlite3.Connection, task_id: int
) -> int | None:
    """Return the id of the nearest deleted ancestor, or ``None``.

    Walks upward from the task's parent.  Carries the same defensive
    ``seen`` guard as the reparent cycle check: an unbounded walk over
    data merely *assumed* acyclic is how a guard becomes the hang it was
    written to prevent.
    """
    seen: set[int] = set()
    row = conn.execute(
        "SELECT parent_id FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    current: int | None = row["parent_id"] if row else None
    while current is not None:
        if current in seen:
            break
        seen.add(current)
        row = conn.execute(
            "SELECT parent_id, deleted_at FROM tasks WHERE id = ?",
            (current,),
        ).fetchone()
        if row is None:
            break
        if row["deleted_at"] is not None:
            return current
        parent: int | None = row["parent_id"]
        current = parent
    return None


def _log_rows(
    cur: sqlite3.Cursor,
    ids: list[int],
    root_id: int,
    verb: str,
    when: str,
    root_parent_note: str,
) -> list[tuple[int, int, str]]:
    """Insert one event per id; return (event_id, task_id, notes).

    The ``scheduled_*`` snapshot columns stay NULL: a deletion says
    nothing about a calendar slot.
    """
    logged: list[tuple[int, int, str]] = []
    for tid in ids:
        note = verb if tid == root_id else root_parent_note
        cur.execute(
            "INSERT INTO task_events "
            "(task_id, event_type, event_at, notes) "
            "VALUES (?, ?, ?, ?)",
            (tid, verb, when, note),
        )
        event_id = cur.lastrowid
        assert event_id is not None  # guaranteed after a successful INSERT
        logged.append((event_id, tid, note))
    return logged


def _events_from(
    logged: list[tuple[int, int, str]], verb: str, when: str
) -> list[TaskEvent]:
    return [
        TaskEvent(
            id=event_id,
            task_id=tid,
            event_type=verb,
            event_at=when,
            scheduled_day=None,
            scheduled_start_min=None,
            scheduled_end_min=None,
            notes=note,
        )
        for event_id, tid, note in logged
    ]


# ── Public service entry points ─────────────────────────────────────────


def delete_task(
    conn: sqlite3.Connection, task_id: int
) -> DeleteResult:
    """Soft-delete a task and every descendant.

    The row and its history survive; every read filters
    ``deleted_at IS NULL``, so the task disappears from all views and
    can be restored by :func:`undelete_task`.

    Deleting an already-deleted task is an idempotent no-op, not an
    error.  Descendants already deleted in an earlier operation keep
    their own timestamp and are left alone, so restoring this deletion
    will not resurrect them.

    Args:
        conn: Database connection.
        task_id: Id of the task to delete.

    Returns:
        A :class:`DeleteResult` describing the outcome.

    Raises:
        TaskNotFoundError: If no live task exists with ``task_id``.
    """
    task = get_by_id(conn, task_id)
    if task is None:
        # Already deleted is a no-op rather than an error, so
        # distinguish "hidden" from "absent" before deciding.
        hidden = get_by_id(conn, task_id, include_deleted=True)
        if hidden is None:
            raise TaskNotFoundError(f"No task found with id {task_id}")
        return DeleteResult(
            task_id=task_id,
            deleted_at=hidden.deleted_at or "",
            cascaded_ids=[],
            was_noop=True,
            events=[],
        )

    # Microseconds, deliberately unlike the rest of the codebase: this
    # value is an undo key, and two deletions in the same second would
    # otherwise share one and restore each other's rows.
    now = datetime.now().isoformat(sep=" ")

    ids = _subtree_ids(conn, task_id)
    # Only rows that are currently live are part of this operation.  A
    # descendant deleted earlier keeps its own timestamp.
    live = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM tasks WHERE deleted_at IS NULL "
            f"AND id IN ({','.join('?' * len(ids))}) ORDER BY id",
            ids,
        ).fetchall()
    ]

    with transaction(conn) as cur:
        cur.execute(
            "UPDATE tasks SET deleted_at = ? "
            f"WHERE deleted_at IS NULL AND id IN ({','.join('?' * len(ids))})",
            [now, *ids],
        )
        logged = _log_rows(
            cur, live, task_id, "deleted", now,
            f"deleted with parent {task_id}",
        )

    return DeleteResult(
        task_id=task_id,
        deleted_at=now,
        cascaded_ids=[i for i in live if i != task_id],
        was_noop=False,
        events=_events_from(logged, "deleted", now),
    )


def undelete_task(
    conn: sqlite3.Connection, task_id: int
) -> UndeleteResult:
    """Restore a soft-deleted task and the descendants it took with it.

    Only rows whose ``deleted_at`` matches the task's are restored, so a
    descendant deleted separately stays deleted.

    Restoring a task that is already live is an idempotent no-op.

    Args:
        conn: Database connection.
        task_id: Id of the task to restore.

    Returns:
        An :class:`UndeleteResult` describing the outcome.

    Raises:
        TaskNotFoundError: If no task exists with ``task_id``.
        TaskNotRestorableError: If an ancestor is itself deleted, which
            would leave the restored task hanging off nothing.
    """
    task = get_by_id(conn, task_id, include_deleted=True)
    if task is None:
        raise TaskNotFoundError(f"No task found with id {task_id}")

    if task.deleted_at is None:
        return UndeleteResult(
            task_id=task_id, restored_ids=[], was_noop=True, events=[]
        )

    blocker = _deleted_ancestor(conn, task_id)
    if blocker is not None:
        raise TaskNotRestorableError(
            f"Task {task_id} cannot be restored while its ancestor "
            f"{blocker} is deleted. Restore task {blocker} first."
        )

    original = task.deleted_at
    ids = _subtree_ids(conn, task_id)
    placeholders = ",".join("?" * len(ids))
    restoring = [
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM tasks WHERE deleted_at = ? "
            f"AND id IN ({placeholders}) ORDER BY id",
            [original, *ids],
        ).fetchall()
    ]

    now = datetime.now().isoformat(sep=" ")
    with transaction(conn) as cur:
        cur.execute(
            f"UPDATE tasks SET deleted_at = NULL WHERE deleted_at = ? "
            f"AND id IN ({placeholders})",
            [original, *ids],
        )
        logged = _log_rows(
            cur, restoring, task_id, "undeleted", now,
            f"undeleted with parent {task_id}",
        )

    return UndeleteResult(
        task_id=task_id,
        restored_ids=[i for i in restoring if i != task_id],
        was_noop=False,
        events=_events_from(logged, "undeleted", now),
    )
