"""Weekly Task Planner — capacity-aware scheduling GUI.

Left: backlog grouped by project.
Right: week view per activity with capacity bars, Niyam block anchors,
tasks under their natural blocks, floating tasks at bottom.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kalsangati.projects import get_all as get_all_projects
from kalsangati.tasks import (
    CapacityInfo,
    Task,
    all_capacities,
    create as create_task,
    delete as delete_task,
    get_all as get_all_tasks,
    set_status,
    update as update_task,
)


class TaskPlanner(QWidget):
    """Weekly task planner with backlog and capacity-aware scheduling.

    Args:
        conn: Database connection.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self._conn = conn
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: Backlog ───────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Backlog"))

        self._backlog_list = QListWidget()
        self._backlog_list.setDragEnabled(True)
        left_layout.addWidget(self._backlog_list)

        # Task action buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("New Task")
        btn_add.clicked.connect(self._on_add_task)
        btn_row.addWidget(btn_add)

        btn_assign = QPushButton("Assign to Week")
        btn_assign.clicked.connect(self._on_assign_to_week)
        btn_row.addWidget(btn_assign)

        btn_done = QPushButton("Mark Done")
        btn_done.clicked.connect(self._on_mark_done)
        btn_row.addWidget(btn_done)

        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._on_delete)
        btn_row.addWidget(btn_del)

        left_layout.addLayout(btn_row)
        splitter.addWidget(left)

        # ── Right: Week view ────────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("This Week — Capacity & Tasks"))

        # Capacity bars
        self._capacity_area = QScrollArea()
        self._capacity_area.setWidgetResizable(True)
        self._capacity_widget = QWidget()
        self._capacity_layout = QVBoxLayout(self._capacity_widget)
        self._capacity_area.setWidget(self._capacity_widget)
        right_layout.addWidget(self._capacity_area)

        # Week tasks table
        self._week_table = QTableWidget()
        self._week_table.setColumnCount(5)
        self._week_table.setHorizontalHeaderLabels([
            "Task", "Activity", "Est. Hours", "Due Date", "Status",
        ])
        self._week_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._week_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        right_layout.addWidget(self._week_table)

        splitter.addWidget(right)
        splitter.setSizes([350, 650])
        layout.addWidget(splitter)

    def refresh(self) -> None:
        """Reload all data."""
        self._refresh_backlog()
        self._refresh_week()

    def _refresh_backlog(self) -> None:
        self._backlog_list.clear()
        tasks = get_all_tasks(self._conn, status="backlog")
        for t in tasks:
            label = f"{t.title}"
            if t.estimated_hours:
                label += f"  ({t.estimated_hours:.1f}h)"
            label += f"  [{t.canonical_activity}]"
            if t.spilled_from:
                label += " ⟲"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, t.id)
            self._backlog_list.addItem(item)

    def _refresh_week(self) -> None:
        # Capacity bars
        # Clear old widgets
        while self._capacity_layout.count():
            child = self._capacity_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        capacities = all_capacities(self._conn)
        for cap in capacities:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{cap.activity}"))

            bar = QProgressBar()
            bar.setMaximum(max(int(cap.niyam_hours * 10), 1))
            bar.setValue(int(cap.logged_hours * 10))
            bar.setFormat(
                f"{cap.logged_hours:.1f} / {cap.niyam_hours:.1f}h "
                f"(slack: {cap.slack:.1f}h)"
            )
            if cap.is_overbooked:
                bar.setStyleSheet("QProgressBar::chunk { background: #FBBF24; }")
            else:
                bar.setStyleSheet("QProgressBar::chunk { background: #22C55E; }")
            row.addWidget(bar)

            container = QWidget()
            container.setLayout(row)
            self._capacity_layout.addWidget(container)

        self._capacity_layout.addStretch()

        # Week tasks table
        week_tasks = get_all_tasks(self._conn, status="this_week")
        week_tasks += get_all_tasks(self._conn, status="in_progress")
        self._week_table.setRowCount(len(week_tasks))

        for i, t in enumerate(week_tasks):
            self._week_table.setItem(i, 0, QTableWidgetItem(t.title))
            self._week_table.setItem(i, 1, QTableWidgetItem(t.canonical_activity))
            est = f"{t.estimated_hours:.1f}" if t.estimated_hours else "—"
            self._week_table.setItem(i, 2, QTableWidgetItem(est))
            self._week_table.setItem(
                i, 3, QTableWidgetItem(t.due_date or "—")
            )
            self._week_table.setItem(i, 4, QTableWidgetItem(t.status))

    def _on_add_task(self) -> None:
        dlg = _NewTaskDialog(self._conn, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            create_task(
                self._conn,
                title=data["title"],
                canonical_activity=data["activity"],
                project_id=data.get("project_id"),
                estimated_hours=data.get("estimated_hours"),
                due_date=data.get("due_date"),
            )
            self.refresh()

    def _on_assign_to_week(self) -> None:
        item = self._backlog_list.currentItem()
        if item is None:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        from kalsangati.analytics import _current_week_start
        from kalsangati.db import get_setting

        start_day = get_setting(self._conn, "week_start_day") or "monday"
        ws = _current_week_start(start_day)
        update_task(self._conn, tid, status="this_week", week_assigned=ws)
        self.refresh()

    def _on_mark_done(self) -> None:
        item = self._backlog_list.currentItem()
        if item is None:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        set_status(self._conn, tid, "done")
        self.refresh()

    def _on_delete(self) -> None:
        item = self._backlog_list.currentItem()
        if item is None:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        delete_task(self._conn, tid)
        self.refresh()


class _NewTaskDialog(QDialog):
    """Dialog for creating a new task."""

    def __init__(
        self, conn: sqlite3.Connection, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Task")
        self._conn = conn
        layout = QFormLayout(self)

        self._title = QLineEdit()
        layout.addRow("Title:", self._title)

        self._activity = QLineEdit()
        self._activity.setPlaceholderText("canonical activity name")
        layout.addRow("Activity:", self._activity)

        self._est_hours = QLineEdit()
        self._est_hours.setPlaceholderText("e.g. 2.0")
        layout.addRow("Estimated hours:", self._est_hours)

        self._due_date = QLineEdit()
        self._due_date.setPlaceholderText("YYYY-MM-DD")
        layout.addRow("Due date:", self._due_date)

        self._project_combo = QComboBox()
        self._project_combo.addItem("(none)", None)
        for p in get_all_projects(conn):
            self._project_combo.addItem(p.name, p.id)
        layout.addRow("Project:", self._project_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> dict:
        est = self._est_hours.text().strip()
        return {
            "title": self._title.text().strip(),
            "activity": self._activity.text().strip(),
            "estimated_hours": float(est) if est else None,
            "due_date": self._due_date.text().strip() or None,
            "project_id": self._project_combo.currentData(),
        }
