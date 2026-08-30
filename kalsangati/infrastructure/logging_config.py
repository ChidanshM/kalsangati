"""Application-wide logging configuration.

Called exactly once, as early as possible in startup, before anything
else runs — see :func:`setup_logging`.  Every other module in the
project just does ``logger = logging.getLogger(__name__)`` at module
level and never touches handlers or levels itself.

Design per ``SKILL-state.md`` §10:

* A rotating file handler under the platform's standard log location
  (``platformdirs``): ``~/.local/share/kalsangati/logs/app.log`` on
  Linux, ``%APPDATA%/Kalsangati/logs`` on Windows,
  ``~/Library/Logs/Kalsangati`` on macOS.  10 MB per file, 5 backups.
* A console handler at WARNING, so a normal run stays quiet on stderr
  while the file keeps the full trail.
* ``KALSANGATI_LOG_LEVEL`` lowers (or raises) both handlers for a
  debugging session without a code change.

Two properties worth stating explicitly, because both are load-bearing:

* **Idempotent.** A second call is a no-op unless ``force=True``.
  Configuring logging twice would double every handler and therefore
  every log line; the guard makes the "called once at startup" rule
  safe rather than merely intended.
* **Never fatal.** If the log directory cannot be created or opened
  (read-only filesystem, permissions, a full disk), the failure is
  reported on the console handler and the application continues with
  console-only logging.  Losing the log trail is bad; refusing to
  start a local-first time tracker because of it is worse.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from platformdirs import user_log_path

# ── Configuration constants ─────────────────────────────────────────────

_APP_NAME = "kalsangati"
_LOG_FILENAME = "app.log"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_ENV_LEVEL = "KALSANGATI_LOG_LEVEL"

_DEFAULT_FILE_LEVEL = logging.INFO
_DEFAULT_CONSOLE_LEVEL = logging.WARNING

# Third-party loggers that are too chatty at INFO.  uvicorn is listed
# ahead of its arrival in Phase 2 — naming a logger that does not exist
# yet is harmless (``getLogger`` creates it on demand) and means the
# FastAPI unit does not have to remember to come back here.
_NOISY_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")

# Module-level guard for the idempotency property described above.
_configured = False


# ── Internals ───────────────────────────────────────────────────────────


def _parse_level(raw: str) -> int | None:
    """Turn a ``KALSANGATI_LOG_LEVEL`` value into a logging level.

    Accepts level names (case-insensitive, e.g. ``debug``, ``WARNING``)
    and numeric strings.  Returns ``None`` for anything unrecognised so
    the caller can fall back to defaults rather than crash on a typo in
    an environment variable.

    Args:
        raw: The raw environment-variable value.

    Returns:
        A logging level int, or ``None`` if the value is not valid.
    """
    candidate = raw.strip().upper()
    if not candidate:
        return None
    if candidate.isdigit():
        return int(candidate)
    resolved = logging.getLevelName(candidate)
    # getLevelName returns the string "Level {name}" for unknown names.
    return resolved if isinstance(resolved, int) else None


def log_directory() -> Path:
    """Return the platform-appropriate directory for log files.

    Does not create the directory — :func:`setup_logging` does that, so
    that callers who only want to *report* the path (a Settings screen,
    a support ticket, a CLI ``--where-are-my-logs``) have no side
    effect.

    Returns:
        The directory that holds ``app.log`` and its rotations.
    """
    return user_log_path(_APP_NAME, appauthor=False, ensure_exists=False)


def _build_file_handler(
    log_dir: Path, level: int
) -> logging.handlers.RotatingFileHandler:
    """Create the rotating file handler, creating the directory first.

    Args:
        log_dir: Directory to hold the log files.
        level: Level for this handler.

    Returns:
        A configured rotating file handler.

    Raises:
        OSError: If the directory or file cannot be created.  The caller
            is expected to degrade to console-only rather than
            propagate this.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / _LOG_FILENAME,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


# ── Public entry point ──────────────────────────────────────────────────


def setup_logging(
    *,
    log_dir: Path | None = None,
    force: bool = False,
) -> Path | None:
    """Configure application-wide logging.  Call once, at startup.

    Installs a rotating file handler and a console handler on the root
    logger.  Safe to call more than once: subsequent calls return
    immediately unless ``force=True``.

    Args:
        log_dir: Override the log directory.  Intended for tests
            (``tmp_path``); production callers should omit it and let
            :func:`log_directory` pick the platform location.
        force: Reconfigure even if logging was already set up, removing
            any handlers this function previously installed.  Intended
            for tests.

    Returns:
        The path to the active log file, or ``None`` when file logging
        could not be set up and the application is running with
        console-only logging.
    """
    global _configured

    if _configured and not force:
        return _active_log_path()

    root = logging.getLogger()

    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()

    env_raw = os.environ.get(_ENV_LEVEL)
    env_level = _parse_level(env_raw) if env_raw else None

    file_level = env_level if env_level is not None else _DEFAULT_FILE_LEVEL
    console_level = (
        env_level if env_level is not None else _DEFAULT_CONSOLE_LEVEL
    )

    # The root logger must sit at the most permissive of the two, or it
    # would filter records before any handler saw them.
    root.setLevel(min(file_level, console_level))

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    target_dir = log_dir if log_dir is not None else log_directory()
    log_path: Path | None = None
    try:
        file_handler = _build_file_handler(target_dir, file_level)
    except OSError:
        # Console handler is already attached, so this is visible.
        root.warning(
            "Could not open the log file in %s — continuing with "
            "console logging only.",
            target_dir,
            exc_info=True,
        )
    else:
        root.addHandler(file_handler)
        log_path = Path(file_handler.baseFilename)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    return log_path


def _active_log_path() -> Path | None:
    """Return the file path of the installed file handler, if any."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            return Path(handler.baseFilename)
    return None
