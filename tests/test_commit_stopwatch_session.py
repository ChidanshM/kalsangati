"""Tests for kalsangati.services.commit_stopwatch_session and the
classification helper ``is_session_unplanned_under`` on niyam.py.

Per the Unit 3 scope lock:

* Per-test inline Niyam setup (no shared fixture beyond ``conn``).
* Resume-window and min-session boundaries tested via keyword-only
  service parameters, not by monkey-patching module constants.
* All tests headless (no PyQt5).
"""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from datetime import datetime, timedelta

import pytest

from kalsangati.exceptions import (
    InvalidSessionBoundsError,
    KalsangatiError,
    SessionTooShortError,
)
from kalsangati.niyam import (
    TimeBlock,
    is_session_unplanned_under,
    set_active,
    update_blocks,
)
from kalsangati.niyam import (
    create as create_niyam,
)
from kalsangati.services.commit_stopwatch_session import (
    MIN_SESSION_SEC,
    RESUME_WINDOW_SEC,
    CommitResult,
    commit_stopwatch_session,
)
from kalsangati.tasks import create as create_task

# ── Classification helper ──────────────────────────────────────────────


class TestIsSessionUnplannedUnder:
    """Pure-function classification of session vs. Niyam block."""

    def _make_niyam_mon_9_11(
        self, conn: sqlite3.Connection, activity: str = "act",
    ) -> None:
        n = create_niyam(conn, "t")
        update_blocks(conn, n.id, {
            "monday": [TimeBlock(
                activity=activity, start_min=540, end_min=660,
                duration_h=2.0,
            )],
        })
        set_active(conn, n.id)

    def test_none_niyam_always_unplanned(
        self, conn: sqlite3.Connection
    ) -> None:
        assert is_session_unplanned_under(
            None, "any", "monday", 540,
        ) is True

    def test_inside_block_matching_activity_planned(
        self, conn: sqlite3.Connection
    ) -> None:
        from kalsangati.niyam import get_active
        self._make_niyam_mon_9_11(conn, activity="act")
        niyam = get_active(conn)
        assert is_session_unplanned_under(
            niyam, "act", "monday", 600,
        ) is False

    def test_inside_block_different_activity_unplanned(
        self, conn: sqlite3.Connection
    ) -> None:
        from kalsangati.niyam import get_active
        self._make_niyam_mon_9_11(conn, activity="act")
        niyam = get_active(conn)
        # Session is at 10:00, block is for "act", session activity is
        # "other" — unplanned.
        assert is_session_unplanned_under(
            niyam, "other", "monday", 600,
        ) is True

    def test_outside_block_unplanned(
        self, conn: sqlite3.Connection
    ) -> None:
        from kalsangati.niyam import get_active
        self._make_niyam_mon_9_11(conn, activity="act")
        niyam = get_active(conn)
        # 14:00 is outside the 09:00–11:00 block
        assert is_session_unplanned_under(
            niyam, "act", "monday", 840,
        ) is True

    def test_block_boundary_end_exclusive(
        self, conn: sqlite3.Connection
    ) -> None:
        """A session starting at exactly end_min is OUTSIDE the block
        (``TimeBlock.contains_minute`` is end-exclusive).
        """
        from kalsangati.niyam import get_active
        self._make_niyam_mon_9_11(conn, activity="act")
        niyam = get_active(conn)
        # start_min=660 (11:00) is the block's end_min — exclusive
        assert is_session_unplanned_under(
            niyam, "act", "monday", 660,
        ) is True


# ── Validation errors ──────────────────────────────────────────────────


