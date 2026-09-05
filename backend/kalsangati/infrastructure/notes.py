"""Markdown bodies for tasks, stored as files on disk.

A task's structured fields live in SQLite, where the constraints and the
audit trail are.  Its prose lives here, in a Markdown file, because
prose in a database cell cannot be opened in an editor, searched with
ordinary tools, or diffed.

Layout, under the platform data directory beside the database::

    notes/
        README.md
        41-problem-set-4.md
        47-final-project.md
        47-final-project/
            52-literature-review.md
            58-draft-outline.md
            58-draft-outline/
                61-find-sources.md

A parent task is **a file and a sibling folder of the same name**.  That
is ``ktd``'s shape, and it means a task's own file never moves when it
gains its first child.

Two properties are load-bearing:

* **The path is derived, never stored.**  It falls out of the task's
  ancestry, so moving a task is one database write; the files can be
  rearranged to match afterwards.  Storing the path would make a move a
  multi-step operation spanning two systems that share no transaction.
  ``tasks.notes_path`` remains as an *override* for a file found
  somewhere unexpected; NULL means derive.

* **The file carries its own task id.**  ``notes_path`` points one way,
  database to file.  If a file is moved from outside the application the
  pointer dangles, and without something inside the file naming its
  task, the writing is stranded even though it is still on disk.  The
  frontmatter turns that from permanent loss into a folder scan.

Deliberately not here:

* **Project folders.**  The spec groups notes under a project directory,
  which needs a ``slug`` column on ``projects`` and therefore a schema
  migration.  Deferred; the derived path makes the later move cheap.
* **Moving files when a task is reparented.**  The derived path changes
  and the file stays put.  Recoverable through the id in the
  frontmatter; ships with the reconcile pass (``SKILL-state.md §14``).
* **Deleting anything.**  Soft-deleting a task leaves its notes exactly
  where they are.  Prose is the one genuinely irreplaceable thing in
  this model: a row can be reconstructed from its event trail, a
  paragraph cannot be reconstructed from anything.

**Caveat on layer placement.**  This module imports ``core/tasks`` to
walk ancestry, so ``infrastructure/`` depends on ``core/`` — the second
module in this package to do so, after ``notifications.py``.  The
tension is recorded in ``SKILL-state.md §14`` rather than resolved here.
Filesystem access is a cross-cutting concern and ``core/`` must stay
headlessly testable without touching disk, so this is the right package
even though the dependency direction is untidy.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from platformdirs import user_data_path

from kalsangati.core.tasks import Task, get_by_id

logger = logging.getLogger(__name__)

_APP_NAME = "kalsangati"
_NOTES_DIRNAME = "notes"
_DELIM = "---"

_ROOT_README = """\
# Kālsangati notes

One Markdown file per task, written by Kālsangati.

## Layout

A task's file is named `{id}-{slug}.md`.  A task with subtasks also has
a folder of the same name beside its file, holding them:

```
41-problem-set-4.md
47-final-project.md
47-final-project/
    52-literature-review.md
    58-draft-outline/
        61-find-sources.md
```

The path is worked out from the task and its ancestors rather than
stored, so the folder tree always mirrors the task tree.

## What the app rewrites, and what it does not

The block at the top of each file, between the `---` markers, and the
first heading below it are regenerated every time the app saves.  They
hold the task's id and title and nothing else: no status, no dates.
Mutable state lives in the database, so that there is never a question
about which copy is right.

**Everything below the heading is yours and is never rewritten.**

The `task_id` line matters more than it looks.  If a file is moved from
outside the app, the app's pointer to it breaks, and that line is the
only thing that makes the file findable again.

## Deletion

