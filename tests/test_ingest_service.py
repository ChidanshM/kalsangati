"""Tests for kalsangati.services.ingest_csv — the IngestCSVService.

Also validates the ``classify_sessions`` refactor to use the pure
:func:`kalsangati.niyam.is_session_unplanned_under` helper (Unit 4
deliverable — single classification code path).
"""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from pathlib import Path

import pytest

from kalsangati.exceptions import (
    IngestFileNotFoundError,
    IngestFormatError,
    KalsangatiError,
)
from kalsangati.labels import add_mapping
from kalsangati.niyam import TimeBlock
from kalsangati.niyam import create as create_niyam
from kalsangati.services.ingest_csv import IngestResult, ingest_csv_file

# ── Helpers ─────────────────────────────────────────────────────────────


def _write_sample_csv(path: Path) -> Path:
    """Write a 3-row sample CSV and return the path."""
    path.write_text(
        "Project name,Task name,Date,Start time,End time,"
        "Duration,Timezone\n"
        "01-02 CIS731,Lecture,2025-03-10,09:00,11:00,"
        "02:00:00,US/Central\n"
        "01-02 CIS731,Homework,2025-03-10,14:00,15:30,"
        "01:30:00,US/Central\n"
        "02-Kitchen,Cooking,2025-03-10,18:00,19:00,"
        "01:00:00,US/Central\n",
        encoding="utf-8",
    )
    return path


def _setup_niyam_monday_9_12(
    conn: sqlite3.Connection,
    activity: str = "01-02-el",
) -> None:
    """Create and activate a Niyam with a monday 09:00–12:00 block."""
    blocks = {
        "monday": [TimeBlock(
            activity=activity, start_min=540, end_min=720,
            duration_h=3.0,
        )],
    }
    create_niyam(conn, "test-niyam", blocks, set_active=True)


# ── Happy path ──────────────────────────────────────────────────────────


class TestIngestHappyPath:
    def test_returns_ingest_result(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        r = ingest_csv_file(conn, csv_p)
        assert isinstance(r, IngestResult)

    def test_imported_count(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        r = ingest_csv_file(conn, csv_p)
        assert r.imported == 3

    def test_rows_in_kalrekha(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        ingest_csv_file(conn, csv_p)
        count = conn.execute(
            "SELECT COUNT(*) FROM kalrekha"
        ).fetchone()[0]
        assert count == 3

    def test_unrecognized_labels_reported(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        r = ingest_csv_file(conn, csv_p)
        # No mappings → both labels are unrecognized
        assert "01-02 CIS731" in r.unrecognized
        assert "02-Kitchen" in r.unrecognized

    def test_recognized_labels_not_in_unrecognized(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        add_mapping(conn, "02-Kitchen", "02-kitchen")
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        r = ingest_csv_file(conn, csv_p)
        assert r.unrecognized == []


# ── Classification step ────────────────────────────────────────────────


class TestIngestClassification:
    def test_sessions_classified_with_active_niyam(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        _setup_niyam_monday_9_12(conn)
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        r = ingest_csv_file(conn, csv_p)
        assert r.classified == 3

    def test_block_classified_flag_set(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        _setup_niyam_monday_9_12(conn)
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        ingest_csv_file(conn, csv_p)
        classified = conn.execute(
            "SELECT COUNT(*) FROM kalrekha "
            "WHERE block_classified = 1"
        ).fetchone()[0]
        assert classified == 3

    def test_planned_session_inside_block(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        """The 09:00 lecture on Monday is inside the 09–12 block."""
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        _setup_niyam_monday_9_12(conn)
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        ingest_csv_file(conn, csv_p)
        planned = conn.execute(
            "SELECT COUNT(*) FROM kalrekha "
            "WHERE block_classified = 1 AND unplanned = 0"
        ).fetchone()[0]
        assert planned >= 1

    def test_unplanned_session_outside_block(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        """The 14:00 homework is outside the 09–12 block → unplanned.
        The kitchen session is a different activity → unplanned.
        """
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        _setup_niyam_monday_9_12(conn)
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        ingest_csv_file(conn, csv_p)
        unplanned = conn.execute(
            "SELECT COUNT(*) FROM kalrekha "
            "WHERE block_classified = 1 AND unplanned = 1"
        ).fetchone()[0]
        assert unplanned >= 2

    def test_no_niyam_classified_zero(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        r = ingest_csv_file(conn, csv_p)
        assert r.classified == 0


# ── Aggregation step ───────────────────────────────────────────────────


class TestIngestAggregation:
    def test_aggregates_created(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        r = ingest_csv_file(conn, csv_p)
        assert r.aggregates_refreshed > 0
        rows = conn.execute(
            "SELECT COUNT(*) FROM weekly_aggregates"
        ).fetchone()[0]
        assert rows > 0


# ── Dedup ──────────────────────────────────────────────────────────────


class TestIngestDedup:
    def test_duplicate_file_returns_zero(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        ingest_csv_file(conn, csv_p)
        r2 = ingest_csv_file(conn, csv_p)
        assert r2.imported == 0
        assert r2.skipped == 0
        assert r2.classified == 0
        assert r2.aggregates_refreshed == 0

    def test_duplicate_skips_classify_and_aggregate(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        """On a dup, only 1 row-set exists in kalrekha from the
        first import — the second call must not re-classify or
        re-aggregate.
        """
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        _setup_niyam_monday_9_12(conn)
        csv_p = _write_sample_csv(tmp_path / "export.csv")
        ingest_csv_file(conn, csv_p)
        agg_before = conn.execute(
            "SELECT COUNT(*) FROM weekly_aggregates"
        ).fetchone()[0]
        r2 = ingest_csv_file(conn, csv_p)
        agg_after = conn.execute(
            "SELECT COUNT(*) FROM weekly_aggregates"
        ).fetchone()[0]
        assert r2.classified == 0
        assert agg_before == agg_after


# ── Error handling ─────────────────────────────────────────────────────


class TestIngestErrors:
    def test_file_not_found_raises_domain_error(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        with pytest.raises(IngestFileNotFoundError):
            ingest_csv_file(conn, tmp_path / "nonexistent.csv")

    def test_malformed_csv_raises_format_error(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_text("foo,bar,baz\n1,2,3\n", encoding="utf-8")
        with pytest.raises(IngestFormatError):
            ingest_csv_file(conn, bad)

    def test_domain_errors_inherit_kalsangati_error(self) -> None:
        assert issubclass(IngestFileNotFoundError, KalsangatiError)
        assert issubclass(IngestFormatError, KalsangatiError)

    def test_db_unchanged_after_file_not_found(
        self, conn: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        with suppress(IngestFileNotFoundError):
            ingest_csv_file(conn, tmp_path / "nope.csv")
        rows = conn.execute(
            "SELECT COUNT(*) FROM kalrekha"
        ).fetchone()[0]
        assert rows == 0
