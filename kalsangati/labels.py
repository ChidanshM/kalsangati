"""Label conversion and group hierarchy.

The label system normalises inconsistent names from external time trackers
into a clean canonical activity hierarchy.  Two tables drive this:

* **label_mappings** — raw imported label → canonical activity name
* **label_groups** — canonical label → parent group (recursive hierarchy)

Both are editable via the GUI label manager.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from kalsangati.db import transaction

# ── Data classes ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LabelMapping:
    """A single raw → canonical label mapping."""

    id: int
    raw_label: str
    canonical_label: str


@dataclass(frozen=True, slots=True)
class LabelGroup:
    """A node in the label hierarchy."""

    id: int
    canonical_label: str
    parent_group: str | None
    level: int


# ── Prefix pattern for auto-suggest ────────────────────────────────────

_PREFIX_RE = re.compile(r"^(\d{2}(?:-\d{2})*)")


def suggest_parent_from_prefix(canonical: str) -> str | None:
    """Derive a parent group name from a numeric prefix convention.

    For a label like ``"01-02-01-lecture"`` the parent is ``"01-02"``,
    then ``"01"``.  For ``"02-kitchen"`` the parent is ``"02"``.
    Returns the immediate parent prefix, or *None* if no prefix is
    detected or the label is already a bare prefix.

    Args:
        canonical: A canonical activity label.

    Returns:
        The suggested parent group string, or None.
    """
    match = _PREFIX_RE.match(canonical)
    if not match:
        return None
    prefix = match.group(1)
    parts = prefix.split("-")

    # If the canonical has content beyond the prefix, the full prefix
    # is the parent (e.g. "02-kitchen" → "02", "01-02-el" → "01-02").
    if len(canonical) > len(prefix):
        return prefix

    # Otherwise strip one numeric segment: "01-02" → "01"
    if len(parts) <= 1:
        return None
    return "-".join(parts[:-1])


def infer_level(canonical: str) -> int:
    """Return the hierarchy depth based on prefix segments and suffix.

    ``"01"`` → 1, ``"01-02"`` → 2, ``"01-02-el"`` → 3,
    ``"02-kitchen"`` → 2.  Labels with no numeric prefix get level 0.

    Args:
        canonical: A canonical activity label.

    Returns:
        Integer depth.
    """
    match = _PREFIX_RE.match(canonical)
    if not match:
        return 0
    prefix = match.group(1)
    depth = len(prefix.split("-"))
    # If there's content beyond the prefix, add one more level
    if len(canonical) > len(prefix):
        depth += 1
    return depth


# ── Label mapping CRUD ──────────────────────────────────────────────────


def get_all_mappings(conn: sqlite3.Connection) -> list[LabelMapping]:
    """Return every raw → canonical mapping, sorted by raw_label.

    Args:
        conn: Database connection.

    Returns:
        List of LabelMapping instances.
    """
    rows = conn.execute(
        "SELECT id, raw_label, canonical_label FROM label_mappings "
        "ORDER BY raw_label"
    ).fetchall()
    return [LabelMapping(r["id"], r["raw_label"], r["canonical_label"]) for r in rows]


def resolve_label(conn: sqlite3.Connection, raw: str) -> str | None:
    """Look up the canonical label for a raw imported string.

    Args:
        conn: Database connection.
        raw: The raw label as it appears in the CSV.

    Returns:
        The canonical label, or None if no mapping exists.
    """
    row = conn.execute(
        "SELECT canonical_label FROM label_mappings WHERE raw_label = ?",
        (raw,),
    ).fetchone()
    return row["canonical_label"] if row else None


def add_mapping(
    conn: sqlite3.Connection, raw_label: str, canonical_label: str
) -> int:
    """Insert a new raw → canonical mapping.

    Args:
        conn: Database connection.
        raw_label: The raw label string.
        canonical_label: The target canonical name.

    Returns:
        The row id of the new mapping.

    Raises:
        sqlite3.IntegrityError: If *raw_label* already exists.
    """
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO label_mappings (raw_label, canonical_label) "
            "VALUES (?, ?)",
            (raw_label, canonical_label),
        )
        return cur.lastrowid  # type: ignore[return-value]


def update_mapping(
    conn: sqlite3.Connection,
    mapping_id: int,
    *,
    raw_label: str | None = None,
    canonical_label: str | None = None,
) -> None:
    """Update an existing mapping's fields.

    Args:
        conn: Database connection.
        mapping_id: Primary key of the mapping.
        raw_label: New raw label (if changing).
        canonical_label: New canonical label (if changing).
    """
    updates: list[str] = []
    params: list[str | int] = []
    if raw_label is not None:
        updates.append("raw_label = ?")
        params.append(raw_label)
    if canonical_label is not None:
        updates.append("canonical_label = ?")
        params.append(canonical_label)
    if not updates:
        return
    params.append(mapping_id)
    conn.execute(
        f"UPDATE label_mappings SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()


def delete_mapping(conn: sqlite3.Connection, mapping_id: int) -> None:
    """Delete a mapping by id.

    Args:
        conn: Database connection.
        mapping_id: Primary key of the mapping.
    """
    conn.execute("DELETE FROM label_mappings WHERE id = ?", (mapping_id,))
    conn.commit()


def get_unrecognized_labels(
    conn: sqlite3.Connection,
) -> list[str]:
    """Return raw labels present in kalrekha but missing from label_mappings.

    Args:
        conn: Database connection.

    Returns:
        Sorted list of unmapped raw project labels.
    """
    rows = conn.execute(
        "SELECT DISTINCT k.project FROM kalrekha k "
        "LEFT JOIN label_mappings lm ON k.project = lm.raw_label "
        "WHERE lm.id IS NULL AND k.project IS NOT NULL "
        "ORDER BY k.project"
    ).fetchall()
    return [r["project"] for r in rows]


# ── Label group CRUD ────────────────────────────────────────────────────


def get_all_groups(conn: sqlite3.Connection) -> list[LabelGroup]:
    """Return every group node, ordered by level then name.

    Args:
        conn: Database connection.

    Returns:
        List of LabelGroup instances.
    """
    rows = conn.execute(
        "SELECT id, canonical_label, parent_group, level "
        "FROM label_groups ORDER BY level, canonical_label"
    ).fetchall()
    return [
        LabelGroup(r["id"], r["canonical_label"], r["parent_group"], r["level"])
        for r in rows
    ]


def get_children(conn: sqlite3.Connection, parent: str) -> list[LabelGroup]:
    """Return direct children of a parent group.

    Args:
        conn: Database connection.
        parent: The parent_group value.

    Returns:
        List of child LabelGroup instances.
    """
    rows = conn.execute(
        "SELECT id, canonical_label, parent_group, level "
        "FROM label_groups WHERE parent_group = ? "
        "ORDER BY canonical_label",
        (parent,),
    ).fetchall()
    return [
        LabelGroup(r["id"], r["canonical_label"], r["parent_group"], r["level"])
        for r in rows
    ]


def add_group(
    conn: sqlite3.Connection,
    canonical_label: str,
    parent_group: str | None = None,
    level: int | None = None,
) -> int:
    """Insert a new group node.

    If *parent_group* is None, it is auto-suggested from the prefix.
    If *level* is None, it is inferred from the prefix.

    Args:
        conn: Database connection.
        canonical_label: The canonical activity name.
        parent_group: Explicit parent, or auto-detected.
        level: Explicit hierarchy depth, or auto-detected.

    Returns:
        The row id of the new group.
    """
    if parent_group is None:
        parent_group = suggest_parent_from_prefix(canonical_label)
    if level is None:
        level = infer_level(canonical_label)

    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO label_groups (canonical_label, parent_group, level) "
            "VALUES (?, ?, ?)",
            (canonical_label, parent_group, level),
        )
        return cur.lastrowid  # type: ignore[return-value]


def update_group(
    conn: sqlite3.Connection,
    group_id: int,
    *,
    parent_group: str | None = None,
    level: int | None = None,
) -> None:
    """Update a group's parent or level.

    Args:
        conn: Database connection.
        group_id: Primary key of the group.
        parent_group: New parent (pass empty string to clear).
        level: New level.
    """
    updates: list[str] = []
    params: list[str | int | None] = []
    if parent_group is not None:
        updates.append("parent_group = ?")
        params.append(parent_group or None)
    if level is not None:
        updates.append("level = ?")
        params.append(level)
    if not updates:
        return
    params.append(group_id)
    conn.execute(
        f"UPDATE label_groups SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()


def delete_group(conn: sqlite3.Connection, group_id: int) -> None:
    """Delete a group node by id.

    Args:
        conn: Database connection.
        group_id: Primary key.
    """
    conn.execute("DELETE FROM label_groups WHERE id = ?", (group_id,))
    conn.commit()


def resolve_hierarchy(
    conn: sqlite3.Connection, canonical: str
) -> list[str]:
    """Walk up the group hierarchy from a canonical label to the root.

    Args:
        conn: Database connection.
        canonical: Starting canonical label.

    Returns:
        List from leaf to root, e.g.
        ``["01-02-01-lecture", "01-02-01", "01-02", "01"]``.
    """
    chain: list[str] = [canonical]
    current = canonical
    seen: set[str] = {canonical}
    while True:
        row = conn.execute(
            "SELECT parent_group FROM label_groups "
            "WHERE canonical_label = ?",
            (current,),
        ).fetchone()
        if not row or not row["parent_group"]:
            break
        parent = row["parent_group"]
        if parent in seen:
            break  # safety: avoid cycles
        seen.add(parent)
        chain.append(parent)
        current = parent
    return chain


def auto_populate_groups(conn: sqlite3.Connection) -> int:
    """Scan label_mappings and insert missing group nodes.

    For each canonical_label in label_mappings, ensures that every
    prefix ancestor exists in label_groups.

    Args:
        conn: Database connection.

    Returns:
        Number of new group nodes created.
    """
    existing = {
        g.canonical_label for g in get_all_groups(conn)
    }
    canonicals = {
        r["canonical_label"]
        for r in conn.execute(
            "SELECT DISTINCT canonical_label FROM label_mappings"
        ).fetchall()
    }

    to_insert: set[str] = set()
    for canon in canonicals:
        # Walk up the prefix tree
        current = canon
        while current:
            if current not in existing and current not in to_insert:
                to_insert.add(current)
            parent = suggest_parent_from_prefix(current)
            if parent is None or parent == current:
                break
            current = parent

    count = 0
    for label in sorted(to_insert):
        add_group(conn, label)
        count += 1

    return count
