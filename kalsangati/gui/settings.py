"""Settings panel — user-configurable app preferences.

Manages notification lead time, toggle, watched folder, refresh interval,
and week start day.
"""

from __future__ import annotations

import sqlite3

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from kalsangati.db import get_setting, set_setting


class SettingsPanel(QWidget):
    """Application settings editor.

    Args:
        conn: Database connection.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self._conn = conn
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Notifications group
        notif_group = QGroupBox("Notifications")
        notif_layout = QFormLayout(notif_group)

        self._notif_enabled = QCheckBox("Enable desktop notifications")
        notif_layout.addRow(self._notif_enabled)

        self._lead_minutes = QSpinBox()
        self._lead_minutes.setRange(1, 60)
        self._lead_minutes.setSuffix(" min")
        notif_layout.addRow("Lead time before block:", self._lead_minutes)

        layout.addWidget(notif_group)

        # Ingest group
        ingest_group = QGroupBox("Ingest")
        ingest_layout = QFormLayout(ingest_group)

        folder_row = QHBoxLayout()
        self._watched_folder = QLineEdit()
        self._watched_folder.setReadOnly(True)
        folder_row.addWidget(self._watched_folder)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(btn_browse)
        ingest_layout.addRow("Watch folder:", folder_row)

        layout.addWidget(ingest_group)

        # Dashboard group
        dash_group = QGroupBox("Dashboard")
        dash_layout = QFormLayout(dash_group)

        self._refresh_interval = QSpinBox()
        self._refresh_interval.setRange(1, 60)
        self._refresh_interval.setSuffix(" min")
        dash_layout.addRow("Auto-refresh interval:", self._refresh_interval)

        self._week_start = QComboBox()
        self._week_start.addItems([
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
        ])
        dash_layout.addRow("Week starts on:", self._week_start)

        layout.addWidget(dash_group)

        # Save button
        btn_save = QPushButton("Save Settings")
        btn_save.clicked.connect(self._on_save)
        layout.addWidget(btn_save)

        layout.addStretch()

    def _load(self) -> None:
        """Load current settings from DB."""
        enabled = get_setting(self._conn, "notifications_enabled") or "true"
        self._notif_enabled.setChecked(enabled.lower() == "true")

        lead = get_setting(self._conn, "notify_lead_minutes") or "5"
        self._lead_minutes.setValue(int(lead))

        folder = get_setting(self._conn, "watched_folder") or ""
        self._watched_folder.setText(folder)

        refresh = get_setting(self._conn, "refresh_interval_min") or "5"
        self._refresh_interval.setValue(int(refresh))

        week_day = get_setting(self._conn, "week_start_day") or "monday"
        idx = self._week_start.findText(week_day.capitalize())
        if idx >= 0:
            self._week_start.setCurrentIndex(idx)

    def _on_browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select CSV Watch Folder"
        )
        if folder:
            self._watched_folder.setText(folder)

    def _on_save(self) -> None:
        """Persist all settings to DB."""
        set_setting(
            self._conn, "notifications_enabled",
            "true" if self._notif_enabled.isChecked() else "false",
        )
        set_setting(
            self._conn, "notify_lead_minutes",
            str(self._lead_minutes.value()),
        )
        set_setting(
            self._conn, "watched_folder",
            self._watched_folder.text(),
        )
        set_setting(
            self._conn, "refresh_interval_min",
            str(self._refresh_interval.value()),
        )
        set_setting(
            self._conn, "week_start_day",
            self._week_start.currentText().lower(),
        )
        QMessageBox.information(self, "Settings", "Settings saved.")
