"""Tests for kalsangati.niyam — CRUD, clone, CSV import, and v2 time format."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kalsangati.db import init_db, parse_time_blocks
from kalsangati.niyam import (
    MINUTES_PER_DAY,
    Niyam,
    TimeBlock,
    activity_summary,
    clone,
    create,
    delete,
    format_time,
    get_active,
    get_all,
    get_by_id,
    import_from_csv,
    rename,
    set_active,
    time_str_to_minutes,
    update_blocks,
)


# ── Time helper tests ───────────────────────────────────────────────────


class TestTimeHelpers:
    def test_hh_mm_round_trip(self) -> None:
        assert time_str_to_minutes("00:00") == 0
        assert time_str_to_minutes("09:00") == 540
        assert time_str_to_minutes("14:09") == 849
        assert time_str_to_minutes("23:59") == 1439
        assert time_str_to_minutes("24:00") == MINUTES_PER_DAY

    def test_hh_mm_ss_ignores_seconds(self) -> None:
        # Stopwatch writes HH:MM:SS to kalrekha; classify_sessions needs
        # this to parse to the same minute as HH:MM.
        assert time_str_to_minutes("14:09:30") == 849
        assert time_str_to_minutes("14:09:00") == 849

    def test_format_time_round_trip(self) -> None:
        assert format_time(0) == "00:00"
        assert format_time(540) == "09:00"
        assert format_time(849) == "14:09"
        assert format_time(MINUTES_PER_DAY) == "24:00"

    def test_invalid_time_raises(self) -> None:
        with pytest.raises(ValueError):
            time_str_to_minutes("bogus")
        with pytest.raises(ValueError):
            time_str_to_minutes("25:00")
        with pytest.raises(ValueError):
            time_str_to_minutes("12:60")

    def test_format_time_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            format_time(-1)
        with pytest.raises(ValueError):
            format_time(MINUTES_PER_DAY + 1)


# ── TimeBlock tests ─────────────────────────────────────────────────────


class TestTimeBlock:
    def test_int_construction(self) -> None:
        b = TimeBlock("study", 540, 660, 2.0)
        assert b.start_min == 540
        assert b.end_min == 660
        assert b.start == "09:00"  # derived display form
        assert b.end == "11:00"
        assert b.duration_min == 120

    def test_contains_minute(self) -> None:
        b = TimeBlock("study", 540, 660, 2.0)
        assert b.contains_minute(540) is True  # inclusive start
        assert b.contains_minute(600) is True
        assert b.contains_minute(660) is False  # exclusive end
        assert b.contains_minute(539) is False

    def test_to_dict_v2_format(self) -> None:
        b = TimeBlock("study", 540, 660, 2.0)
        d = b.to_dict()
        assert d == {
            "activity": "study",
            "start_min": 540,
            "end_min": 660,
            "duration_h": 2.0,
        }

    def test_from_dict_v2(self) -> None:
        b = TimeBlock.from_dict(
            {"activity": "study", "start_min": 540, "end_min": 660, "duration_h": 2.0}
        )
        assert b.start_min == 540
        assert b.end_min == 660

    def test_from_dict_v1_legacy(self) -> None:
        """Back-compat: legacy HH:MM dicts still parse."""
        b = TimeBlock.from_dict(
            {"activity": "study", "start": "09:00", "end": "11:00", "duration_h": 2.0}
        )
        assert b.start_min == 540
        assert b.end_min == 660


# ── CRUD tests (updated to int construction) ────────────────────────────


class TestNiyamCrud:
    def test_create_and_get(self, conn: sqlite3.Connection) -> None:
        blocks = {
            "monday": [
                TimeBlock("study", 540, 660, 2.0),
                TimeBlock("exercise", 360, 420, 1.0),
            ]
        }
        n = create(conn, "Test Niyam", blocks)
        assert n.name == "Test Niyam"
        assert n.total_hours == 3.0
        assert n.slot_count == 2

    def test_set_active(self, conn: sqlite3.Connection) -> None:
        n1 = create(conn, "A")
        n2 = create(conn, "B")
        set_active(conn, n2.id)
        active = get_active(conn)
        assert active is not None
        assert active.id == n2.id
        # n1 should not be active
        n1_fresh = get_by_id(conn, n1.id)
        assert n1_fresh is not None
        assert not n1_fresh.is_active

    def test_create_with_set_active(self, conn: sqlite3.Connection) -> None:
        n = create(conn, "Active One", set_active=True)
        assert get_active(conn) is not None
        assert get_active(conn).id == n.id

    def test_rename(self, conn: sqlite3.Connection) -> None:
        n = create(conn, "Old Name")
        rename(conn, n.id, "New Name")
        assert get_by_id(conn, n.id).name == "New Name"

    def test_delete(self, conn: sqlite3.Connection) -> None:
        n = create(conn, "ToDelete")
        delete(conn, n.id)
        assert get_by_id(conn, n.id) is None

    def test_clone(self, conn: sqlite3.Connection) -> None:
        blocks = {"tuesday": [TimeBlock("work", 540, 1020, 8.0)]}
        original = create(conn, "Original", blocks)
        cloned = clone(conn, original.id, "Copy")
        assert cloned.name == "Copy"
        assert cloned.total_hours == original.total_hours
        assert cloned.id != original.id

    def test_clone_nonexistent_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError):
            clone(conn, 9999, "Bad Clone")

    def test_update_blocks(self, conn: sqlite3.Connection) -> None:
        n = create(conn, "Updatable")
        new_blocks = {
            "friday": [TimeBlock("relax", 1080, 1200, 2.0)]
        }
        update_blocks(conn, n.id, new_blocks)
        updated = get_by_id(conn, n.id)
        assert updated.total_hours == 2.0

    def test_get_all_ordered(self, conn: sqlite3.Connection) -> None:
        create(conn, "First")
        create(conn, "Second")
        all_n = get_all(conn)
        # Newest first
        assert len(all_n) == 2
        assert all_n[0].id > all_n[1].id

    def test_minute_precise_times_persist(self, conn: sqlite3.Connection) -> None:
        """A block with an odd-minute start like 14:09 must survive a round-trip."""
        blocks = {
            "monday": [
                TimeBlock("work", time_str_to_minutes("14:09"),
                          time_str_to_minutes("15:47"), 1.633)
            ]
        }
        n = create(conn, "Precise", blocks)
        loaded = get_by_id(conn, n.id)
        mon = loaded.blocks_for_day("monday")
        assert len(mon) == 1
        assert mon[0].start_min == 849
        assert mon[0].end_min == 947
        assert mon[0].start == "14:09"
        assert mon[0].end == "15:47"


class TestNiyamMethods:
    def test_hours_for_activity(self, conn: sqlite3.Connection) -> None:
        blocks = {
            "monday": [TimeBlock("study", 540, 660, 2.0)],
            "wednesday": [TimeBlock("study", 540, 660, 2.0)],
        }
        n = create(conn, "Test", blocks)
        assert n.hours_for_activity("study") == 4.0
        assert n.hours_for_activity("missing") == 0.0

    def test_block_at_str(self, conn: sqlite3.Connection) -> None:
        blocks = {"monday": [TimeBlock("study", 540, 660, 2.0)]}
        n = create(conn, "Test", blocks)
        assert n.block_at("monday", "10:00") is not None
        assert n.block_at("monday", "10:00").activity == "study"
        # Exclusive end
        assert n.block_at("monday", "11:00") is None

    def test_block_at_accepts_hh_mm_ss(self, conn: sqlite3.Connection) -> None:
        blocks = {"monday": [TimeBlock("study", 540, 660, 2.0)]}
        n = create(conn, "Test", blocks)
        # kalrekha writes "HH:MM:SS" — block_at must accept it
        assert n.block_at("monday", "10:00:30") is not None

    def test_block_at_minute(self, conn: sqlite3.Connection) -> None:
        blocks = {"monday": [TimeBlock("study", 540, 660, 2.0)]}
        n = create(conn, "Test", blocks)
        assert n.block_at_minute("monday", 600).activity == "study"
        assert n.block_at_minute("monday", 660) is None

    def test_activity_summary(self, conn: sqlite3.Connection) -> None:
        blocks = {
            "monday": [
                TimeBlock("a", 540, 600, 1.0),
                TimeBlock("a", 840, 900, 1.0),
                TimeBlock("b", 600, 720, 2.0),
            ],
        }
        n = create(conn, "Sum", blocks)
        s = activity_summary(n)
        assert s["a"]["hours"] == 2.0
        assert s["a"]["slots"] == 2
        assert s["b"]["slots"] == 1


class TestNiyamCsvImport:
    def test_import(self, conn: sqlite3.Connection, niyam_csv: Path) -> None:
        n = import_from_csv(conn, niyam_csv, "Spring 26")
        assert n.name == "Spring 26"
        assert n.total_hours > 0
        assert "01-02-el" in n.activity_set
        # Verify the imported CSV times (HH:MM strings) land as ints
        mon = n.blocks_for_day("monday")
        assert len(mon) >= 1
        for block in mon:
            assert isinstance(block.start_min, int)
            assert isinstance(block.end_min, int)

    def test_import_set_active(
        self, conn: sqlite3.Connection, niyam_csv: Path
    ) -> None:
        import_from_csv(conn, niyam_csv, "Active", set_active_flag=True)
        assert get_active(conn) is not None


# ── Migration tests (v1 → v2) ───────────────────────────────────────────


class TestV2Migration:
    """Verify the v1→v2 migration rewrites HH:MM strings to int minutes."""

    def test_migration_converts_legacy_data(self, tmp_path: Path) -> None:
        """Seed a v1-format DB manually, open with init_db, verify v2."""
        db_path = tmp_path / "legacy.db"

        # Step 1: build a v1-format DB by hand — schema only, no migration
        # row recorded, with a Niyam that has HH:MM string blocks.
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE niyam (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_active INTEGER DEFAULT 0,
                time_blocks TEXT
            );
            CREATE TABLE _migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO _migrations (version) VALUES (1);
        """)
        legacy_blocks = {
            "monday": [
                {"activity": "01-02-el", "start": "14:09",
                 "end": "15:47", "duration_h": 1.633},
                {"activity": "02-kitchen", "start": "18:00",
                 "end": "19:00", "duration_h": 1.0},
            ],
            "tuesday": [
                {"activity": "01-02-el", "start": "09:00",
                 "end": "11:00", "duration_h": 2.0},
            ],
        }
        conn.execute(
            "INSERT INTO niyam (name, is_active, time_blocks) VALUES (?, ?, ?)",
            ("Legacy", 1, json.dumps(legacy_blocks)),
        )
        conn.commit()
        conn.close()

        # Step 2: open with init_db — should apply the v2 migration.
        new_conn = init_db(db_path)

        # Step 3: verify _migrations now records version 2.
        rows = new_conn.execute(
            "SELECT version FROM _migrations ORDER BY version"
        ).fetchall()
        versions = [r["version"] for r in rows]
        assert 2 in versions

        # Step 4: verify the JSON stored in niyam.time_blocks is now v2 form.
        raw = new_conn.execute(
            "SELECT time_blocks FROM niyam WHERE name = ?", ("Legacy",)
        ).fetchone()["time_blocks"]
        data = json.loads(raw)

        mon = data["monday"]
        assert mon[0]["start_min"] == 849  # 14:09
        assert mon[0]["end_min"] == 947    # 15:47
        assert "start" not in mon[0]
        assert "end" not in mon[0]
        assert mon[0]["duration_h"] == 1.633

        assert mon[1]["start_min"] == 1080  # 18:00
        assert mon[1]["end_min"] == 1140    # 19:00

        tue = data["tuesday"]
        assert tue[0]["start_min"] == 540
        assert tue[0]["end_min"] == 660

        # Step 5: and the high-level API (get_all) returns TimeBlock ints.
        niyams = get_all(new_conn)
        assert len(niyams) == 1
        n = niyams[0]
        mon_blocks = n.blocks_for_day("monday")
        assert mon_blocks[0].start_min == 849
        assert mon_blocks[0].end_min == 947
        assert mon_blocks[0].start == "14:09"  # display form
        new_conn.close()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Running init_db twice on a migrated DB should be safe."""
        db_path = tmp_path / "twice.db"
        conn1 = init_db(db_path)
        # Seed a v2-format Niyam through the normal API.
        blocks = {"monday": [TimeBlock("x", 540, 600, 1.0)]}
        create(conn1, "V2Native", blocks)
        conn1.close()

        # Reopen — migration should see v2 already applied.
        conn2 = init_db(db_path)
        versions = [
            r["version"]
            for r in conn2.execute(
                "SELECT version FROM _migrations"
            ).fetchall()
        ]
        # Exactly one row for v2, not two.
        assert versions.count(2) == 1
        # Data still intact.
        n = get_all(conn2)[0]
        assert n.blocks_for_day("monday")[0].start_min == 540
        conn2.close()

    def test_migration_skips_already_v2_blocks(self, tmp_path: Path) -> None:
        """Blocks that already have start_min are left alone."""
        db_path = tmp_path / "mixed.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE niyam (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_active INTEGER DEFAULT 0,
                time_blocks TEXT
            );
            CREATE TABLE _migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO _migrations (version) VALUES (1);
        """)
        # Block already in v2 form.
        already_v2 = {
            "monday": [
                {"activity": "x", "start_min": 540,
                 "end_min": 600, "duration_h": 1.0}
            ]
        }
        conn.execute(
            "INSERT INTO niyam (name, is_active, time_blocks) VALUES (?, ?, ?)",
            ("AlreadyV2", 0, json.dumps(already_v2)),
        )
        conn.commit()
        conn.close()

        new_conn = init_db(db_path)
        raw = new_conn.execute(
            "SELECT time_blocks FROM niyam WHERE name = ?", ("AlreadyV2",)
        ).fetchone()["time_blocks"]
        data = json.loads(raw)
        assert data["monday"][0]["start_min"] == 540
        new_conn.close()
