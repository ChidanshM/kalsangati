"""Commit a stopwatch session into the Kālrekhā log.

This is the first service in the six-service plan (see
``SKILL-state.md §9``).  It exists to pull the one remaining direct-
INSERT site (``gui/stopwatch.py::_end_session``) out of the GUI and
route it through a validated, testable, PyQt5-free core code path —
resolving ``SKILL-state.md`` pitfall #19.

What the service does, in order:

1. Validate bounds.  ``end_time`` must be strictly after ``start_time``
   (``InvalidSessionBoundsError``) and the total duration must be at least
   ``min_session_sec`` seconds (``SessionTooShortError``).
2. Resolve the session's canonical activity through the label
   converter.
3. Classify planned vs. unplanned against the currently-active Niyam
   via :func:`kalsangati.niyam.is_session_unplanned_under`.
4. Decide resume-extend vs. new row: if the most recent ``kalrekha``
   row for the same canonical activity + task title ended within the
   last ``resume_window_sec`` seconds and is on the same date, the new
   session extends that row's ``end`` and ``duration_min``.  Otherwise
   a new row is inserted.
5. Return a :class:`CommitResult` describing what happened.

Design notes:

* Session classification is computed at commit time and never
  recomputed on extend.  The stored ``unplanned`` flag reflects the
  first-write's start moment only.  A session that drifts out of its
  Niyam block mid-way is still "planned" — that kind of drift is a
  Vimarśa-side analysis concern, not a commit-time decision.
* On resume-extend, a non-None ``override_reason`` argument overwrites
  the stored value; a ``None`` argument leaves the previously-stored
  reason intact.  This lets the GUI attach a reason at commit time,
  then let subsequent resume-extends run without needing to re-pass
  the same reason, while still allowing a second override to supersede
  the first.

Duration vs. timestamps (E1, resolved):

* ``start_time`` / ``end_time`` are wall-clock and are what get stored
  in the ``date`` / ``start`` / ``end`` columns.  Wall-clock is the
  correct answer for *when* a session happened.
* *How long* a session lasted is a different question, and wall-clock
  arithmetic answers it wrongly whenever the clock jumps — a laptop
  suspend, an NTP correction, or a DST transition mid-session all
  inflate or deflate ``end_time - start_time``.  Callers that can
  measure true elapsed time (the stopwatch widget, via
  ``time.monotonic()``) pass it as ``duration_sec``, which is then
  authoritative for validation and for the stored ``duration_min``.
* ``duration_sec=None`` falls back to the wall-clock delta.  That is
  the right behaviour for the CSV ingest path and for tests, where
  sessions are already-recorded historical data and the delta is both
  all we have and correct.
* On resume-extend the new total is the *sum* of the stored duration
  and this segment's duration, not the span from the original start to
  the new end.  Summing keeps every segment monotonic-correct and
  excludes the inter-segment gap (up to ``resume_window_sec``), which
  was never time actually spent on the activity.

Residual: a backwards clock jump can still store an ``end`` earlier
than ``start`` in the wall-clock columns.  The recorded *duration*
remains correct.  See ``SKILL-state.md §17 E7`` for the remaining
timezone/DST work on the timestamp columns themselves.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from kalsangati.db import transaction
from kalsangati.exceptions import InvalidSessionBoundsError, SessionTooShortError
from kalsangati.labels import resolve_label
from kalsangati.niyam import DAYS, get_active, is_session_unplanned_under
from kalsangati.tasks import get_by_id as get_task_by_id

# Default thresholds.  Exposed as keyword-only parameters on the
# service function so tests can exercise boundary behaviour without
# monkey-patching module constants.  Production callers should not
# pass them.
MIN_SESSION_SEC: float = 1.0
RESUME_WINDOW_SEC: float = 120.0


# ── Result type ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class CommitResult:
    """Outcome of a ``commit_stopwatch_session`` call.

    Attributes:
        session_id: Row id of the ``kalrekha`` row that holds this
            session.  On resume-extend this is the id of the pre-existing
            row whose ``end`` was pushed forward.
        extended: ``True`` if the call extended an existing row,
            ``False`` if a new row was inserted.
        unplanned: Classification against the active Niyam at the
            session's start moment.  On resume-extend, this reflects
            the original row's stored value and is not recomputed.
        duration_sec: Total duration of the session as committed, in
            seconds.  On resume-extend this is the full combined
            span from the original ``start`` to the new ``end``.
    """

    session_id: int
    extended: bool
    unplanned: bool
    duration_sec: float


# ── Internals ───────────────────────────────────────────────────────────


def _resolve_task_title(
    conn: sqlite3.Connection, task_id: int | None
) -> str | None:
    """Look up a task's title by id.  ``None`` in → ``None`` out."""
    if task_id is None:
        return None
    task = get_task_by_id(conn, task_id)
    return task.title if task else None


