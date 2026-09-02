"""Persistence layer — schema, migrations, and connection management.

Holds ``db.py``: the SQLite schema DDL, the forward-only migration
runner, the connection factory, and the ``transaction`` savepoint
context manager.

This package is a leaf.  It imports nothing from ``core``, ``services``,
``infrastructure``, or ``gui``, and that direction is load-bearing — the
layer split above it only holds if the bottom stays dependency-free.

``SKILL-state.md`` §13 lists this package as "db, migrations, schema".
``db.py`` is deliberately unsplit for now: separating its contents in
the same commit that rewrote every import in the tree would have made a
red gate unreadable.  Split it when it earns it.

Like ``core``, ``services``, and ``infrastructure``, this package must
never import PyQt5 or any other GUI toolkit (``SKILL-state.md``
pitfall #18).
"""

from __future__ import annotations
