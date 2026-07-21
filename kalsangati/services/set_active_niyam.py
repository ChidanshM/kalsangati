"""Activate a Niyam as the current blueprint schedule.

Third service in the six-service plan (see ``SKILL-state.md §9``).
Unlike the first two services — which pulled significant logic out of
the GUI and the ingest pipeline respectively — this one is deliberately
small.  It exists to establish the "atomic small service" shape that
services #4–6 will follow: a validation step, a domain operation, a
structured result, and a presentation-layer exception pattern.

What the service does, in order:

1. Validate that the target Niyam exists (raises
   :class:`kalsangati.exceptions.NiyamNotFoundError` if not).
2. Capture the currently-active Niyam's id (if any) before mutating.
3. Detect the already-active case — activating the already-active
   Niyam is a no-op, not an error.  The call succeeds and the result
   carries ``was_already_active=True`` for consumers that want to
   distinguish real activations from no-ops.
4. Execute the atomic two-step UPDATE (deactivate all, then activate
   the target) inside a single transaction.  Delegates to
   :func:`kalsangati.niyam.set_active` — the core function still owns
   the SQL; the service adds the validation wrapper and the result
   shape.

Design notes:

* Idempotent by design.  Re-activating the already-active Niyam
  succeeds silently.  This was a scope decision at the Unit 6 review —
  raising would force callers to pre-check, and activation is the kind
  of operation where "already in the desired state" is a trivially-
  satisfied precondition, not an error.
* No ``AppSignals`` emission yet.  The GUI emits its own
  ``niyam_changed`` signal after a successful call.  Signal unification
  at the service layer is deferred until the signal-consumer count
  justifies the abstraction (§14).
* The ``previous_active_id`` field on the result is informational —
  useful for undo flows and for audit logging when those arrive.  It is
  ``None`` when no Niyam was active before the call, or when the target
  itself was already active (in which case ``was_already_active`` is
  the signal, not ``previous_active_id``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from kalsangati.exceptions import NiyamNotFoundError
from kalsangati.niyam import get_active, get_by_id, set_active

# ── Result type ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class SetActiveResult:
    """Outcome of a :func:`set_active_niyam` call.

    Attributes:
        niyam_id: Id of the Niyam that is now active.  Always set to
            the id passed into the service (echoed back for consumer
            convenience).
        previous_active_id: Id of the Niyam that was active before the
            call.  ``None`` when no Niyam was active beforehand, or
            when the target itself was already active (see
            ``was_already_active``).
        was_already_active: ``True`` when the target Niyam was already
            the active one at the moment of the call.  In that case
            no UPDATE was issued and ``previous_active_id`` is
            ``None``.  Callers that want to suppress user-facing
            feedback on no-op activations can branch on this flag.
    """

    niyam_id: int
    previous_active_id: int | None
    was_already_active: bool


# ── Public service entry point ──────────────────────────────────────────


def set_active_niyam(
    conn: sqlite3.Connection,
    niyam_id: int,
) -> SetActiveResult:
    """Activate a Niyam as the current blueprint schedule.

    Idempotent: activating the already-active Niyam succeeds without
    mutation and returns a result with ``was_already_active=True``.

    Args:
        conn: Database connection.
        niyam_id: Id of the Niyam to activate.

    Returns:
        A :class:`SetActiveResult` describing the outcome.

    Raises:
        NiyamNotFoundError: If no Niyam exists with the given id.
    """
    # 1. Validate existence.
    target = get_by_id(conn, niyam_id)
    if target is None:
        raise NiyamNotFoundError(
            f"No Niyam found with id {niyam_id}"
        )

    # 2. Already-active short-circuit.  The core `set_active` function
    # would also produce a correct end state here, but issuing the
    # UPDATE statements unnecessarily is wasteful and would obscure
    # the "nothing changed" signal in the result.
    if target.is_active:
        return SetActiveResult(
            niyam_id=niyam_id,
            previous_active_id=None,
            was_already_active=True,
        )

    # 3. Capture the previously-active Niyam (if any) before mutating.
    current = get_active(conn)
    previous_id = current.id if current is not None else None

    # 4. Delegate the SQL to the core function.  It already runs the
    # two-step UPDATE in a transaction and deactivates all other rows.
    set_active(conn, niyam_id)

    return SetActiveResult(
        niyam_id=niyam_id,
        previous_active_id=previous_id,
        was_already_active=False,
    )
