"""Live Analytics Dashboard.

Today view, week-in-progress (Kālachakra), pacing alerts, streak
indicators, and summary health score.
"""

from __future__ import annotations

import sqlite3

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QGroupBox,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kalsangati.analytics import (
    ActivityMetric,
    today_summary,
    week_summary,
)
from kalsangati.vimarsha import build_vimarsha


class AnalyticsDashboard(QWidget):
    """Main analytics dashboard panel.

    Args:
        conn: Database connection.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self._conn = conn
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        # Health score banner
        self._health_label = QLabel("—")
        self._health_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._health_label.setStyleSheet(
            "font-size: 24px; font-weight: bold; padding: 12px;"
        )
        layout.addWidget(self._health_label)

        # Today section
        today_group = QGroupBox("Today")
        today_layout = QVBoxLayout(today_group)
        self._today_table = self._make_metrics_table()
        today_layout.addWidget(self._today_table)
        layout.addWidget(today_group)

        # Week section
        week_group = QGroupBox("Kālachakra — Week in Progress")
        week_layout = QVBoxLayout(week_group)
        self._week_table = self._make_metrics_table()
        week_layout.addWidget(self._week_table)
        layout.addWidget(week_group)

        # Pacing alerts
        alerts_group = QGroupBox("Pacing Alerts")
        alerts_layout = QVBoxLayout(alerts_group)
        self._alerts_label = QLabel("No alerts")
        self._alerts_label.setWordWrap(True)
        alerts_layout.addWidget(self._alerts_label)
        layout.addWidget(alerts_group)

        # Reflection flags
        flags_group = QGroupBox("Vimarśa — Reflection Flags")
        flags_layout = QVBoxLayout(flags_group)
        self._flags_label = QLabel("No flags")
        self._flags_label.setWordWrap(True)
        flags_layout.addWidget(self._flags_label)
        layout.addWidget(flags_group)

        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

    @staticmethod
    def _make_metrics_table() -> QTableWidget:
        table = QTableWidget()
        headers = [
            "Activity", "Prescribed", "Planned", "Unplanned",
            "Total", "Delta", "Completion %",
        ]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        return table

    def refresh(self) -> None:
        """Reload all dashboard data."""
        # Today
        ts = today_summary(self._conn)
        self._populate_table(self._today_table, ts.metrics)

        # Week
        ws = week_summary(self._conn)
        self._populate_table(self._week_table, ws.metrics)

        # Health score
        score = ws.health_score
        color = "#22C55E" if score >= 75 else "#FBBF24" if score >= 50 else "#EF4444"
        self._health_label.setText(f"Weekly Health Score: {score:.0f}")
        self._health_label.setStyleSheet(
            f"font-size: 24px; font-weight: bold; padding: 12px; color: {color};"
        )

        # Pacing alerts
        if ws.pacing_alerts:
            lines = [f"⚠ {a.message}" for a in ws.pacing_alerts]
            self._alerts_label.setText("\n".join(lines))
        else:
            self._alerts_label.setText("✓ On pace for all activities")

        # Reflection flags
        try:
            vs = build_vimarsha(
                self._conn, ws.week_start,
                ws.week_start,  # single week for simplicity
            )
            if vs.flags:
                lines = []
                for f in vs.flags:
                    lines.append(f"• [{f.flag_type}] {f.activity}: {f.signal}")
                    lines.append(f"  → {f.suggestion}")
                self._flags_label.setText("\n".join(lines))
            else:
                self._flags_label.setText("✓ No reflection flags this week")
        except Exception:
            self._flags_label.setText("Unable to compute reflection flags")

    def _populate_table(
        self, table: QTableWidget, metrics: list[ActivityMetric]
    ) -> None:
        table.setRowCount(len(metrics))
        for i, m in enumerate(metrics):
            table.setItem(i, 0, QTableWidgetItem(m.activity))
            table.setItem(i, 1, self._ritem(f"{m.prescribed_hours:.1f}"))
            table.setItem(i, 2, self._ritem(f"{m.planned_hours:.1f}"))
            table.setItem(i, 3, self._ritem(f"{m.unplanned_hours:.1f}"))
            table.setItem(i, 4, self._ritem(f"{m.actual_hours:.1f}"))

            delta_item = QTableWidgetItem(f"{m.delta:+.1f}")
            delta_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            if m.delta > 0:
                delta_item.setForeground(QColor("#EF4444"))  # under
            elif m.delta < 0:
                delta_item.setForeground(QColor("#FBBF24"))  # over
            else:
                delta_item.setForeground(QColor("#22C55E"))
            table.setItem(i, 5, delta_item)

            pct = min(m.completion_pct, 999)
            table.setItem(i, 6, self._ritem(f"{pct:.0f}%"))

    @staticmethod
    def _ritem(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        return item
