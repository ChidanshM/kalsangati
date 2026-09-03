"""Tests for ``kalsangati.services.set_active_niyam``.

Service #3 in the six-service plan.  Atomic small service —
validation + structured result + exception wrapping on top of the
core :func:`kalsangati.core.niyam.set_active`.
"""

from __future__ import annotations

import sqlite3

import pytest

from kalsangati.core.exceptions import KalsangatiError, NiyamNotFoundError
from kalsangati.core.niyam import create, get_active, get_by_id
from kalsangati.services.set_active_niyam import (
    SetActiveResult,
    set_active_niyam,
)

# ── Happy-path behaviour ────────────────────────────────────────────────


class TestSetActiveNiyamSuccess:
    """Activations that succeed and mutate state."""

    def test_activates_when_none_were_active(
        self, conn: sqlite3.Connection
    ) -> None:
        """From a clean slate, the target becomes active."""
        n = create(conn, "Spring 26")

        result = set_active_niyam(conn, n.id)

        assert isinstance(result, SetActiveResult)
        assert result.niyam_id == n.id
        assert result.previous_active_id is None
        assert result.was_already_active is False

        active = get_active(conn)
        assert active is not None
        assert active.id == n.id

    def test_switches_active_recording_previous(
        self, conn: sqlite3.Connection
    ) -> None:
        """Switching from one active Niyam to another records the
        previous one in the result."""
        n1 = create(conn, "A", set_active=True)
        n2 = create(conn, "B")

        result = set_active_niyam(conn, n2.id)

        assert result.niyam_id == n2.id
        assert result.previous_active_id == n1.id
        assert result.was_already_active is False

        # n2 is now active, n1 is not.
        assert get_active(conn).id == n2.id
        n1_fresh = get_by_id(conn, n1.id)
        assert n1_fresh is not None
        assert n1_fresh.is_active is False

    def test_deactivates_all_others(
        self, conn: sqlite3.Connection
    ) -> None:
        """With three Niyams, exactly one ends up active after the call."""
        n1 = create(conn, "A", set_active=True)
        n2 = create(conn, "B")
        n3 = create(conn, "C")

        set_active_niyam(conn, n3.id)

        active_count = conn.execute(
            "SELECT COUNT(*) FROM niyam WHERE is_active = 1"
        ).fetchone()[0]
        assert active_count == 1
        assert get_active(conn).id == n3.id
        # Touch n1 and n2 via the return value for good measure.
        assert all(
            not get_by_id(conn, nid).is_active for nid in (n1.id, n2.id)
        )


# ── Idempotence (already-active case) ───────────────────────────────────


class TestAlreadyActiveIsNoOp:
    """Activating the already-active Niyam succeeds without mutation."""

    def test_result_flags_already_active(
        self, conn: sqlite3.Connection
    ) -> None:
        n = create(conn, "Only", set_active=True)

        result = set_active_niyam(conn, n.id)

        assert result.niyam_id == n.id
        assert result.was_already_active is True
        # previous_active_id is None in the no-op path — the flag, not
        # the id, is the signal.
        assert result.previous_active_id is None

    def test_no_other_niyam_affected(
        self, conn: sqlite3.Connection
    ) -> None:
        """A redundant activation must not touch other rows."""
        n1 = create(conn, "A", set_active=True)
        n2 = create(conn, "B")

        set_active_niyam(conn, n1.id)

        # n1 still active, n2 still inactive.
        assert get_by_id(conn, n1.id).is_active is True
        assert get_by_id(conn, n2.id).is_active is False

    def test_idempotent_under_repetition(
        self, conn: sqlite3.Connection
    ) -> None:
        """Calling repeatedly reports already-active from the second
        call onward."""
        n = create(conn, "Only")

        r1 = set_active_niyam(conn, n.id)
        r2 = set_active_niyam(conn, n.id)
        r3 = set_active_niyam(conn, n.id)

        assert r1.was_already_active is False
        assert r2.was_already_active is True
        assert r3.was_already_active is True


# ── Error path: Niyam not found ─────────────────────────────────────────


class TestNiyamNotFound:
    """Activating a non-existent id raises NiyamNotFoundError."""

    def test_bogus_id_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(NiyamNotFoundError):
            set_active_niyam(conn, 999)

    def test_error_inherits_kalsangati_error(
        self, conn: sqlite3.Connection
    ) -> None:
        """Presentation layer relies on the base-class catch."""
        assert issubclass(NiyamNotFoundError, KalsangatiError)

    def test_empty_db_raises(self, conn: sqlite3.Connection) -> None:
        """No Niyams at all — any id is not-found."""
        with pytest.raises(NiyamNotFoundError):
            set_active_niyam(conn, 1)

    def test_db_unchanged_on_failure(
        self, conn: sqlite3.Connection
    ) -> None:
        """A failed call must not mutate the active flag."""
        n = create(conn, "A", set_active=True)

        with pytest.raises(NiyamNotFoundError):
            set_active_niyam(conn, 999)

        # n is still the active one.
        assert get_active(conn).id == n.id

    def test_deleted_niyam_raises(
        self, conn: sqlite3.Connection
    ) -> None:
        """A stale id (niyam deleted between fetch and activate)
        surfaces as NiyamNotFoundError."""
        n = create(conn, "Transient")
        conn.execute("DELETE FROM niyam WHERE id = ?", (n.id,))
        conn.commit()

        with pytest.raises(NiyamNotFoundError):
            set_active_niyam(conn, n.id)


# ── Result type invariants ──────────────────────────────────────────────


def test_result_is_dataclass_with_slots(
    conn: sqlite3.Connection,
) -> None:
    """Sanity check on the SetActiveResult shape."""
    n = create(conn, "A")
    result = set_active_niyam(conn, n.id)

    # slots=True means no __dict__.
    assert not hasattr(result, "__dict__")
    # Three documented fields, in the documented order.
    assert result.__slots__ == (
        "niyam_id",
        "previous_active_id",
        "was_already_active",
    )
