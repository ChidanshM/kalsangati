"""Tests for kalsangati.db — schema, settings, helpers."""

from __future__ import annotations

import sqlite3

from kalsangati.db import (
    get_setting,
    parse_time_blocks,
    serialize_time_blocks,
    set_setting,
)


class TestInitDb:
    def test_creates_all_tables(self, conn: sqlite3.Connection) -> None:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "niyam", "kalrekha", "weekly_aggregates",
            "label_mappings", "label_groups", "projects", "tasks",
            "settings", "_migrations",
        }
        assert expected.issubset(tables)

    def test_default_settings_seeded(self, conn: sqlite3.Connection) -> None:
        val = get_setting(conn, "notify_lead_minutes")
        assert val == "5"

    def test_wal_mode(self, conn: sqlite3.Connection) -> None:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_foreign_keys_on(self, conn: sqlite3.Connection) -> None:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


class TestSettings:
    def test_get_set(self, conn: sqlite3.Connection) -> None:
        set_setting(conn, "test_key", "test_value")
        assert get_setting(conn, "test_key") == "test_value"

    def test_upsert(self, conn: sqlite3.Connection) -> None:
        set_setting(conn, "test_key", "v1")
        set_setting(conn, "test_key", "v2")
        assert get_setting(conn, "test_key") == "v2"

    def test_missing_key(self, conn: sqlite3.Connection) -> None:
        assert get_setting(conn, "nonexistent") is None


class TestTimeBlocksJson:
    def test_roundtrip(self) -> None:
        blocks = {
            "monday": [
                {
                    "activity": "study", "start": "09:00",
                    "end": "11:00", "duration_h": 2.0,
                }
            ]
        }
        serialized = serialize_time_blocks(blocks)
        parsed = parse_time_blocks(serialized)
        assert parsed == blocks

    def test_empty(self) -> None:
        assert parse_time_blocks(None) == {}
        assert parse_time_blocks("") == {}
