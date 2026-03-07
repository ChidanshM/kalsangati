# Task Planner

The task planner is Kālsangati's capacity-aware weekly scheduling system. It connects your to-do list to your ideal schedule — tasks are assigned to activities, and the planner shows how much of each activity's weekly time budget is already spoken for.

---

## Core Concept

Every task in Kālsangati is linked to a `canonical_activity`. This means a research task isn't just a to-do item — it knows it belongs to `01-03-RshWrk`, which has 9.8 hours available in your ideal schedule this week. The planner uses this to tell you whether you're over- or under-committing your time before the week begins.

```
Task: "Write lit review section"
  canonical_activity: 01-03-RshWrk
  estimated_hours:    2.5
  week_assigned:      2026-W10

Available capacity for 01-03-RshWrk in W10:
  Ideal hours:        9.8h
  Already logged:     3.0h    ← from kalrekha mid-week
  Remaining:          6.8h

Tasks assigned to 01-03-RshWrk in W10:
  Write lit review:   2.5h
  Review paper batch: 1.5h
  Total assigned:     4.0h

Slack:                2.8h    ← unscheduled capacity
```

---

## Task Data Model

```sql
CREATE TABLE tasks (
    id                 INTEGER PRIMARY KEY,
    title              TEXT NOT NULL,
    project            TEXT,
    canonical_activity TEXT NOT NULL,   -- FK to label_groups
    estimated_hours    REAL,
    due_date           TEXT,
    status             TEXT DEFAULT 'backlog',
    week_assigned      TEXT,            -- ISO week e.g. "2026-W10"
    notes              TEXT,
    created_at         TEXT NOT NULL
);
```

**Status lifecycle:**

```
backlog → this_week → in_progress → done
                ↓
           (spill) → next week backlog
```

---

## Weekly Task Planner UI

The task planner is a two-column panel accessible from the sidebar.

### Left Column — Backlog

Tasks grouped by `canonical_activity`, sorted by due date. Each group shows:
- Activity name and colour swatch
- Total estimated hours in backlog for that activity
- Individual task rows with title, estimated hours, and due date

### Right Column — Week View

One section per activity that has tasks or capacity. Each section shows:
- **Capacity bar** — ideal hours for the week, with logged hours consumed and assigned task hours overlaid
- **Assigned tasks** — tasks dragged into this week, listed with estimated hours
- **Slack indicator** — remaining unassigned capacity in hours

### Scheduling a Task

1. Find the task in the backlog (left column)
2. Drag it into the target week section for its activity (right column)
3. The capacity bar updates immediately
4. If the assignment puts the activity over capacity, the bar turns amber and a warning is shown

### Switching Weeks

