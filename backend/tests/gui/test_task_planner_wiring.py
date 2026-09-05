"""Tests for gui/task_planner.py status wiring — Unit 8.

Runs the TaskPlanner widget under headless Qt
(``QT_QPA_PLATFORM=offscreen``).  Verifies the Unit-8 wiring: the
week-table status dropdown offers only legal moves, ``_apply_status``
routes through :func:`update_task_status` with the presentation-layer
exception pattern, and refresh runs whether or not the call succeeds.

The service is patched in the dispatch tests so the wiring is isolated
from service behaviour (that coverage lives in
``test_update_task_status.py``).  One end-to-end test exercises the real
service against the ``conn`` fixture to prove the path mutates state.
"""

from __future__ import annotations

import os

# Must be set before PyQt5 is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3  # noqa: E402
from collections.abc import Generator  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QComboBox, QMessageBox  # noqa: E402

from kalsangati.core.exceptions import (  # noqa: E402
    InvalidTaskTransitionError,
    TaskCycleError,
)
from kalsangati.core.tasks import create, get_by_id, get_task_events  # noqa: E402
from kalsangati.gui.task_planner import TaskPlanner  # noqa: E402
from kalsangati.services.update_task_status import (  # noqa: E402
    allowed_transitions,
)

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Headless QApplication shared across the module's tests."""
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


@pytest.fixture
def planner(
    conn: sqlite3.Connection, qapp: QApplication
) -> Generator[TaskPlanner, None, None]:
    """A TaskPlanner backed by the shared ``conn`` fixture."""
    w = TaskPlanner(conn)
    yield w
    w.close()


def _combo_targets(combo: QComboBox) -> list[str]:
    """Item data for every entry after index 0 (the transition targets)."""
    return [combo.itemData(i) for i in range(1, combo.count())]


# ── Status dropdown construction ────────────────────────────────────────


class TestStatusCombo:
    """The dropdown offers the current status plus only its legal moves."""

    def test_index_zero_is_current_status(self, planner: TaskPlanner) -> None:
        combo = planner._make_status_combo(1, "in_progress")
        assert combo.itemData(0) == "in_progress"

    @pytest.mark.parametrize(
        "current",
        ["backlog", "this_week", "in_progress", "on_hold", "done"],
    )
    def test_targets_match_allowed_transitions(
        self, planner: TaskPlanner, current: str
    ) -> None:
        combo = planner._make_status_combo(1, current)
        assert set(_combo_targets(combo)) == allowed_transitions(current)

    def test_current_status_not_offered_as_target(
        self, planner: TaskPlanner
    ) -> None:
        combo = planner._make_status_combo(1, "in_progress")
        assert "in_progress" not in _combo_targets(combo)

    def test_illegal_target_absent(self, planner: TaskPlanner) -> None:
        """A done task offers no on_hold / this_week option."""
        combo = planner._make_status_combo(1, "done")
        targets = _combo_targets(combo)
        assert "on_hold" not in targets
        assert "this_week" not in targets
        assert set(targets) == {"in_progress", "backlog"}


# ── _apply_status dispatch ──────────────────────────────────────────────


