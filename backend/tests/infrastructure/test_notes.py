"""Tests for infrastructure/notes.py — Markdown bodies on disk.

Every test patches ``notes_directory`` to a ``tmp_path``.  Nothing here
may touch the real notes folder: these tests write files, and the real
folder holds the user's actual prose.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from kalsangati.core import tasks
from kalsangati.infrastructure import notes


@pytest.fixture(autouse=True)
def notes_root(tmp_path: Path) -> Iterator[Path]:
    """Point the notes directory at a temporary folder.

    **autouse on purpose.**  These tests write real files.  A test that
    forgets to request this fixture writes them into the user's actual
    notes folder — silently, while still passing, because the write
    itself succeeds.  autouse removes the possibility rather than
    relying on every future test remembering.
    """
    root = tmp_path / "notes"
    original = notes.notes_directory
    notes.notes_directory = lambda: root  # type: ignore[assignment]
    yield root
    notes.notes_directory = original  # type: ignore[assignment]


def _task(
    conn: sqlite3.Connection, title: str, *, parent_id: int | None = None
) -> tasks.Task:
    return tasks.create(conn, title, "01-02-el", parent_id=parent_id)


# ── Path derivation ─────────────────────────────────────────────────────


class TestDerivePath:
    def test_root_task_is_a_flat_file(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        t = _task(conn, "Problem set 4")
        path = notes.derive_path(conn, t.id)
        assert path == notes_root / f"{t.id}-problem-set-4.md"

    def test_child_lives_in_the_parents_folder(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        parent = _task(conn, "Final project")
        child = _task(conn, "Literature review", parent_id=parent.id)
        path = notes.derive_path(conn, child.id)
        assert path == (
            notes_root
            / f"{parent.id}-final-project"
            / f"{child.id}-literature-review.md"
        )

    def test_grandchild_nests_twice(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        c = _task(conn, "C", parent_id=b.id)
        path = notes.derive_path(conn, c.id)
        assert path is not None
        assert path.parent.name == f"{b.id}-b"
        assert path.parent.parent.name == f"{a.id}-a"

    def test_parent_file_sits_beside_its_folder(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """A task's own file never moves when it gains a child."""
        parent = _task(conn, "Parent")
        before = notes.derive_path(conn, parent.id)
        _task(conn, "Child", parent_id=parent.id)
        after = notes.derive_path(conn, parent.id)
        assert before == after

    def test_slugless_task_falls_back_to_its_id(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """``slug`` is nullable, having been introduced dormant."""
        t = _task(conn, "Whatever")
        conn.execute("UPDATE tasks SET slug = NULL WHERE id = ?", (t.id,))
        conn.commit()
        path = notes.derive_path(conn, t.id)
        assert path == notes_root / f"task-{t.id}.md"

    def test_notes_path_overrides_the_derivation(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        t = _task(conn, "Elsewhere")
        tasks.update(conn, t.id, notes_path="somewhere/else.md")
        path = notes.derive_path(conn, t.id)
        assert path == notes_root / "somewhere/else.md"

    def test_unknown_task_has_no_path(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        assert notes.derive_path(conn, 99999) is None

    def test_deleted_task_still_has_a_path(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """Deleting a task keeps its notes, so the path must stay
        derivable to find them."""
        from kalsangati.services.delete_task import delete_task

        t = _task(conn, "Gone")
        expected = notes.derive_path(conn, t.id)
        delete_task(conn, t.id)
        assert notes.derive_path(conn, t.id) == expected

    def test_reparenting_changes_the_path_but_not_the_file(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """Documents a known gap rather than a desired behaviour.

        The derived path follows the move; the file does not.  The file
        is then reachable only through the task id in its frontmatter,
        which is what the reconcile pass will use.
        """
        from kalsangati.services.reparent_task import reparent_task

        parent = _task(conn, "Parent")
        child = _task(conn, "Child")
        written = notes.write_body(conn, child.id, "some prose")

        reparent_task(conn, child.id, new_parent_id=parent.id)

        moved = notes.derive_path(conn, child.id)
        assert moved != written
        assert written.is_file()  # still where it was
        assert not moved.is_file()  # nothing at the new location


# ── Round trip ──────────────────────────────────────────────────────────


class TestReadWrite:
    def test_round_trips_the_body(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        t = _task(conn, "Notes")
        notes.write_body(conn, t.id, "## Approach\n\nStart with the CTE.")
        assert notes.read_body(conn, t.id) == (
            "## Approach\n\nStart with the CTE."
        )

    def test_frontmatter_carries_id_and_title(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        t = _task(conn, "Carries identity")
        path = notes.write_body(conn, t.id, "body")
        text = path.read_text(encoding="utf-8")
        assert f"task_id: {t.id}" in text
        assert 'title: "Carries identity"' in text
        assert "# Carries identity" in text

    def test_title_with_a_colon_is_quoted(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """An unquoted colon-space is invalid YAML, and such titles are
        ordinary here."""
        t = _task(conn, "Fix E1: wall-clock drift")
        path = notes.write_body(conn, t.id, "body")
        text = path.read_text(encoding="utf-8")
        assert 'title: "Fix E1: wall-clock drift"' in text

    def test_body_containing_a_horizontal_rule_survives(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """The naive ``split('---')`` breaks exactly here."""
        t = _task(conn, "Ruled")
        body = "Before\n\n---\n\nAfter"
        notes.write_body(conn, t.id, body)
        assert notes.read_body(conn, t.id) == body

    def test_empty_body_writes_header_only(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        t = _task(conn, "Blank")
        path = notes.write_body(conn, t.id, "")
        assert path.is_file()
        assert notes.read_body(conn, t.id) == ""

    def test_rename_updates_the_header_and_keeps_the_body(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """The app owns the frontmatter and the heading, nothing else."""
        t = _task(conn, "Original")
        notes.write_body(conn, t.id, "prose that must survive")
        tasks.update(conn, t.id, title="Renamed")

        path = notes.write_body(
            conn, t.id, notes.read_body(conn, t.id) or ""
        )
        text = path.read_text(encoding="utf-8")

        assert 'title: "Renamed"' in text
        assert "# Renamed" in text
        assert "prose that must survive" in text

    def test_write_creates_parent_directories(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        a = _task(conn, "A")
        b = _task(conn, "B", parent_id=a.id)
        c = _task(conn, "C", parent_id=b.id)
        path = notes.write_body(conn, c.id, "deep")
        assert path.is_file()

    def test_write_on_unknown_task_raises(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        with pytest.raises(ValueError):
            notes.write_body(conn, 99999, "orphan")


class TestMissingAndUnusualFiles:
    def test_no_file_reads_as_none(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """``None`` means *no file*, to be reported rather than treated
        as empty: silently recreating it would erase the evidence that
        something went wrong."""
        t = _task(conn, "Never written")
        assert notes.read_body(conn, t.id) is None

    def test_reading_does_not_create_a_file(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        t = _task(conn, "Untouched")
        notes.read_body(conn, t.id)
        path = notes.derive_path(conn, t.id)
        assert path is not None
        assert not path.is_file()

    def test_file_without_frontmatter_is_all_body(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """It may have been written by hand; refusing it would lose it."""
        t = _task(conn, "Handwritten")
        path = notes.derive_path(conn, t.id)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("just some prose", encoding="utf-8")
        assert notes.read_body(conn, t.id) == "just some prose"

    def test_unterminated_frontmatter_is_all_body(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        t = _task(conn, "Broken")
        path = notes.derive_path(conn, t.id)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntask_id: 1\nno closing", encoding="utf-8")
        assert notes.read_body(conn, t.id) is not None


class TestDeletionLeavesFilesAlone:
    def test_soft_delete_keeps_the_file(
        self, conn: sqlite3.Connection, notes_root: Path
    ) -> None:
        """Prose is the one irreplaceable thing here: a row can be
        rebuilt from its event trail, a paragraph cannot."""
        from kalsangati.services.delete_task import delete_task

        t = _task(conn, "Deleted but written")
        path = notes.write_body(conn, t.id, "still here")

        delete_task(conn, t.id)

        assert path.is_file()
        assert notes.read_body(conn, t.id) == "still here"


class TestRootReadme:
    def test_created_on_demand(self, notes_root: Path) -> None:
        notes.ensure_root_readme()
        assert (notes_root / "README.md").is_file()

    def test_never_overwrites(self, notes_root: Path) -> None:
        notes_root.mkdir(parents=True, exist_ok=True)
        readme = notes_root / "README.md"
        readme.write_text("my own words", encoding="utf-8")
        notes.ensure_root_readme()
        assert readme.read_text(encoding="utf-8") == "my own words"
