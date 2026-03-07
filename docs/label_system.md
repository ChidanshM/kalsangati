# Label System

The label system is the connective tissue of Kālsangati. It translates the messy, inconsistent strings that come in from CSV exports into a clean, hierarchical activity structure that drives all analytics and comparisons.

---

## The Problem It Solves

Time tracker apps let you name projects and tasks freely. Over time you end up with names like:

```
01-02 CIS731
02-Kitchen 
01-00-Personal work
03-Cleaning 
```

These have trailing spaces, inconsistent capitalisation, course codes instead of descriptive names, and no shared structure. Meanwhile your ideal schedule uses names like:

```
01-02-el
02-kitchen
01-00-Personal-work
03-cleanup
```

Without a conversion layer, none of these join. The label system bridges them.

---

## Two Components

### 1. Label Converter

A flat mapping table: raw string → canonical name.

```
label_mappings
───────────────────────────────────────────────
raw_label              canonical_label
───────────────────────────────────────────────
"01-02 CIS731"      →  "01-02-el"
"01-02 CIS522"      →  "01-02-el"       ← multiple raws → same canonical
"02-Kitchen "       →  "02-kitchen"     ← trailing space handled
"01-00-Personal work" → "01-00-Personal-work"
"03-Cleaning "      →  "03-cleanup"
───────────────────────────────────────────────
```

**How it runs:** Every session written to `kalrekha` passes through `labels.convert_label(raw)` at ingest time. If no mapping exists, the session is stored with the raw label and flagged in the review queue. The Label Manager UI shows all flagged labels so you can map them before the next comparison.

### 2. Label Groups

A hierarchy table: canonical label → parent group → root domain.

```
label_groups
────────────────────────────────────────────────
canonical_label      parent_group       level
────────────────────────────────────────────────
01-02-01-lecture  →  01-02-learning  →  L3
01-02-01-prep     →  01-02-learning  →  L3
01-02-el          →  01-02-learning  →  L3
01-02-learning    →  01-learning     →  L2
01-03-RshWrk      →  01-learning     →  L2
01-04-Application →  01-learning     →  L2
01-learning       →  (root)          →  L1
────────────────────────────────────────────────
```

This hierarchy is used in two places: the comparison view (which can roll up per-activity deltas into group totals), and the dashboard (which shows group-level progress bars alongside granular ones).

---

## Prefix Convention

The naming convention follows a numeric prefix pattern inherited from common personal productivity systems:

```
00-       Spiritual / foundational
01-       Learning & work
  01-00-  Personal work / admin
  01-02-  Coursework / deep learning
  01-03-  Research
  01-04-  Applications
02-       Sustenance (food, kitchen, walk)
03-       Chores (cleaning, laundry)
04-       Fitness (workout, run, gym)
05-       Job / shifts / LP
```

Kālsangati does not enforce this convention — you can name activities however you like. However, the **Auto-suggest** feature in the Label Manager uses this pattern to propose a hierarchy automatically: it scans all canonical labels for shared numeric prefixes and groups them accordingly.

---

## Auto-suggest Algorithm

When you click **Auto-suggest** in the Label Manager:

1. All canonical labels are scanned for leading numeric segments (e.g. `01-02`, `01-03`)
2. Labels sharing the same prefix are proposed as siblings under a generated parent group
3. Nesting depth is determined by the number of prefix segments (e.g. `01-02-01` is deeper than `01-02`)
4. The full proposed hierarchy is shown for review before anything is saved

Auto-suggest is non-destructive — it never overwrites existing mappings without confirmation.

---

## Unmapped Labels

When a CSV is imported and a raw label has no entry in `label_mappings`, Kālsangati:

1. Stores the session with the raw label as-is
2. Adds the raw label to an internal review queue
3. Shows a warning badge on the Label Manager nav item
4. Excludes unmapped sessions from comparison views (to avoid skewing results)

To resolve an unmapped label, open the Label Manager, find the flagged entry (shown in amber), and assign it a canonical name and group. The next time analytics refresh, those sessions will be included.

---

## Editing Labels

All mappings are editable in the GUI Label Manager:

- **Add mapping** — type a raw label and select or create a canonical name
- **Edit mapping** — click any row to modify it inline
- **Delete mapping** — removes the mapping; affected sessions are re-flagged
- **Add group** — create a new parent group node
- **Edit hierarchy** — drag-and-drop in the group tree to restructure levels

Changes take effect on the next analytics refresh (triggered automatically after saving).
