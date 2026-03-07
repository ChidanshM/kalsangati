"""Main application window — tab-based navigation hub.

Hosts the analytics dashboard, Niyam editor, label manager, task planner,
Kālrekhā viewer, Niyam comparison, settings, and the always-on-top
stopwatch widget.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from kalsangati.db import get_setting, init_db
from kalsangati.gui.analytics_dashboard import AnalyticsDashboard
from kalsangati.gui.label_manager import LabelManager
from kalsangati.gui.niyam_compare import NiyamCompare
from kalsangati.gui.niyam_editor import NiyamEditor
from kalsangati.gui.settings import SettingsPanel
from kalsangati.gui.stopwatch import StopwatchWidget
from kalsangati.gui.task_planner import TaskPlanner
from kalsangati.ingest import ingest_csv, refresh_weekly_aggregates, classify_sessions
from kalsangati.notifications import NotificationScheduler

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """The primary Kālsangati application window.

    Args:
        db_path: Override path to the SQLite database.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        super().__init__()
        self.setWindowTitle("Kālsangati — Coherence with Time")
        self.setMinimumSize(1100, 720)

        # Database
        self._conn: sqlite3.Connection = init_db(db_path)

        # Notification scheduler
        self._notifier = NotificationScheduler(
            conn_factory=lambda: init_db(db_path)
        )

        # Build UI
        self._build_menu_bar()
        self._build_tabs()
        self._build_status_bar()

        # Stopwatch (floating widget)
        self._stopwatch = StopwatchWidget(self._conn, parent=None)

        # Auto-refresh timer
        interval_str = get_setting(self._conn, "refresh_interval_min") or "5"
        interval_ms = int(interval_str) * 60 * 1000
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_auto_refresh)
        self._refresh_timer.start(interval_ms)

        # Start notifications
        enabled = get_setting(self._conn, "notifications_enabled")
        if enabled and enabled.lower() == "true":
            self._notifier.start()

    # ── Menu bar ────────────────────────────────────────────────────────

    def _build_menu_bar(self) -> None:
        menu = self.menuBar()

        # File menu
        file_menu = menu.addMenu("&File")

        import_action = QAction("&Import CSV…", self)
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self._on_import_csv)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # View menu
        view_menu = menu.addMenu("&View")

        stopwatch_action = QAction("Show &Stopwatch", self)
        stopwatch_action.setShortcut("Ctrl+T")
        stopwatch_action.triggered.connect(self._toggle_stopwatch)
        view_menu.addAction(stopwatch_action)

        refresh_action = QAction("&Refresh", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self._on_auto_refresh)
        view_menu.addAction(refresh_action)

    # ── Tabs ────────────────────────────────────────────────────────────

    def _build_tabs(self) -> None:
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._dashboard = AnalyticsDashboard(self._conn)
        self._niyam_editor = NiyamEditor(self._conn)
        self._niyam_compare = NiyamCompare(self._conn)
        self._label_manager = LabelManager(self._conn)
        self._task_planner = TaskPlanner(self._conn)
        self._settings_panel = SettingsPanel(self._conn)

        self._tabs.addTab(self._dashboard, "Dashboard")
        self._tabs.addTab(self._niyam_editor, "Niyam Editor")
        self._tabs.addTab(self._niyam_compare, "Niyam Compare")
        self._tabs.addTab(self._label_manager, "Label Manager")
        self._tabs.addTab(self._task_planner, "Task Planner")
        self._tabs.addTab(self._settings_panel, "Settings")

    # ── Status bar ──────────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready")

    # ── Actions ─────────────────────────────────────────────────────────

    def _on_import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Time Tracker CSV", "", "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            result = ingest_csv(self._conn, Path(path))
            classify_sessions(self._conn)
            refresh_weekly_aggregates(self._conn)
            self._dashboard.refresh()

            msg = (
                f"Imported {result['imported']} sessions, "
                f"skipped {result['skipped']} duplicates."
            )
            unrec = result.get("unrecognized", [])
            if unrec:
                msg += f"\n\nUnrecognized labels ({len(unrec)}):\n"
                msg += "\n".join(f"  • {l}" for l in unrec[:20])
            QMessageBox.information(self, "Import Complete", msg)
            self._status.showMessage(
                f"Imported {result['imported']} sessions", 5000
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))
            logger.exception("CSV import failed")

    def _toggle_stopwatch(self) -> None:
        if self._stopwatch.isVisible():
            self._stopwatch.hide()
        else:
            self._stopwatch.show()
            self._stopwatch.raise_()

    def _on_auto_refresh(self) -> None:
        self._dashboard.refresh()
        self._status.showMessage("Refreshed", 3000)

    # ── Lifecycle ───────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._notifier.stop()
        self._stopwatch.close()
        self._refresh_timer.stop()
        self._conn.close()
        super().closeEvent(event)


# ── Entry point ─────────────────────────────────────────────────────────


def main() -> None:
    """Launch the Kālsangati application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Kālsangati")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
