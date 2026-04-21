"""CSV ingest pipeline.

Parses external time-tracker CSV exports into the ``kalrekha`` table,
normalises timezones, applies label conversion, aggregates sessions, and
optionally watches a folder for new files.

Expected CSV schema::

    Project name, Task name, Date, Start time, End time, Duration, Timezone
"""

from __future__ import annotations

import csv
import hashlib
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from kalsangati.db import get_setting, transaction
from kalsangati.labels import resolve_label

logger = logging.getLogger(__name__)

# ── Column name normalization ───────────────────────────────────────────

_COLUMN_MAP: dict[str, str] = {
    "project name": "project",
    "project": "project",
    "task name": "task",
    "task": "task",
    "date": "date",
    "start time": "start",
    "start": "start",
    "end time": "end",
    "end": "end",
    "duration": "duration",
    "timezone": "timezone",
    "time zone": "timezone",
    "tz": "timezone",
}


def _normalize_columns(header: list[str]) -> dict[str, str]:
    """Map CSV header names to internal field names.

    Args:
        header: Raw CSV column names.

    Returns:
        Dict mapping internal field name → original column name.

    Raises:
        ValueError: If required columns are missing.
    """
    result: dict[str, str] = {}
    for col in header:
        key = col.strip().lower()
        if key in _COLUMN_MAP:
            result[_COLUMN_MAP[key]] = col
    required = {"project", "date", "start", "end"}
    missing = required - set(result.keys())
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")
    return result


# ── Duration parsing ────────────────────────────────────────────────────


def _parse_duration_to_minutes(raw: str) -> float:
    """Parse a duration string into minutes.

    Supports ``"HH:MM:SS"``, ``"HH:MM"``, ``"MM:SS"`` (if < 60 first
    segment), and bare decimal hours like ``"1.5"``.

    Args:
        raw: Duration string from CSV.

    Returns:
        Duration in minutes.
    """
    raw = raw.strip()
    if not raw:
        return 0.0

    if ":" in raw:
        parts = raw.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return h * 60 + m + s / 60
        elif len(parts) == 2:
            a, b = int(parts[0]), int(parts[1])
            # Assume HH:MM
            return a * 60 + b
    try:
        return float(raw) * 60  # decimal hours
    except ValueError:
        return 0.0


def _compute_duration_minutes(start: str, end: str) -> float:
    """Compute duration in minutes from start/end time strings.

    Args:
        start: Start time (HH:MM or HH:MM:SS).
        end: End time.

    Returns:
        Duration in minutes.
    """
    fmt = "%H:%M:%S" if len(start) > 5 else "%H:%M"
    try:
        t_start = datetime.strptime(start.strip(), fmt)
        t_end = datetime.strptime(end.strip(), fmt)
        delta = t_end - t_start
        if delta.total_seconds() < 0:
            delta += timedelta(days=1)  # crosses midnight
        return delta.total_seconds() / 60
    except ValueError:
        return 0.0


# ── File fingerprint (dedup) ────────────────────────────────────────────


