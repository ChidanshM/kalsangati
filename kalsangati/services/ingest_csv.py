"""Ingest a time-tracker CSV file into the Kālrekhā log.

Second service in the six-service plan (see ``SKILL-state.md §9``).
Orchestrates the three ingest steps — CSV parse, block classification,
and weekly aggregation — into a single call with a structured result
type and domain-level exception raising.

What the service does, in order:

1. Parse the CSV via :func:`kalsangati.ingest.ingest_csv` (inserts
   sessions into ``kalrekha``).
2. Classify the freshly-imported sessions against the active Niyam via
   :func:`kalsangati.ingest.classify_sessions` (only touches rows with
   ``block_classified = 0``; already-classified sessions are skipped).
3. Rebuild weekly aggregates via
   :func:`kalsangati.ingest.refresh_weekly_aggregates`.

The three steps are *not* wrapped in a single transaction — they each
manage their own.  If classify fails after a successful ingest, the
imported rows survive.  This matches the pre-service behaviour.

Exceptions from the underlying functions are caught and re-raised as
:class:`~kalsangati.exceptions.KalsangatiError` subclasses so the
presentation layer can handle them uniformly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from kalsangati.exceptions import IngestFileNotFoundError, IngestFormatError
from kalsangati.ingest import (
    classify_sessions,
    ingest_csv,
    refresh_weekly_aggregates,
)

# ── Result type ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class IngestResult:
    """Outcome of an :func:`ingest_csv_file` call.

    Attributes:
        imported: Number of new sessions inserted into ``kalrekha``.
        skipped: Number of duplicate rows skipped (file-hash dedup or
            row-level dedup).
        classified: Number of sessions retroactively classified against
            the active Niyam.  Zero when no Niyam is active.
        aggregates_refreshed: Number of new aggregate rows upserted
            into ``weekly_aggregates``.
        unrecognized: Raw label strings from the CSV that have no
            converter mapping.  Sorted alphabetically.
    """

    imported: int
    skipped: int
    classified: int
    aggregates_refreshed: int
    unrecognized: list[str] = field(default_factory=list)


# ── Public service entry point ──────────────────────────────────────────


def ingest_csv_file(
    conn: sqlite3.Connection,
    csv_path: Path | str,
    *,
    skip_duplicates: bool = True,
) -> IngestResult:
    """Ingest a time-tracker CSV into the Kālrekhā log.

    Runs the full pipeline: parse → classify → aggregate.  See the
    module docstring for ordering and transactional guarantees.

    Args:
        conn: Database connection.
        csv_path: Path to the CSV file.
        skip_duplicates: When ``True`` (the default), files whose
            SHA-256 hash has already been recorded are skipped entirely
            — ``imported`` will be 0 and no classify/aggregate step
            runs.

    Returns:
        An :class:`IngestResult` summarising what happened.

    Raises:
        IngestFileNotFoundError: If ``csv_path`` does not exist on
            disk.
        IngestFormatError: If the CSV header is missing or lacks the
            required columns (project, date, start, end).
    """
    # 1. Parse and insert.
    try:
        raw = ingest_csv(conn, csv_path, skip_duplicates=skip_duplicates)
    except FileNotFoundError as exc:
        raise IngestFileNotFoundError(str(exc)) from exc
    except ValueError as exc:
        raise IngestFormatError(str(exc)) from exc

    imported: int = raw["imported"]  # type: ignore[assignment]
    skipped: int = raw["skipped"]  # type: ignore[assignment]
    unrecognized: list[str] = raw["unrecognized"]  # type: ignore[assignment]

    # If nothing was imported (duplicate file or empty CSV), skip the
    # downstream steps — there's nothing new to classify or aggregate.
    if imported == 0:
        return IngestResult(
            imported=0,
            skipped=skipped,
            classified=0,
            aggregates_refreshed=0,
            unrecognized=unrecognized,
        )

    # 2. Classify freshly-imported sessions against the active Niyam.
    classified = classify_sessions(conn)

    # 3. Rebuild weekly aggregates.
    aggregates_refreshed = refresh_weekly_aggregates(conn)

    return IngestResult(
        imported=imported,
        skipped=skipped,
        classified=classified,
        aggregates_refreshed=aggregates_refreshed,
        unrecognized=unrecognized,
    )
