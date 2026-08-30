"""Infrastructure layer — cross-cutting concerns, no domain logic.

Holds the things the application needs in order to *run* rather than in
order to *mean* anything: logging configuration, thread safety helpers,
and (from Phase 2) the AppSignals bridge, the FastAPI app, and the
embedded server.

Like `core/`, `persistence/`, and `services/`, this package must never
import PyQt5 or any other GUI toolkit — the backend has to stay
installable without a frontend (``SKILL-state.md`` pitfall #18).

Per the Shape D plan (``SKILL-state.md`` §13) this package eventually
lives at ``backend/kalsangati/infrastructure/``.  It is created at the
current flat level now and moves with the rest of the tree when the
reorganization happens; spawning ``backend/`` for one package would be
a half-done reorg.
"""

from __future__ import annotations
