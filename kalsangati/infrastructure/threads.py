"""Thread-safety helpers.

Python's :class:`threading.Thread` swallows exceptions raised in a
thread target: the thread dies, nothing is printed to a logger, and the
rest of the process carries on unaware.  In a desktop app with
background workers this is the worst possible failure mode — the
notification scheduler simply stops notifying and the user's first clue
is that nothing has fired for a week (``SKILL-state.md`` §17 E9).

:func:`safe_thread` converts silent death into a logged stack trace.
Per pitfall #23, every ``threading.Thread`` target in the project is
wrapped with it.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def safe_thread(fn: Callable[P, R]) -> Callable[P, R | None]:
    """Log any unhandled exception raised by a thread target.

    Wrap every ``threading.Thread(target=…)`` callable with this.  The
    wrapped function returns ``None`` instead of raising when it fails,
    which is why the return type widens to ``R | None``: a thread
    target's return value is discarded by :class:`threading.Thread`
    anyway, so nothing downstream depends on it.

    The exception is logged against the *wrapped function's* module, not
    this one, so the log line points at the code that actually failed.

    Note what this does and does not buy:

    * It guarantees a failure leaves a trace.
    * It does **not** keep a loop running.  A target that raises still
      stops — it just stops loudly.  A worker that should survive a
      transient error needs its own ``try``/``except`` around the body
      of its loop, with this decorator as the outer net for everything
      that guard does not catch (see
      :meth:`kalsangati.notifications.NotificationScheduler._run`).

    Args:
        fn: The thread target to wrap.

    Returns:
        The wrapped callable, returning ``None`` where ``fn`` raised.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | None:
        try:
            return fn(*args, **kwargs)
        except Exception:
            logging.getLogger(fn.__module__).exception(
                "Unhandled exception in thread target %s", fn.__qualname__
            )
            return None

    return wrapper