class TestSessionValidation:
    def _start(self) -> datetime:
        return datetime(2026, 4, 20, 9, 0, 0)

    def test_end_equal_to_start_raises_invalid_bounds(
        self, conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(InvalidSessionBoundsError):
            commit_stopwatch_session(
                conn, "act", self._start(), self._start(),
            )

    def test_end_before_start_raises_invalid_bounds(
        self, conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(InvalidSessionBoundsError):
            commit_stopwatch_session(
                conn, "act",
                self._start(),
                self._start() - timedelta(seconds=5),
            )

    def test_below_min_duration_raises_too_short(
        self, conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(SessionTooShortError):
            commit_stopwatch_session(
                conn, "act",
                self._start(),
                self._start() + timedelta(milliseconds=500),
            )

    def test_validation_errors_inherit_kalsangati_error(self) -> None:
        """Presentation layer can catch by base class."""
        assert issubclass(SessionTooShortError, KalsangatiError)
        assert issubclass(InvalidSessionBoundsError, KalsangatiError)

    def test_db_unchanged_after_validation_failures(
        self, conn: sqlite3.Connection
    ) -> None:
        """Failed validation must not write anything."""
        start = self._start()
        with suppress(InvalidSessionBoundsError):
            commit_stopwatch_session(conn, "act", start, start)
        with suppress(SessionTooShortError):
            commit_stopwatch_session(
                conn, "act", start,
                start + timedelta(milliseconds=100),
            )
        rows = conn.execute(
            "SELECT COUNT(*) FROM kalrekha"
        ).fetchone()[0]
        assert rows == 0

    def test_duration_exactly_min_accepted(
        self, conn: sqlite3.Connection
    ) -> None:
        """The floor is inclusive — duration == min is accepted."""
        start = self._start()
        end = start + timedelta(seconds=MIN_SESSION_SEC)
        r = commit_stopwatch_session(conn, "act", start, end)
        assert r.duration_sec == MIN_SESSION_SEC


# ── New-row commit ─────────────────────────────────────────────────────


class TestCommitNewRow:
    def test_commit_returns_result_dataclass(
        self, conn: sqlite3.Connection
    ) -> None:
        r = commit_stopwatch_session(
            conn, "act",
            datetime(2026, 4, 20, 9, 0, 0),
            datetime(2026, 4, 20, 9, 30, 0),
        )
        assert isinstance(r, CommitResult)
        assert r.extended is False
        assert r.session_id > 0
        assert r.duration_sec == 1800.0

    def test_row_stored_with_correct_fields(
        self, conn: sqlite3.Connection
    ) -> None:
        r = commit_stopwatch_session(
            conn, "01-02-el",
            datetime(2026, 4, 20, 9, 0, 0),
            datetime(2026, 4, 20, 9, 30, 0),
        )
        row = conn.execute(
            "SELECT * FROM kalrekha WHERE id = ?", (r.session_id,),
        ).fetchone()
        assert row["project"] == "01-02-el"
        assert row["date"] == "2026-04-20"
        assert row["start"] == "09:00:00"
        assert row["end"] == "09:30:00"
        assert row["duration_min"] == 30.0
        assert row["source"] == "manual_stopwatch"
        assert row["block_classified"] == 1

    def test_no_niyam_marks_unplanned(
        self, conn: sqlite3.Connection
    ) -> None:
        r = commit_stopwatch_session(
            conn, "act",
            datetime(2026, 4, 20, 9, 0, 0),
            datetime(2026, 4, 20, 9, 30, 0),
        )
        assert r.unplanned is True
        row = conn.execute(
            "SELECT unplanned FROM kalrekha WHERE id = ?",
            (r.session_id,),
        ).fetchone()
        assert row["unplanned"] == 1


# ── Classification — planned vs. unplanned ─────────────────────────────


class TestClassificationAtCommit:
    def _make_active_niyam(
        self, conn: sqlite3.Connection, activity: str = "01-02-el",
    ) -> None:
        n = create_niyam(conn, "t")
        update_blocks(conn, n.id, {
            "monday": [TimeBlock(
                activity=activity, start_min=540, end_min=660,
                duration_h=2.0,
            )],
        })
        set_active(conn, n.id)

    def test_planned_inside_block(
        self, conn: sqlite3.Connection
    ) -> None:
        self._make_active_niyam(conn)
        # 2026-04-20 is a Monday
        r = commit_stopwatch_session(
            conn, "01-02-el",
            datetime(2026, 4, 20, 9, 30, 0),
            datetime(2026, 4, 20, 10, 0, 0),
        )
        assert r.unplanned is False

    def test_unplanned_outside_block(
        self, conn: sqlite3.Connection
    ) -> None:
        self._make_active_niyam(conn)
        r = commit_stopwatch_session(
            conn, "01-02-el",
            datetime(2026, 4, 20, 14, 0, 0),
            datetime(2026, 4, 20, 14, 30, 0),
        )
        assert r.unplanned is True

    def test_unplanned_wrong_activity(
        self, conn: sqlite3.Connection
    ) -> None:
        """Inside a block but activity doesn't match → unplanned."""
        self._make_active_niyam(conn, activity="01-02-el")
        r = commit_stopwatch_session(
            conn, "02-kitchen",  # different from the block
            datetime(2026, 4, 20, 9, 30, 0),
            datetime(2026, 4, 20, 10, 0, 0),
        )
        assert r.unplanned is True


# ── Label resolution ───────────────────────────────────────────────────


class TestRawLabelResolution:
    def test_raw_resolved_to_canonical(
        self, conn: sqlite3.Connection
    ) -> None:
        """If a label_mapping exists, canonical is stored."""
        conn.execute(
            "INSERT INTO label_mappings (raw_label, canonical_label) "
            "VALUES (?, ?)",
            ("01-02 CIS731", "01-02-el"),
        )
        conn.commit()
        r = commit_stopwatch_session(
            conn, "01-02 CIS731",
            datetime(2026, 4, 20, 9, 0, 0),
            datetime(2026, 4, 20, 9, 10, 0),
        )
        row = conn.execute(
            "SELECT project FROM kalrekha WHERE id = ?",
            (r.session_id,),
        ).fetchone()
        assert row["project"] == "01-02-el"

    def test_unmapped_label_stored_as_raw(
        self, conn: sqlite3.Connection
    ) -> None:
        """No mapping → raw label used, session still recorded."""
        r = commit_stopwatch_session(
            conn, "some-new-label",
            datetime(2026, 4, 20, 9, 0, 0),
            datetime(2026, 4, 20, 9, 10, 0),
        )
        row = conn.execute(
            "SELECT project FROM kalrekha WHERE id = ?",
            (r.session_id,),
        ).fetchone()
        assert row["project"] == "some-new-label"


# ── Resume-extend: matching rows ───────────────────────────────────────


class TestResumeExtend:
    def test_within_window_same_activity_extends(
        self, conn: sqlite3.Connection
    ) -> None:
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(conn, "act", s1, e1)
        # 60s gap
        s2 = e1 + timedelta(seconds=60)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(conn, "act", s2, e2)
        assert r2.extended is True
        assert r2.session_id == r1.session_id
        # Combined duration spans s1 → e2
        assert r2.duration_sec == (e2 - s1).total_seconds()

    def test_extend_updates_end_and_duration_min(
        self, conn: sqlite3.Connection
    ) -> None:
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(conn, "act", s1, e1)
        s2 = e1 + timedelta(seconds=30)
        e2 = s2 + timedelta(minutes=5)
        commit_stopwatch_session(conn, "act", s2, e2)
        row = conn.execute(
            'SELECT start, "end", duration_min FROM kalrekha '
            "WHERE id = ?",
            (r1.session_id,),
        ).fetchone()
        assert row["start"] == s1.strftime("%H:%M:%S")
        assert row["end"] == e2.strftime("%H:%M:%S")
        # duration_min covers the full s1 → e2 span
        expected_min = (e2 - s1).total_seconds() / 60.0
        assert abs(row["duration_min"] - expected_min) < 0.01

    def test_gap_zero_extends(
        self, conn: sqlite3.Connection
    ) -> None:
        """Zero-gap resume (immediate restart) is valid."""
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(conn, "act", s1, e1)
        s2 = e1  # zero-gap
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(conn, "act", s2, e2)
        assert r2.extended is True
        assert r2.session_id == r1.session_id

    def test_task_id_none_on_both_sides_extends(
        self, conn: sqlite3.Connection
    ) -> None:
        """Symmetric-None rule: no task on both sides → still extends."""
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(conn, "act", s1, e1, task_id=None)
        s2 = e1 + timedelta(seconds=30)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(conn, "act", s2, e2, task_id=None)
        assert r2.extended is True
        assert r2.session_id == r1.session_id

    def test_same_task_id_extends(
        self, conn: sqlite3.Connection
    ) -> None:
        t = create_task(conn, "Task X", "act")
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(
            conn, "act", s1, e1, task_id=t.id,
        )
        s2 = e1 + timedelta(seconds=30)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(
            conn, "act", s2, e2, task_id=t.id,
        )
        assert r2.extended is True
        assert r2.session_id == r1.session_id


# ── Resume-extend: excluded (new row) ──────────────────────────────────


class TestResumeExcluded:
    def test_different_activity_does_not_extend(
        self, conn: sqlite3.Connection
    ) -> None:
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(conn, "act-a", s1, e1)
        s2 = e1 + timedelta(seconds=30)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(conn, "act-b", s2, e2)
        assert r2.extended is False
        assert r2.session_id != r1.session_id

    def test_different_task_id_does_not_extend(
        self, conn: sqlite3.Connection
    ) -> None:
        t1 = create_task(conn, "Task A", "act")
        t2 = create_task(conn, "Task B", "act")
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        commit_stopwatch_session(conn, "act", s1, e1, task_id=t1.id)
        s2 = e1 + timedelta(seconds=30)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(
            conn, "act", s2, e2, task_id=t2.id,
        )
        assert r2.extended is False

    def test_task_id_asymmetry_does_not_extend(
        self, conn: sqlite3.Connection
    ) -> None:
        """None vs. Some(task) → new row (not symmetric)."""
        t = create_task(conn, "Task X", "act")
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        commit_stopwatch_session(conn, "act", s1, e1, task_id=None)
        s2 = e1 + timedelta(seconds=30)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(
            conn, "act", s2, e2, task_id=t.id,
        )
        assert r2.extended is False

    def test_midnight_crossing_does_not_extend(
        self, conn: sqlite3.Connection
    ) -> None:
        """Same activity, same task, gap < 120s, but different date.
        Must NOT extend — the date-boundary rule is absolute.
        """
        s1 = datetime(2026, 4, 20, 23, 59, 0)
        e1 = datetime(2026, 4, 20, 23, 59, 30)
        commit_stopwatch_session(conn, "act", s1, e1)
        # 60s gap, but crosses midnight
        s2 = datetime(2026, 4, 21, 0, 0, 30)
        e2 = datetime(2026, 4, 21, 0, 1, 0)
        r2 = commit_stopwatch_session(conn, "act", s2, e2)
        assert r2.extended is False
        count = conn.execute(
            "SELECT COUNT(*) FROM kalrekha"
        ).fetchone()[0]
        assert count == 2

    def test_gap_over_window_does_not_extend(
        self, conn: sqlite3.Connection
    ) -> None:
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        commit_stopwatch_session(conn, "act", s1, e1)
        # 121s gap — just past the default 120s window
        s2 = e1 + timedelta(seconds=121)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(conn, "act", s2, e2)
        assert r2.extended is False


# ── Window boundary (the purpose of the kw-only parameter) ─────────────


class TestWindowBoundary:
    def test_gap_exactly_at_window_extends(
        self, conn: sqlite3.Connection
    ) -> None:
        """Boundary inclusive: gap == resume_window_sec still extends."""
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(conn, "act", s1, e1)
        s2 = e1 + timedelta(seconds=RESUME_WINDOW_SEC)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(conn, "act", s2, e2)
        assert r2.extended is True
        assert r2.session_id == r1.session_id

    def test_custom_window_short(
        self, conn: sqlite3.Connection
    ) -> None:
        """A test-time override of resume_window_sec works: a 10s gap
        does not extend under a 5s window.
        """
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        commit_stopwatch_session(conn, "act", s1, e1)
        s2 = e1 + timedelta(seconds=10)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(
            conn, "act", s2, e2, resume_window_sec=5.0,
        )
        assert r2.extended is False

    def test_custom_min_session_rejects_lower(
        self, conn: sqlite3.Connection
    ) -> None:
        """A test-time override of min_session_sec flips the
        accept/reject line."""
        start = datetime(2026, 4, 20, 9, 0, 0)
        end = start + timedelta(seconds=5)
        # Default accepts 5s.  A 10s floor rejects.
        with pytest.raises(SessionTooShortError):
            commit_stopwatch_session(
                conn, "act", start, end, min_session_sec=10.0,
            )


# ── Override reason semantics on resume-extend ─────────────────────────


class TestOverrideReasonOnExtend:
    def test_none_preserves_stored_reason(
        self, conn: sqlite3.Connection
    ) -> None:
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(
            conn, "act", s1, e1, override_reason="original",
        )
        s2 = e1 + timedelta(seconds=30)
        e2 = s2 + timedelta(minutes=1)
        commit_stopwatch_session(
            conn, "act", s2, e2, override_reason=None,
        )
        row = conn.execute(
            "SELECT override_reason FROM kalrekha WHERE id = ?",
            (r1.session_id,),
        ).fetchone()
        assert row["override_reason"] == "original"

    def test_non_none_overwrites_stored_reason(
        self, conn: sqlite3.Connection
    ) -> None:
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 10, 0)
        r1 = commit_stopwatch_session(
            conn, "act", s1, e1, override_reason="original",
        )
        s2 = e1 + timedelta(seconds=30)
        e2 = s2 + timedelta(minutes=1)
        commit_stopwatch_session(
            conn, "act", s2, e2, override_reason="superseded",
        )
        row = conn.execute(
            "SELECT override_reason FROM kalrekha WHERE id = ?",
            (r1.session_id,),
        ).fetchone()
        assert row["override_reason"] == "superseded"

    def test_two_overwrites_in_a_row(
        self, conn: sqlite3.Connection
    ) -> None:
        """Second overwrite wins over first — preserve is only
        vs. None."""
        s1 = datetime(2026, 4, 20, 9, 0, 0)
        e1 = datetime(2026, 4, 20, 9, 5, 0)
        r1 = commit_stopwatch_session(
            conn, "act", s1, e1, override_reason="first",
        )
        s2 = e1 + timedelta(seconds=10)
        e2 = s2 + timedelta(minutes=1)
        commit_stopwatch_session(
            conn, "act", s2, e2, override_reason="second",
        )
        s3 = e2 + timedelta(seconds=10)
        e3 = s3 + timedelta(minutes=1)
        commit_stopwatch_session(
            conn, "act", s3, e3, override_reason="third",
        )
        row = conn.execute(
            "SELECT override_reason FROM kalrekha WHERE id = ?",
            (r1.session_id,),
        ).fetchone()
        assert row["override_reason"] == "third"


# ── Classification on resume-extend (per Flag 3) ───────────────────────


class TestClassificationNotRecomputedOnExtend:
    def test_unplanned_preserved_on_extend(
        self, conn: sqlite3.Connection
    ) -> None:
        """Original row was classified unplanned; resume-extend
        MUST NOT recompute against the new start.
        """
        n = create_niyam(conn, "t")
        update_blocks(conn, n.id, {
            "monday": [TimeBlock(
                activity="act", start_min=540, end_min=660,
                duration_h=2.0,
            )],
        })
        set_active(conn, n.id)
        # Start at 14:00 — outside block, unplanned
        s1 = datetime(2026, 4, 20, 14, 0, 0)
        e1 = datetime(2026, 4, 20, 14, 5, 0)
        r1 = commit_stopwatch_session(conn, "act", s1, e1)
        assert r1.unplanned is True
        # Resume-extend — CommitResult.unplanned reflects stored value
        s2 = e1 + timedelta(seconds=60)
        e2 = s2 + timedelta(minutes=5)
        r2 = commit_stopwatch_session(conn, "act", s2, e2)
        assert r2.extended is True
        assert r2.unplanned is True
        # And the DB row's flag is still 1
        row = conn.execute(
            "SELECT unplanned FROM kalrekha WHERE id = ?",
            (r1.session_id,),
        ).fetchone()
        assert row["unplanned"] == 1