Deleting a task in the app does not delete its notes.
"""


# ── Location ────────────────────────────────────────────────────────────


def notes_directory() -> Path:
    """Return the notes root, without creating it.

    Beside the database in the platform data directory.  Reports the
    path side-effect-free so a settings screen or a test can ask
    without touching disk, mirroring
    :func:`kalsangati.infrastructure.logging_config.log_directory`.
    """
    return Path(user_data_path(_APP_NAME)) / _NOTES_DIRNAME


def ensure_root_readme() -> None:
    """Write ``notes/README.md`` if it is not already there.

    Never overwrites: the file explains the folder to someone who finds
    it without the app, and clobbering a user's edits to it would be
    rude for no gain.
    """
    root = notes_directory()
    readme = root / "README.md"
    if readme.exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    readme.write_text(_ROOT_README, encoding="utf-8")


# ── Path derivation ─────────────────────────────────────────────────────


def _segment(task: Task) -> str:
    """``{id}-{slug}`` for a task, falling back if the slug is missing.

    ``slug`` is nullable at the schema level (it was introduced dormant),
    so a row created before the core layer populated it can still be
    NULL.  The fallback matches what ``slugify`` would have produced.
    """
    return f"{task.id}-{task.slug}" if task.slug else f"task-{task.id}"


def _ancestry(conn: sqlite3.Connection, task_id: int) -> list[Task]:
    """The chain from the root down to ``task_id``, inclusive.

    Walks upward and reverses.  Carries the same defensive ``seen``
    guard as ``reparent_task`` and ``delete_task``: the cycle trigger
    makes a loop near-impossible, which is exactly why an unbounded walk
    would go unnoticed until it hung.

    Soft-deleted tasks are included — a deleted task keeps its notes,
    and the path has to stay derivable to find them.
    """
    chain: list[Task] = []
    seen: set[int] = set()
    current: int | None = task_id
    while current is not None:
        if current in seen:
            break
        seen.add(current)
        task = get_by_id(conn, current, include_deleted=True)
        if task is None:
            break
        chain.append(task)
        current = task.parent_id
    chain.reverse()
    return chain


def derive_path(conn: sqlite3.Connection, task_id: int) -> Path | None:
    """Where a task's notes file belongs.

    Ancestors become folders, the task itself becomes the filename.  A
    non-NULL ``notes_path`` on the row overrides the derivation, for a
    file the reconcile pass found somewhere unexpected.

    Args:
        conn: Database connection.
        task_id: The task.

    Returns:
        The path, or ``None`` if no such task exists.  The file itself
        may or may not be there.
    """
    chain = _ancestry(conn, task_id)
    if not chain:
        return None

    task = chain[-1]
    if task.notes_path:
        return notes_directory() / task.notes_path

    directory = notes_directory()
    for ancestor in chain[:-1]:
        directory = directory / _segment(ancestor)
    return directory / f"{_segment(task)}.md"


# ── Frontmatter ─────────────────────────────────────────────────────────


def _render(task: Task, body: str) -> str:
    """Assemble the full file: frontmatter, heading, then the body.

    Only ``task_id`` and ``title`` go in the block.  Mutable state
    (status, week, parent) is deliberately excluded: the app does not
    watch files, so an edit made outside it would never be noticed, and
    two copies of a mutable value have no non-arbitrary resolution.

    The title is quoted because a colon followed by a space inside an
    unquoted YAML scalar is invalid, and titles like
    ``Fix E1: wall-clock drift`` are ordinary here.
    """
    escaped = task.title.replace('"', '\\"')
    head = (
        f"{_DELIM}\n"
        f"task_id: {task.id}\n"
        f'title: "{escaped}"\n'
        f"{_DELIM}\n"
        f"\n"
        f"# {task.title}\n"
    )
    if not body.strip():
        return head
    return f"{head}\n{body.rstrip()}\n"


def _strip(text: str) -> str:
    """Return the user's prose: everything after frontmatter and ``H1``.

    Splits on the **first two** delimiter lines only.  A naive
    ``text.split('---')`` would break on any body containing a
    horizontal rule or a nested YAML block, which is ordinary Markdown.

    A file with no frontmatter is treated as entirely body rather than
    rejected: it may have been written by hand, and refusing to read it
    would lose it.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIM:
        return text.strip()

    closing = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == _DELIM),
        None,
    )
    if closing is None:
        return text.strip()  # unterminated block; treat it all as body

    rest = lines[closing + 1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    if rest and rest[0].lstrip().startswith("# "):
        rest.pop(0)
    return "\n".join(rest).strip()


# ── Read and write ──────────────────────────────────────────────────────


def read_body(conn: sqlite3.Connection, task_id: int) -> str | None:
    """The task's prose, or ``None`` if there is no file.

    ``None`` means *no file*, which the caller should report rather than
    quietly treat as empty.  A missing file is evidence that something
    went wrong — a bad sync, a mistaken ``rm``, a folder moved — and
    silently recreating it would erase that evidence.
    """
    path = derive_path(conn, task_id)
    if path is None or not path.is_file():
        return None
    return _strip(path.read_text(encoding="utf-8"))


def write_body(conn: sqlite3.Connection, task_id: int, body: str) -> Path:
    """Write the task's prose, creating the file and its folders.

    Files are created here and nowhere else: a task with no notes has no
    file, so the folder holds only what was actually written.

    Args:
        conn: Database connection.
        task_id: The task.
        body: The prose below the heading.  Frontmatter and the heading
            are regenerated and must not be included.

    Returns:
        The path written.

    Raises:
        ValueError: If no task exists with ``task_id``.
        OSError: If the file cannot be written.  Deliberately not
            swallowed \u2014 the caller decides whether a read-only disk is
            worth a dialog.
    """
    path = derive_path(conn, task_id)
    if path is None:
        raise ValueError(f"No task found with id {task_id}")

    task = get_by_id(conn, task_id, include_deleted=True)
    assert task is not None  # derive_path already proved it exists

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(task, body), encoding="utf-8")
    logger.debug("Wrote notes for task %s to %s", task_id, path)
    return path
