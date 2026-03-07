"""Always-on-top stopwatch widget.

Features:
- Dropdown to select current activity
- Start/stop button logging sessions to kalrekha
- Quick-switch: change activity mid-session without stopping
- Block-aware task dropdown: auto-populates with tasks for the current
  Niyam block; other activities' tasks greyed out but visible
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kalsangati.db import transaction
from kalsangati.niyam import get_active
from kalsangati.tasks import check_block_alignment


class StopwatchWidget(QWidget):
    """Floating always-on-top stopwatch for manual time tracking.

    Args:
        conn: Database connection.
        parent: Optional parent widget.
    """

    def __init__(
        self, conn: sqlite3.Connection, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._elapsed_seconds = 0
        self._is_running = False
        self._session_start: Optional[datetime] = None
        self._current_activity: Optional[str] = None

        self.setWindowTitle("Kālsangati Stopwatch")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedWidth(320)
        self._build_ui()

        # Timer for display updates
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)

        # Refresh activities periodically
        self._activity_timer = QTimer(self)
        self._activity_timer.timeout.connect(self._refresh_activities)
        self._activity_timer.start(30_000)

        self._refresh_activities()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        # Time display
        self._time_label = QLabel("00:00:00")
        font = QFont("Monospace", 28, QFont.Weight.Bold)
        self._time_label.setFont(font)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._time_label)

        # Activity selector
        self._activity_combo = QComboBox()
        self._activity_combo.setPlaceholderText("Select activity…")
        self._activity_combo.currentTextChanged.connect(self._on_activity_changed)
        layout.addWidget(self._activity_combo)

        # Task selector (block-aware)
        self._task_combo = QComboBox()
        self._task_combo.setPlaceholderText("Select task…")
        layout.addWidget(self._task_combo)

        # Buttons
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start")
        self._start_btn.clicked.connect(self._toggle)
        btn_row.addWidget(self._start_btn)

        self._reset_btn = QPushButton("Reset")
        self._reset_btn.clicked.connect(self._reset)
        self._reset_btn.setEnabled(False)
        btn_row.addWidget(self._reset_btn)

        layout.addLayout(btn_row)

        # Status
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

    def _refresh_activities(self) -> None:
        """Populate activity dropdown from the active Niyam."""
        current = self._activity_combo.currentText()
        self._activity_combo.clear()

        niyam = get_active(self._conn)
        if niyam:
            activities = sorted(niyam.activity_set)
            self._activity_combo.addItems(activities)

        # Restore selection
        idx = self._activity_combo.findText(current)
        if idx >= 0:
            self._activity_combo.setCurrentIndex(idx)

    def _on_activity_changed(self, activity: str) -> None:
        """Update task dropdown when activity changes; handle quick-switch."""
        if self._is_running and self._current_activity != activity:
            # Quick-switch: end current segment, start new one
            self._end_session()
            self._current_activity = activity
            self._start_session()

        self._current_activity = activity
        self._refresh_tasks()

    def _refresh_tasks(self) -> None:
        """Populate task dropdown based on current activity."""
        self._task_combo.clear()
        self._task_combo.addItem("(no task)")

        activity = self._activity_combo.currentText()
        if not activity:
            return

        # Fetch tasks for this activity
        rows = self._conn.execute(
            "SELECT id, title, canonical_activity FROM tasks "
            "WHERE status IN ('this_week', 'in_progress') "
            "ORDER BY CASE WHEN canonical_activity = ? THEN 0 ELSE 1 END, "
            "         COALESCE(due_date, '9999-12-31'), title",
            (activity,),
        ).fetchall()

        for row in rows:
            label = row["title"]
            if row["canonical_activity"] != activity:
                label = f"⚫ {label} [{row['canonical_activity']}]"
            self._task_combo.addItem(label, row["id"])

    def _toggle(self) -> None:
        """Start or stop the timer."""
        if self._is_running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        activity = self._activity_combo.currentText()
        if not activity:
            self._status_label.setText("Select an activity first")
            return

        # Check block alignment
        alignment = check_block_alignment(self._conn, activity)
        if not alignment["aligned"]:
            self._status_label.setText(
                f"⚠ Out of block — next: {alignment['next_block_day']} "
                f"at {alignment['next_block_time']}"
            )
            # In full implementation, this triggers the override dialog.
            # For now, proceed with unplanned flag.

        self._current_activity = activity
        self._is_running = True
        self._start_session()
        self._tick_timer.start(1000)
        self._start_btn.setText("Stop")
        self._reset_btn.setEnabled(False)
        self._status_label.setText(f"Tracking: {activity}")

    def _stop(self) -> None:
        self._is_running = False
        self._tick_timer.stop()
        self._end_session()
        self._start_btn.setText("Start")
        self._reset_btn.setEnabled(True)
        self._status_label.setText("Stopped")

    def _reset(self) -> None:
        self._elapsed_seconds = 0
        self._time_label.setText("00:00:00")
        self._reset_btn.setEnabled(False)
        self._status_label.setText("")

    def _tick(self) -> None:
        self._elapsed_seconds += 1
        h = self._elapsed_seconds // 3600
        m = (self._elapsed_seconds % 3600) // 60
        s = self._elapsed_seconds % 60
        self._time_label.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _start_session(self) -> None:
        self._session_start = datetime.now()

    def _end_session(self) -> None:
        if self._session_start is None or self._current_activity is None:
            return

        end = datetime.now()
        duration_min = (end - self._session_start).total_seconds() / 60

        # Ignore very short sessions (< 10 seconds)
        if duration_min < 0.17:
            self._session_start = None
            return

        # Check if this was unplanned
        alignment = check_block_alignment(
            self._conn, self._current_activity, self._session_start
        )
        is_unplanned = not alignment["aligned"]

        # Get selected task
        task_text = self._task_combo.currentText()
        task = task_text if task_text != "(no task)" else None

        with transaction(self._conn) as cur:
            cur.execute(
                "INSERT INTO kalrekha "
                "(project, task, date, start, \"end\", duration_min, "
                " source, unplanned, block_classified) "
                "VALUES (?, ?, ?, ?, ?, ?, 'manual_stopwatch', ?, 1)",
                (
                    self._current_activity,
                    task,
                    self._session_start.strftime("%Y-%m-%d"),
                    self._session_start.strftime("%H:%M:%S"),
                    end.strftime("%H:%M:%S"),
                    round(duration_min, 2),
                    int(is_unplanned),
                ),
            )

        self._session_start = None
