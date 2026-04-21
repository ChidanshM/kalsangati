"""Project management — CRUD and activity lookup.

Each project belongs to exactly one canonical activity.  Tasks inherit
their ``canonical_activity`` from their parent project.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from kalsangati.db import transaction


@dataclass(slots=True)
class Project:
    """A project linked to a canonical activity."""

    id: int
    name: str
    canonical_activity: str
    color: str | None = None
    notes: str | None = None


# ── Row conversion ──────────────────────────────────────────────────────


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        canonical_activity=row["canonical_activity"],
        color=row["color"],
        notes=row["notes"],
    )


# ── CRUD ────────────────────────────────────────────────────────────────


def get_all(conn: sqlite3.Connection) -> list[Project]:
    """Return all projects, sorted by name.

    Args:
        conn: Database connection.

    Returns:
        List of Project instances.
    """
    rows = conn.execute(
        "SELECT * FROM projects ORDER BY name"
    ).fetchall()
    return [_row_to_project(r) for r in rows]


def get_by_id(conn: sqlite3.Connection, project_id: int) -> Project | None:
    """Fetch a project by primary key.

    Args:
        conn: Database connection.
        project_id: Row id.

    Returns:
        A Project, or None.
    """
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    return _row_to_project(row) if row else None


def get_by_activity(
    conn: sqlite3.Connection, canonical_activity: str
) -> list[Project]:
    """Return projects belonging to a canonical activity.

    Args:
        conn: Database connection.
        canonical_activity: The activity label.

    Returns:
        List of matching projects.
    """
    rows = conn.execute(
        "SELECT * FROM projects WHERE canonical_activity = ? ORDER BY name",
        (canonical_activity,),
    ).fetchall()
    return [_row_to_project(r) for r in rows]


def create(
    conn: sqlite3.Connection,
    name: str,
    canonical_activity: str,
    color: str | None = None,
    notes: str | None = None,
) -> Project:
    """Create a new project.

    Args:
        conn: Database connection.
        name: Display name.
        canonical_activity: The activity this project belongs to.
        color: Optional hex color for UI display.
        notes: Optional free-text notes.

    Returns:
        The newly created Project.
    """
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO projects (name, canonical_activity, color, notes) "
            "VALUES (?, ?, ?, ?)",
            (name, canonical_activity, color, notes),
        )
        pid = cur.lastrowid
    assert pid is not None  # guaranteed after a successful INSERT
    result = get_by_id(conn, pid)
    assert result is not None  # round-trip of the row we just inserted
    return result


def update(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    name: str | None = None,
    canonical_activity: str | None = None,
    color: str | None = None,
    notes: str | None = None,
) -> None:
    """Update project fields.

    Args:
        conn: Database connection.
        project_id: Row id.
        name: New name (if changing).
        canonical_activity: New activity (if changing).
        color: New color (if changing).
        notes: New notes (if changing).
    """
    fields: list[str] = []
    params: list[str | int | None] = []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if canonical_activity is not None:
        fields.append("canonical_activity = ?")
        params.append(canonical_activity)
    if color is not None:
        fields.append("color = ?")
        params.append(color)
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes)
    if not fields:
        return
    params.append(project_id)
    conn.execute(
        f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", params
    )
    conn.commit()


def delete(conn: sqlite3.Connection, project_id: int) -> None:
    """Delete a project and orphan its tasks.

    Args:
        conn: Database connection.
        project_id: Row id.
    """
    with transaction(conn) as cur:
        cur.execute(
            "UPDATE tasks SET project_id = NULL WHERE project_id = ?",
            (project_id,),
        )
        cur.execute("DELETE FROM projects WHERE id = ?", (project_id,))
