# Contributing to Kālsangati

Thank you for taking the time to contribute. This document explains how to set up the development environment, the conventions the project follows, and the process for submitting changes.

---

## Table of Contents

- [Development Setup](#development-setup)
- [Project Conventions](#project-conventions)
- [Branch Strategy](#branch-strategy)
- [Commit Messages](#commit-messages)
- [Running Tests](#running-tests)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Reporting Bugs](#reporting-bugs)

---

## Development Setup

### 1. Fork and clone

```bash
git clone https://github.com/your-username/chronos.git
cd chronos
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate  # Linux / macOS
.venv\Scripts\activate     # Windows
```

### 3. Install in editable mode with dev dependencies

```bash
pip install -e ".[dev]"
```

### 4. Install pre-commit hooks

```bash
pre-commit install
```

Pre-commit runs `ruff` (linting + formatting) and `mypy` (type checking) automatically before every commit.

### 5. Verify setup

```bash
pytest tests/
```

All tests should pass on a clean clone.

---

## Project Conventions

### Type hints

Every function must have complete type annotations:

```python
# correct
def aggregate_sessions(df: pd.DataFrame, date: str) -> pd.DataFrame:
    ...

# incorrect — missing annotations
def aggregate_sessions(df, date):
    ...
```

### Docstrings

All public functions and classes require a Google-style docstring:

```python
def convert_label(raw: str) -> str:
    """Convert a raw imported label to its canonical form.

    Args:
        raw: The raw label string as it appears in the CSV export.

    Returns:
        The canonical activity name, or the original string if no
        mapping exists.

    Raises:
        LabelNotFoundWarning: If the label is unmapped and has been
            added to the review queue.
    """
```

### Formatting

The project uses `black` for formatting and `ruff` for linting. Both run automatically via pre-commit. You can also run them manually:

```bash
ruff check .
ruff format .
mypy kalsangati/
```

Do not disable linting rules inline unless there is a specific, documented reason.

---

## Branch Strategy

```
main        ← always stable; tagged releases only
dev         ← integration branch; all PRs target here
feat/*      ← new features       e.g. feat/notification-snooze
fix/*       ← bug fixes          e.g. fix/label-trailing-whitespace
chore/*     ← maintenance        e.g. chore/update-watchdog
docs/*      ← documentation only e.g. docs/label-system-guide
```

Always branch off `dev`, not `main`.

```bash
git checkout dev
git pull origin dev
git checkout -b feat/your-feature-name
```

---

## Commit Messages

Kālsangati follows the [Conventional Commits](https://www.conventionalcommits.org/) specification. This enables automatic changelog generation.

**Format:**

```
type(scope): short description

optional body

optional footer
```

**Valid types:**

| Type | When to use |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `chore` | Dependency updates, build config |
| `refactor` | Code change with no behaviour change |
| `test` | Adding or updating tests |
| `perf` | Performance improvement |

**Examples:**

```
feat(notifications): add configurable lead-time setting
fix(labels): handle trailing whitespace in raw label strings
docs(readme): add installation instructions for Windows
chore(deps): bump pandas to 2.2.0
test(ingest): add coverage for fragmented session aggregation
```

---

## Running Tests

```bash
# run all tests
pytest tests/

# with coverage report
pytest tests/ --cov=chronos --cov-report=term-missing

# run a specific test file
pytest tests/test_labels.py
```

Write tests for all new behaviour. Tests live in `tests/` and mirror the module structure:

```
tests/
├── test_db.py
├── test_ingest.py
├── test_labels.py
├── test_vimarsha.py
├── test_analytics.py
└── test_notifications.py
```

---

## Submitting a Pull Request

1. Ensure all tests pass: `pytest tests/`
2. Ensure linting is clean: `ruff check .`
3. Ensure types are clean: `mypy kalsangati/`
4. Push your branch and open a PR against `dev`
5. Fill in the pull request template
6. CI must pass before the PR can be merged

Keep PRs focused. One feature or fix per PR. If you find an unrelated issue while working, open a separate issue rather than bundling it into the same PR.

---

## Reporting Bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include:

- Your OS and Python version
- Steps to reproduce
- Expected vs actual behaviour
- Any relevant log output or error messages

---

## Questions

Open a [discussion](https://github.com/your-username/kalsangati/discussions) rather than an issue for questions, ideas, or general feedback.