class TestApplyStatusDispatch:
    """_apply_status forwards to the service and dispatches errors."""

    def test_forwards_to_service_and_refreshes(
        self, planner: TaskPlanner
    ) -> None:
        with (
            patch("kalsangati.gui.task_planner.update_task_status") as mock_svc,
            patch.object(planner, "refresh") as mock_refresh,
        ):
            planner._apply_status(7, "done")

        assert mock_svc.call_count == 1
        args = mock_svc.call_args.args
        assert args[0] is planner._conn
        assert args[1] == 7
        assert args[2] == "done"
        assert mock_refresh.called  # finally always refreshes

    def test_domain_error_shows_warning(self, planner: TaskPlanner) -> None:
        with (
            patch(
                "kalsangati.gui.task_planner.update_task_status",
                side_effect=InvalidTaskTransitionError("nope"),
            ),
            patch.object(planner, "refresh") as mock_refresh,
            patch.object(QMessageBox, "warning") as mock_warning,
            patch.object(QMessageBox, "critical") as mock_critical,
        ):
            planner._apply_status(7, "on_hold")

        assert mock_warning.called
        assert not mock_critical.called
        assert mock_refresh.called  # refresh runs even on failure

    def test_unexpected_error_shows_critical_and_logs(
        self,
        planner: TaskPlanner,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            patch(
                "kalsangati.gui.task_planner.update_task_status",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(planner, "refresh"),
            patch.object(QMessageBox, "warning") as mock_warning,
            patch.object(QMessageBox, "critical") as mock_critical,
            caplog.at_level("ERROR", logger="kalsangati.gui.task_planner"),
        ):
            planner._apply_status(7, "done")

        assert mock_critical.called
        assert not mock_warning.called
        assert any(
            "boom" in r.getMessage() or "Unexpected error" in r.getMessage()
            for r in caplog.records
        )


# ── End-to-end through the real service ─────────────────────────────────


class TestApplyStatusEndToEnd:
    """A real _apply_status call mutates the DB and logs an event."""

    def test_mark_done_changes_status_and_logs(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        task = create(conn, "T", "01-02-el", status="backlog")

        planner._apply_status(task.id, "done")

        fresh = get_by_id(conn, task.id)
        assert fresh is not None
        assert fresh.status == "done"
        events = get_task_events(conn, task.id)
        assert events[-1].event_type == "ended"


# ── Backlog tree construction (P2U05) ──────────────────────────────


def _tree_titles(planner: TaskPlanner) -> list[str]:
    """Top-level item labels, in display order."""
    tree = planner._backlog_tree
    return [
        tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())
    ]


def _child_titles(planner: TaskPlanner, index: int) -> list[str]:
    item = planner._backlog_tree.topLevelItem(index)
    return [item.child(i).text(0) for i in range(item.childCount())]


class TestBacklogTree:
    def test_child_nests_under_parent(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        parent = create(conn, "Parent", "01-02-el")
        create(conn, "Child", "01-02-el", parent_id=parent.id)

        planner.refresh()

        assert planner._backlog_tree.topLevelItemCount() == 1
        assert _child_titles(planner, 0)[0].startswith("Child")

    def test_three_levels_nest(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        a = create(conn, "A", "01-02-el")
        b = create(conn, "B", "01-02-el", parent_id=a.id)
        create(conn, "C", "01-02-el", parent_id=b.id)

        planner.refresh()

        top = planner._backlog_tree.topLevelItem(0)
        assert top.childCount() == 1
        assert top.child(0).childCount() == 1
        assert top.child(0).child(0).text(0).startswith("C")

    def test_siblings_follow_sort_order(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        create(conn, "First", "01-02-el")
        create(conn, "Second", "01-02-el")
        create(conn, "Third", "01-02-el")

        planner.refresh()

        titles = _tree_titles(planner)
        assert titles[0].startswith("First")
        assert titles[1].startswith("Second")
        assert titles[2].startswith("Third")

    def test_orphan_renders_at_root(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        """A child whose parent left the backlog must stay visible.

        The query filters ``status="backlog"``, so a parent moved to
        in_progress is absent from the result set.  Dropping its children
        would look exactly like the tasks had been deleted.
        """
        parent = create(conn, "Parent", "01-02-el")
        create(conn, "Orphan", "01-02-el", parent_id=parent.id)
        planner._apply_status(parent.id, "in_progress")

        titles = _tree_titles(planner)
        assert any(t.startswith("Orphan") for t in titles)

    def test_empty_backlog_is_empty_tree(
        self, planner: TaskPlanner
    ) -> None:
        planner.refresh()
        assert planner._backlog_tree.topLevelItemCount() == 0

    def test_item_carries_task_id(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        t = create(conn, "Carrier", "01-02-el")
        planner.refresh()
        item = planner._backlog_tree.topLevelItem(0)
        assert item.data(0, Qt.ItemDataRole.UserRole) == t.id


# ── Drag to reparent ──────────────────────────────────────────


class TestApplyReparent:
    """The drop handler, tested directly.

    Simulating a Qt drag with QTest mouse events is unreliable; the
    handler is the unit under test, so it is called directly.
    """

    def test_move_sets_parent(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        a = create(conn, "A", "01-02-el")
        b = create(conn, "B", "01-02-el")

        planner._apply_reparent(b.id, a.id)

        fresh = get_by_id(conn, b.id)
        assert fresh is not None
        assert fresh.parent_id == a.id

    def test_drop_on_blank_promotes_to_root(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        a = create(conn, "A", "01-02-el")
        b = create(conn, "B", "01-02-el", parent_id=a.id)

        planner._apply_reparent(b.id, None)

        fresh = get_by_id(conn, b.id)
        assert fresh is not None
        assert fresh.parent_id is None

    def test_cycle_warns_and_leaves_data_alone(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        """Dragging a task onto its own descendant.

        The path that makes this widget worth building: the user sees a
        sentence, and the tree still agrees with the database.
        """
        a = create(conn, "A", "01-02-el")
        b = create(conn, "B", "01-02-el", parent_id=a.id)

        with (
            patch.object(QMessageBox, "warning") as mock_warning,
            patch.object(QMessageBox, "critical") as mock_critical,
        ):
            planner._apply_reparent(a.id, b.id)

        assert mock_warning.called
        assert not mock_critical.called
        fresh = get_by_id(conn, a.id)
        assert fresh is not None
        assert fresh.parent_id is None

    def test_domain_error_surfaces_as_warning(
        self, planner: TaskPlanner
    ) -> None:
        with (
            patch(
                "kalsangati.gui.task_planner.reparent_task",
                side_effect=TaskCycleError("nope"),
            ),
            patch.object(planner, "refresh") as mock_refresh,
            patch.object(QMessageBox, "warning") as mock_warning,
        ):
            planner._apply_reparent(1, 2)

        assert mock_warning.called
        assert mock_refresh.called

    def test_unexpected_error_shows_critical(
        self, planner: TaskPlanner
    ) -> None:
        with (
            patch(
                "kalsangati.gui.task_planner.reparent_task",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(planner, "refresh") as mock_refresh,
            patch.object(QMessageBox, "warning") as mock_warning,
            patch.object(QMessageBox, "critical") as mock_critical,
        ):
            planner._apply_reparent(1, 2)

        assert mock_critical.called
        assert not mock_warning.called
        assert mock_refresh.called

    def test_tree_redraws_from_the_database(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        """After a successful move the tree reflects the new shape.

        Qt's InternalMove is deliberately not used, so this passing is
        evidence that refresh() — not the widget — did the redraw.
        """
        a = create(conn, "A", "01-02-el")
        b = create(conn, "B", "01-02-el")
        planner.refresh()
        assert planner._backlog_tree.topLevelItemCount() == 2

        planner._apply_reparent(b.id, a.id)

        assert planner._backlog_tree.topLevelItemCount() == 1
        assert _child_titles(planner, 0)[0].startswith("B")


# ── New Subtask ──────────────────────────────────────────────


class TestNewSubtask:
    def test_button_disabled_without_selection(
        self, planner: TaskPlanner
    ) -> None:
        planner.refresh()
        assert planner._btn_subtask.isEnabled() is False

    def test_button_enabled_with_selection(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        create(conn, "Selectable", "01-02-el")
        planner.refresh()
        planner._backlog_tree.setCurrentItem(
            planner._backlog_tree.topLevelItem(0)
        )
        assert planner._btn_subtask.isEnabled() is True

    def test_creates_a_child_of_the_selection(
        self, planner: TaskPlanner, conn: sqlite3.Connection
    ) -> None:
        parent = create(conn, "Parent", "01-02-el")
        planner.refresh()
        planner._backlog_tree.setCurrentItem(
            planner._backlog_tree.topLevelItem(0)
        )

        with patch.object(
            planner, "_create_task"
        ) as mock_create:
            planner._on_add_subtask()

        mock_create.assert_called_once_with(parent_id=parent.id)

    def test_noop_without_selection(self, planner: TaskPlanner) -> None:
        planner.refresh()
        with patch.object(planner, "_create_task") as mock_create:
            planner._on_add_subtask()
        assert not mock_create.called
