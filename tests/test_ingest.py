"""Tests for kalsangati.ingest — CSV parsing, aggregation, classification."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kalsangati.ingest import (
    classify_sessions,
    ingest_csv,
    refresh_weekly_aggregates,
)
from kalsangati.labels import add_mapping
from kalsangati.niyam import TimeBlock
from kalsangati.niyam import create as create_niyam


class TestIngestCsv:
    def test_basic_import(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        result = ingest_csv(conn, sample_csv)
        assert result["imported"] == 5
        assert result["skipped"] == 0

    def test_dedup_by_hash(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        ingest_csv(conn, sample_csv)
        result = ingest_csv(conn, sample_csv)
        assert result["imported"] == 0  # all skipped

    def test_dedup_by_row(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        result = ingest_csv(conn, sample_csv, skip_duplicates=False)
        assert result["imported"] == 5
        result2 = ingest_csv(conn, sample_csv, skip_duplicates=False)
        assert result2["skipped"] == 5

    def test_flags_unrecognized(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        result = ingest_csv(conn, sample_csv)
        assert len(result["unrecognized"]) > 0

    def test_recognized_not_flagged(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        add_mapping(conn, "02-Kitchen", "02-kitchen")
        add_mapping(conn, "03-Exercise", "03-exercise")
        result = ingest_csv(conn, sample_csv)
        assert result["unrecognized"] == []

    def test_sessions_stored(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        ingest_csv(conn, sample_csv)
        count = conn.execute("SELECT COUNT(*) FROM kalrekha").fetchone()[0]
        assert count == 5

    def test_source_tag(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        ingest_csv(conn, sample_csv)
        row = conn.execute(
            "SELECT source FROM kalrekha LIMIT 1"
        ).fetchone()
        assert row["source"] == "csv_import"


class TestAggregation:
    def test_refresh_creates_rows(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        ingest_csv(conn, sample_csv)
        count = refresh_weekly_aggregates(conn)
        assert count > 0
        rows = conn.execute("SELECT * FROM weekly_aggregates").fetchall()
        assert len(rows) > 0


class TestClassification:
    def test_classify_against_niyam(
        self, conn: sqlite3.Connection, sample_csv: Path
    ) -> None:
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        ingest_csv(conn, sample_csv)

        # Create and activate a Niyam with matching blocks
        blocks = {
            "monday": [TimeBlock("01-02-el", 540, 720, 3.0)],   # 09:00=540, 12:00=720
            "tuesday": [TimeBlock("01-02-el", 540, 720, 3.0)],
        }
        create_niyam(conn, "Test", blocks, set_active=True)

        classified = classify_sessions(conn)
        assert classified > 0

        # Check at least one session is classified as planned
        planned = conn.execute(
            "SELECT COUNT(*) FROM kalrekha "
            "WHERE block_classified = 1 AND unplanned = 0"
        ).fetchone()[0]
        assert planned > 0
