"""Tests for kalsangati.labels — conversion, grouping, hierarchy."""

from __future__ import annotations

import sqlite3

import pytest

from kalsangati.labels import (
    add_group,
    add_mapping,
    auto_populate_groups,
    delete_mapping,
    get_all_groups,
    get_all_mappings,
    get_unrecognized_labels,
    infer_level,
    resolve_hierarchy,
    resolve_label,
    suggest_parent_from_prefix,
    update_mapping,
)


class TestPrefixHelpers:
    def test_suggest_parent(self) -> None:
        assert suggest_parent_from_prefix("01-02-el") == "01-02"
        assert suggest_parent_from_prefix("02-kitchen") == "02"
        assert suggest_parent_from_prefix("01-02") == "01"
        assert suggest_parent_from_prefix("01") is None
        assert suggest_parent_from_prefix("reading") is None

    def test_infer_level(self) -> None:
        assert infer_level("01") == 1
        assert infer_level("01-02") == 2
        assert infer_level("01-02-el") == 3
        assert infer_level("02-kitchen") == 2
        assert infer_level("reading") == 0


class TestLabelMappings:
    def test_add_and_resolve(self, conn: sqlite3.Connection) -> None:
        add_mapping(conn, "01-02 CIS731", "01-02-el")
        assert resolve_label(conn, "01-02 CIS731") == "01-02-el"

    def test_resolve_missing(self, conn: sqlite3.Connection) -> None:
        assert resolve_label(conn, "unknown") is None

    def test_update(self, conn: sqlite3.Connection) -> None:
        mid = add_mapping(conn, "raw1", "canon1")
        update_mapping(conn, mid, canonical_label="canon2")
        assert resolve_label(conn, "raw1") == "canon2"

    def test_delete(self, conn: sqlite3.Connection) -> None:
        mid = add_mapping(conn, "raw_del", "canon_del")
        delete_mapping(conn, mid)
        assert resolve_label(conn, "raw_del") is None

    def test_duplicate_raw_raises(self, conn: sqlite3.Connection) -> None:
        add_mapping(conn, "dup", "c1")
        with pytest.raises(sqlite3.IntegrityError):
            add_mapping(conn, "dup", "c2")

    def test_get_all(self, conn: sqlite3.Connection) -> None:
        add_mapping(conn, "b_raw", "b_canon")
        add_mapping(conn, "a_raw", "a_canon")
        mappings = get_all_mappings(conn)
        assert [m.raw_label for m in mappings] == ["a_raw", "b_raw"]


class TestUnrecognized:
    def test_flags_unmapped_projects(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO kalrekha (project, date, start, \"end\", duration_min) "
            "VALUES ('unknown_proj', '2025-01-01', '09:00', '10:00', 60)"
        )
        conn.commit()
        unrec = get_unrecognized_labels(conn)
        assert "unknown_proj" in unrec


class TestLabelGroups:
    def test_add_with_auto_parent(self, conn: sqlite3.Connection) -> None:
        add_group(conn, "01")
        add_group(conn, "01-02")
        groups = get_all_groups(conn)
        g = next(g for g in groups if g.canonical_label == "01-02")
        assert g.parent_group == "01"
        assert g.level == 2

    def test_hierarchy_walk(self, conn: sqlite3.Connection) -> None:
        add_group(conn, "01")
        add_group(conn, "01-02")
        add_group(conn, "01-02-01")
        chain = resolve_hierarchy(conn, "01-02-01")
        assert chain == ["01-02-01", "01-02", "01"]

    def test_auto_populate(self, conn: sqlite3.Connection) -> None:
        add_mapping(conn, "raw1", "01-02-01-lecture")
        add_mapping(conn, "raw2", "01-02-01-prep")
        count = auto_populate_groups(conn)
        assert count >= 3  # 01, 01-02, 01-02-01 at minimum
        groups = {g.canonical_label for g in get_all_groups(conn)}
        assert "01" in groups
        assert "01-02" in groups
