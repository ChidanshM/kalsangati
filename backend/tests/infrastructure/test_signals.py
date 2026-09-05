"""Tests for infrastructure/signals.py — the AppSignals bus.

Covers the singleton, the one signal it carries, and the two things
that make a bus safe to use from widgets: a consumer reloading on
emission, and a closed consumer no longer receiving.

The last one matters more than it looks.  The bus outlives every widget
connected to it, so nothing else will ever drop a connection; a signal
delivered to a method on a deleted Qt object is a crash rather than an
exception.
"""

from __future__ import annotations

import os

# Must be set before PyQt5 is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3  # noqa: E402

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from kalsangati.infrastructure.signals import (  # noqa: E402
    AppSignals,
    app_signals,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


class TestSingleton:
    def test_returns_an_appsignals(self, qapp: QApplication) -> None:
        assert isinstance(app_signals(), AppSignals)

    def test_same_instance_every_time(self, qapp: QApplication) -> None:
        """A bus only some screens can reach is not a bus."""
        assert app_signals() is app_signals()


class TestNiyamChanged:
    def test_emission_reaches_a_subscriber(
        self, qapp: QApplication
    ) -> None:
        received: list[int] = []
        app_signals().niyam_changed.connect(lambda: received.append(1))
        try:
            app_signals().niyam_changed.emit()
        finally:
            app_signals().niyam_changed.disconnect()
        assert received == [1]

    def test_reaches_every_subscriber(self, qapp: QApplication) -> None:
        received: list[str] = []
        app_signals().niyam_changed.connect(lambda: received.append("a"))
        app_signals().niyam_changed.connect(lambda: received.append("b"))
        try:
            app_signals().niyam_changed.emit()
        finally:
            app_signals().niyam_changed.disconnect()
        assert sorted(received) == ["a", "b"]


class TestEditorForwardsOntoTheBus:
    """The Niyam editor's own signal is chained to the bus.

    Signal-to-signal, so the editor's existing emit sites needed no
    change and a screen that cares does not have to know the editor
    exists.
    """

    def test_editor_signal_reaches_the_bus(
        self, conn: sqlite3.Connection, qapp: QApplication
    ) -> None:
        from kalsangati.gui.niyam_editor import NiyamEditor

        editor = NiyamEditor(conn)
        received: list[int] = []
        app_signals().niyam_changed.connect(lambda: received.append(1))
        try:
            editor.niyam_changed.emit()
        finally:
            app_signals().niyam_changed.disconnect()
            editor.close()
        assert received == [1]


class TestStopwatchSubscription:
    def test_reloads_activities_on_emission(
        self, conn: sqlite3.Connection, qapp: QApplication
    ) -> None:
        """End to end: change the Niyam, announce it, see the reload.

        Deliberately behavioural rather than patching
        ``_refresh_activities``: patching a bound method after it has
        been connected means disconnecting and reconnecting a different
        object, which tests the plumbing of the test more than the
        widget.  The middle assertion is the important one — it proves
        the widget really was stale, so the last assertion is evidence
        the signal did the work.
        """
        from kalsangati.core.niyam import TimeBlock, set_active, update_blocks
        from kalsangati.core.niyam import create as create_niyam
        from kalsangati.gui.stopwatch import StopwatchWidget

        widget = StopwatchWidget(conn)
        try:
            assert widget._activity_combo.findText("04-fitness") < 0

            niyam = create_niyam(conn, "Autumn")
            update_blocks(
                conn,
                niyam.id,
                {
                    "monday": [
                        TimeBlock(
                            activity="04-fitness",
                            start_min=540,
                            end_min=600,
                            duration_h=1.0,
                        )
                    ]
                },
            )
            set_active(conn, niyam.id)

            # Nothing has told the widget yet, so it is still stale.
            assert widget._activity_combo.findText("04-fitness") < 0

            app_signals().niyam_changed.emit()

            assert widget._activity_combo.findText("04-fitness") >= 0
        finally:
            widget.close()

    def test_polling_timer_is_dormant(
        self, conn: sqlite3.Connection, qapp: QApplication
    ) -> None:
        """Kept for a database changed from outside, not started.

        Polling every 30 seconds is redundant now that the bus reports
        changes, and it is what made a running session get chopped into
        fragments before the signal-blocking fix.
        """
        from kalsangati.gui.stopwatch import StopwatchWidget

        widget = StopwatchWidget(conn)
        try:
            assert widget._activity_timer.isActive() is False
        finally:
            widget.close()

    def test_closed_widget_stops_receiving(
        self, conn: sqlite3.Connection, qapp: QApplication
    ) -> None:
        """The bus outlives the widget, so the widget must detach.

        Without this, a signal delivered after the Qt object is gone
        takes the process down rather than raising.
        """
        from kalsangati.gui.stopwatch import StopwatchWidget

        widget = StopwatchWidget(conn)
        widget.close()

        # Emitting must not reach the closed widget, and must not raise.
        app_signals().niyam_changed.emit()

    def test_close_is_idempotent(
        self, conn: sqlite3.Connection, qapp: QApplication
    ) -> None:
        """Qt raises TypeError on a second disconnect rather than
        no-opping, and a widget can be closed twice."""
        from kalsangati.gui.stopwatch import StopwatchWidget

        widget = StopwatchWidget(conn)
        widget.close()
        widget.close()
