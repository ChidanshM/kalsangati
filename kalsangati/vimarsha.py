"""Vimarśa — the reflection engine.

Three-layer comparison (prescribed / planned / unplanned) and reflection
flags that surface patterns like chronic overrides and underused blocks.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from kalsangati.db import get_setting
from kalsangati.labels import resolve_label, resolve_hierarchy
from kalsangati.niyam import Niyam, get_active, get_by_id


# ── Data classes ────────────────────────────────────────────────────────


@dataclass(slots=True)
class ReflectionRow:
    """One row of the Vimarśa table — per activity or group."""

    activity: str
    prescribed_hours: float = 0.0
    planned_hours: float = 0.0
    unplanned_hours: float = 0.0

    @property
    def actual_hours(self) -> float:
        return self.planned_hours + self.unplanned_hours

    @property
    def delta(self) -> float:
        """Prescribed minus total actual.  Positive = under-tracked."""
        return self.prescribed_hours - self.actual_hours

    @property
    def unplanned_pct(self) -> float:
        if self.actual_hours == 0:
            return 0.0
        return (self.unplanned_hours / self.actual_hours) * 100

    @property
    def planned_pct(self) -> float:
        if self.prescribed_hours == 0:
            return 0.0
        return (self.planned_hours / self.prescribed_hours) * 100


@dataclass(frozen=True, slots=True)
class ReflectionFlag:
    """A reflection insight surfaced to the user."""

    activity: str
    flag_type: str    # "high_unplanned" | "low_planned" | "chronic_override"
    signal: str
    suggestion: str


@dataclass(slots=True)
class VimarshaSummary:
    """Full Vimarśa report for a date range."""

    date_start: str
    date_end: str
    rows: list[ReflectionRow] = field(default_factory=list)
    grouped_rows: list[ReflectionRow] = field(default_factory=list)
    flags: list[ReflectionFlag] = field(default_factory=list)


# ── Helpers ─────────────────────────────────────────────────────────────

_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday")


def _prescribed_map(niyam: Niyam) -> dict[str, float]:
    """Activity → total weekly prescribed hours from a Niyam."""
    result: dict[str, float] = {}
    for day in _DAY_NAMES:
        for b in niyam.blocks_for_day(day):
            result[b.activity] = result.get(b.activity, 0.0) + b.duration_h
    return result


def _logged_split(
    conn: sqlite3.Connection, date_start: str, date_end: str
) -> dict[str, dict[str, float]]:
    """Activity → {planned, unplanned} hours from kalrekha."""
    rows = conn.execute(
        """
        SELECT project,
               SUM(CASE WHEN unplanned = 0 THEN duration_min ELSE 0 END) / 60.0
                   AS planned,
               SUM(CASE WHEN unplanned = 1 THEN duration_min ELSE 0 END) / 60.0
                   AS unplanned
        FROM kalrekha
        WHERE date >= ? AND date <= ?
        GROUP BY project
        """,
        (date_start, date_end),
    ).fetchall()

    result: dict[str, dict[str, float]] = {}
    for r in rows:
        canonical = resolve_label(conn, r["project"])
        act = canonical or r["project"]
        entry = result.setdefault(act, {"planned": 0.0, "unplanned": 0.0})
        entry["planned"] += r["planned"]
        entry["unplanned"] += r["unplanned"]
    return result


def _aggregate_to_groups(
    conn: sqlite3.Connection,
    rows: list[ReflectionRow],
) -> list[ReflectionRow]:
    """Roll up granular rows into parent-group level."""
    group_map: dict[str, ReflectionRow] = {}
    for row in rows:
        chain = resolve_hierarchy(conn, row.activity)
        # Use the top-level parent (last in chain) as group
        group = chain[-1] if chain else row.activity
        if group not in group_map:
            group_map[group] = ReflectionRow(activity=group)
        g = group_map[group]
        g.prescribed_hours += row.prescribed_hours
        g.planned_hours += row.planned_hours
        g.unplanned_hours += row.unplanned_hours
    return sorted(group_map.values(), key=lambda r: r.activity)


# ── Core Vimarśa ────────────────────────────────────────────────────────


def build_vimarsha(
    conn: sqlite3.Connection,
    date_start: str,
    date_end: str,
    niyam_id: Optional[int] = None,
) -> VimarshaSummary:
    """Build the full three-layer Vimarśa report.

    Args:
        conn: Database connection.
        date_start: Start of range (inclusive), YYYY-MM-DD.
        date_end: End of range (inclusive), YYYY-MM-DD.
        niyam_id: Compare against this Niyam.  Uses active if None.

    Returns:
        A VimarshaSummary with granular rows, grouped rows, and flags.
    """
    if niyam_id is not None:
        niyam = get_by_id(conn, niyam_id)
    else:
        niyam = get_active(conn)

    prescribed = _prescribed_map(niyam) if niyam else {}
    logged = _logged_split(conn, date_start, date_end)

    all_activities = set(prescribed.keys()) | set(logged.keys())
    rows: list[ReflectionRow] = []
    for act in sorted(all_activities):
        rows.append(
            ReflectionRow(
                activity=act,
                prescribed_hours=prescribed.get(act, 0.0),
                planned_hours=logged.get(act, {}).get("planned", 0.0),
                unplanned_hours=logged.get(act, {}).get("unplanned", 0.0),
            )
        )

    grouped = _aggregate_to_groups(conn, rows)
    flags = _detect_flags(conn, rows)

    return VimarshaSummary(
        date_start=date_start,
        date_end=date_end,
        rows=rows,
        grouped_rows=grouped,
        flags=flags,
    )


# ── Reflection flags ────────────────────────────────────────────────────

_HIGH_UNPLANNED_THRESHOLD = 40.0  # percent
_LOW_PLANNED_THRESHOLD = 30.0     # percent
_CHRONIC_OVERRIDE_WEEKS = 3


def _detect_flags(
    conn: sqlite3.Connection,
    rows: list[ReflectionRow],
) -> list[ReflectionFlag]:
    """Scan rows for actionable reflection flags.

    Args:
        conn: Database connection.
        rows: Granular ReflectionRow list.

    Returns:
        List of ReflectionFlag instances.
    """
    flags: list[ReflectionFlag] = []

    for row in rows:
        # High unplanned %
        if row.actual_hours > 0.5 and row.unplanned_pct > _HIGH_UNPLANNED_THRESHOLD:
            flags.append(
                ReflectionFlag(
                    activity=row.activity,
                    flag_type="high_unplanned",
                    signal=f"{row.unplanned_pct:.0f}% of work is outside scheduled blocks",
                    suggestion=(
                        "Move Niyam block to when this work naturally happens"
                    ),
                )
            )

        # Low planned %
        if row.prescribed_hours > 0.5 and row.planned_pct < _LOW_PLANNED_THRESHOLD:
            flags.append(
                ReflectionFlag(
                    activity=row.activity,
                    flag_type="low_planned",
                    signal=f"Only {row.planned_pct:.0f}% of block time used",
                    suggestion=(
                        "Reduce allocation or investigate label mapping"
                    ),
                )
            )

    # Chronic override: check past N weeks
    flags.extend(_detect_chronic_overrides(conn))

    return flags


def _detect_chronic_overrides(
    conn: sqlite3.Connection,
) -> list[ReflectionFlag]:
    """Check for activities overridden 3+ consecutive weeks."""
    start_day = get_setting(conn, "week_start_day") or "monday"
    now = datetime.now()
    day_idx = {d: i for i, d in enumerate(_DAY_NAMES)}
    offset = (now.weekday() - day_idx.get(start_day, 0)) % 7
    current_ws = now - timedelta(days=offset)

    flags: list[ReflectionFlag] = []

    # Get activities with overrides — deduplicate by canonical name
    activities = conn.execute(
        "SELECT DISTINCT project FROM kalrekha WHERE unplanned = 1"
    ).fetchall()

    seen_canonical: set[str] = set()
    for row in activities:
        canonical = resolve_label(conn, row["project"])
        act = canonical or row["project"]
        if act in seen_canonical:
            continue
        seen_canonical.add(act)
        consecutive = 0

        for i in range(_CHRONIC_OVERRIDE_WEEKS):
            ws = (current_ws - timedelta(weeks=i)).strftime("%Y-%m-%d")
            we = (current_ws - timedelta(weeks=i) + timedelta(days=6)).strftime(
                "%Y-%m-%d"
            )
            override_count = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM kalrekha
                WHERE unplanned = 1 AND date >= ? AND date <= ?
                  AND (project = ? OR project IN (
                       SELECT raw_label FROM label_mappings
                       WHERE canonical_label = ?))
                """,
                (ws, we, act, act),
            ).fetchone()
            if override_count and override_count["cnt"] > 0:
                consecutive += 1
            else:
                break

        if consecutive >= _CHRONIC_OVERRIDE_WEEKS:
            flags.append(
                ReflectionFlag(
                    activity=act,
                    flag_type="chronic_override",
                    signal=(
                        f"Overridden {consecutive} consecutive weeks"
                    ),
                    suggestion="Offer Pariṇāma (adjusted Niyam)",
                )
            )

    return flags
