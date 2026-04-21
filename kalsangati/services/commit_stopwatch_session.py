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

TODO(E1): duration is computed from ``end_time - start_time`` which is
wall-clock arithmetic.  After a laptop sleep / clock jump the value
can be wildly wrong — see ``SKILL-state.md §17 E1``.  Fix requires
monotonic-clock tracking in the stopwatch widget itself; handled in a
future unit.
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
    stored_start_hms: str,
    stored_start_date: str,
    new_end_time: datetime,
    new_override_reason: str | None,
    preserve_override: bool,
) -> float:
    """Extend a kalrekha row's end + duration_min.  Returns new total
    duration in seconds.

    ``preserve_override`` tells us whether the caller passed
    ``override_reason=None`` (preserve) or non-None (overwrite).
    """
    stored_start = datetime.strptime(
        f"{stored_start_date} {stored_start_hms}", "%Y-%m-%d %H:%M:%S"
    )
    new_duration_sec = (new_end_time - stored_start).total_seconds()
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
    # 1. Bounds check.
    duration_sec = (end_time - start_time).total_seconds()
    if duration_sec <= 0:
        raise InvalidSessionBoundsError(
            f"end_time ({end_time.isoformat()}) must be strictly after "
            f"start_time ({start_time.isoformat()})"
        )
    if duration_sec < min_session_sec:
        raise SessionTooShortError(
            f"session duration {duration_sec:.3f}s is below minimum "
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
            # Resume-extend: push the prior row's ``end`` forward.
            # Retrieve the stored start + date via a separate fetch
            # (we already have the id; keep the query explicit).
            stored = conn.execute(
                'SELECT start, date, unplanned FROM kalrekha WHERE id = ?',
                (existing["id"],),
            ).fetchone()
            assert stored is not None  # id came from _find_resumable_row
            combined_duration_sec = _extend_row(
                conn,
                row_id=existing["id"],
                stored_start_hms=stored["start"],
                stored_start_date=stored["date"],
                new_end_time=end_time,
                new_override_reason=override_reason,
                preserve_override=(override_reason is None),
            )
            return CommitResult(
                session_id=existing["id"],
                extended=True,
                unplanned=bool(stored["unplanned"]),
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
                round(duration_sec / 60.0, 2),
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
        duration_sec=duration_sec,
    )
