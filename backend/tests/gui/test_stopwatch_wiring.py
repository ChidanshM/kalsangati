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

from kalsangati.core.exceptions import SessionTooShortError  # noqa: E402
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


# ── Refresh must not disturb a running session (P2U08) ───────────────


class TestRefreshDoesNotSegmentTheSession:
    """``_refresh_activities`` runs on a 30-second timer.

    It repopulates the activity combo, and Qt emits
    ``currentTextChanged`` on programmatic mutation exactly as it does
    on a user's click.  Before this fix the emptied combo reached
    ``_on_activity_changed("")``, which looked like a quick-switch:
    the running session was committed and a new one started, resetting
    the monotonic anchor.  A three-minute session was chopped into
    30-second fragments, each rejected as too short, and recorded
    nothing.

    Every test here drives ``_refresh_activities`` **while running**,
    which is the state the existing tests never put the widget in.
    """

    @staticmethod
    def _run(widget: StopwatchWidget, activity: str = "01-02-el") -> None:
        widget._activity_combo.blockSignals(True)
        widget._activity_combo.clear()
        widget._activity_combo.addItems([activity])
        widget._activity_combo.setCurrentIndex(0)
        widget._activity_combo.blockSignals(False)
        widget._current_activity = activity
        widget._is_running = True
        widget._start_session()

    def test_refresh_does_not_commit_anything(
        self, widget: StopwatchWidget
    ) -> None:
        self._run(widget)

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session"
        ) as mock_commit:
            widget._refresh_activities()

        assert not mock_commit.called

    def test_monotonic_anchor_survives_a_refresh(
        self, widget: StopwatchWidget
    ) -> None:
        """The anchor is what makes the recorded duration correct.

        Resetting it mid-session is why a three-minute session
        committed 0.000 seconds.
        """
        self._run(widget)
        anchor = widget._session_monotonic_start

        with patch("kalsangati.gui.stopwatch.commit_stopwatch_session"):
            widget._refresh_activities()

        assert widget._session_monotonic_start == anchor

    def test_session_start_survives_a_refresh(
        self, widget: StopwatchWidget
    ) -> None:
        self._run(widget)
        started = widget._session_start

        with patch("kalsangati.gui.stopwatch.commit_stopwatch_session"):
            widget._refresh_activities()

        assert widget._session_start == started

    def test_current_activity_survives_a_refresh(
        self, widget: StopwatchWidget
    ) -> None:
        self._run(widget)

        with patch("kalsangati.gui.stopwatch.commit_stopwatch_session"):
            widget._refresh_activities()

        assert widget._current_activity == "01-02-el"

    def test_repeated_refreshes_never_commit(
        self, widget: StopwatchWidget
    ) -> None:
        """Seven refreshes is roughly what a 3:30 session saw."""
        self._run(widget)
        anchor = widget._session_monotonic_start

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session"
        ) as mock_commit:
            for _ in range(7):
                widget._refresh_activities()

        assert not mock_commit.called
        assert widget._session_monotonic_start == anchor


class TestEmptyActivityIsNotASession:
    """An empty string is not an activity.

    It arrives from an emptied combo.  Committing against it was
    rejected by the service's one-second minimum — by luck, not by
    design.  A longer fragment would have written a row with no
    activity at all.
    """

    def test_empty_activity_change_is_ignored(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = "01-02-el"
        widget._is_running = True
        widget._start_session()

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session"
        ) as mock_commit:
            widget._on_activity_changed("")

        assert not mock_commit.called
        assert widget._current_activity == "01-02-el"

    def test_end_session_refuses_an_empty_activity(
        self, widget: StopwatchWidget
    ) -> None:
        widget._current_activity = ""
        widget._session_start = datetime.now() - timedelta(seconds=5)

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session"
        ) as mock_commit:
            widget._end_session()

        assert not mock_commit.called

    def test_a_real_quick_switch_still_works(
        self, widget: StopwatchWidget
    ) -> None:
        """The guard must not break the feature it sits next to."""
        widget._current_activity = "01-02-el"
        widget._is_running = True
        widget._start_session()

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session",
            return_value=_make_commit_return(),
        ) as mock_commit:
            widget._on_activity_changed("04-fitness")

        assert mock_commit.call_count == 1
        assert mock_commit.call_args.kwargs["activity"] == "01-02-el"
        assert widget._current_activity == "04-fitness"
        assert widget._session_start is not None  # new segment running


