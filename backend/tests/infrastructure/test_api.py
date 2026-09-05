"""Tests for the embedded API and its daemon thread.

Split deliberately in two.

The **app** is tested through ``TestClient``, which drives FastAPI
directly with no thread and no port.  Every behavioural property lives
here.

The **server** is tested sparingly, and never by starting uvicorn and
issuing a real request.  That needs a live port, a readiness wait, and a
shutdown that ``daemon=True`` does not provide — three sources of flake
for properties ``TestClient`` already covers.  What remains here are the
things ``TestClient`` cannot see: that the thread is a daemon, that a
busy port degrades rather than raises, and that each start mints a fresh
token.
"""

from __future__ import annotations

import socket
import threading

import pytest
from fastapi.testclient import TestClient

from kalsangati.infrastructure import api as api_module
from kalsangati.infrastructure.api import API_VERSION, create_app, set_token
from kalsangati.infrastructure.server import (
    DEFAULT_PORT,
    ServerHandle,
    start_embedded_server,
)

_TOKEN = "test-token"
_HEADER = "X-Kalsangati-Token"


@pytest.fixture
def client() -> TestClient:
    """An app with a known token installed."""
    set_token(_TOKEN)
    return TestClient(create_app("0.1.0"))


@pytest.fixture(autouse=True)
def _restore_token() -> None:
    """Leave the module-level token as we found it.

    It is process-wide state; a test that changed it would otherwise
    reach every test after it.
    """
    original = api_module._expected_token
    yield
    api_module._expected_token = original


# ── The app ─────────────────────────────────────────────────────────────


class TestHealth:
    def test_returns_ok_with_a_valid_token(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/health", headers={_HEADER: _TOKEN})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_reports_both_versions(self, client: TestClient) -> None:
        """The API contract version is separate from the app version.

        Tying them together would force a frontend release for every
        patch bump.
        """
        body = client.get(
            "/api/health", headers={_HEADER: _TOKEN}
        ).json()
        assert body["api_version"] == API_VERSION
        assert body["app_version"] == "0.1.0"

    def test_api_version_is_a_string(self, client: TestClient) -> None:
        """A future "2.1" needs no change to a frontend's comparison."""
        body = client.get(
            "/api/health", headers={_HEADER: _TOKEN}
        ).json()
        assert isinstance(body["api_version"], str)


class TestTokenAuth:
    def test_missing_token_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/health").status_code == 401

    def test_wrong_token_is_rejected(self, client: TestClient) -> None:
        response = client.get(
            "/api/health", headers={_HEADER: "not-the-token"}
        )
        assert response.status_code == 401

    def test_missing_and_wrong_are_indistinguishable(
        self, client: TestClient
    ) -> None:
        """Telling them apart would report which half a prober got
        right."""
        missing = client.get("/api/health")
        wrong = client.get("/api/health", headers={_HEADER: "nope"})
        assert missing.status_code == wrong.status_code
        assert missing.json() == wrong.json()

    def test_no_token_installed_rejects_everything(self) -> None:
        """A server that never set a token must not be open."""
        api_module._expected_token = None
        response = TestClient(create_app("0.1.0")).get(
            "/api/health", headers={_HEADER: "anything"}
        )
        assert response.status_code == 401


class TestSurface:
    def test_unknown_path_is_404(self, client: TestClient) -> None:
        response = client.get(
            "/api/nonsense", headers={_HEADER: _TOKEN}
        )
        assert response.status_code == 404

    def test_interactive_docs_are_disabled(
        self, client: TestClient
    ) -> None:
        """No docs UI on a local-only port."""
        assert client.get("/docs").status_code == 404

    def test_health_is_the_only_route(self, client: TestClient) -> None:
        """This unit is a skeleton: one endpoint, no domain surface.

        Fails loudly if a later unit adds a route without also deciding
        whether it belongs.
        """
        paths = {
            route.path  # type: ignore[attr-defined]
            for route in client.app.routes  # type: ignore[attr-defined]
            if getattr(route, "path", "").startswith("/api")
        }
        assert paths == {"/api/health"}


# ── The server thread ───────────────────────────────────────────────────


class TestServerLifecycle:
    def test_thread_is_a_daemon(self) -> None:
        """A non-daemon thread keeps the process alive after the last
        window closes, which presents as "the app will not quit"."""
        handle = start_embedded_server(port=_free_port())
        assert handle.running is True
        api_thread = next(
            t for t in threading.enumerate() if t.name == "kalsangati-api"
        )
        assert api_thread.daemon is True

    def test_busy_port_degrades_rather_than_raises(self) -> None:
        """A local-first tracker does not refuse to start because a port
        is busy — the same rule logging follows."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            busy_port = held.getsockname()[1]

            handle = start_embedded_server(port=busy_port)

        assert isinstance(handle, ServerHandle)
        assert handle.running is False
        assert handle.port == busy_port
        assert handle.token  # still populated, for logging

    def test_each_start_mints_a_fresh_token(self) -> None:
        first = start_embedded_server(port=_free_port())
        second = start_embedded_server(port=_free_port())
        assert first.token != second.token

    def test_base_url_is_loopback(self) -> None:
        """Never 0.0.0.0: the port must not be reachable from the
        network."""
        handle = start_embedded_server(port=_free_port())
        assert handle.base_url.startswith("http://127.0.0.1:")

    def test_default_port_is_the_documented_one(self) -> None:
        assert DEFAULT_PORT == 24570


def _free_port() -> int:
    """An unused loopback port, so parallel runs do not collide."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port
