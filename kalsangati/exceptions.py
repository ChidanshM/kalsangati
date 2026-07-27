"""Domain exceptions for Kālsangati.

All domain-level failures raised from core and services inherit from
:class:`KalsangatiError`.  The presentation layer catches these and
converts them to user-facing messages (``QMessageBox.warning`` in GUI;
structured JSON across the future API/bridge).  Unexpected
(non-``KalsangatiError``) exceptions are treated as bugs and logged
with a stack trace.

This module is intentionally PyQt5-free and has no dependencies on
any other kalsangati module — it can be imported anywhere, including
from tests and background threads.

Per ``SKILL-state.md §11``: exception types are defined only here.
``raise Exception("msg")`` without a named class is prohibited; bare
``except:`` and ``except Exception: pass`` are prohibited.  Exception
classes grow on demand as services discover new failure modes.
"""

from __future__ import annotations


class KalsangatiError(Exception):
    """Base class for all Kālsangati domain errors.

    Callers at the presentation layer should catch this (and its
    subclasses) to convert expected failures to user-facing messages.
    Anything that isn't a ``KalsangatiError`` is a bug.
    """


# ── Session / commit-time errors ────────────────────────────────────────


class SessionTooShortError(KalsangatiError):
    """Raised when a stopwatch session is shorter than the configured
    minimum duration.

    The minimum is a service parameter; see
    :func:`kalsangati.services.commit_stopwatch_session.commit_stopwatch_session`.
    Default minimum is 1 second, intended to catch programmer errors
    (zero-length or negative sessions) rather than accidental clicks —
    short sessions are editable after the fact.
    """


class InvalidSessionBoundsError(KalsangatiError):
    """Raised when a session's ``end_time`` is not strictly after its
    ``start_time``.

    Includes the zero-duration case.  The caller should never be able
    to commit a session with ``end_time <= start_time``; the check
    exists as a defensive invariant at the service boundary.
    """


# ── Ingest errors ───────────────────────────────────────────────────────


class IngestFileNotFoundError(KalsangatiError):
    """Raised when the CSV path passed to the ingest service does not
    exist on disk.

    Wraps the stdlib ``FileNotFoundError`` into the domain hierarchy so
    the presentation layer can catch it via ``KalsangatiError``.
    """


class IngestFormatError(KalsangatiError):
    """Raised when a CSV file cannot be parsed — typically because
    required columns (project, date, start, end) are missing from the
    header row or the header row is absent entirely.
    """


# ── Niyam errors ────────────────────────────────────────────────────────


class NiyamNotFoundError(KalsangatiError):
    """Raised when a Niyam id is referenced but no row exists with
    that id.

    Currently surfaces from
    :func:`kalsangati.services.set_active_niyam.set_active_niyam` — a
    caller passes an id that doesn't match any row (e.g. stale id from
    a deleted Niyam still held by a GUI widget).  The presentation
    layer catches this through the :class:`KalsangatiError` base and
    shows a warning dialog.
    """


# ── Task errors ─────────────────────────────────────────────────────────


class TaskNotFoundError(KalsangatiError):
    """Raised when a task id is referenced but no row exists with that
    id.

    Surfaces from
    :func:`kalsangati.services.update_task_status.update_task_status`
    when a caller passes an id that matches no ``tasks`` row (e.g. a
    stale id from a deleted task still held by a GUI widget).  Checked
    at the service boundary before any transition logic so the caller
    gets a clean domain error rather than a bare no-op UPDATE or a
    foreign-key ``IntegrityError``.  Second concrete ``*NotFoundError``;
    a shared ``NotFoundError`` base remains deferred under the rule of
    three (``SKILL-state.md §14``).
    """


class InvalidTaskTransitionError(KalsangatiError):
    """Raised when a task status change is not a legal lifecycle move,
    or the requested target is not a recognised status value.

    The status lifecycle graph lives in
    :mod:`kalsangati.services.update_task_status`
    (``_LEGAL_TRANSITIONS``).  Two situations raise this: the target
    string is not one of the five statuses (a bad argument), or the
    move is between two valid statuses but not an allowed edge (e.g.
    ``done → on_hold``).  Re-setting a task to its current status is
    *not* an error — that path is an idempotent no-op, not a raise.
    """
