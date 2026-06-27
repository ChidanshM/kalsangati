"""Tests for ``gui/niyam_editor.py::_on_activate`` — Unit 6 wiring.

Runs the NiyamEditor widget under headless Qt
(``QT_QPA_PLATFORM=offscreen``).  Verifies the Unit-6 service-layer
wiring: ``_on_activate`` forwards to
:func:`kalsangati.services.set_active_niyam.set_active_niyam`,
dispatches exceptions to the correct message-box path, and emits
``niyam_changed`` only when the call actually mutated state.

No rendering or display access.  ``set_active_niyam`` is patched in
every test so the service itself is never exercised — that coverage
lives in ``test_set_active_niyam.py``.  The widget's own DB
connection (from the ``conn`` fixture) is also untouched by these
tests; the widget just needs one to initialise.
"""

from __future__ import annotations

import os

# Must be set before PyQt5 is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3  # noqa: E402
from collections.abc import Generator  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from kalsangati.exceptions import NiyamNotFoundError  # noqa: E402
from kalsangati.gui.niyam_editor import NiyamEditor  # noqa: E402
from kalsangati.niyam import create  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Headless QApplication shared across the module's tests."""
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


@pytest.fixture
def editor(
    conn: sqlite3.Connection, qapp: QApplication
) -> Generator[NiyamEditor, None, None]:
    """A NiyamEditor backed by the shared ``conn`` fixture.

    One Niyam is pre-created so the editor has a valid
    ``_current_niyam`` for ``_on_activate`` to use.  Every test
    patches ``set_active_niyam`` before invoking the handler.
    """
    n = create(conn, "Test Niyam")
    w = NiyamEditor(conn)
    # Point the editor at the Niyam we just made.
    w._current_niyam = n
    yield w
    w.close()


def _ok_result(niyam_id: int, *, already_active: bool = False) -> MagicMock:
    """A plausible SetActiveResult-shaped mock return value."""
    return MagicMock(
        niyam_id=niyam_id,
        previous_active_id=None,
        was_already_active=already_active,
    )


# ── Happy path: service dispatch + signal emission ──────────────────────


class TestOnActivateServiceCall:
    """``_on_activate`` forwards to the service and emits correctly."""

    def test_service_called_with_current_niyam_id(
        self, editor: NiyamEditor
    ) -> None:
        niyam_id = editor._current_niyam.id

        with patch(
            "kalsangati.gui.niyam_editor.set_active_niyam",
            return_value=_ok_result(niyam_id),
        ) as mock_svc:
            editor._on_activate()

        assert mock_svc.call_count == 1
        # The service takes (conn, niyam_id) positionally — check both.
        call_args = mock_svc.call_args
        passed_conn = call_args.args[0] if call_args.args else call_args.kwargs.get("conn")
        passed_id = (
            call_args.args[1] if len(call_args.args) > 1
            else call_args.kwargs.get("niyam_id")
        )
        assert passed_conn is editor._conn
        assert passed_id == niyam_id

    def test_emits_niyam_changed_on_real_activation(
        self, editor: NiyamEditor
    ) -> None:
        """Signal fires when the service actually mutated state."""
        emitted: list[None] = []
        editor.niyam_changed.connect(lambda: emitted.append(None))

        with patch(
            "kalsangati.gui.niyam_editor.set_active_niyam",
            return_value=_ok_result(
                editor._current_niyam.id, already_active=False
            ),
        ):
            editor._on_activate()

        assert len(emitted) == 1

    def test_does_not_emit_on_already_active(
        self, editor: NiyamEditor
    ) -> None:
        """Signal is suppressed on the already-active no-op path."""
        emitted: list[None] = []
        editor.niyam_changed.connect(lambda: emitted.append(None))

        with patch(
            "kalsangati.gui.niyam_editor.set_active_niyam",
            return_value=_ok_result(
                editor._current_niyam.id, already_active=True
            ),
        ):
            editor._on_activate()

        assert emitted == []

    def test_no_op_when_current_niyam_is_none(
        self, editor: NiyamEditor
    ) -> None:
        editor._current_niyam = None

        with patch(
            "kalsangati.gui.niyam_editor.set_active_niyam"
        ) as mock_svc:
            editor._on_activate()

        assert not mock_svc.called


# ── Exception dispatch ─────────────────────────────────────────────────


class TestOnActivateExceptionHandling:
    """Domain errors → warning; unexpected → critical + log."""

    def test_niyam_not_found_shows_warning(
        self, editor: NiyamEditor
    ) -> None:
        niyam_id = editor._current_niyam.id

        with (
            patch(
                "kalsangati.gui.niyam_editor.set_active_niyam",
                side_effect=NiyamNotFoundError(f"No Niyam found with id {niyam_id}"),
            ),
            patch.object(QMessageBox, "warning") as mock_warning,
            patch.object(QMessageBox, "critical") as mock_critical,
        ):
            editor._on_activate()

        assert mock_warning.called
        assert not mock_critical.called
        # Third positional arg to QMessageBox.warning is the body text;
        # str(exc) is forwarded as-is.
        body = mock_warning.call_args.args[2]
        assert "No Niyam found" in body or str(niyam_id) in body

    def test_unexpected_exception_shows_critical_and_logs(
        self,
        editor: NiyamEditor,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            patch(
                "kalsangati.gui.niyam_editor.set_active_niyam",
                side_effect=RuntimeError("unexpected boom"),
            ),
            patch.object(QMessageBox, "warning") as mock_warning,
            patch.object(QMessageBox, "critical") as mock_critical,
            caplog.at_level("ERROR", logger="kalsangati.gui.niyam_editor"),
        ):
            editor._on_activate()

        assert mock_critical.called
        assert not mock_warning.called
        assert any(
            "unexpected boom" in r.getMessage()
            or "Unexpected error" in r.getMessage()
            for r in caplog.records
        )

    def test_no_signal_emitted_on_domain_failure(
        self, editor: NiyamEditor
    ) -> None:
        """A NiyamNotFoundError must not fire niyam_changed."""
        emitted: list[None] = []
        editor.niyam_changed.connect(lambda: emitted.append(None))

        with (
            patch(
                "kalsangati.gui.niyam_editor.set_active_niyam",
                side_effect=NiyamNotFoundError("x"),
            ),
            patch.object(QMessageBox, "warning"),
        ):
            editor._on_activate()

        assert emitted == []

    def test_no_signal_emitted_on_unexpected_failure(
        self, editor: NiyamEditor
    ) -> None:
        emitted: list[None] = []
        editor.niyam_changed.connect(lambda: emitted.append(None))

        with (
            patch(
                "kalsangati.gui.niyam_editor.set_active_niyam",
                side_effect=RuntimeError("x"),
            ),
            patch.object(QMessageBox, "critical"),
        ):
            editor._on_activate()

        assert emitted == []
