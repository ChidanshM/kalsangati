"""Tests for kalsangati.niyam — CRUD, clone, CSV import."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kalsangati.niyam import (
    Niyam,
    TimeBlock,
    activity_summary,
    clone,
    create,
    delete,
    get_active,
    get_all,
    get_by_id,
    import_from_csv,
    rename,
    set_active,
    update_blocks,
)


class TestNiyamCrud:
    def test_create_and_get(self, conn: sqlite3.Connection) -> None:
        blocks = {
            "monday": [
                TimeBlock("study", "09:00", "11:00", 2.0),
                TimeBlock("exercise", "06:00", "07:00", 1.0),
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
        blocks = {"tuesday": [TimeBlock("work", "09:00", "17:00", 8.0)]}
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
            "friday": [TimeBlock("relax", "18:00", "20:00", 2.0)]
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


class TestNiyamMethods:
    def test_hours_for_activity(self, conn: sqlite3.Connection) -> None:
        blocks = {
            "monday": [TimeBlock("study", "09:00", "11:00", 2.0)],
            "wednesday": [TimeBlock("study", "09:00", "11:00", 2.0)],
        }
        n = create(conn, "Test", blocks)
        assert n.hours_for_activity("study") == 4.0
        assert n.hours_for_activity("missing") == 0.0

    def test_block_at(self, conn: sqlite3.Connection) -> None:
        blocks = {
            "monday": [TimeBlock("study", "09:00", "11:00", 2.0)]
        }
        n = create(conn, "Test", blocks)
        assert n.block_at("monday", "10:00") is not None
        assert n.block_at("monday", "10:00").activity == "study"
        assert n.block_at("monday", "11:00") is None

    def test_activity_summary(self, conn: sqlite3.Connection) -> None:
        blocks = {
            "monday": [
                TimeBlock("a", "09:00", "10:00", 1.0),
                TimeBlock("a", "14:00", "15:00", 1.0),
                TimeBlock("b", "10:00", "12:00", 2.0),
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

    def test_import_set_active(
        self, conn: sqlite3.Connection, niyam_csv: Path
    ) -> None:
        import_from_csv(conn, niyam_csv, "Active", set_active_flag=True)
        assert get_active(conn) is not None
