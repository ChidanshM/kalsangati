"""Core domain layer — the modules that give Kālsangati its meaning.

Niyam (blueprint schedules), Kālrekhā ingest, label resolution,
analytics, Vimarśa reflection, tasks, projects, and the domain
exception hierarchy.

Every module here receives its ``sqlite3.Connection`` by parameter and
never opens one itself (``SKILL-core.md`` §5).  Core depends downward on
``persistence`` for the ``transaction`` savepoint helper, the settings
accessors, and the ``time_blocks`` JSON codec; ``persistence`` must
never import from here.

Like ``persistence``, ``services``, and ``infrastructure``, this package
must never import PyQt5 or any other GUI toolkit — the backend has to
stay installable without a frontend (``SKILL-state.md`` pitfall #18).
"""

from __future__ import annotations
