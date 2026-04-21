"""Schedule notifications — desktop alerts before Niyam blocks.

Runs a background thread that checks the active Niyam every minute and
fires a desktop notification at a configurable lead time before each
block starts.  Uses ``plyer`` for cross-platform delivery.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime

from kalsangati.db import get_setting
from kalsangati.niyam import Niyam, TimeBlock, get_active

logger = logging.getLogger(__name__)

# ── Notification backend ────────────────────────────────────────────────

_NOTIFY_FN: Callable[[str, str], None] | None = None


def _default_notify(title: str, message: str) -> None:
    """Send a desktop notification via plyer."""
    try:
        from plyer import notification as plyer_notification

        plyer_notification.notify(
            title=title,
            message=message,
            app_name="Kālsangati",
            timeout=10,
        )
    except Exception:
        logger.warning("Failed to send notification: %s — %s", title, message)


def set_notify_backend(fn: Callable[[str, str], None]) -> None:
    """Override the notification delivery function (useful for testing).

    Args:
        fn: A callable accepting (title, message) strings.
    """
    global _NOTIFY_FN
    _NOTIFY_FN = fn


def _notify(title: str, message: str) -> None:
    """Dispatch a notification through the configured backend."""
    fn = _NOTIFY_FN or _default_notify
    fn(title, message)


# ── Scheduler logic ─────────────────────────────────────────────────────


def _next_blocks(
    niyam: Niyam,
    now: datetime,
    lead_minutes: int,
) -> list[TimeBlock]:
    """Find blocks starting within the lead window from *now*.

    Args:
        niyam: Active Niyam.
        now: Current datetime.
        lead_minutes: Minutes before block start to trigger.

    Returns:
        List of TimeBlock objects starting within the window.
    """
    day_names = ("monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday")
    day = day_names[now.weekday()]
    current_min = now.hour * 60 + now.minute
    lead_min = current_min + lead_minutes

    results: list[TimeBlock] = []
    for block in niyam.blocks_for_day(day):
        if current_min <= block.start_min <= lead_min:
            results.append(block)
    return results


def _count_tasks_for_activity(
    conn: sqlite3.Connection, activity: str
) -> int:
    """Count active tasks for an activity."""
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM tasks "
        "WHERE canonical_activity = ? AND status IN ('this_week', 'in_progress')",
        (activity,),
    ).fetchone()
    return row["cnt"] if row else 0


def _is_stopwatch_tracking(
    conn: sqlite3.Connection, activity: str
) -> bool:
    """Check if the stopwatch is currently tracking the given activity.

    This is a best-effort check — looks for an open session (no end time)
    for this activity.  A real implementation would check in-memory state.
    """
    # Placeholder: in production this queries the stopwatch widget state
    return False


class NotificationScheduler:
    """Background thread that fires pre-block desktop notifications.

    Args:
        conn_factory: Callable returning a new sqlite3.Connection
            (each thread needs its own connection).
        poll_interval: Seconds between checks (default 60).
    """

    def __init__(
        self,
        conn_factory: Callable[[], sqlite3.Connection],
        poll_interval: int = 60,
    ) -> None:
        self._conn_factory = conn_factory
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._notified: set[str] = set()  # track already-fired block keys

    def start(self) -> None:
        """Start the background scheduler thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._notified.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="kalsangati-notify"
        )
        self._thread.start()
        logger.info("Notification scheduler started")

    def stop(self) -> None:
        """Signal the scheduler to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Notification scheduler stopped")

    @property
    def is_running(self) -> bool:
        """Whether the scheduler thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        """Main scheduler loop."""
        conn = self._conn_factory()
        try:
            while not self._stop_event.is_set():
                self._check_and_notify(conn)
                self._stop_event.wait(self._poll_interval)
        finally:
            conn.close()

    def _check_and_notify(self, conn: sqlite3.Connection) -> None:
        """Check for upcoming blocks and fire notifications."""
        enabled = get_setting(conn, "notifications_enabled")
        if enabled and enabled.lower() != "true":
            return

        lead_str = get_setting(conn, "notify_lead_minutes") or "5"
        try:
            lead_minutes = int(lead_str)
        except ValueError:
            lead_minutes = 5

        niyam = get_active(conn)
        if niyam is None:
            return

        now = datetime.now()
        upcoming = _next_blocks(niyam, now, lead_minutes)

        for block in upcoming:
            key = f"{now.strftime('%Y-%m-%d')}:{block.activity}:{block.start}"
            if key in self._notified:
                continue

            # Suppress if already tracking this activity
            if _is_stopwatch_tracking(conn, block.activity):
                self._notified.add(key)
                continue

            task_count = _count_tasks_for_activity(conn, block.activity)
            title = f"Upcoming: {block.activity}"
            msg = (
                f"Starts at {block.start} ({block.duration_h}h)"
                f"\n{task_count} task{'s' if task_count != 1 else ''} queued"
            )
            _notify(title, msg)
            self._notified.add(key)
            logger.info("Notified: %s at %s", block.activity, block.start)

        # Reset notified set at midnight
        if now.hour == 0 and now.minute == 0:
            self._notified.clear()
