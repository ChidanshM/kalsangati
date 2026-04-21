"""Tests for ``gui/stopwatch.py::_end_session`` — Unit 5 wiring.

Runs the stopwatch widget under a headless Qt platform
(``QT_QPA_PLATFORM=offscreen``).  These tests exercise the service-
wiring introduced in Unit 5: that ``_end_session`` forwards the right
arguments to :func:`kalsangati.services.commit_stopwatch_session`,
narrows the task id correctly (the latent ``currentText`` vs.
``currentData`` bug fix), dispatches exceptions to the correct
message-box path, and clears ``_session_start`` even on failure.

No rendering or display access.  The offscreen platform plugin ships
with PyQt5; no pytest-qt dependency.  ``commit_stopwatch_session`` is
patched in every test so nothing hits SQLite through the service path
— the ``conn`` fixture exists only because ``StopwatchWidget.__init__``
needs it for the activity-dropdown refresh.
"""

from __future__ import annotations

import os

# Must be set before PyQt5 is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3  # noqa: E402
from collections.abc import Generator  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from kalsangati.exceptions import SessionTooShortError  # noqa: E402
from kalsangati.gui.stopwatch import StopwatchWidget  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Headless QApplication shared across the module's tests.

    ``QApplication.instance()`` returns the existing singleton if one
    has already been constructed (e.g. by another test module in the
    same run); otherwise we create one.
    """
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


@pytest.fixture
def widget(
    conn: sqlite3.Connection, qapp: QApplication
) -> Generator[StopwatchWidget, None, None]:
    """A StopwatchWidget backed by the shared ``conn`` fixture.

    Every test in the module patches ``commit_stopwatch_session``
    before calling ``_end_session``, so the widget's DB connection
    is never actually written to.
    """
    w = StopwatchWidget(conn)
    yield w
    w.close()


def _make_commit_return() -> MagicMock:
    """A plausible CommitResult-shaped mock return value."""
    return MagicMock(
        session_id=1, extended=False, unplanned=False, duration_sec=5.0
    )


# ── Happy path: service dispatch ────────────────────────────────────────


class TestEndSessionServiceCall:
    """``_end_session`` forwards the session to the service correctly."""

    def test_service_called_with_correct_args(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = "01-02-el"
        widget._session_start = datetime.now() - timedelta(seconds=5)

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session",
            return_value=_make_commit_return(),
        ) as mock_commit:
            widget._end_session()

        assert mock_commit.call_count == 1
        kwargs = mock_commit.call_args.kwargs
        assert kwargs["activity"] == "01-02-el"
        assert kwargs["task_id"] is None
        assert kwargs["override_reason"] is None
        # Sanity: end_time must be after start_time.
        assert kwargs["end_time"] > kwargs["start_time"]

    def test_no_op_when_session_start_is_none(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = "01-02-el"
        widget._session_start = None

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session"
        ) as mock_commit:
            widget._end_session()

        assert not mock_commit.called

    def test_no_op_when_current_activity_is_none(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = None
        widget._session_start = datetime.now()

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session"
        ) as mock_commit:
            widget._end_session()

        assert not mock_commit.called


# ── Task id narrowing (the incidental latent-bug fix) ───────────────────


class TestTaskIdNarrowing:
    """Verifies the ``currentData()``-based task id narrowing.

    Pre-Unit-5 the code read ``currentText()`` — which returned the
    display label, including the ``⚫ Title [activity]`` cross-activity
    decoration — and wrote that straight into ``kalrekha.task``.  The
    refactor switched to ``currentData()`` (the int task id).  These
    tests lock that behaviour in.
    """

    def test_no_task_placeholder_forwards_none(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = "01-02-el"
        widget._session_start = datetime.now() - timedelta(seconds=5)
        # Fresh combo with only the "(no task)" placeholder (no data
        # payload — addItem called without the second argument).
        widget._task_combo.clear()
        widget._task_combo.addItem("(no task)")
        widget._task_combo.setCurrentIndex(0)

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session",
            return_value=_make_commit_return(),
        ) as mock_commit:
            widget._end_session()

        assert mock_commit.call_args.kwargs["task_id"] is None

    def test_task_with_int_data_forwards_that_int(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = "01-02-el"
        widget._session_start = datetime.now() - timedelta(seconds=5)
        widget._task_combo.clear()
        widget._task_combo.addItem("My task", 42)
        widget._task_combo.setCurrentIndex(0)

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session",
            return_value=_make_commit_return(),
        ) as mock_commit:
            widget._end_session()

        assert mock_commit.call_args.kwargs["task_id"] == 42


# ── Exception dispatch ─────────────────────────────────────────────────


class TestExceptionHandling:
    """Domain errors → warning; unexpected errors → critical + log."""

    def test_kalsangati_error_shows_warning(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = "01-02-el"
        widget._session_start = datetime.now() - timedelta(milliseconds=500)

        with (
            patch(
                "kalsangati.gui.stopwatch.commit_stopwatch_session",
                side_effect=SessionTooShortError("session too short"),
            ),
            patch.object(QMessageBox, "warning") as mock_warning,
            patch.object(QMessageBox, "critical") as mock_critical,
        ):
            widget._end_session()

        assert mock_warning.called
        assert not mock_critical.called
        # Third positional arg to QMessageBox.warning is the body text;
        # str(exc) is forwarded as-is.
        assert "too short" in mock_warning.call_args.args[2]

    def test_unexpected_exception_shows_critical_and_logs(
        self,
        widget: StopwatchWidget,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        widget._current_activity = "01-02-el"
        widget._session_start = datetime.now() - timedelta(seconds=5)

        with (
            patch(
                "kalsangati.gui.stopwatch.commit_stopwatch_session",
                side_effect=RuntimeError("unexpected boom"),
            ),
            patch.object(QMessageBox, "warning") as mock_warning,
            patch.object(QMessageBox, "critical") as mock_critical,
            caplog.at_level("ERROR", logger="kalsangati.gui.stopwatch"),
        ):
            widget._end_session()

        assert mock_critical.called
        assert not mock_warning.called
        # ``logger.exception`` emits an ERROR-level record with the
        # original message captured via %r / %s substitution.
        assert any(
            "unexpected boom" in r.getMessage()
            or "Unexpected error" in r.getMessage()
            for r in caplog.records
        )

    def test_session_start_cleared_on_domain_failure(
        self, widget: StopwatchWidget
    ) -> None:
        """State hygiene — a failed commit doesn't leave us mid-session."""
        widget._current_activity = "01-02-el"
        widget._session_start = datetime.now() - timedelta(milliseconds=500)

        with (
            patch(
                "kalsangati.gui.stopwatch.commit_stopwatch_session",
                side_effect=SessionTooShortError("x"),
            ),
            patch.object(QMessageBox, "warning"),
        ):
            widget._end_session()

        assert widget._session_start is None

    def test_session_start_cleared_on_unexpected_failure(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = "01-02-el"
        widget._session_start = datetime.now() - timedelta(seconds=5)

        with (
            patch(
                "kalsangati.gui.stopwatch.commit_stopwatch_session",
                side_effect=RuntimeError("x"),
            ),
            patch.object(QMessageBox, "critical"),
        ):
            widget._end_session()

        assert widget._session_start is None


# ── Successful commit also clears state ─────────────────────────────────


def test_session_start_cleared_after_success(
    widget: StopwatchWidget,
) -> None:
    widget._current_activity = "01-02-el"
    widget._session_start = datetime.now() - timedelta(seconds=5)

    with patch(
        "kalsangati.gui.stopwatch.commit_stopwatch_session",
        return_value=_make_commit_return(),
    ):
        widget._end_session()

    assert widget._session_start is None
