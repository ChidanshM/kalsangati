"""Live analytics — today/week metrics, pacing alerts, streaks.

Vimarśa — the mind examining itself.  This module powers the dashboard
with real-time comparisons of Kālrekhā (actual) vs Niyam (prescribed).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from kalsangati.db import get_setting
from kalsangati.labels import resolve_label
from kalsangati.niyam import Niyam, get_active

# ── Data classes ────────────────────────────────────────────────────────


@dataclass(slots=True)
class ActivityMetric:
    """Metrics for a single activity within a time window."""

    activity: str
    prescribed_hours: float = 0.0
    planned_hours: float = 0.0
    unplanned_hours: float = 0.0

    @property
    def actual_hours(self) -> float:
        """Total logged hours (planned + unplanned)."""
        return self.planned_hours + self.unplanned_hours

    @property
    def delta(self) -> float:
        """Prescribed minus actual (positive = under-tracked)."""
        return self.prescribed_hours - self.actual_hours

    @property
    def completion_pct(self) -> float:
        """Percentage of prescribed hours completed."""
        if self.prescribed_hours == 0:
            return 100.0 if self.actual_hours == 0 else float("inf")
        return (self.actual_hours / self.prescribed_hours) * 100

    @property
    def unplanned_pct(self) -> float:
        """Percentage of actual hours that were unplanned."""
        if self.actual_hours == 0:
            return 0.0
        return (self.unplanned_hours / self.actual_hours) * 100


@dataclass(slots=True)
class PacingAlert:
    """A pacing warning for an activity falling behind."""

    activity: str
    hours_remaining: float
    days_remaining: int
    required_daily_rate: float
    message: str


@dataclass(slots=True)
class DaySummary:
    """Aggregated metrics for a single day."""

    date: str
    metrics: list[ActivityMetric] = field(default_factory=list)

    @property
    def total_prescribed(self) -> float:
        return sum(m.prescribed_hours for m in self.metrics)

    @property
    def total_actual(self) -> float:
        return sum(m.actual_hours for m in self.metrics)

    @property
    def health_score(self) -> float:
        """Adherence score 0–100 based on how closely actual matches prescribed."""
        if self.total_prescribed == 0:
            return 100.0
        ratio = min(self.total_actual / self.total_prescribed, 1.5)
        # Penalise both over and under
        if ratio <= 1.0:
            return ratio * 100
        return max(0, 100 - (ratio - 1.0) * 100)


@dataclass(slots=True)
class WeekSummary:
    """Aggregated metrics for a full Kālachakra (weekly cycle)."""

    week_start: str
    metrics: list[ActivityMetric] = field(default_factory=list)
    pacing_alerts: list[PacingAlert] = field(default_factory=list)

    @property
    def total_prescribed(self) -> float:
        return sum(m.prescribed_hours for m in self.metrics)

    @property
    def total_actual(self) -> float:
        return sum(m.actual_hours for m in self.metrics)

    @property
    def health_score(self) -> float:
        """Weekly health score: weighted average of per-activity completion."""
        if not self.metrics:
            return 100.0
        total_p = self.total_prescribed
        if total_p == 0:
            return 100.0
        weighted = sum(
            min(m.completion_pct, 100) * m.prescribed_hours
            for m in self.metrics
            if m.prescribed_hours > 0
        )
        return weighted / total_p


# ── Helpers ─────────────────────────────────────────────────────────────

_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday")


def _current_week_start(start_day: str = "monday") -> str:
    """ISO date of the current week's start."""
    day_idx = {d: i for i, d in enumerate(_DAY_NAMES)}
    now = datetime.now()
    offset = (now.weekday() - day_idx.get(start_day, 0)) % 7
    ws = now - timedelta(days=offset)
    return ws.strftime("%Y-%m-%d")


def _days_remaining_in_week(start_day: str = "monday") -> int:
    """Number of days left in the current week (including today)."""
    ws = _current_week_start(start_day)
    ws_dt = datetime.strptime(ws, "%Y-%m-%d")
    week_end = ws_dt + timedelta(days=7)
    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    remaining = (week_end - now).days
    return max(remaining, 1)


def _prescribed_hours_for_day(
    niyam: Niyam, day_name: str
) -> dict[str, float]:
    """Return {activity: hours} for a specific day from a Niyam."""
    result: dict[str, float] = {}
    for block in niyam.blocks_for_day(day_name):
        result[block.activity] = result.get(block.activity, 0.0) + block.duration_h
    return result


def _prescribed_hours_for_week(niyam: Niyam) -> dict[str, float]:
    """Return {activity: total_weekly_hours} from a Niyam."""
    result: dict[str, float] = {}
    for day in _DAY_NAMES:
        for block in niyam.blocks_for_day(day):
            result[block.activity] = (
                result.get(block.activity, 0.0) + block.duration_h
            )
    return result


def _logged_hours(
    conn: sqlite3.Connection,
    date_start: str,
    date_end: str | None = None,
) -> dict[str, dict[str, float]]:
    """Query logged hours by activity, split planned/unplanned.

    Args:
        conn: Database connection.
        date_start: Start date (inclusive).
        date_end: End date (inclusive).  Defaults to date_start.

    Returns:
        Dict of activity → {"planned": float, "unplanned": float}.
    """
    date_end = date_end or date_start
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
        activity = canonical or r["project"]
        if activity not in result:
            result[activity] = {"planned": 0.0, "unplanned": 0.0}
        result[activity]["planned"] += r["planned"]
        result[activity]["unplanned"] += r["unplanned"]
    return result


