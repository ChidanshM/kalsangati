"""Run the embedded API on a daemon thread.

Principle 4: *the embedded server starts and dies with the main process.
Daemon thread. No external lifecycle management.*  There is no stop
function here on purpose \u2014 the thread is a daemon, so it goes when the
process goes, and a graceful-shutdown path would be machinery with no
caller.

Three conditions were attached when the fourth background thread was
approved (pitfall #13 requires review for any new one):

1. **``daemon=True``.**  A non-daemon thread keeps the process alive
   after the last window closes, which presents as \"the app will not
   quit\" and is miserable to diagnose.
2. **``@safe_thread``.**  Pitfall #23.  An unhandled exception in a
   thread target dies silently; a silently dead API is worse than an
   absent one, because a caller hangs rather than failing.
3. **Its own database connection**, when a real endpoint eventually
   needs one.  ``sqlite3.Connection`` objects may not cross threads.
   Nothing here touches the database yet.

**A failed bind must not stop the application from starting.**  Same
rule as ``logging_config``: a local-first tracker does not refuse to run
because a port is busy.  The failure is logged, a handle marked
not-running comes back, and the desktop app works exactly as it did
before this module existed.

**Bound to 127.0.0.1 only**, never ``0.0.0.0``.  The port must not be
reachable from the network.
"""

from __future__ import annotations

import logging
import secrets
import socket
import threading
from dataclasses import dataclass

import uvicorn

from kalsangati import __version__
from kalsangati.infrastructure.api import create_app, set_token
from kalsangati.infrastructure.threads import safe_thread

logger = logging.getLogger(__name__)

DEFAULT_PORT = 24570
_HOST = "127.0.0.1"


@dataclass(slots=True)
class ServerHandle:
    """What the caller needs in order to talk to the embedded server.

    Attributes:
        running: ``False`` when the port could not be bound.  Everything
            else is still populated so a caller can log or display it.
        host: Always ``127.0.0.1``.
        port: The bound port.
        token: The shared secret every request must present.  Held in
            memory only; never written to disk.
    """

    running: bool
    host: str
    port: int
    token: str

    @property
    def base_url(self) -> str:
        """Where a frontend should point itself."""
        return f"http://{self.host}:{self.port}"


def _port_is_free(host: str, port: int) -> bool:
    """Check the port before uvicorn does.

    uvicorn calls ``sys.exit`` on a bind failure rather than raising,
    which inside a thread would kill the thread with no useful error.
    Checking first turns that into a value we can return.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def start_embedded_server(*, port: int = DEFAULT_PORT) -> ServerHandle:
    """Start the API on a daemon thread and return its handle.

    Never raises for an unavailable port: a busy port yields a handle
    with ``running=False`` and a logged warning.

    Args:
        port: TCP port to bind on the loopback interface.

    Returns:
        A :class:`ServerHandle`.  Check ``running`` before relying on it.
    """
    token = secrets.token_urlsafe(32)
    set_token(token)

    if not _port_is_free(_HOST, port):
        logger.warning(
            "Embedded API not started: port %s is already in use. "
            "The desktop application is unaffected.",
            port,
        )
        return ServerHandle(
            running=False, host=_HOST, port=port, token=token
        )

    app = create_app(__version__)
    config = uvicorn.Config(
        app,
        host=_HOST,
        port=port,
        log_level="warning",   # access logs would flood the app log
        access_log=False,
    )
    server = uvicorn.Server(config)

    @safe_thread
    def _run() -> None:
        server.run()

    thread = threading.Thread(
        target=_run, name="kalsangati-api", daemon=True
    )
    thread.start()

    logger.info("Embedded API listening on %s:%s", _HOST, port)
    return ServerHandle(running=True, host=_HOST, port=port, token=token)
