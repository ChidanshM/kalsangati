"""Shared test fixtures for Kālsangati."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from kalsangati.db import init_db


@pytest.fixture
def conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Yield a fresh in-memory-like DB connection for each test."""
    db_path = tmp_path / "test.db"
    connection = init_db(db_path)
    yield connection
    connection.close()


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    """Create a minimal sample time-tracker CSV."""
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "Project name,Task name,Date,Start time,End time,Duration,Timezone\n"
        "01-02 CIS731,Lecture,2025-03-10,09:00,11:00,02:00:00,US/Central\n"
        "01-02 CIS731,Homework,2025-03-10,14:00,15:30,01:30:00,US/Central\n"
        "02-Kitchen,Cooking,2025-03-10,18:00,19:00,01:00:00,US/Central\n"
        "01-02 CIS731,Lecture,2025-03-11,09:00,11:00,02:00:00,US/Central\n"
        "03-Exercise,Running,2025-03-11,06:00,07:00,01:00:00,US/Central\n",
        encoding="utf-8",
    )
    return csv_path


@pytest.fixture
def niyam_csv(tmp_path: Path) -> Path:
    """Create a sample Niyam CSV for import."""
    csv_path = tmp_path / "spring26.csv"
    csv_path.write_text(
        "day,activity,start,end,duration_h\n"
        "monday,01-02-el,09:00,11:00,2.0\n"
        "monday,02-kitchen,18:00,19:00,1.0\n"
        "tuesday,01-02-el,09:00,11:00,2.0\n"
        "tuesday,03-exercise,06:00,07:00,1.0\n"
        "wednesday,01-02-el,09:00,11:00,2.0\n"
        "thursday,01-02-el,09:00,11:00,2.0\n"
        "friday,01-02-el,09:00,11:00,2.0\n",
        encoding="utf-8",
    )
    return csv_path
