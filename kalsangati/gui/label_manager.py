"""Label Manager — converter table and grouping hierarchy editor.

Provides GUI for editing label_mappings (raw → canonical) and
label_groups (canonical → parent group hierarchy).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kalsangati.labels import (
    add_group,
    add_mapping,
    auto_populate_groups,
    delete_group,
    delete_mapping,
    get_all_groups,
    get_all_mappings,
    get_children,
    get_unrecognized_labels,
    update_group,
    update_mapping,
)


class LabelManager(QWidget):
    """Combined label converter table and group hierarchy editor.

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

        # Left: Label mappings table
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Label Converter (raw → canonical)"))

        self._mapping_table = QTableWidget()
        self._mapping_table.setColumnCount(3)
        self._mapping_table.setHorizontalHeaderLabels(["ID", "Raw Label", "Canonical"])
        self._mapping_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._mapping_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._mapping_table.setColumnHidden(0, True)
        self._mapping_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        left_layout.addWidget(self._mapping_table)

        # Mapping buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Mapping")
        btn_add.clicked.connect(self._on_add_mapping)
        btn_row.addWidget(btn_add)

        btn_edit = QPushButton("Edit")
        btn_edit.clicked.connect(self._on_edit_mapping)
        btn_row.addWidget(btn_edit)

        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._on_delete_mapping)
        btn_row.addWidget(btn_del)

        left_layout.addLayout(btn_row)

        # Unrecognized labels
        self._unrec_label = QLabel("")
        left_layout.addWidget(self._unrec_label)

        splitter.addWidget(left)

        # Right: Group hierarchy tree
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Label Group Hierarchy"))

        self._group_tree = QTreeWidget()
        self._group_tree.setHeaderLabels(["Label", "Level"])
        self._group_tree.setColumnWidth(0, 250)
        right_layout.addWidget(self._group_tree)

        # Group buttons
        grp_btns = QHBoxLayout()
        btn_add_grp = QPushButton("Add Group")
        btn_add_grp.clicked.connect(self._on_add_group)
        grp_btns.addWidget(btn_add_grp)

        btn_auto = QPushButton("Auto-populate")
        btn_auto.clicked.connect(self._on_auto_populate)
        grp_btns.addWidget(btn_auto)

        btn_del_grp = QPushButton("Delete")
        btn_del_grp.clicked.connect(self._on_delete_group)
        grp_btns.addWidget(btn_del_grp)

        right_layout.addLayout(grp_btns)
        splitter.addWidget(right)

        layout.addWidget(splitter)

    def refresh(self) -> None:
        """Reload all data from the database."""
        self._refresh_mappings()
        self._refresh_groups()
        self._refresh_unrecognized()

    def _refresh_mappings(self) -> None:
        mappings = get_all_mappings(self._conn)
        self._mapping_table.setRowCount(len(mappings))
        for i, m in enumerate(mappings):
            self._mapping_table.setItem(i, 0, QTableWidgetItem(str(m.id)))
            self._mapping_table.setItem(i, 1, QTableWidgetItem(m.raw_label))
            self._mapping_table.setItem(i, 2, QTableWidgetItem(m.canonical_label))

    def _refresh_groups(self) -> None:
        self._group_tree.clear()
        groups = get_all_groups(self._conn)

        # Build tree by parent
        items: dict[str, QTreeWidgetItem] = {}
        roots: list[QTreeWidgetItem] = []

        # Sort by level so parents are created first
        for g in sorted(groups, key=lambda x: x.level):
            item = QTreeWidgetItem([g.canonical_label, str(g.level)])
            item.setData(0, Qt.ItemDataRole.UserRole, g.id)
            items[g.canonical_label] = item

            if g.parent_group and g.parent_group in items:
                items[g.parent_group].addChild(item)
            else:
                roots.append(item)

        for root in roots:
            self._group_tree.addTopLevelItem(root)
        self._group_tree.expandAll()

    def _refresh_unrecognized(self) -> None:
        unrec = get_unrecognized_labels(self._conn)
        if unrec:
            self._unrec_label.setText(
                f"⚠ {len(unrec)} unrecognized labels: {', '.join(unrec[:10])}"
                + ("…" if len(unrec) > 10 else "")
            )
        else:
            self._unrec_label.setText("✓ All labels mapped")

    def _on_add_mapping(self) -> None:
        raw, ok1 = QInputDialog.getText(self, "Add Mapping", "Raw label:")
        if not ok1 or not raw.strip():
            return
        canon, ok2 = QInputDialog.getText(self, "Add Mapping", "Canonical label:")
        if not ok2 or not canon.strip():
            return
        try:
            add_mapping(self._conn, raw.strip(), canon.strip())
            self.refresh()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_edit_mapping(self) -> None:
        row = self._mapping_table.currentRow()
        if row < 0:
            return
        mid = int(self._mapping_table.item(row, 0).text())
        old_raw = self._mapping_table.item(row, 1).text()
        old_canon = self._mapping_table.item(row, 2).text()

        raw, ok1 = QInputDialog.getText(
            self, "Edit Mapping", "Raw label:", text=old_raw
        )
        if not ok1:
            return
        canon, ok2 = QInputDialog.getText(
            self, "Edit Mapping", "Canonical label:", text=old_canon
        )
        if not ok2:
            return
        update_mapping(
            self._conn, mid,
            raw_label=raw.strip() or None,
            canonical_label=canon.strip() or None,
        )
        self.refresh()

    def _on_delete_mapping(self) -> None:
        row = self._mapping_table.currentRow()
        if row < 0:
            return
        mid = int(self._mapping_table.item(row, 0).text())
        delete_mapping(self._conn, mid)
        self.refresh()

    def _on_add_group(self) -> None:
        label, ok = QInputDialog.getText(
            self, "Add Group", "Canonical label:"
        )
        if ok and label.strip():
            try:
                add_group(self._conn, label.strip())
                self._refresh_groups()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

    def _on_auto_populate(self) -> None:
        count = auto_populate_groups(self._conn)
        self._refresh_groups()
        QMessageBox.information(
            self, "Auto-populate", f"Created {count} new group node(s)."
        )

    def _on_delete_group(self) -> None:
        item = self._group_tree.currentItem()
        if item is None:
            return
        gid = item.data(0, Qt.ItemDataRole.UserRole)
        if gid is not None:
            delete_group(self._conn, gid)
            self._refresh_groups()
