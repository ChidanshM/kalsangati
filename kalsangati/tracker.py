"""Cross-platform active window tracker (future).

Platform abstraction layer for detecting the currently active window and
mapping it to a canonical activity via the label system.

Status: Linux primary (ewmh + Xlib), Windows/macOS stubs for future.
"""

from __future__ import annotations

import logging
import platform
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """Information about the currently focused window."""

    title: str
    app_name: str
    pid: int


def get_active_window() -> WindowInfo | None:
    """Detect the currently active window.

    Returns:
        A WindowInfo instance, or None if detection fails.
    """
    system = platform.system()
    if system == "Linux":
        return _get_active_window_linux()
    elif system == "Windows":
        return _get_active_window_windows()
    elif system == "Darwin":
        return _get_active_window_macos()
    else:
        logger.warning("Unsupported platform: %s", system)
        return None


def _get_active_window_linux() -> WindowInfo | None:
    """Linux implementation using ewmh + Xlib."""
    try:
        from ewmh import EWMH  # type: ignore[import-untyped]

        wm = EWMH()
        win = wm.getActiveWindow()
        if win is None:
            return None
        title = wm.getWmName(win) or ""
        pid = wm.getWmPid(win) or 0
        wm_class = win.get_wm_class()
        app_name = wm_class[1] if wm_class else ""
        return WindowInfo(title=title, app_name=app_name, pid=pid)
    except ImportError:
        logger.info("ewmh not installed; window tracking unavailable")
        return None
    except Exception as e:
        logger.debug("Failed to get active window: %s", e)
        return None


def _get_active_window_windows() -> WindowInfo | None:
    """Windows implementation (future: pygetwindow)."""
    logger.info("Windows window tracking not yet implemented")
    return None


def _get_active_window_macos() -> WindowInfo | None:
    """macOS implementation (future: AppKit)."""
    logger.info("macOS window tracking not yet implemented")
    return None


def map_window_to_activity(
    conn: sqlite3.Connection, window: WindowInfo
) -> str | None:
    """Resolve a window to a canonical activity via label mappings.

    Checks the app_name and title against label_mappings.

    Args:
        conn: Database connection.
        window: Active window info.

    Returns:
        Canonical activity name, or None if no mapping matches.
    """
    from kalsangati.labels import resolve_label

    # Try app_name first, then title
    result = resolve_label(conn, window.app_name)
    if result:
        return result
    return resolve_label(conn, window.title)
