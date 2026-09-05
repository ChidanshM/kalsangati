"""Weekly Task Planner — capacity-aware scheduling GUI.

Left: backlog as a task tree, subtasks nested under their parent.
Right: week view per activity with capacity bars, Niyam block anchors,
tasks under their natural blocks, floating tasks at bottom.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QDropEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kalsangati.core.exceptions import KalsangatiError
from kalsangati.core.projects import Project
from kalsangati.core.projects import create as project_create
from kalsangati.core.projects import delete as project_delete
from kalsangati.core.projects import get_all as get_all_projects
from kalsangati.core.projects import get_by_id as project_get_by_id
from kalsangati.core.projects import update as project_update
from kalsangati.core.tasks import (
    Task,
    all_capacities,
)
from kalsangati.core.tasks import (
    create as create_task,
)
from kalsangati.core.tasks import (
    get_all as get_all_tasks,
)
from kalsangati.core.tasks import (
    get_by_id as get_task_by_id,
)
from kalsangati.core.tasks import (
    update as update_task,
)
from kalsangati.infrastructure import notes as notes_files
from kalsangati.services.delete_task import delete_task
from kalsangati.services.reparent_task import reparent_task
from kalsangati.services.update_task_status import (
    allowed_transitions,
    update_task_status,
)

logger = logging.getLogger(__name__)


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

        self._backlog_tree = _BacklogTree(self)
        left_layout.addWidget(self._backlog_tree)

        # Task action buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("New Task")
        btn_add.clicked.connect(self._on_add_task)
        btn_row.addWidget(btn_add)

        self._btn_subtask = QPushButton("New Subtask")
        self._btn_subtask.clicked.connect(self._on_add_subtask)
        self._btn_subtask.setEnabled(False)
        btn_row.addWidget(self._btn_subtask)

        self._btn_edit = QPushButton("Edit")
        self._btn_edit.clicked.connect(self._on_edit_selected)
        self._btn_edit.setEnabled(False)
        btn_row.addWidget(self._btn_edit)

        btn_assign = QPushButton("Assign to Week")
        btn_assign.clicked.connect(self._on_assign_to_week)
        btn_row.addWidget(btn_assign)

        btn_done = QPushButton("Mark Done")
        btn_done.clicked.connect(self._on_mark_done)
        btn_row.addWidget(btn_done)

        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._on_delete)
        btn_row.addWidget(btn_del)

        btn_projects = QPushButton("Projects…")
        btn_projects.clicked.connect(self._on_projects)
        btn_row.addWidget(btn_projects)

        self._backlog_tree.itemSelectionChanged.connect(
            self._on_selection_changed
        )
        self._backlog_tree.itemDoubleClicked.connect(
            lambda item, _col: self._edit_task(
                item.data(0, Qt.ItemDataRole.UserRole)
            )
        )

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
        # Tasks in this_week / in_progress / on_hold live only here, so
        # without this they would stay uneditable — the same gap in a
        # different place.
        self._week_table.cellDoubleClicked.connect(
            lambda row, _col: self._edit_task(self._week_task_id(row))
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
        """Rebuild the backlog tree from the database.

        Always redraws from scratch — never trusts a drag to have moved
        anything.  A refused reparent leaves the display and the data
        agreeing, which is the point.
        """
        self._backlog_tree.clear()
        tasks = get_all_tasks(self._conn, status="backlog")
        present = {t.id for t in tasks}

        by_parent: dict[int | None, list[Task]] = defaultdict(list)
        for t in tasks:
            # A child whose parent is not in this result set — the parent
            # is in_progress, done, or deleted — is rendered at root
            # level rather than dropped.  Dropping it would look exactly
            # like the task had disappeared.
            key = t.parent_id if t.parent_id in present else None
            by_parent[key].append(t)

        for group in by_parent.values():
            group.sort(key=lambda t: (t.sort_order, t.title))

        seen: set[int] = set()

        def add(parent_item: QTreeWidgetItem | None, task: Task) -> None:
            # `seen` is unreachable while trg_tasks_no_cycle holds; it
            # costs nothing and matches the guards in reparent_task and
            # delete_task.
            if task.id in seen:
                return
            seen.add(task.id)
            item = QTreeWidgetItem([self._backlog_label(task)])
            item.setData(0, Qt.ItemDataRole.UserRole, task.id)
            if parent_item is None:
                self._backlog_tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            for child in by_parent.get(task.id, []):
                add(item, child)

        for root in by_parent.get(None, []):
            add(None, root)

        self._backlog_tree.expandAll()
        self._on_selection_changed()

    @staticmethod
    def _backlog_label(t: Task) -> str:
        label = f"{t.title}"
        if t.estimated_hours:
            label += f"  ({t.estimated_hours:.1f}h)"
        label += f"  [{t.canonical_activity}]"
        if t.spilled_from:
            label += " ⟲"
        return label

    def _selected_task_id(self) -> int | None:
        """Id of the selected backlog task, or None."""
        item = self._backlog_tree.currentItem()
        if item is None:
            return None
        task_id: int | None = item.data(0, Qt.ItemDataRole.UserRole)
        return task_id

    def _on_selection_changed(self) -> None:
        has = self._selected_task_id() is not None
        self._btn_subtask.setEnabled(has)
        self._btn_edit.setEnabled(has)

    def _week_task_id(self, row: int) -> int | None:
        """Task id behind a week-table row.

        The id rides on the first column's item data; the week table
        stores it differently from the backlog tree, which is why this
        exists rather than reusing ``_selected_task_id``.
        """
        item = self._week_table.item(row, 0)
        if item is None:
            return None
        task_id: int | None = item.data(Qt.ItemDataRole.UserRole)
        return task_id

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

        # Week tasks table — this_week, in_progress, and on_hold all
        # stay visible.  Without on_hold here, pausing a task would drop
        # it out of both this view and the backlog.
        week_tasks = get_all_tasks(self._conn, status="this_week")
        week_tasks += get_all_tasks(self._conn, status="in_progress")
        week_tasks += get_all_tasks(self._conn, status="on_hold")
        self._week_table.setRowCount(len(week_tasks))

        for i, t in enumerate(week_tasks):
            title_item = QTableWidgetItem(t.title)
            title_item.setData(Qt.ItemDataRole.UserRole, t.id)
            self._week_table.setItem(i, 0, title_item)
            self._week_table.setItem(i, 1, QTableWidgetItem(t.canonical_activity))
            est = f"{t.estimated_hours:.1f}" if t.estimated_hours else "—"
            self._week_table.setItem(i, 2, QTableWidgetItem(est))
            self._week_table.setItem(
                i, 3, QTableWidgetItem(t.due_date or "—")
            )
            self._week_table.setCellWidget(
                i, 4, self._make_status_combo(t.id, t.status)
            )

    def _make_status_combo(self, task_id: int, current: str) -> QComboBox:
        """Build a status dropdown for a week-table row.

        Item 0 is the current status (selected); the remaining items are
        the legal transition targets, so an illegal move can never be
        chosen.  ``activated`` fires only on user selection, so populating
        the combo here does not trigger a transition.

        Args:
            task_id: Id of the task this row represents.
            current: The task's current status.

        Returns:
            A configured combo box wired to the status-change handler.
        """
        combo = QComboBox()
        combo.addItem(current, current)  # index 0 = current status
        for target in sorted(allowed_transitions(current)):
            combo.addItem(target, target)
        combo.activated.connect(
            lambda _idx, tid=task_id, c=combo: self._on_status_combo(tid, c)
        )
        return combo

    def _on_status_combo(self, task_id: int, combo: QComboBox) -> None:
        """Apply a status dropdown selection.

        The apply is deferred with a zero-delay timer so refresh() can
        rebuild the table — deleting this very combo — without destroying
        the widget while it is still inside its own ``activated`` slot.
        """
        target = combo.currentData()
        original = combo.itemData(0)
        if target is None or target == original:
            return  # re-selected the current status; nothing to do
        QTimer.singleShot(0, lambda: self._apply_status(task_id, target))

    def _apply_status(self, task_id: int, new_status: str) -> None:
        """Route a status change through the service, surfacing errors.

        Shared by the backlog "Mark Done" button and the week-table
        status dropdowns.  Presentation-layer exception pattern: domain
        errors become a warning dialog; unexpected errors are logged with
        a stack trace and shown generically.  refresh() runs either way,
        so on failure the dropdown resets to the task's real status.
        """
        try:
            update_task_status(self._conn, task_id, new_status)
        except KalsangatiError as e:
            QMessageBox.warning(self, "Cannot change status", str(e))
        except Exception:
            logger.exception(
                "Unexpected error setting task %s to %s", task_id, new_status
            )
            QMessageBox.critical(
                self, "Unexpected error", "Check logs for details."
            )
        finally:
            self.refresh()

    def _on_add_task(self) -> None:
        self._create_task(parent_id=None)

    def _on_add_subtask(self) -> None:
        """Create a task under the current backlog selection."""
        parent_id = self._selected_task_id()
        if parent_id is None:
            return
        self._create_task(parent_id=parent_id)

    def _create_task(self, *, parent_id: int | None) -> None:
        dlg = _TaskDialog(self._conn, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            create_task(
                self._conn,
                title=data["title"],
                canonical_activity=data["activity"],
                project_id=data.get("project_id"),
                parent_id=parent_id,
                estimated_hours=data.get("estimated_hours"),
                due_date=data.get("due_date"),
            )
            self.refresh()

    def _on_edit_selected(self) -> None:
        self._edit_task(self._selected_task_id())

    def _edit_task(self, task_id: int | None) -> None:
        """Open the task dialog prefilled, and apply what comes back.

        Shared by the Edit button, a backlog double-click, and a
        week-table double-click.

        Writes through ``core.tasks.update`` rather than a service:
        renaming or re-dating a task changes no invariant and is
        deliberately not audited, unlike status, parent, and deletion,
        which each have their own owner.

        ``update`` raises ``sqlite3.IntegrityError`` rather than a domain
        error, so bad input lands in the unexpected branch — recorded as
        a gap rather than papered over here.
        """
        if task_id is None:
            return
        task = get_task_by_id(self._conn, task_id)
        if task is None:
            self.refresh()  # stale id; redraw and move on
            return

        dlg = _TaskDialog(self._conn, self, task=task)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.get_data()
        try:
            update_task(
                self._conn,
                task_id,
                title=data["title"],
                canonical_activity=data["activity"],
                estimated_hours=data["estimated_hours"],
                due_date=data["due_date"],
                project_id=data["project_id"],
            )
        except KalsangatiError as e:
            QMessageBox.warning(self, "Cannot save task", str(e))
        except Exception:
            logger.exception("Unexpected error updating task %s", task_id)
            QMessageBox.critical(
                self, "Unexpected error", "Check logs for details."
            )
        else:
            # Fields first, file second, and only if it changed.  A
            # read-only disk must not cost the rest of the edit.
            body = dlg.notes_body()
            if body is not None:
                try:
                    notes_files.write_body(self._conn, task_id, body)
                except OSError as e:
                    logger.exception(
                        "Could not write notes for task %s", task_id
                    )
                    QMessageBox.warning(
                        self,
                        "Notes not saved",
                        f"The task was saved, but its notes were not:\n{e}",
                    )
        finally:
            self.refresh()

    def _on_projects(self) -> None:
        """Open the project manager, then redraw.

        The task dialog reads its project list at construction, so a
        project created here is visible the next time it opens.
        """
        _ProjectsDialog(self._conn, self).exec()
        self.refresh()

    def _on_assign_to_week(self) -> None:
        tid = self._selected_task_id()
        if tid is None:
            return
        from kalsangati.core.analytics import _current_week_start
        from kalsangati.persistence.db import get_setting

        start_day = get_setting(self._conn, "week_start_day") or "monday"
        ws = _current_week_start(start_day)
        update_task(self._conn, tid, status="this_week", week_assigned=ws)
        self.refresh()

    def _on_mark_done(self) -> None:
        tid = self._selected_task_id()
        if tid is None:
            return
        self._apply_status(tid, "done")

    def _on_delete(self) -> None:
        """Soft-delete the selected task and its subtree.

        Routes through the service rather than ``core.tasks.delete``:
        the core primitive does not cascade, so deleting a parent would
        leave children live and orphaned.  Deletion is reversible, so
        there is no confirmation dialog.
        """
        tid = self._selected_task_id()
        if tid is None:
            return
        try:
            delete_task(self._conn, tid)
        except KalsangatiError as e:
            QMessageBox.warning(self, "Cannot delete task", str(e))
        except Exception:
            logger.exception("Unexpected error deleting task %s", tid)
            QMessageBox.critical(
                self, "Unexpected error", "Check logs for details."
            )
        finally:
            self.refresh()

    def _apply_reparent(
        self, task_id: int, new_parent_id: int | None
    ) -> None:
        """Route a drag-drop move through the reparent service.

        Presentation-layer exception pattern.  The most interesting path
        is ``TaskCycleError`` — dragging a task onto its own descendant —
        which becomes a sentence rather than a stack trace.

        ``refresh()`` runs either way, redrawing from the database, so a
        refused move cannot leave the tree showing something the data
        does not agree with.
        """
        try:
            reparent_task(self._conn, task_id, new_parent_id=new_parent_id)
        except KalsangatiError as e:
            QMessageBox.warning(self, "Cannot move task", str(e))
        except Exception:
            logger.exception(
                "Unexpected error reparenting task %s under %s",
                task_id,
                new_parent_id,
            )
            QMessageBox.critical(
                self, "Unexpected error", "Check logs for details."
            )
        finally:
            self.refresh()


class _BacklogTree(QTreeWidget):
    """Backlog tree with drag-to-reparent.

    Qt's own ``InternalMove`` is deliberately **not** used.  It moves the
    item inside the widget before any handler runs, so a move the service
    then refuses would leave the display disagreeing with the database.
    This subclass intercepts the drop, calls the service, and lets the
    planner redraw from the database instead.
    """

    def __init__(self, planner: TaskPlanner) -> None:
        super().__init__()
        self._planner = planner
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802
        """Reparent the dragged task instead of moving the widget item.

        Dropping onto an item makes the dragged task its child; dropping
        onto blank space promotes the task to a root.

        The parameter is ``QDropEvent | None`` because that is how PyQt5
        types the virtual.  Narrowing it to a bare ``QDropEvent`` would
        be a Liskov violation and is the second half of pitfall #32; the
        camelCase name is Qt's, hence the ``noqa``.
        """
        if event is None:
            return
        dragged = self.currentItem()
        if dragged is None:
            return
        task_id: int | None = dragged.data(0, Qt.ItemDataRole.UserRole)
        if task_id is None:
            return

        target = self.itemAt(event.pos())
        new_parent_id: int | None = (
            target.data(0, Qt.ItemDataRole.UserRole)
            if target is not None
            else None
        )

        # Accept so the drag ends cleanly, but never call super(): the
        # base class would move the item itself, and refresh() is the
        # only thing allowed to redraw.
        event.acceptProposedAction()
        self._planner._apply_reparent(task_id, new_parent_id)


class _TaskDialog(QDialog):
    """Create or edit a task.

    One dialog, two modes: pass ``task`` to edit it, omit it to create.
    The fields are exactly those ``core.tasks.update`` accepts.

    Deliberately absent: status (the week-table dropdown owns it, and
    only legal transitions may be offered), parent (``reparent_task``
    owns it, and a move needs a cycle check), and slug (fixed at
    creation so a rename does not move a file).

    Args:
        conn: Database connection.
        parent: Parent widget.
        task: When given, the dialog opens in edit mode with this
            task's values prefilled.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        parent: QWidget | None = None,
        *,
        task: Task | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Task" if task else "New Task")
        self._conn = conn
        self._task = task
        self._notes_edit: QPlainTextEdit | None = None
        self._notes_original: str = ""

        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)

        fields = QWidget()
        layout = QFormLayout(fields)
        tabs.addTab(fields, "Details")

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
        self._project_activity: dict[int, str] = {}
        for p in get_all_projects(conn):
            self._project_combo.addItem(p.name, p.id)
            self._project_activity[p.id] = p.canonical_activity
        self._project_combo.currentIndexChanged.connect(
            self._on_project_changed
        )
        layout.addRow("Project:", self._project_combo)

        # PyQt5-stubs declares the bitwise-OR of StandardButton enum
        # values as int, but QDialogButtonBox actually accepts it as
        # StandardButtons.  Runtime is correct; type check is wrong.
        buttons = QDialogButtonBox(  # type: ignore[call-overload]
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # Notes need a task id to derive a path, so a task being
        # created has nowhere to put them yet.
        if task is not None:
            tabs.addTab(self._build_notes_tab(task), "Notes")
            self._prefill(task)

    def _build_notes_tab(self, task: Task) -> QWidget:
        """The Markdown body, loaded from disk.

        A missing file is announced rather than shown as an empty box:
        "nothing written yet" and "the file has gone" are different
        situations and only one of them is fine.
        """
        page = QWidget()
        box = QVBoxLayout(page)

        body: str | None = None
        try:
            body = notes_files.read_body(self._conn, task.id)
        except OSError:
            logger.exception("Could not read notes for task %s", task.id)
            box.addWidget(QLabel("Notes could not be read — check logs."))

        if body is None:
            path = notes_files.derive_path(self._conn, task.id)
            box.addWidget(
                QLabel(
                    f"No notes file yet. It will be created at:\n{path}"
                )
            )
            body = ""

        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlainText(body)
        font = self._notes_edit.font()
        font.setFamily("monospace")
        self._notes_edit.setFont(font)
        self._notes_original = body
        box.addWidget(self._notes_edit)
        return page

    def notes_body(self) -> str | None:
        """The edited body, or ``None`` if it was not changed.

        ``None`` means "do not write", so opening a task and closing it
        does not rewrite a file for no reason.
        """
        if self._notes_edit is None:
            return None
        current = self._notes_edit.toPlainText()
        return current if current != self._notes_original else None

    def _prefill(self, task: Task) -> None:
        """Load an existing task's values into the fields."""
        self._title.setText(task.title)
        self._activity.setText(task.canonical_activity)
        self._est_hours.setText(
            "" if task.estimated_hours is None else str(task.estimated_hours)
        )
        self._due_date.setText(task.due_date or "")
        if task.project_id is not None:
            index = self._project_combo.findData(task.project_id)
            if index >= 0:
                self._project_combo.setCurrentIndex(index)

    def _on_project_changed(self, _index: int) -> None:
        """Fill the activity from the chosen project.

        Activity is an overridable default from the project: a task
        usually belongs to the same kind of time as its project, and
        occasionally does not.  Without this the rule is written down
        and does nothing, and you end up filing tasks in the wrong
        project to make the accounting come out right.

        **Only fills an empty field, or one still holding another
        project's activity.**  A value the user typed is never
        overwritten.
        """
        project_id = self._project_combo.currentData()
        if project_id is None:
            return
        activity = self._project_activity.get(project_id)
        if activity is None:
            return
        current = self._activity.text().strip()
        if current and current not in self._project_activity.values():
            return  # user-supplied; leave it alone
        self._activity.setText(activity)

    def _on_accept(self) -> None:
        """Validate before closing.

        The dialog previously converted the estimate with a bare
        ``float()`` in ``get_data``, which raised straight out of the
        accept handler on any non-numeric input, and stored the due date
        unvalidated — a malformed date then sorted arbitrarily under
        ``COALESCE(due_date, '9999-12-31')`` rather than failing.
        """
        problem = self._validation_error()
        if problem is not None:
            QMessageBox.warning(self, "Check the form", problem)
            return  # stay open
        self.accept()

    def _validation_error(self) -> str | None:
        """Return the first problem with the form, or None."""
        if not self._title.text().strip():
            return "A title is required."
        if not self._activity.text().strip():
            return "An activity is required."

        est = self._est_hours.text().strip()
        if est:
            try:
                value = float(est)
            except ValueError:
                return f"Estimated hours must be a number, not {est!r}."
            if value < 0:
                return "Estimated hours cannot be negative."

        due = self._due_date.text().strip()
        if due:
            try:
                datetime.strptime(due, "%Y-%m-%d")
            except ValueError:
                return f"Due date must look like 2026-09-30, not {due!r}."
        return None

    def get_data(self) -> dict:
        """Field values, validated by ``_on_accept`` before the dialog
        closes.  Safe to call after an accepted exec.
        """
        est = self._est_hours.text().strip()
        return {
            "title": self._title.text().strip(),
            "activity": self._activity.text().strip(),
            "estimated_hours": float(est) if est else None,
            "due_date": self._due_date.text().strip() or None,
            "project_id": self._project_combo.currentData(),
        }


class _ProjectsDialog(QDialog):
    """List, create, rename and delete projects.

    Deleting a project does **not** delete its tasks: ``projects.delete``
    clears their ``project_id`` first.  The confirmation says how many
    will survive, because "delete project" reasonably reads as "delete
    its tasks".

    Unlike tasks, this deletion is irreversible — which is why it asks
    and task deletion does not.
    """

    def __init__(
        self, conn: sqlite3.Connection, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Projects")
        self._conn = conn
        layout = QVBoxLayout(self)

        self._list = QListWidget()
        layout.addWidget(self._list)

        row = QHBoxLayout()
        btn_new = QPushButton("New")
        btn_new.clicked.connect(self._on_new)
        row.addWidget(btn_new)

        self._btn_rename = QPushButton("Rename")
        self._btn_rename.clicked.connect(self._on_rename)
        row.addWidget(self._btn_rename)

        self._btn_delete = QPushButton("Delete")
        self._btn_delete.clicked.connect(self._on_delete)
        row.addWidget(self._btn_delete)
        layout.addLayout(row)

        close = QDialogButtonBox(  # type: ignore[call-overload]
            QDialogButtonBox.StandardButton.Close
        )
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self._list.itemSelectionChanged.connect(self._sync_buttons)
        self.refresh()

    def refresh(self) -> None:
        """Reload the list from the database."""
        self._list.clear()
        for p in get_all_projects(self._conn):
            item = QListWidgetItem(f"{p.name}  [{p.canonical_activity}]")
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            self._list.addItem(item)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        has = self._selected_id() is not None
        self._btn_rename.setEnabled(has)
        self._btn_delete.setEnabled(has)

    def _selected_id(self) -> int | None:
        item = self._list.currentItem()
        if item is None:
            return None
        project_id: int | None = item.data(Qt.ItemDataRole.UserRole)
        return project_id

    def _on_new(self) -> None:
        dlg = _ProjectDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, activity = dlg.get_data()
        try:
            project_create(self._conn, name, activity)
        except Exception:
            logger.exception("Unexpected error creating project %r", name)
            QMessageBox.critical(
                self, "Unexpected error", "Check logs for details."
            )
        finally:
            self.refresh()

    def _on_rename(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        existing = project_get_by_id(self._conn, pid)
        if existing is None:
            self.refresh()
            return
        dlg = _ProjectDialog(self, project=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, activity = dlg.get_data()
        try:
            project_update(
                self._conn, pid, name=name, canonical_activity=activity
            )
        except Exception:
            logger.exception("Unexpected error renaming project %s", pid)
            QMessageBox.critical(
                self, "Unexpected error", "Check logs for details."
            )
        finally:
            self.refresh()

    def _on_delete(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        count = self._conn.execute(
            "SELECT COUNT(*) FROM tasks "
            "WHERE project_id = ? AND deleted_at IS NULL",
            (pid,),
        ).fetchone()[0]
        message = (
            f"Delete this project?\n\n{count} task(s) will keep existing "
            "without a project."
            if count
            else "Delete this project?"
        )
        answer = QMessageBox.question(
            self,
            "Delete project",
            message,
            # PyQt5-stubs declares the bitwise-OR of StandardButton
            # values as int, but question() accepts it as
            # StandardButtons.  Runtime is correct; the stub is wrong.
            # Same defect as the QDialogButtonBox construction below.
            QMessageBox.StandardButton.Yes  # type: ignore[arg-type]
            | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            project_delete(self._conn, pid)
        except Exception:
            logger.exception("Unexpected error deleting project %s", pid)
            QMessageBox.critical(
                self, "Unexpected error", "Check logs for details."
            )
        finally:
            self.refresh()


class _ProjectDialog(QDialog):
    """Name and activity for a single project."""

    def __init__(
        self, parent: QWidget | None = None, *, project: Project | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Project" if project else "New Project")
        layout = QFormLayout(self)

        self._name = QLineEdit()
        layout.addRow("Name:", self._name)

        self._activity = QLineEdit()
        self._activity.setPlaceholderText("canonical activity name")
        layout.addRow("Activity:", self._activity)

        buttons = QDialogButtonBox(  # type: ignore[call-overload]
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        if project is not None:
            self._name.setText(project.name)
            self._activity.setText(project.canonical_activity)

    def _on_accept(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "Check the form", "A name is required.")
            return
        if not self._activity.text().strip():
            QMessageBox.warning(
                self, "Check the form", "An activity is required."
            )
            return
        self.accept()

    def get_data(self) -> tuple[str, str]:
        return (
            self._name.text().strip(),
            self._activity.text().strip(),
        )
