"""Niyam Comparison — side-by-side Niyam A vs Niyam B.

Detailed comparison table with hours, slots, deltas, sort/filter
controls, and optional actuals panel.
"""

from __future__ import annotations

import sqlite3

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kalsangati.niyam import Niyam, activity_summary, get_all, get_by_id


class NiyamCompare(QWidget):
    """Side-by-side Niyam comparison panel.

    Args:
        conn: Database connection.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self._conn = conn
        self._niyam_a: Niyam | None = None
        self._niyam_b: Niyam | None = None
        self._build_ui()
        self._refresh_dropdowns()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Selectors
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Niyam A:"))
        self._combo_a = QComboBox()
        self._combo_a.setMinimumWidth(180)
        self._combo_a.currentIndexChanged.connect(self._on_compare)
        selector_row.addWidget(self._combo_a)

        selector_row.addWidget(QLabel("Niyam B:"))
        self._combo_b = QComboBox()
        self._combo_b.setMinimumWidth(180)
        self._combo_b.currentIndexChanged.connect(self._on_compare)
        selector_row.addWidget(self._combo_b)
        selector_row.addStretch()
        layout.addLayout(selector_row)

        # Summary strip
        self._summary_label = QLabel("")
        layout.addWidget(self._summary_label)

        # Filter row
        filter_row = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems([
            "Show all", "Only differences", "Only in A", "Only in B"
        ])
        self._filter_combo.currentIndexChanged.connect(self._on_compare)
        filter_row.addWidget(QLabel("Filter:"))
        filter_row.addWidget(self._filter_combo)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search activity…")
        self._search_input.textChanged.connect(self._on_compare)
        filter_row.addWidget(self._search_input)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Comparison table
        self._table = QTableWidget()
        headers = [
            "Activity", "A: Hours", "B: Hours", "Δ Hours",
            "A: Slots", "B: Slots", "Δ Slots",
        ]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.setSortingEnabled(True)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self._table)

    def _refresh_dropdowns(self) -> None:
        for combo in (self._combo_a, self._combo_b):
            combo.blockSignals(True)
            combo.clear()
            for n in get_all(self._conn):
                suffix = " ★" if n.is_active else ""
                combo.addItem(f"{n.name}{suffix}", n.id)
            combo.blockSignals(False)
        if self._combo_a.count() > 0:
            self._combo_a.setCurrentIndex(0)
        if self._combo_b.count() > 1:
            self._combo_b.setCurrentIndex(1)
        self._on_compare()

    def _on_compare(self, _: int = 0) -> None:
        id_a = self._combo_a.currentData()
        id_b = self._combo_b.currentData()
        if id_a is None or id_b is None:
            return

        self._niyam_a = get_by_id(self._conn, id_a)
        self._niyam_b = get_by_id(self._conn, id_b)
        if not self._niyam_a or not self._niyam_b:
            return

        sum_a = activity_summary(self._niyam_a)
        sum_b = activity_summary(self._niyam_b)

        # Summary strip
        self._summary_label.setText(
            f"Niyam A: {self._niyam_a.total_hours:.0f}h · "
            f"{len(self._niyam_a.activity_set)} activities · "
            f"{self._niyam_a.slot_count} slots   |   "
            f"Niyam B: {self._niyam_b.total_hours:.0f}h · "
            f"{len(self._niyam_b.activity_set)} activities · "
            f"{self._niyam_b.slot_count} slots   |   "
            f"Δ: {self._niyam_b.total_hours - self._niyam_a.total_hours:+.0f}h · "
            f"{len(self._niyam_b.activity_set) - len(self._niyam_a.activity_set):+d} "
            f"activities · "
            f"{self._niyam_b.slot_count - self._niyam_a.slot_count:+d} slots"
        )

        all_activities = sorted(set(sum_a.keys()) | set(sum_b.keys()))

        # Apply filters
        filter_mode = self._filter_combo.currentText()
        search = self._search_input.text().strip().lower()

        rows = []
        for act in all_activities:
            a = sum_a.get(act, {"hours": 0.0, "slots": 0})
            b = sum_b.get(act, {"hours": 0.0, "slots": 0})

            if filter_mode == "Only differences":
                if a["hours"] == b["hours"] and a["slots"] == b["slots"]:
                    continue
            elif filter_mode == "Only in A":
                if act not in sum_a:
                    continue
            elif filter_mode == "Only in B" and act not in sum_b:
                continue

            if search and search not in act.lower():
                continue

            rows.append((act, a, b))

        # Populate table
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, (act, a, b) in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(act))

            a_hours = float(a["hours"])
            b_hours = float(b["hours"])
            d_hours = b_hours - a_hours

            self._table.setItem(i, 1, self._num_item(a_hours))
            self._table.setItem(i, 2, self._num_item(b_hours))
            self._table.setItem(i, 3, self._delta_item(d_hours))

            a_slots = int(a["slots"])
            b_slots = int(b["slots"])
            d_slots = b_slots - a_slots

            self._table.setItem(i, 4, self._num_item(a_slots))
            self._table.setItem(i, 5, self._num_item(b_slots))
            self._table.setItem(i, 6, self._delta_item(d_slots))

        self._table.setSortingEnabled(True)

    @staticmethod
    def _num_item(val: float | int) -> QTableWidgetItem:
        text = f"{val:.1f}" if isinstance(val, float) else str(val)
        item = QTableWidgetItem(text)
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        return item

    @staticmethod
    def _delta_item(val: float | int) -> QTableWidgetItem:
        text = f"{val:+.1f}" if isinstance(val, float) else f"{val:+d}"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if val > 0:
            item.setForeground(QColor("#22C55E"))
        elif val < 0:
            item.setForeground(QColor("#EF4444"))
        return item

    def refresh(self) -> None:
        """Reload dropdown data and rerun comparison."""
        self._refresh_dropdowns()
