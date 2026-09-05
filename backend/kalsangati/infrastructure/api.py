"""FastAPI application for the embedded read API.

The desktop process owns the data; this is infrastructure it provides,
not a host it depends on (principle 1).  The app runs on a daemon thread
inside the same process \u2014 see :mod:`kalsangati.infrastructure.server`.

**One endpoint in this unit.**  ``GET /api/health`` is the version
handshake named in the split hooks: a frontend validates it on startup
and refuses to run against an incompatible backend.  No domain
endpoints, no writes.

Design notes:

* **``api_version`` is separate from the application version.**  Tying
  them together would force a frontend release for every patch bump.
  It is a string, not an integer, so a future ``"2.1"`` needs no schema
  change on the frontend's comparison.

* **Every route requires a shared-secret token.**  Generated at startup,
  held in memory, never written to disk.  This is not real security:
  anything that can read the process can read the token.  It guards
  against *other local processes* stumbling onto the port, which is the
  actual risk on a single-user machine.

* **Writes.**  ``SKILL-core.md`` \u00a74 permits an API write only when it is
  enumerated in that table, goes through a service, and holds the shared
  write lock.  Nothing here writes, so the table is unchanged.

* **Database access.**  A ``sqlite3.Connection`` may not be shared
  across threads.  When the first real endpoint arrives it must open its
  own connection rather than borrow the desktop process's.  Recorded
  here now rather than discovered then.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status

# The contract version a frontend checks on startup.  Bump when a
# response shape changes in a way that would break a caller.
API_VERSION = "1"

_TOKEN_HEADER = "X-Kalsangati-Token"

# Set once by the server module before the app serves anything.  Module
# state rather than a parameter because FastAPI dependencies are
# resolved per-request and cannot easily close over startup values.
_expected_token: str | None = None


def set_token(token: str) -> None:
    """Install the shared secret every request must present."""
    global _expected_token
    _expected_token = token


def _require_token(
    x_kalsangati_token: str | None = Header(default=None),
) -> None:
    """Reject any request without the current shared secret.

    Returns 401 for both a missing and a wrong token, deliberately: the
    two cases are not worth distinguishing to a caller, and doing so
    tells a prober which half they got right.

    Raises:
        HTTPException: 401 when the token is absent or incorrect.
    """
    if _expected_token is None or x_kalsangati_token != _expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token.",
        )


def create_app(app_version: str) -> FastAPI:
    """Build the FastAPI application.

    A factory rather than a module-level singleton so tests can build an
    app without touching the server thread or a real port.

    Args:
        app_version: The package version, reported alongside the API
            contract version.

    Returns:
        A configured FastAPI instance.
    """
    app = FastAPI(
        title="K\u0101lsangati",
        version=app_version,
        docs_url=None,      # no interactive docs on a local-only port
        redoc_url=None,
    )

    @app.get("/api/health", dependencies=[Depends(_require_token)])
    def health() -> dict[str, Any]:
        """Liveness and version handshake."""
        return {
            "status": "ok",
            "api_version": API_VERSION,
            "app_version": app_version,
        }

    return app
