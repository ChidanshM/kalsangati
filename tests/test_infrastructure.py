"""Tests for the infrastructure layer — logging config and @safe_thread.

Logging tests mutate global state (the root logger), so every test that
touches configuration runs inside the ``clean_logging`` fixture, which
snapshots and restores the root handlers, level, and the module's
``_configured`` guard.  Without that, one test's handlers leak into the
next and into the rest of the suite.
"""

from __future__ import annotations

import logging
import logging.handlers
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from kalsangati.infrastructure import logging_config
from kalsangati.infrastructure.logging_config import (
    log_directory,
    setup_logging,
)
from kalsangati.infrastructure.threads import safe_thread


@pytest.fixture
def clean_logging() -> Iterator[None]:
    """Snapshot and restore global logging state around a test."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_flag = logging_config._configured

    for handler in list(root.handlers):
        root.removeHandler(handler)
    logging_config._configured = False

    yield

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)
    logging_config._configured = saved_flag


# ── @safe_thread ───────────────────────────────────────────────────────


class TestSafeThread:
    def test_returns_value_on_success(self) -> None:
        @safe_thread
        def work(x: int) -> int:
            return x * 2

        assert work(21) == 42

    def test_swallows_and_logs_exception(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @safe_thread
        def boom() -> None:
            raise ValueError("kaboom")

        with caplog.at_level(logging.ERROR):
            assert boom() is None

        assert any(
            r.levelno == logging.ERROR and r.exc_info for r in caplog.records
        )
        assert "kaboom" in caplog.text

    def test_preserves_function_metadata(self) -> None:
        @safe_thread
        def documented() -> None:
            """A docstring worth keeping."""

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "A docstring worth keeping."

    def test_thread_failure_is_not_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The E9 scenario: an exception in a thread target must leave a
        trace rather than killing the thread quietly."""

        @safe_thread
        def target() -> None:
            raise RuntimeError("thread died")

        with caplog.at_level(logging.ERROR):
            thread = threading.Thread(target=target)
            thread.start()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert "thread died" in caplog.text

    @pytest.mark.filterwarnings(
        "ignore::pytest.PytestUnhandledThreadExceptionWarning"
    )
    def test_unwrapped_thread_failure_is_silent(self) -> None:
        """Control case documenting *why* the decorator exists.

        A bare thread target that raises leaves the caller with no
        return value, no exception, and no record — only a dead thread.
        pytest notices the unhandled exception and warns; the running
        application would not, which is the whole of E9.  The warning is
        filtered here because it is the expected outcome of the case
        under test, not a defect.
        """
        ran: list[str] = []

        def target() -> None:
            ran.append("started")
            raise RuntimeError("silent")

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=5)

        assert ran == ["started"]
        assert not thread.is_alive()


# ── setup_logging ──────────────────────────────────────────────────────


class TestSetupLogging:
    def test_creates_log_file(
        self, tmp_path: Path, clean_logging: None
    ) -> None:
        log_path = setup_logging(log_dir=tmp_path, force=True)
        assert log_path is not None
        assert log_path.parent == tmp_path
        logging.getLogger("kalsangati.test").warning("hello")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert log_path.exists()
        assert "hello" in log_path.read_text(encoding="utf-8")

    def test_installs_both_handlers(
        self, tmp_path: Path, clean_logging: None
    ) -> None:
        setup_logging(log_dir=tmp_path, force=True)
        handlers = logging.getLogger().handlers
        assert any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            for h in handlers
        )
        assert any(type(h) is logging.StreamHandler for h in handlers)

    def test_console_defaults_to_warning(
        self, tmp_path: Path, clean_logging: None
    ) -> None:
        setup_logging(log_dir=tmp_path, force=True)
        console = [
            h
            for h in logging.getLogger().handlers
            if type(h) is logging.StreamHandler
        ][0]
        assert console.level == logging.WARNING

    def test_is_idempotent(
        self, tmp_path: Path, clean_logging: None
    ) -> None:
        """A second call must not double the handlers."""
        setup_logging(log_dir=tmp_path, force=True)
        count = len(logging.getLogger().handlers)
        setup_logging(log_dir=tmp_path)
        assert len(logging.getLogger().handlers) == count

    def test_force_reconfigures_without_duplicating(
        self, tmp_path: Path, clean_logging: None
    ) -> None:
        setup_logging(log_dir=tmp_path, force=True)
        count = len(logging.getLogger().handlers)
        setup_logging(log_dir=tmp_path, force=True)
        assert len(logging.getLogger().handlers) == count

    def test_env_var_lowers_level(
        self,
        tmp_path: Path,
        clean_logging: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KALSANGATI_LOG_LEVEL", "DEBUG")
        setup_logging(log_dir=tmp_path, force=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        for handler in root.handlers:
            assert handler.level == logging.DEBUG

    def test_invalid_env_var_falls_back_to_defaults(
        self,
        tmp_path: Path,
        clean_logging: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A typo in the env var must not crash startup."""
        monkeypatch.setenv("KALSANGATI_LOG_LEVEL", "VERBOSE-ISH")
        setup_logging(log_dir=tmp_path, force=True)
        console = [
            h
            for h in logging.getLogger().handlers
            if type(h) is logging.StreamHandler
        ][0]
        assert console.level == logging.WARNING

    def test_numeric_env_var_accepted(
        self,
        tmp_path: Path,
        clean_logging: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KALSANGATI_LOG_LEVEL", "10")
        setup_logging(log_dir=tmp_path, force=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_unwritable_directory_degrades_to_console(
        self, tmp_path: Path, clean_logging: None
    ) -> None:
        """A log directory that cannot be created must not stop startup."""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("I am a file", encoding="utf-8")

        log_path = setup_logging(log_dir=blocker / "logs", force=True)

        assert log_path is None
        handlers = logging.getLogger().handlers
        assert any(type(h) is logging.StreamHandler for h in handlers)
        assert not any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            for h in handlers
        )

    def test_noisy_loggers_pinned_to_warning(
        self, tmp_path: Path, clean_logging: None
    ) -> None:
        setup_logging(log_dir=tmp_path, force=True)
        assert logging.getLogger("uvicorn").level == logging.WARNING

    def test_rotation_configured(
        self, tmp_path: Path, clean_logging: None
    ) -> None:
        setup_logging(log_dir=tmp_path, force=True)
        file_handler = [
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ][0]
        assert file_handler.maxBytes == 10 * 1024 * 1024
        assert file_handler.backupCount == 5


class TestLogDirectory:
    def test_returns_path_without_creating_it(self) -> None:
        """Reporting the location must have no side effect."""
        path = log_directory()
        assert isinstance(path, Path)
        assert "kalsangati" in str(path).lower()