# ── Public API ──────────────────────────────────────────────────────────


def today_summary(conn: sqlite3.Connection) -> DaySummary:
    """Build today's metrics against the active Niyam.

    Args:
        conn: Database connection.

    Returns:
        A DaySummary for today.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    day_name = _DAY_NAMES[datetime.now().weekday()]

    niyam = get_active(conn)
    prescribed = _prescribed_hours_for_day(niyam, day_name) if niyam else {}
    logged = _logged_hours(conn, today)

    all_activities = set(prescribed.keys()) | set(logged.keys())
    metrics: list[ActivityMetric] = []
    for act in sorted(all_activities):
        metrics.append(
            ActivityMetric(
                activity=act,
                prescribed_hours=prescribed.get(act, 0.0),
                planned_hours=logged.get(act, {}).get("planned", 0.0),
                unplanned_hours=logged.get(act, {}).get("unplanned", 0.0),
            )
        )

    return DaySummary(date=today, metrics=metrics)


def week_summary(
    conn: sqlite3.Connection,
    week_start: str | None = None,
) -> WeekSummary:
    """Build the weekly Kālachakra summary with pacing alerts.

    Args:
        conn: Database connection.
        week_start: Override week start date.  Defaults to current week.

    Returns:
        A WeekSummary with metrics and pacing alerts.
    """
    start_day = get_setting(conn, "week_start_day") or "monday"
    ws = week_start or _current_week_start(start_day)
    we = (datetime.strptime(ws, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")

    niyam = get_active(conn)
    prescribed = _prescribed_hours_for_week(niyam) if niyam else {}
    logged = _logged_hours(conn, ws, we)
    days_left = _days_remaining_in_week(start_day)

    all_activities = set(prescribed.keys()) | set(logged.keys())
    metrics: list[ActivityMetric] = []
    alerts: list[PacingAlert] = []

    for act in sorted(all_activities):
        m = ActivityMetric(
            activity=act,
            prescribed_hours=prescribed.get(act, 0.0),
            planned_hours=logged.get(act, {}).get("planned", 0.0),
            unplanned_hours=logged.get(act, {}).get("unplanned", 0.0),
        )
        metrics.append(m)

        # Pacing alert: if behind and days remain
        if m.delta > 0 and days_left > 0:
            rate = m.delta / days_left
            if m.completion_pct < (100 * (7 - days_left) / 7) * 0.9:
                alerts.append(
                    PacingAlert(
                        activity=act,
                        hours_remaining=m.delta,
                        days_remaining=days_left,
                        required_daily_rate=round(rate, 2),
                        message=(
                            f"{act}: {m.delta:.1f}h remaining over "
                            f"{days_left} days ({rate:.1f}h/day needed)"
                        ),
                    )
                )

    return WeekSummary(week_start=ws, metrics=metrics, pacing_alerts=alerts)


def streak_data(
    conn: sqlite3.Connection,
    activity: str,
    n_weeks: int = 8,
) -> list[dict[str, float | str]]:
    """Return per-week adherence for an activity over N past Kālachakra.

    Args:
        conn: Database connection.
        activity: Canonical activity name.
        n_weeks: Number of past weeks to include.

    Returns:
        List of dicts with ``week_start``, ``prescribed``, ``actual``,
        ``pct`` keys, newest first.
    """
    start_day = get_setting(conn, "week_start_day") or "monday"
    current_ws = _current_week_start(start_day)
    niyam = get_active(conn)
    prescribed_weekly = (
        niyam.hours_for_activity(activity) if niyam else 0.0
    )

    result: list[dict[str, float | str]] = []
    ws_dt = datetime.strptime(current_ws, "%Y-%m-%d")
    for i in range(n_weeks):
        ws = (ws_dt - timedelta(weeks=i)).strftime("%Y-%m-%d")
        we = (ws_dt - timedelta(weeks=i) + timedelta(days=6)).strftime("%Y-%m-%d")

        row = conn.execute(
            """
            SELECT COALESCE(SUM(duration_min), 0) / 60.0 AS hours
            FROM kalrekha
            WHERE date >= ? AND date <= ?
              AND (project = ? OR project IN (
                    SELECT raw_label FROM label_mappings
                    WHERE canonical_label = ?
              ))
            """,
            (ws, we, activity, activity),
        ).fetchone()

        actual = row["hours"] if row else 0.0
        pct = (actual / prescribed_weekly * 100) if prescribed_weekly > 0 else 0.0
        result.append({
            "week_start": ws,
            "prescribed": prescribed_weekly,
            "actual": round(actual, 2),
            "pct": round(pct, 1),
        })

    return result


def adherence_score(
    conn: sqlite3.Connection,
    week_start: str | None = None,
) -> float:
    """Compute a single 0–100 adherence score for a week.

    Args:
        conn: Database connection.
        week_start: Override week start.  Defaults to current week.

    Returns:
        A float score from 0 to 100.
    """
    ws = week_summary(conn, week_start)
    return round(ws.health_score, 1)
