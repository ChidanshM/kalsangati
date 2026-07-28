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
from PyQt5.QtWidgets import QApplication, QComboBox, QMessageBox  # noqa: E402

from kalsangati.exceptions import InvalidTaskTransitionError  # noqa: E402
from kalsangati.gui.task_planner import TaskPlanner  # noqa: E402
from kalsangati.services.update_task_status import (  # noqa: E402
    allowed_transitions,
)
from kalsangati.tasks import create, get_by_id, get_task_events  # noqa: E402

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
