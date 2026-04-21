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