Use the **This week / Next week** toggle at the top of the right column. You can plan next week while this week is still in progress — the capacity calculation for next week uses the ideal hours from the active schedule version without deducting logged time (since the week hasn't started yet).

---

## Capacity Calculation

Capacity is calculated differently depending on whether you are planning the current week or a future week.

**Current week:**
```
available = ideal_hours_this_week - actual_logged_hours_this_week
```
Logged hours are pulled from `weekly_aggregates` and update as sessions are recorded.

**Future week:**
```
available = ideal_hours_for_that_week
```
No deduction — the week hasn't started.

**Assigned hours:**
```
assigned = sum(estimated_hours) for tasks WHERE
           canonical_activity = ? AND week_assigned = ?
           AND status != 'done'
```

**Slack:**
```
slack = available - assigned
```
Negative slack = overbooked. Shown in amber with the exact overage.

---

## Task Spillover

At the end of each week, Kālsangati checks for tasks with `status != 'done'` and `week_assigned` equal to the week that just ended. These tasks are automatically moved to the backlog with their `week_assigned` cleared. A summary notification lists the spilled tasks so you can re-schedule them deliberately rather than losing track of them.

Spillover is non-destructive — the original `week_assigned` is preserved in a `spilled_from` field so you can see how long a task has been bouncing.

---

## Integration with the Stopwatch

When you start a session in the stopwatch, Kālsangati checks whether there are any `in_progress` tasks for the current activity. If there are, they appear in a small panel below the timer so you can mark them done without leaving the tracker view.

Marking a task done:
- Sets `status = 'done'`
- Removes it from the capacity calculation immediately
- Updates the capacity bar in the task planner in real time

---

## Integration with Notifications

If a time block is starting and there are `in_progress` or `this_week` tasks assigned to that activity, the notification includes the count:

```
01-03-RshWrk starting in 5 min
2h planned · 2 tasks assigned
```

---

## Creating and Editing Tasks

Tasks can be created from:
- **Task Planner** — click **+ Task** in any backlog group
- **Stopwatch** — click **+ Task** while a session is running to attach a task to the current activity
- **Weekly View** — right-click a time block and select **Add task to this block**

Fields:
- **Title** — required
- **Project** — optional free text (for your own reference)
- **Activity** — required; select from canonical activity list
- **Estimated hours** — optional but recommended for capacity tracking
- **Due date** — optional; tasks with due dates are sorted to the top of the backlog
- **Notes** — free text

Tasks can be edited by clicking them anywhere they appear. Bulk actions (mark done, reschedule, delete) are available via right-click in the backlog.

---

## Time Enforcement & Override Alert

When you attempt to start a task whose `canonical_activity` has no currently
active or imminent scheduled block, Kālsangati fires a three-option alert rather
than silently allowing or blocking the work:

```
┌─────────────────────────────────────────────────────────┐
│  Out-of-block work detected                             │
│                                                         │
│  "Write lit review" belongs to 01-03-RshWrk.           │
│  No block is scheduled for this activity right now.     │
│  Next block: Thursday 14:00 – 16:00                     │
│                                                         │
│  [Continue anyway]  [Wait / Snooze]  [Switch activity]  │
└─────────────────────────────────────────────────────────┘
```

**Continue anyway** — starts the session and logs it with `unplanned = true`.
An optional text field lets you record why (e.g. "deadline pressure",
"block moved"). The session and task time are tracked normally.

**Wait / Snooze** — dismisses the alert. Kālsangati re-alerts when the next
scheduled block for that activity approaches (using the notification lead time).

**Switch activity** — opens the activity selector so you can log something
else that does have an active block right now.

### Why overrides are tracked

Overridden sessions are not discarded or hidden — they appear in all analytics
views, clearly tagged as unplanned. This is intentional: if you regularly
override the same activity, the Vimarśa panel surfaces it as a signal that
the block placement in your ideal schedule is wrong for how you actually work.

---

## Three-Layer Analytics

Every session is classified into one of three layers:

| Layer | Definition |
|-------|-----------|
| **Prescribed** | Hours allocated in the active ideal schedule |
| **Planned** | Hours logged during a scheduled block for the correct activity |
| **Unplanned** | Hours logged outside a scheduled block (override taken) |

The analytics view shows all three layers per activity and per project:

```
01-03-RshWrk — Week 10
  Prescribed:  9.8h  ████████████████████
  Planned:     5.4h  ███████████░░░░░░░░░
  Unplanned:   1.8h  ████░░░░░░░░░░░░░░░░  (flagged amber)
  Delta:       -2.6h
```

### Reflection flags

**High unplanned %** — you are doing this work, but outside its scheduled
blocks. The block placement may not match when you naturally do this work.
Suggested action: move the block to the time you actually use.

**Low planned %** — blocks exist but sessions are not being logged against
them. Either the work isn't happening, or it is happening under a different
activity label. Suggested action: investigate label mapping or reduce ideal
allocation.

**Chronic override** — the same activity has been overridden 3 or more weeks
in a row. This is a structural signal, not a one-off. Suggested action:
generate an adjusted schedule version that relocates the block.

### Imported session classification

Sessions imported from CSV exports are retroactively classified as planned
or unplanned by comparing their timestamps against the ideal schedule's
time blocks. Sessions that fall outside any block are marked `unplanned = true`
with `override_reason = "imported — no matching block"`. The
`block_classified` flag is set to `true` once classification has run so it
is not re-run on subsequent imports.
