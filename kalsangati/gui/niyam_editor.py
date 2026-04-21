"""Niyam Editor — visual weekly time-block creator.

Provides a grid-based view where users can click/drag to assign activities
to day/hour slots.  Supports creating, editing, and cloning Niyam.
"""

from __future__ import annotations

import sqlite3

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kalsangati.niyam import (
    DAYS,
    Niyam,
    TimeBlock,
    clone,
    create,
    delete,
    get_all,
    get_by_id,
    rename,
    set_active,
    time_str_to_minutes,
    update_blocks,
)

# ── Constants ───────────────────────────────────────────────────────────

HOURS = [f"{h:02d}:00" for h in range(24)]
HALF_HOURS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]

_ACTIVITY_COLORS = [
    "#4A90D9", "#50C878", "#FF6B6B", "#FFD93D", "#A78BFA",
    "#F472B6", "#34D399", "#FB923C", "#60A5FA", "#C084FC",
    "#F87171", "#38BDF8", "#FBBF24", "#A3E635", "#E879F9",
]


class NiyamEditor(QWidget):
    """Visual Niyam editor with grid-based time-block assignment.

    Args:
        conn: Database connection.
    """

    niyam_changed = pyqtSignal()

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self._conn = conn
        self._current_niyam: Niyam | None = None
        self._activity_color_map: dict[str, str] = {}
        self._build_ui()
        self._refresh_niyam_list()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Top bar: niyam selector + action buttons
        top_bar = QHBoxLayout()

        self._niyam_combo = QComboBox()
        self._niyam_combo.setMinimumWidth(200)
        self._niyam_combo.currentIndexChanged.connect(self._on_niyam_selected)
        top_bar.addWidget(QLabel("Niyam:"))
        top_bar.addWidget(self._niyam_combo)

        self._active_label = QLabel("")
        top_bar.addWidget(self._active_label)
        top_bar.addStretch()

        btn_new = QPushButton("New")
        btn_new.clicked.connect(self._on_new)
        top_bar.addWidget(btn_new)

        btn_clone = QPushButton("Clone")
        btn_clone.clicked.connect(self._on_clone)
        top_bar.addWidget(btn_clone)

        btn_activate = QPushButton("Set Active")
        btn_activate.clicked.connect(self._on_activate)
        top_bar.addWidget(btn_activate)

        btn_rename = QPushButton("Rename")
        btn_rename.clicked.connect(self._on_rename)
        top_bar.addWidget(btn_rename)

        btn_delete = QPushButton("Delete")
        btn_delete.clicked.connect(self._on_delete)
        top_bar.addWidget(btn_delete)

        layout.addLayout(top_bar)

        # Grid: rows = half-hour slots, columns = days
        self._grid = QTableWidget(len(HALF_HOURS), len(DAYS))
        self._grid.setHorizontalHeaderLabels(
            [d.capitalize() for d in DAYS]
        )
        self._grid.setVerticalHeaderLabels(HALF_HOURS)
        self._grid.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._grid.verticalHeader().setDefaultSectionSize(22)
        self._grid.setSelectionMode(
            QTableWidget.SelectionMode.ContiguousSelection
        )
        self._grid.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self._grid)

        # Bottom bar: assign activity to selection
        bottom_bar = QHBoxLayout()
        self._activity_input = QLineEdit()
        self._activity_input.setPlaceholderText("Activity name…")
        bottom_bar.addWidget(QLabel("Activity:"))
        bottom_bar.addWidget(self._activity_input)

        btn_assign = QPushButton("Assign to Selection")
        btn_assign.clicked.connect(self._on_assign)
        bottom_bar.addWidget(btn_assign)

        btn_clear = QPushButton("Clear Selection")
        btn_clear.clicked.connect(self._on_clear_selection)
        bottom_bar.addWidget(btn_clear)

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self._on_save)
        bottom_bar.addWidget(btn_save)

        layout.addLayout(bottom_bar)

        # Summary
        self._summary_label = QLabel("")
        layout.addWidget(self._summary_label)

    def _refresh_niyam_list(self) -> None:
        self._niyam_combo.blockSignals(True)
        self._niyam_combo.clear()
        niyams = get_all(self._conn)
        for n in niyams:
            suffix = " ★" if n.is_active else ""
            self._niyam_combo.addItem(f"{n.name}{suffix}", n.id)
        self._niyam_combo.blockSignals(False)
        if niyams:
            self._niyam_combo.setCurrentIndex(0)
            self._on_niyam_selected(0)

    def _on_niyam_selected(self, index: int) -> None:
        niyam_id = self._niyam_combo.currentData()
        if niyam_id is None:
            return
        self._current_niyam = get_by_id(self._conn, niyam_id)
        self._populate_grid()
        self._update_summary()

    def _populate_grid(self) -> None:
        self._grid.clearContents()
        if self._current_niyam is None:
            return

        self._activity_color_map.clear()
        color_idx = 0

        for col, day in enumerate(DAYS):
            for block in self._current_niyam.blocks_for_day(day):
                # Find row range for this block
                start_row = (
                    HALF_HOURS.index(block.start)
                    if block.start in HALF_HOURS else None
                )
                end_slot = block.end
                end_row = HALF_HOURS.index(end_slot) if end_slot in HALF_HOURS else None

                if start_row is None:
                    continue
                if end_row is None:
                    end_row = min(
                        start_row + int(block.duration_h * 2), len(HALF_HOURS)
                    )

                # Assign color
                if block.activity not in self._activity_color_map:
                    self._activity_color_map[block.activity] = (
                        _ACTIVITY_COLORS[color_idx % len(_ACTIVITY_COLORS)]
                    )
                    color_idx += 1

                color = QColor(self._activity_color_map[block.activity])
                for row in range(start_row, end_row):
                    item = QTableWidgetItem(block.activity)
                    item.setBackground(color)
                    self._grid.setItem(row, col, item)

        active_label = "★ Active" if self._current_niyam.is_active else ""
        self._active_label.setText(active_label)

    def _update_summary(self) -> None:
        if self._current_niyam is None:
            self._summary_label.setText("")
            return
        n = self._current_niyam
        self._summary_label.setText(
            f"{n.name}: {n.total_hours:.1f}h · "
            f"{len(n.activity_set)} activities · "
            f"{n.slot_count} blocks"
        )

    def _on_assign(self) -> None:
        activity = self._activity_input.text().strip()
        if not activity:
            return
        selected = self._grid.selectedRanges()
        for rng in selected:
            for row in range(rng.topRow(), rng.bottomRow() + 1):
                for col in range(rng.leftColumn(), rng.rightColumn() + 1):
                    item = QTableWidgetItem(activity)
                    if activity not in self._activity_color_map:
                        idx = len(self._activity_color_map)
                        self._activity_color_map[activity] = (
                            _ACTIVITY_COLORS[idx % len(_ACTIVITY_COLORS)]
                        )
                    item.setBackground(
                        QColor(self._activity_color_map[activity])
                    )
                    self._grid.setItem(row, col, item)

    def _on_clear_selection(self) -> None:
        for rng in self._grid.selectedRanges():
            for row in range(rng.topRow(), rng.bottomRow() + 1):
                for col in range(rng.leftColumn(), rng.rightColumn() + 1):
                    self._grid.setItem(row, col, QTableWidgetItem(""))

    def _on_save(self) -> None:
        if self._current_niyam is None:
            return
        blocks = self._grid_to_blocks()
        update_blocks(self._conn, self._current_niyam.id, blocks)
        self._current_niyam = get_by_id(self._conn, self._current_niyam.id)
        self._update_summary()
        self.niyam_changed.emit()
        QMessageBox.information(self, "Saved", "Niyam updated successfully.")

    def _grid_to_blocks(self) -> dict[str, list[TimeBlock]]:
        """Convert grid contents to TimeBlock dict."""
        blocks: dict[str, list[TimeBlock]] = {d: [] for d in DAYS}
        for col, day in enumerate(DAYS):
            current_activity: str | None = None
            start_row: int | None = None

            for row in range(len(HALF_HOURS)):
                item = self._grid.item(row, col)
                text = item.text().strip() if item else ""

                if text and text == current_activity:
                    continue  # extend current block
                else:
                    # Close previous block
                    if current_activity and start_row is not None:
                        end_row = row
                        end_slot = (
                            HALF_HOURS[end_row]
                            if end_row < len(HALF_HOURS)
                            else "24:00"
                        )
                        blocks[day].append(
                            TimeBlock(
                                activity=current_activity,
                                start_min=time_str_to_minutes(HALF_HOURS[start_row]),
                                end_min=time_str_to_minutes(end_slot),
                                duration_h=(end_row - start_row) * 0.5,
                            )
                        )
                    current_activity = text if text else None
                    start_row = row if text else None

            # Close last block
            if current_activity and start_row is not None:
                end_row = len(HALF_HOURS)
                blocks[day].append(
                    TimeBlock(
                        activity=current_activity,
                        start_min=time_str_to_minutes(HALF_HOURS[start_row]),
                        end_min=time_str_to_minutes("24:00"),
                        duration_h=(end_row - start_row) * 0.5,
                    )
                )
        return blocks

    def _on_new(self) -> None:
        name, ok = QInputDialog.getText(self, "New Niyam", "Name:")
        if ok and name.strip():
            create(self._conn, name.strip())
            self._refresh_niyam_list()

    def _on_clone(self) -> None:
        if self._current_niyam is None:
            return
        name, ok = QInputDialog.getText(
            self, "Clone Niyam", "New name:", text=f"{self._current_niyam.name} (copy)"
        )
        if ok and name.strip():
            clone(self._conn, self._current_niyam.id, name.strip())
            self._refresh_niyam_list()

    def _on_activate(self) -> None:
        if self._current_niyam is None:
            return
        set_active(self._conn, self._current_niyam.id)
        self._refresh_niyam_list()
        self.niyam_changed.emit()

    def _on_rename(self) -> None:
        if self._current_niyam is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename Niyam", "New name:", text=self._current_niyam.name
        )
        if ok and name.strip():
            rename(self._conn, self._current_niyam.id, name.strip())
            self._refresh_niyam_list()

    def _on_delete(self) -> None:
        if self._current_niyam is None:
            return
        reply = QMessageBox.question(
            self, "Delete Niyam",
            f"Delete '{self._current_niyam.name}'?  This cannot be undone.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            delete(self._conn, self._current_niyam.id)
            self._current_niyam = None
            self._refresh_niyam_list()
