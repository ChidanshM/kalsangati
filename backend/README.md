# Kālsangati backend

The Python side of Kālsangati: the domain logic, the local SQLite
database, and the PyQt5 desktop application.

This directory is installable and testable on its own. The project's
front door is the [root README](../README.md); this file covers running
the backend by itself.

## Layout

```
kalsangati/
    core/            domain logic, free of any GUI toolkit
    persistence/     schema, migrations, connection management
    services/        use-case orchestration
    infrastructure/  logging, threads, notifications
    gui/             PyQt5 screens
tests/               mirrors the package one to one
```

Dependencies run downward. `persistence/` imports nothing internal;
`core/` imports `persistence/`; `services/` imports both; `gui/` imports
everything. Only `gui/` may import a GUI toolkit. That rule is what
keeps the backend installable without a frontend, and it is enforced by
review rather than by tooling, so it is worth stating plainly.

## Install

Python 3.10 or newer.

```bash
pip install -e ".[dev]"
```

On Linux, active-window tracking has extra dependencies that are not
installed by default:

```bash
pip install -e ".[dev,linux]"
```

## Run

```bash
kalsangati
```

The database and logs live in the platform's standard user data
directory, not in this repository. On Linux that is
`~/.local/share/kalsangati/`.

Set `KALSANGATI_LOG_LEVEL` to change verbosity:

```bash
KALSANGATI_LOG_LEVEL=DEBUG kalsangati
```

## Checks

The same three commands CI runs:

```bash
ruff check kalsangati/ tests/
mypy kalsangati/
pytest --cov=kalsangati --cov-report=term-missing
```

GUI tests need no display. They set `QT_QPA_PLATFORM=offscreen`
themselves, so the whole suite runs headless.

## A note on the sibling directory

`../frontend/` is empty for now. The backend never imports from it, and
it will never import from the backend. See its README for why.
