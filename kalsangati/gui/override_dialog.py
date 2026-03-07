"""Override Dialog — time enforcement alert for out-of-block work.

Fires when a task is started outside its canonical activity's scheduled
block in the active Niyam.  Offers three options: continue anyway (logs
as unplanned), wait/snooze, or switch activity.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class OverrideChoice(Enum):
    """The user's choice from the override dialog."""

    CONTINUE = "continue"
    SNOOZE = "snooze"
    SWITCH = "switch"


class OverrideDialog(QDialog):
    """Three-option alert for starting work outside a Niyam block.

    Args:
        activity: The canonical activity being started.
        next_block_day: Day of the next scheduled block.
        next_block_time: Start time of the next block.
        available_activities: List of activities for the switch option.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        activity: str,
        next_block_day: str,
        next_block_time: str,
        available_activities: Optional[list[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Out-of-Block Alert")
        self.setMinimumWidth(420)
        self._choice: OverrideChoice = OverrideChoice.SNOOZE
        self._override_reason: str = ""
        self._switched_activity: Optional[str] = None

        layout = QVBoxLayout(self)

        # Message
        msg = QLabel(
            f"This task belongs to <b>{activity}</b>.\n"
            f"Next scheduled block: <b>{next_block_day}</b> "
            f"at <b>{next_block_time}</b>.\n\n"
            f"What would you like to do?"
        )
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        # Override reason input (shown for "continue anyway")
        self._reason_label = QLabel("Override reason (optional):")
        self._reason_input = QLineEdit()
        self._reason_label.hide()
        self._reason_input.hide()
        layout.addWidget(self._reason_label)
        layout.addWidget(self._reason_input)

        # Activity switcher (shown for "switch activity")
        self._switch_label = QLabel("Switch to:")
        self._switch_combo = QComboBox()
        if available_activities:
            self._switch_combo.addItems(available_activities)
        self._switch_label.hide()
        self._switch_combo.hide()
        layout.addWidget(self._switch_label)
        layout.addWidget(self._switch_combo)

        # Buttons
        btn_row = QHBoxLayout()

        btn_continue = QPushButton("Continue anyway")
        btn_continue.setToolTip(
            "Log session with unplanned=true, optional override reason"
        )
        btn_continue.clicked.connect(self._on_continue)
        btn_row.addWidget(btn_continue)

        btn_snooze = QPushButton("Wait / Snooze")
        btn_snooze.setToolTip("Dismiss; re-alert at next block start")
        btn_snooze.clicked.connect(self._on_snooze)
        btn_row.addWidget(btn_snooze)

        btn_switch = QPushButton("Switch activity")
        btn_switch.setToolTip("Open activity selector")
        btn_switch.clicked.connect(self._on_switch)
        btn_row.addWidget(btn_switch)

        layout.addLayout(btn_row)

    @property
    def choice(self) -> OverrideChoice:
        """The user's selected action."""
        return self._choice

    @property
    def override_reason(self) -> str:
        """Reason text if the user chose to continue."""
        return self._override_reason

    @property
    def switched_activity(self) -> Optional[str]:
        """The new activity if the user chose to switch."""
        return self._switched_activity

    def _on_continue(self) -> None:
        # Show reason input first time; accept on second click
        if self._reason_input.isHidden():
            self._reason_label.show()
            self._reason_input.show()
            self._reason_input.setFocus()
            return
        self._choice = OverrideChoice.CONTINUE
        self._override_reason = self._reason_input.text().strip()
        self.accept()

    def _on_snooze(self) -> None:
        self._choice = OverrideChoice.SNOOZE
        self.reject()

    def _on_switch(self) -> None:
        if self._switch_combo.isHidden():
            self._switch_label.show()
            self._switch_combo.show()
            return
        self._choice = OverrideChoice.SWITCH
        self._switched_activity = self._switch_combo.currentText()
        self.accept()
