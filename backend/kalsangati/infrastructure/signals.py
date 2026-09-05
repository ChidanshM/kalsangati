"""AppSignals — the one bridge between layers.

Principle 5 of the hybrid architecture: state changes flow through
signals, not shared mutable state.  A screen that needs to know
something changed elsewhere subscribes here rather than holding a
reference to the screen that changed it.

The alternative, connecting screens to each other through the main
window, works for two screens and multiplies with every screen after
that: each new consumer means editing the producer.  A bus means
neither side knows the other exists.

**This module is the single sanctioned PyQt5 import outside ``gui/``.**

Pitfall #18 forbids GUI-toolkit imports in ``core``, ``persistence``,
``services`` and ``infrastructure``, so that the backend stays
installable without a frontend.  ``QObject`` and ``pyqtSignal`` break
that here, deliberately: the architecture (§13) rosters ``signals.py``
under ``infrastructure/`` and principle 5 names AppSignals as the
bridge.  Two consequences worth stating rather than discovering:

* The carve-out is **this file only**.  Nothing else in those four
  packages may import PyQt5.
* It **weakens** the backend-without-frontend property.  A backend-only
  install would still pull in PyQt5 for this module alone.  When that
  install path actually exists (§14), the fix is a protocol here and a
  Qt adapter in ``gui/`` — not now, while there is one consumer and no
  backend-only install to be broken.

Emission currently comes from ``gui/``, not from the service layer.
Services stay PyQt5-free; moving emission into them is a separate
decision, and the deferred signals in §14 (``ingest_complete``,
``task_status_changed``, ``task_scheduled``, ``task_reparented``) wait
on it.
"""

from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal


class AppSignals(QObject):
    """Application-wide signals.

    Attributes:
        niyam_changed: The active Niyam changed, or its blocks were
            edited.  Anything displaying activities or prescribed time
            should reload.  Carries no payload on purpose \u2014 a consumer
            that needs detail should re-read the database rather than
            trust what a producer chose to pass along.
    """

    niyam_changed = pyqtSignal()


_instance: AppSignals | None = None


def app_signals() -> AppSignals:
    """Return the process-wide :class:`AppSignals` instance.

    A module-level singleton rather than something passed down the
    widget tree: a bus that only some screens can reach is not a bus.
    Constructed lazily so importing this module does not require a
    ``QApplication``.
    """
    global _instance
    if _instance is None:
        _instance = AppSignals()
    return _instance