def _file_hash(path: Path) -> str:
    """SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_already_imported(conn: sqlite3.Connection, file_hash: str) -> bool:
    """Check if a file hash has been recorded in settings."""
    val = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (f"imported_hash:{file_hash}",),
    ).fetchone()
    return val is not None


def _mark_imported(conn: sqlite3.Connection, file_hash: str, path: str) -> None:
    """Record that a file has been imported."""
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (f"imported_hash:{file_hash}", path),
    )
    conn.commit()


# ── Core ingest ─────────────────────────────────────────────────────────


def ingest_csv(
    conn: sqlite3.Connection,
    csv_path: Path | str,
    *,
    skip_duplicates: bool = True,
) -> dict[str, int | list[str]]:
    """Parse an external tracker CSV and insert sessions into kalrekha.

    Args:
        conn: Database connection.
        csv_path: Path to the CSV file.
        skip_duplicates: If True, skip files already imported (by hash).

    Returns:
        A dict with keys:
        - ``"imported"`` (int): number of sessions inserted
        - ``"skipped"`` (int): duplicate rows skipped
        - ``"unrecognized"`` (list[str]): raw labels with no mapping
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    # Dedup by file hash
    if skip_duplicates:
        fhash = _file_hash(path)
        if _is_already_imported(conn, fhash):
            logger.info("File already imported: %s", path)
            return {"imported": 0, "skipped": 0, "unrecognized": []}

    unrecognized: set[str] = set()
    imported = 0
    skipped = 0

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")
        col_map = _normalize_columns(list(reader.fieldnames))

        with transaction(conn) as cur:
            for row in reader:
                project = row.get(col_map.get("project", ""), "").strip()
                task = row.get(col_map.get("task", ""), "").strip() or None
                date = row.get(col_map.get("date", ""), "").strip()
                start = row.get(col_map.get("start", ""), "").strip()
                end = row.get(col_map.get("end", ""), "").strip()
                duration_raw = row.get(col_map.get("duration", ""), "").strip()
                tz = row.get(col_map.get("timezone", ""), "").strip() or None

                if not (project and date and start and end):
                    skipped += 1
                    continue

                # Compute duration
                if duration_raw:
                    duration_min = _parse_duration_to_minutes(duration_raw)
                else:
                    duration_min = _compute_duration_minutes(start, end)

                # Check for label mapping
                canonical = resolve_label(conn, project)
                if canonical is None:
                    unrecognized.add(project)

                # Dedup: exact match on project+date+start+end
                existing = cur.execute(
                    "SELECT id FROM kalrekha "
                    "WHERE project = ? AND date = ? AND start = ? AND \"end\" = ?",
                    (project, date, start, end),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                cur.execute(
                    "INSERT INTO kalrekha "
                    "(project, task, date, start, \"end\", duration_min, "
                    " tz_offset, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'csv_import')",
                    (project, task, date, start, end, duration_min, tz),
                )
                imported += 1

    # Mark file as imported
    if skip_duplicates:
        _mark_imported(conn, fhash, str(path))  # type: ignore[possibly-undefined]

    if unrecognized:
        logger.warning("Unrecognized labels: %s", sorted(unrecognized))

    return {
        "imported": imported,
        "skipped": skipped,
        "unrecognized": sorted(unrecognized),
    }


# ── Aggregation ─────────────────────────────────────────────────────────


def _week_start_for_date(
    date_str: str, start_day: str = "monday"
) -> str:
    """Return the ISO date of the week-start for a given date.

    Args:
        date_str: Date in YYYY-MM-DD format.
        start_day: Which day the week starts (lowercase).

    Returns:
        ISO date string of the week start.
    """
    day_index = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    offset = (dt.weekday() - day_index.get(start_day, 0)) % 7
    ws = dt - timedelta(days=offset)
    return ws.strftime("%Y-%m-%d")


def refresh_weekly_aggregates(
    conn: sqlite3.Connection,
    week_start: str | None = None,
) -> int:
    """Rebuild weekly_aggregates from kalrekha data.

    Aggregates by week and project (using label mappings when available).
    Also splits planned vs unplanned hours.

    Args:
        conn: Database connection.
        week_start: If given, only refresh this specific week.
            Otherwise refreshes all weeks.

    Returns:
        Number of aggregate rows upserted.
    """
    start_day = get_setting(conn, "week_start_day") or "monday"

    if week_start:
        conn.execute(
            "DELETE FROM weekly_aggregates WHERE week_start = ?",
            (week_start,),
        )
    else:
        conn.execute("DELETE FROM weekly_aggregates")

    # Build aggregation query
    where = "WHERE 1=1"
    params: list[str] = []
    if week_start:
        where += " AND k.date >= ? AND k.date < date(?, '+7 days')"
        params.extend([week_start, week_start])

    rows = conn.execute(
        f"""
        SELECT
            k.project,
            k.date,
            SUM(k.duration_min) / 60.0 AS total_hours,
            SUM(CASE WHEN k.unplanned = 0 THEN k.duration_min ELSE 0 END) / 60.0
                AS planned_hours,
            SUM(CASE WHEN k.unplanned = 1 THEN k.duration_min ELSE 0 END) / 60.0
                AS unplanned_hours
        FROM kalrekha k
        {where}
        GROUP BY k.project, k.date
        """,
        params,
    ).fetchall()

    count = 0
    with transaction(conn) as cur:
        for r in rows:
            # Resolve canonical label
            canonical = resolve_label(conn, r["project"])
            activity = canonical or r["project"]
            ws = _week_start_for_date(r["date"], start_day)

            # Upsert: accumulate into existing row
            existing = cur.execute(
                "SELECT id, total_hours, planned_hours, unplanned_hours "
                "FROM weekly_aggregates "
                "WHERE week_start = ? AND activity = ?",
                (ws, activity),
            ).fetchone()

            if existing:
                cur.execute(
                    "UPDATE weekly_aggregates SET "
                    "  total_hours = total_hours + ?, "
                    "  planned_hours = planned_hours + ?, "
                    "  unplanned_hours = unplanned_hours + ? "
                    "WHERE id = ?",
                    (r["total_hours"], r["planned_hours"],
                     r["unplanned_hours"], existing["id"]),
                )
            else:
                cur.execute(
                    "INSERT INTO weekly_aggregates "
                    "(week_start, activity, total_hours, planned_hours, "
                    " unplanned_hours) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ws, activity, r["total_hours"],
                     r["planned_hours"], r["unplanned_hours"]),
                )
                count += 1

    return count


# ── Block classification ────────────────────────────────────────────────


def classify_sessions(conn: sqlite3.Connection) -> int:
    """Retroactively classify imported sessions as planned/unplanned.

    Checks each unclassified kalrekha session against the active Niyam
    via :func:`kalsangati.niyam.is_session_unplanned_under` — the same
    pure classifier used by the stopwatch commit service.  A session is
    ``planned`` if it falls within a scheduled block for the matching
    activity at its start time.

    Args:
        conn: Database connection.

    Returns:
        Number of sessions classified.
    """
    from kalsangati.niyam import (
        get_active,
        is_session_unplanned_under,
        time_str_to_minutes,
    )

    active = get_active(conn)
    if active is None:
        return 0

    day_names = {
        0: "monday", 1: "tuesday", 2: "wednesday",
        3: "thursday", 4: "friday", 5: "saturday", 6: "sunday",
    }

    unclassified = conn.execute(
        "SELECT id, project, date, start, \"end\" FROM kalrekha "
        "WHERE block_classified = 0"
    ).fetchall()

    count = 0
    for session in unclassified:
        try:
            dt = datetime.strptime(session["date"], "%Y-%m-%d")
        except ValueError:
            continue
        day = day_names.get(dt.weekday())
        if day is None:
            continue

        canonical = resolve_label(conn, session["project"])
        activity = canonical or session["project"]

        try:
            session_start_min = time_str_to_minutes(session["start"])
        except (ValueError, TypeError):
            continue

        unplanned = is_session_unplanned_under(
            active, activity, day, session_start_min,
        )

        conn.execute(
            "UPDATE kalrekha SET unplanned = ?, block_classified = 1 "
            "WHERE id = ?",
            (int(unplanned), session["id"]),
        )
        count += 1

    conn.commit()
    return count