# ── Task selection fills the activity (P2U08) ──────────────────────


class TestTaskSelectionFillsActivity:
    """A task already knows what kind of time it is.

    Same overridable-default rule as project to task and parent to
    subtask: picking a task should not then require picking its
    activity as well.
    """

    @staticmethod
    def _stocked(
        widget: StopwatchWidget, activities: list[str]
    ) -> None:
        widget._activity_combo.blockSignals(True)
        widget._activity_combo.clear()
        widget._activity_combo.addItems(activities)
        widget._activity_combo.setCurrentIndex(0)
        widget._activity_combo.blockSignals(False)

    def test_selecting_a_task_sets_its_activity(
        self, widget: StopwatchWidget
    ) -> None:
        self._stocked(widget, ["01-02-el", "04-fitness"])
        widget._task_combo.blockSignals(True)
        widget._task_combo.clear()
        widget._task_combo.addItem("(no task)")
        widget._task_combo.addItem("Gym", 7)
        widget._task_activity[7] = "04-fitness"
        widget._task_combo.blockSignals(False)

        widget._task_combo.setCurrentIndex(1)

        assert widget._activity_combo.currentText() == "04-fitness"
        assert widget._current_activity == "04-fitness"

    def test_task_selection_survives_the_activity_change(
        self, widget: StopwatchWidget
    ) -> None:
        """Setting the activity must not repopulate the task combo.

        ``_on_activity_changed`` calls ``_refresh_tasks``, which clears
        it — discarding the selection that triggered all this.
        """
        self._stocked(widget, ["01-02-el", "04-fitness"])
        widget._task_combo.blockSignals(True)
        widget._task_combo.clear()
        widget._task_combo.addItem("(no task)")
        widget._task_combo.addItem("Gym", 7)
        widget._task_activity[7] = "04-fitness"
        widget._task_combo.blockSignals(False)

        widget._task_combo.setCurrentIndex(1)

        assert widget._task_combo.currentData() == 7

    def test_no_task_placeholder_changes_nothing(
        self, widget: StopwatchWidget
    ) -> None:
        self._stocked(widget, ["01-02-el", "04-fitness"])
        widget._task_combo.blockSignals(True)
        widget._task_combo.clear()
        widget._task_combo.addItem("(no task)")
        widget._task_combo.blockSignals(False)

        widget._task_combo.setCurrentIndex(0)

        assert widget._activity_combo.currentText() == "01-02-el"

    def test_activity_outside_the_niyam_is_left_alone(
        self, widget: StopwatchWidget
    ) -> None:
        """The dropdown shows what was prescribed; do not smuggle in
        an activity the Niyam does not contain."""
        self._stocked(widget, ["01-02-el"])
        widget._task_combo.blockSignals(True)
        widget._task_combo.clear()
        widget._task_combo.addItem("(no task)")
        widget._task_combo.addItem("Chore", 9)
        widget._task_activity[9] = "03-chores"
        widget._task_combo.blockSignals(False)

        widget._task_combo.setCurrentIndex(1)

        assert widget._activity_combo.currentText() == "01-02-el"

    def test_ignored_while_running(
        self, widget: StopwatchWidget
    ) -> None:
        """Mid-session an activity change means a quick-switch, which
        ends the running segment.  Doing that as a side effect of
        picking a task would be a surprising way to lose time."""
        self._stocked(widget, ["01-02-el", "04-fitness"])
        widget._task_combo.blockSignals(True)
        widget._task_combo.clear()
        widget._task_combo.addItem("(no task)")
        widget._task_combo.addItem("Gym", 7)
        widget._task_activity[7] = "04-fitness"
        widget._task_combo.blockSignals(False)
        widget._current_activity = "01-02-el"
        widget._is_running = True
        widget._start_session()

        with patch(
            "kalsangati.gui.stopwatch.commit_stopwatch_session"
        ) as mock_commit:
            widget._task_combo.setCurrentIndex(1)

        assert not mock_commit.called
        assert widget._activity_combo.currentText() == "01-02-el"


class TestWindowSizing:
    def test_has_a_bounded_default_height(
        self, widget: StopwatchWidget
    ) -> None:
        """Without an explicit height the window manager stretched this
        to full screen, leaving the timer in a void."""
        assert widget.height() <= 500
        assert widget.width() == 320
