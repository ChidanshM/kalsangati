"""Niyam CRUD — blueprint schedule management.

A Niyam is a named weekly layout (e.g. "Spring 26", "Exam Week") stored as
a JSON blob of time blocks.  One Niyam is marked active at a time.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Optional

from kalsangati.db import parse_time_blocks, serialize_time_blocks, transaction

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


# ── Data classes ────────────────────────────────────────────────────────


@dataclass(slots=True)
class TimeBlock:
    """A single scheduled block within a day."""

    activity: str
    start: str       # "HH:MM"
    end: str         # "HH:MM"
    duration_h: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON storage."""
        return {
            "activity": self.activity,
            "start": self.start,
            "end": self.end,
            "duration_h": self.duration_h,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TimeBlock:
        """Construct from a stored dict."""
        return cls(
            activity=d["activity"],
            start=d["start"],
            end=d["end"],
            duration_h=float(d["duration_h"]),
        )


@dataclass(slots=True)
class Niyam:
    """A named blueprint schedule."""

    id: int
    name: str
    created_at: str
    is_active: bool
    time_blocks: dict[str, list[TimeBlock]] = field(default_factory=dict)

    @property
    def total_hours(self) -> float:
        """Sum of all block durations across the week."""
        return sum(
            b.duration_h for blocks in self.time_blocks.values() for b in blocks
        )

    @property
    def activity_set(self) -> set[str]:
        """Unique activity names in this Niyam."""
        return {
            b.activity for blocks in self.time_blocks.values() for b in blocks
        }

    @property
    def slot_count(self) -> int:
        """Total number of scheduled blocks."""
        return sum(len(blocks) for blocks in self.time_blocks.values())

    def hours_for_activity(self, activity: str) -> float:
        """Total weekly hours for a specific activity."""
        return sum(
            b.duration_h
            for blocks in self.time_blocks.values()
            for b in blocks
            if b.activity == activity
        )

    def blocks_for_day(self, day: str) -> list[TimeBlock]:
        """Return blocks for a given day name (lowercase)."""
        return self.time_blocks.get(day.lower(), [])

    def block_at(self, day: str, time_str: str) -> Optional[TimeBlock]:
        """Find the block covering a given time on a day.

        Args:
            day: Lowercase day name.
            time_str: Time in "HH:MM" format.

        Returns:
            The matching TimeBlock, or None.
        """
        for block in self.blocks_for_day(day):
            if block.start <= time_str < block.end:
                return block
        return None


# ── Row ↔ Niyam conversion ──────────────────────────────────────────────


def _row_to_niyam(row: sqlite3.Row) -> Niyam:
    """Convert a database row into a Niyam instance."""
    raw_blocks = parse_time_blocks(row["time_blocks"])
    blocks: dict[str, list[TimeBlock]] = {}
    for day, block_list in raw_blocks.items():
        blocks[day.lower()] = [TimeBlock.from_dict(b) for b in block_list]
    return Niyam(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        is_active=bool(row["is_active"]),
        time_blocks=blocks,
    )


def _blocks_to_json(blocks: dict[str, list[TimeBlock]]) -> str:
    """Serialize a blocks dict to JSON for storage."""
    raw: dict[str, list[dict[str, Any]]] = {}
    for day, block_list in blocks.items():
        raw[day.lower()] = [b.to_dict() for b in block_list]
    return serialize_time_blocks(raw)


# ── CRUD ────────────────────────────────────────────────────────────────


def get_all(conn: sqlite3.Connection) -> list[Niyam]:
    """Return all saved Niyam, newest first.

    Args:
        conn: Database connection.

    Returns:
        List of Niyam instances.
    """
    rows = conn.execute(
        "SELECT * FROM niyam ORDER BY id DESC"
    ).fetchall()
    return [_row_to_niyam(r) for r in rows]


def get_by_id(conn: sqlite3.Connection, niyam_id: int) -> Optional[Niyam]:
    """Fetch a single Niyam by primary key.

    Args:
        conn: Database connection.
        niyam_id: Row id.

    Returns:
        A Niyam instance, or None if not found.
    """
    row = conn.execute(
        "SELECT * FROM niyam WHERE id = ?", (niyam_id,)
    ).fetchone()
    return _row_to_niyam(row) if row else None


def get_active(conn: sqlite3.Connection) -> Optional[Niyam]:
    """Return the currently active Niyam, or None.

    Args:
        conn: Database connection.

    Returns:
        The active Niyam, or None.
    """
    row = conn.execute(
        "SELECT * FROM niyam WHERE is_active = 1"
    ).fetchone()
    return _row_to_niyam(row) if row else None


def create(
    conn: sqlite3.Connection,
    name: str,
    time_blocks: Optional[dict[str, list[TimeBlock]]] = None,
    *,
    set_active: bool = False,
) -> Niyam:
    """Create a new Niyam.

    Args:
        conn: Database connection.
        name: Display name (e.g. "Spring 26").
        time_blocks: Initial schedule blocks.  Empty dict if omitted.
        set_active: If True, deactivate others and set this as active.

    Returns:
        The newly created Niyam instance.
    """
    blocks = time_blocks or {}
    blocks_json = _blocks_to_json(blocks)
    now = datetime.now().isoformat(sep=" ", timespec="seconds")

    with transaction(conn) as cur:
        if set_active:
            cur.execute("UPDATE niyam SET is_active = 0")
        cur.execute(
            "INSERT INTO niyam (name, created_at, is_active, time_blocks) "
            "VALUES (?, ?, ?, ?)",
            (name, now, int(set_active), blocks_json),
        )
        niyam_id = cur.lastrowid

    return get_by_id(conn, niyam_id)  # type: ignore[return-value]


def update_blocks(
    conn: sqlite3.Connection,
    niyam_id: int,
    time_blocks: dict[str, list[TimeBlock]],
) -> None:
    """Replace the time_blocks JSON for a Niyam.

    Args:
        conn: Database connection.
        niyam_id: The Niyam to update.
        time_blocks: New blocks dict.
    """
    blocks_json = _blocks_to_json(time_blocks)
    conn.execute(
        "UPDATE niyam SET time_blocks = ? WHERE id = ?",
        (blocks_json, niyam_id),
    )
    conn.commit()


def rename(conn: sqlite3.Connection, niyam_id: int, new_name: str) -> None:
    """Rename a Niyam.

    Args:
        conn: Database connection.
        niyam_id: The Niyam to rename.
        new_name: New display name.
    """
    conn.execute(
        "UPDATE niyam SET name = ? WHERE id = ?", (new_name, niyam_id)
    )
    conn.commit()


def set_active(conn: sqlite3.Connection, niyam_id: int) -> None:
    """Mark a Niyam as the active one (deactivates all others).

    Args:
        conn: Database connection.
        niyam_id: The Niyam to activate.
    """
    with transaction(conn) as cur:
        cur.execute("UPDATE niyam SET is_active = 0")
        cur.execute(
            "UPDATE niyam SET is_active = 1 WHERE id = ?", (niyam_id,)
        )


def delete(conn: sqlite3.Connection, niyam_id: int) -> None:
    """Delete a Niyam by id.

    Args:
        conn: Database connection.
        niyam_id: The Niyam to remove.
    """
    conn.execute("DELETE FROM niyam WHERE id = ?", (niyam_id,))
    conn.commit()


def clone(
    conn: sqlite3.Connection,
    source_id: int,
    new_name: str,
) -> Niyam:
    """Clone an existing Niyam under a new name.

    Args:
        conn: Database connection.
        source_id: Id of the Niyam to clone.
        new_name: Name for the cloned copy.

    Returns:
        The newly created clone.

    Raises:
        ValueError: If the source Niyam doesn't exist.
    """
    source = get_by_id(conn, source_id)
    if source is None:
        raise ValueError(f"Niyam {source_id} not found")
    return create(conn, new_name, source.time_blocks)


# ── CSV import ──────────────────────────────────────────────────────────


def import_from_csv(
    conn: sqlite3.Connection,
    csv_path: Path | str,
    name: str,
    *,
    set_active_flag: bool = False,
) -> Niyam:
    """Import a Niyam from a structured CSV file.

    Expected CSV columns: ``day, activity, start, end, duration_h``

    Args:
        conn: Database connection.
        csv_path: Path to the CSV.
        name: Name for the imported Niyam.
        set_active_flag: Whether to activate immediately.

    Returns:
        The created Niyam.
    """
    blocks: dict[str, list[TimeBlock]] = {d: [] for d in DAYS}
    path = Path(csv_path)

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day = row["day"].strip().lower()
            if day not in blocks:
                continue
            blocks[day].append(
                TimeBlock(
                    activity=row["activity"].strip(),
                    start=row["start"].strip(),
                    end=row["end"].strip(),
                    duration_h=float(row["duration_h"].strip()),
                )
            )

    # Sort each day's blocks by start time
    for day in blocks:
        blocks[day].sort(key=lambda b: b.start)

    return create(conn, name, blocks, set_active=set_active_flag)


# ── Query helpers ───────────────────────────────────────────────────────


def activity_summary(niyam: Niyam) -> dict[str, dict[str, float | int]]:
    """Per-activity summary: total hours and slot count.

    Args:
        niyam: A Niyam instance.

    Returns:
        Dict mapping activity name → ``{"hours": float, "slots": int}``.
    """
    summary: dict[str, dict[str, float | int]] = {}
    for blocks in niyam.time_blocks.values():
        for b in blocks:
            entry = summary.setdefault(b.activity, {"hours": 0.0, "slots": 0})
            entry["hours"] += b.duration_h  # type: ignore[operator]
            entry["slots"] += 1  # type: ignore[operator]
    return summary
