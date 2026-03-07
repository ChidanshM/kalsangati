"""Task management, scheduling, and time enforcement.

Tasks belong to projects (which belong to canonical activities).  This
module handles CRUD, capacity calculation, weekly assignment, and the
Kālachakra boundary spillover logic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from kalsangati.db import get_setting, transaction
from kalsangati.niyam import get_active


# ── Data classes ────────────────────────────────────────────────────────


@dataclass(slots=True)
class Task:
    """A task with scheduling metadata."""

    id: int
    title: str
    project_id: Optional[int]
    canonical_activity: str
    estimated_hours: Optional[float]
    due_date: Optional[str]
    status: str  # backlog | this_week | in_progress | done
    week_assigned: Optional[str]
    spilled_from: Optional[str]
    override_reason: Optional[str]
    notes: Optional[str]
    created_at: str


@dataclass(slots=True)
class CapacityInfo:
    """Capacity summary for an activity in a given week."""

    activity: str
    niyam_hours: float
    logged_hours: float
    assigned_hours: float

    @property
    def available(self) -> float:
        """Hours remaining after logged time."""
        return max(self.niyam_hours - self.logged_hours, 0.0)

    @property
    def slack(self) -> float:
        """Hours remaining after assigned tasks."""
        return self.available - self.assigned_hours

    @property
    def is_overbooked(self) -> bool:
        return self.slack < 0


# ── Row conversion ──────────────────────────────────────────────────────


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        project_id=row["project_id"],
        canonical_activity=row["canonical_activity"],
        estimated_hours=row["estimated_hours"],
        due_date=row["due_date"],
        status=row["status"],
        week_assigned=row["week_assigned"],
        spilled_from=row["spilled_from"],
        override_reason=row["override_reason"],
        notes=row["notes"],
        created_at=row["created_at"],
    )


# ── CRUD ────────────────────────────────────────────────────────────────


def get_all(
    conn: sqlite3.Connection,
    *,
    status: Optional[str] = None,
    activity: Optional[str] = None,
    week: Optional[str] = None,
) -> list[Task]:
    """Query tasks with optional filters.

    Args:
        conn: Database connection.
        status: Filter by status.
        activity: Filter by canonical_activity.
        week: Filter by week_assigned.

    Returns:
        List of matching tasks, ordered by due_date then title.
    """
    clauses: list[str] = []
    params: list[str] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if activity:
        clauses.append("canonical_activity = ?")
        params.append(activity)
    if week:
        clauses.append("week_assigned = ?")
        params.append(week)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM tasks {where} "
        "ORDER BY COALESCE(due_date, '9999-12-31'), title",
        params,
    ).fetchall()
    return [_row_to_task(r) for r in rows]


def get_by_id(conn: sqlite3.Connection, task_id: int) -> Optional[Task]:
    """Fetch a task by primary key.

    Args:
        conn: Database connection.
        task_id: Row id.

    Returns:
        A Task, or None.
    """
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return _row_to_task(row) if row else None


def create(
    conn: sqlite3.Connection,
    title: str,
    canonical_activity: str,
    *,
    project_id: Optional[int] = None,
    estimated_hours: Optional[float] = None,
    due_date: Optional[str] = None,
    status: str = "backlog",
    week_assigned: Optional[str] = None,
    notes: Optional[str] = None,
) -> Task:
    """Create a new task.

    Args:
        conn: Database connection.
        title: Task title.
        canonical_activity: Activity this task belongs to.
        project_id: Optional parent project.
        estimated_hours: Estimated time to complete.
        due_date: Target date (YYYY-MM-DD).
        status: Initial status.
        week_assigned: Week to schedule in.
        notes: Free-text notes.

    Returns:
        The newly created Task.
    """
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO tasks "
            "(title, project_id, canonical_activity, estimated_hours, "
            " due_date, status, week_assigned, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, project_id, canonical_activity, estimated_hours,
             due_date, status, week_assigned, notes, now),
        )
        tid = cur.lastrowid
    return get_by_id(conn, tid)  # type: ignore[return-value]


def update(
    conn: sqlite3.Connection,
    task_id: int,
    **kwargs: Optional[str | int | float],
) -> None:
    """Update task fields.

    Args:
        conn: Database connection.
        task_id: Row id.
        **kwargs: Field name → new value pairs.
    """
    allowed = {
        "title", "project_id", "canonical_activity", "estimated_hours",
        "due_date", "status", "week_assigned", "spilled_from",
        "override_reason", "notes",
    }
    fields: list[str] = []
    params: list[str | int | float | None] = []
    for key, val in kwargs.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        params.append(val)  # type: ignore[arg-type]
    if not fields:
        return
    params.append(task_id)
    conn.execute(
        f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", params
    )
    conn.commit()


def delete(conn: sqlite3.Connection, task_id: int) -> None:
    """Delete a task.

    Args:
        conn: Database connection.
        task_id: Row id.
    """
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()


def set_status(
    conn: sqlite3.Connection, task_id: int, status: str
) -> None:
    """Change a task's status.

    Args:
        conn: Database connection.
        task_id: Row id.
        status: New status value.
    """
    update(conn, task_id, status=status)


# ── Capacity ────────────────────────────────────────────────────────────


def capacity_for_activity(
    conn: sqlite3.Connection,
    activity: str,
    week_start: Optional[str] = None,
) -> CapacityInfo:
    """Compute capacity for an activity in a given week.

    capacity = niyam_hours − logged_hours
    slack    = capacity − sum(estimated_hours of assigned tasks)

    Args:
        conn: Database connection.
        activity: Canonical activity name.
        week_start: Week start date.  Defaults to current week.

    Returns:
        A CapacityInfo instance.
    """
    from kalsangati.analytics import _current_week_start

    start_day = get_setting(conn, "week_start_day") or "monday"
    ws = week_start or _current_week_start(start_day)
    we = (datetime.strptime(ws, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")

    # Niyam hours
    niyam = get_active(conn)
    niyam_hours = niyam.hours_for_activity(activity) if niyam else 0.0

    # Logged hours
    row = conn.execute(
        """
        SELECT COALESCE(SUM(duration_min), 0) / 60.0 AS hours
        FROM kalrekha
        WHERE date >= ? AND date <= ?
          AND (project = ? OR project IN (
               SELECT raw_label FROM label_mappings
               WHERE canonical_label = ?))
        """,
        (ws, we, activity, activity),
    ).fetchone()
    logged = row["hours"] if row else 0.0

    # Assigned estimated hours
    row2 = conn.execute(
        """
        SELECT COALESCE(SUM(estimated_hours), 0) AS hours
        FROM tasks
        WHERE canonical_activity = ?
          AND week_assigned = ?
          AND status IN ('this_week', 'in_progress')
        """,
        (activity, ws),
    ).fetchone()
    assigned = row2["hours"] if row2 else 0.0

    return CapacityInfo(
        activity=activity,
        niyam_hours=niyam_hours,
        logged_hours=logged,
        assigned_hours=assigned,
    )


def all_capacities(
    conn: sqlite3.Connection,
    week_start: Optional[str] = None,
) -> list[CapacityInfo]:
    """Compute capacity for all activities with assigned tasks or Niyam hours.

    Args:
        conn: Database connection.
        week_start: Week to compute for.

    Returns:
        List of CapacityInfo, sorted by activity name.
    """
    niyam = get_active(conn)
    activities: set[str] = set()
    if niyam:
        activities.update(niyam.activity_set)

    # Also include activities from assigned tasks
    from kalsangati.analytics import _current_week_start
    start_day = get_setting(conn, "week_start_day") or "monday"
    ws = week_start or _current_week_start(start_day)

    rows = conn.execute(
        "SELECT DISTINCT canonical_activity FROM tasks "
        "WHERE week_assigned = ?",
        (ws,),
    ).fetchall()
    activities.update(r["canonical_activity"] for r in rows)

    return sorted(
        [capacity_for_activity(conn, a, ws) for a in activities],
        key=lambda c: c.activity,
    )


# ── Spillover ───────────────────────────────────────────────────────────


def process_spillover(
    conn: sqlite3.Connection,
    week_start: str,
) -> int:
    """Move incomplete tasks from a past week back to backlog.

    Sets ``spilled_from`` to preserve the original ``week_assigned``.
    Called at Kālachakra boundary.

    Args:
        conn: Database connection.
        week_start: The week that just ended.

    Returns:
        Number of tasks spilled.
    """
    with transaction(conn) as cur:
        cur.execute(
            """
            UPDATE tasks
            SET status = 'backlog',
                spilled_from = week_assigned,
                week_assigned = NULL
            WHERE week_assigned = ?
              AND status IN ('this_week', 'in_progress')
            """,
            (week_start,),
        )
        return cur.rowcount


# ── Time enforcement helpers ────────────────────────────────────────────


def check_block_alignment(
    conn: sqlite3.Connection,
    activity: str,
    at_time: Optional[datetime] = None,
) -> dict[str, str | bool]:
    """Check if starting work on an activity is within its Niyam block.

    Args:
        conn: Database connection.
        activity: Canonical activity to check.
        at_time: Time to check (defaults to now).

    Returns:
        Dict with:
        - ``aligned`` (bool): True if within a scheduled block.
        - ``next_block_day`` (str): Day of next scheduled block.
        - ``next_block_time`` (str): Start time of next block.
    """
    now = at_time or datetime.now()
    day_names = ("monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday")
    current_day = day_names[now.weekday()]
    current_time = now.strftime("%H:%M")

    niyam = get_active(conn)
    if niyam is None:
        return {"aligned": True, "next_block_day": "", "next_block_time": ""}

    # Check current block
    for block in niyam.blocks_for_day(current_day):
        if block.activity == activity and block.start <= current_time < block.end:
            return {"aligned": True, "next_block_day": "", "next_block_time": ""}

    # Find next block for this activity
    for offset in range(7):
        check_day = day_names[(now.weekday() + offset) % 7]
        for block in niyam.blocks_for_day(check_day):
            if block.activity != activity:
                continue
            if offset == 0 and block.start <= current_time:
                continue  # already past this block today
            return {
                "aligned": False,
                "next_block_day": check_day.capitalize(),
                "next_block_time": block.start,
            }

    return {"aligned": False, "next_block_day": "None", "next_block_time": ""}