def _find_resumable_row(
    conn: sqlite3.Connection,
    *,
    activity: str,
    task_title: str | None,
    start_time: datetime,
    resume_window_sec: float,
) -> sqlite3.Row | None:
    """Return the most recent kalrekha row that can absorb this session.

    A row qualifies when:

    * its ``project`` equals ``activity`` (canonical),
    * its ``task`` equals ``task_title`` (both can be ``NULL``; the
      symmetric-None rule is explicit here),
    * its ``date`` equals the new session's start date (this rules out
      midnight-crossing resumes),
    * the gap between its ``end`` and ``start_time`` is non-negative
      and no greater than ``resume_window_sec``.

    Returns ``None`` when no row qualifies.
    """
    start_date = start_time.strftime("%Y-%m-%d")

    if task_title is None:
        task_clause = "task IS NULL"
        params: tuple[str, ...] = (activity, start_date)
    else:
        task_clause = "task = ?"
        params = (activity, task_title, start_date)

    row: sqlite3.Row | None = conn.execute(
        f"""
        SELECT id, start, "end", duration_min, unplanned, override_reason
        FROM kalrekha
        WHERE project = ?
          AND {task_clause}
          AND date = ?
        ORDER BY "end" DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()

    if row is None:
        return None

    # Compute gap between the stored end and the incoming start, both
    # on the same date, as a ``datetime`` arithmetic.  The stored
    # "end" column is ``"HH:MM:SS"``.
    prev_end = datetime.strptime(
        f"{start_date} {row['end']}", "%Y-%m-%d %H:%M:%S"
    )
    gap = (start_time - prev_end).total_seconds()

    # Reject negative gaps (previous row ends after this start — i.e.
    # this isn't a newer session) and gaps exceeding the window.
    if gap < 0 or gap > resume_window_sec:
        return None

    return row


def _extend_row(
    conn: sqlite3.Connection,
    *,
    row_id: int,
    prior_duration_sec: float,
    segment_duration_sec: float,
    new_end_time: datetime,
    new_override_reason: str | None,
    preserve_override: bool,
) -> float:
    """Extend a kalrekha row's end + duration_min.  Returns new total
    duration in seconds.

    The new total is ``prior_duration_sec + segment_duration_sec`` —
    a sum of measured segments, not the wall-clock span from the
    original start to ``new_end_time``.  This keeps the total correct
    across clock jumps and excludes the inter-segment gap (see the
    module docstring, E1).

    ``prior_duration_sec`` is reconstructed from the stored
    ``duration_min``, which is rounded to two decimals — so each extend
    can introduce up to ~0.3s of rounding drift.  Negligible against
    session lengths measured in minutes.

    ``preserve_override`` tells us whether the caller passed
    ``override_reason=None`` (preserve) or non-None (overwrite).
    """
    new_duration_sec = prior_duration_sec + segment_duration_sec
    new_duration_min = new_duration_sec / 60.0
    new_end_hms = new_end_time.strftime("%H:%M:%S")

    if preserve_override:
        conn.execute(
            'UPDATE kalrekha SET "end" = ?, duration_min = ? '
            "WHERE id = ?",
            (new_end_hms, round(new_duration_min, 2), row_id),
        )
    else:
        conn.execute(
            'UPDATE kalrekha SET "end" = ?, duration_min = ?, '
            "override_reason = ? WHERE id = ?",
            (new_end_hms, round(new_duration_min, 2),
             new_override_reason, row_id),
        )
    return new_duration_sec


# ── Public service entry point ──────────────────────────────────────────


def commit_stopwatch_session(
    conn: sqlite3.Connection,
    activity: str,
    start_time: datetime,
    end_time: datetime,
    task_id: int | None = None,
    override_reason: str | None = None,
    *,
    duration_sec: float | None = None,
    min_session_sec: float = MIN_SESSION_SEC,
    resume_window_sec: float = RESUME_WINDOW_SEC,
) -> CommitResult:
    """Commit a stopwatch session to the Kālrekhā log.

    See the module docstring for the resume-vs-new decision rules and
    the classification semantics.

    Args:
        conn: Database connection.
        activity: Raw activity label as seen at commit time.  Will be
            resolved through ``labels.resolve_label`` to the canonical
            name before storage.
        start_time: Session start (wall-clock ``datetime``).
        end_time: Session end (wall-clock ``datetime``).  Must be
            strictly after ``start_time``.
        task_id: Optional task id.  When provided, the task's title is
            resolved and stored in ``kalrekha.task``; the title (not
            the id) is what the schema carries today.  Task rename
            inside the resume window would break the symmetric-task
            match; acceptable given a 120s window.
        override_reason: Optional free-text reason.  On a new-row
            commit, stored as-is.  On a resume-extend, a non-None
            value overwrites the stored value; ``None`` leaves the
            previously-stored value intact.
        duration_sec: True elapsed seconds for this segment, as
            measured by a monotonic clock.  When given it is
            authoritative for both validation and the stored
            duration, making the commit immune to clock jumps and
            laptop suspends (E1).  When ``None`` the wall-clock delta
            ``end_time - start_time`` is used instead — correct for
            historical/imported sessions, where no monotonic
            measurement exists.
        min_session_sec: Minimum session duration.  Test/tuning
            parameter.  Production callers should not pass this.
        resume_window_sec: Maximum gap (seconds) between a previous
            row's end and this start for the row to qualify as
            resumable.  Test/tuning parameter.  Production callers
            should not pass this.

    Returns:
        A :class:`CommitResult` describing the outcome.

    Raises:
        InvalidSessionBoundsError: If ``end_time <= start_time``.
        SessionTooShortError: If the duration is below ``min_session_sec``.
    """
    # 1. Bounds check, against the measured duration when the caller
    #    supplied one (monotonic) and the wall-clock delta otherwise.
    measured_sec = (
        duration_sec
        if duration_sec is not None
        else (end_time - start_time).total_seconds()
    )
    if measured_sec <= 0:
        raise InvalidSessionBoundsError(
            f"end_time ({end_time.isoformat()}) must be strictly after "
            f"start_time ({start_time.isoformat()})"
        )
    if measured_sec < min_session_sec:
        raise SessionTooShortError(
            f"session duration {measured_sec:.3f}s is below minimum "
            f"{min_session_sec:.3f}s"
        )

    # 2. Resolve canonical activity.  ``resolve_label`` returns None
    # when no converter mapping exists; fall back to the raw label so
    # the session is still recorded (just uncategorised in the
    # converter's view).  Users can add a mapping later and
    # re-classify via the Label Manager.
    canonical = resolve_label(conn, activity) or activity

    # 3. Classify against the active Niyam.
    day = DAYS[start_time.weekday()]
    start_min = start_time.hour * 60 + start_time.minute
    niyam = get_active(conn)
    unplanned = is_session_unplanned_under(niyam, canonical, day, start_min)

    # 4. Task title lookup (for the ``kalrekha.task`` column + resume
    # matching).
    task_title = _resolve_task_title(conn, task_id)

    # 5. Resume-or-new decision.
    with transaction(conn) as cur:
        existing = _find_resumable_row(
            conn,
            activity=canonical,
            task_title=task_title,
            start_time=start_time,
            resume_window_sec=resume_window_sec,
        )

        if existing is not None:
            # Resume-extend: push the prior row's ``end`` forward and
            # add this segment's duration to the stored total.
            # ``_find_resumable_row`` already selected duration_min and
            # unplanned, so no second fetch is needed.
            combined_duration_sec = _extend_row(
                conn,
                row_id=existing["id"],
                prior_duration_sec=float(existing["duration_min"]) * 60.0,
                segment_duration_sec=measured_sec,
                new_end_time=end_time,
                new_override_reason=override_reason,
                preserve_override=(override_reason is None),
            )
            return CommitResult(
                session_id=existing["id"],
                extended=True,
                unplanned=bool(existing["unplanned"]),
                duration_sec=combined_duration_sec,
            )

        # New-row insert.  source is always 'manual_stopwatch';
        # block_classified is 1 because we just did it.
        cur.execute(
            "INSERT INTO kalrekha "
            '(project, task, date, start, "end", duration_min, '
            " source, unplanned, override_reason, block_classified) "
            "VALUES (?, ?, ?, ?, ?, ?, 'manual_stopwatch', ?, ?, 1)",
            (
                canonical,
                task_title,
                start_time.strftime("%Y-%m-%d"),
                start_time.strftime("%H:%M:%S"),
                end_time.strftime("%H:%M:%S"),
                round(measured_sec / 60.0, 2),
                int(unplanned),
                override_reason,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None  # guaranteed after INSERT

    return CommitResult(
        session_id=new_id,
        extended=False,
        unplanned=unplanned,
        duration_sec=measured_sec,
    )
