# User Guide

This guide walks through everything you need to use Kālsangati day-to-day: importing data, building your ideal schedule, reading the dashboard, and acting on the comparison results.

---

## Table of Contents

- [First Launch](#first-launch)
- [Importing a CSV Export](#importing-a-csv-export)
- [Resolving Labels](#resolving-labels)
- [Creating an Ideal Schedule](#creating-an-ideal-schedule)
- [Reading the Dashboard](#reading-the-dashboard)
- [Using the Stopwatch](#using-the-stopwatch)
- [Comparison & Reflection](#comparison--reflection)
- [Managing Schedule Versions](#managing-schedule-versions)
- [Configuring Notifications](#configuring-notifications)
- [Watch Mode](#watch-mode)
- [Settings Reference](#settings-reference)

---

## First Launch

On first launch Kālsangati creates a local database at:

```
~/.local/share/kalsangati/kalsangati.db       # Linux
%APPDATA%\Kālsangati\kalsangati.db            # Windows (planned)
~/Library/Application Support/Kālsangati/ # macOS (planned)
```

You will be prompted to either import a CSV export or start from scratch.

---

## Importing a CSV Export

1. Go to **File → Import CSV** (or drag a CSV file onto the main window)
2. Kālsangati validates the file schema. The expected columns are:

   ```
   Project name, Task name, Date, Start time, End time, Duration, Time zone
   ```

   See [`docs/ideal_schedule_format.md`](ideal_schedule_format.md) for the full format spec.

3. After validation, the ingest pipeline runs:
   - Timezone is normalised
   - Fragmented sessions (same project, same day) are merged
   - Labels are converted via your mappings
   - Any unmapped labels are flagged for review

4. A summary dialog shows how many sessions were imported, merged, and flagged.

---

## Resolving Labels

After importing, open **Label Manager** from the sidebar.

- **Flagged labels** appear in amber with a warning icon — these sessions are excluded from analytics until mapped
- Click **Map now** next to any flagged label
- Type or select the canonical activity name it should map to
- Assign it to a group (or create a new group)
- Click **Save** — analytics refresh automatically

You only need to map each raw label once. Future imports will use the saved mapping.

---

## Creating an Ideal Schedule

1. Open **Weekly View** from the sidebar
2. Click **New Version** in the top bar
3. Give it a name (e.g. "Spring 26", "Exam Week")
4. Click any empty cell in the grid to add a time block:
   - Select the activity from the dropdown
   - The block fills the clicked slot
   - Drag the bottom edge to extend duration
5. Click **Set as Active** when finished

The active version is used for all dashboard comparisons and notifications.

---

## Reading the Dashboard

The dashboard updates every 5 minutes (configurable in Settings).

| Element | What it shows |
|---------|---------------|
| **Today Logged** | Total hours tracked so far today |
| **Week Progress** | % of weekly ideal hours logged so far |
| **Adherence Score** | Composite score (0–100) based on how closely actuals match the ideal across all categories |
| **Active Session** | The activity currently being tracked by the stopwatch |
| **Activity breakdown** | Per-activity bar chart: actual vs ideal for today |
| **Pacing alerts** | Activities falling behind with time remaining to recover |

**Reading pacing alerts:**

- Red = critical — significantly behind with few days left
- Amber = behind — still recoverable with effort this week
- Green = on track or ahead

---

## Using the Stopwatch

The stopwatch widget is always accessible from the sidebar or as a floating window (Pin to top option).

1. Select an activity from the dropdown
2. Click **Start** — the timer begins and the session is opened in the database
3. Click **Switch** to change activity mid-session without stopping the clock
4. Click **Stop** to close the session — duration is written to `kalrekha`

Today's session log appears below the timer. Click any entry to edit start/end times manually.

---

## Comparison & Reflection

Open **Compare** from the sidebar.

The comparison view shows:

- **Ideal vs actual bars** — for each activity, a dim bar shows the ideal and a bright bar shows actual logged time
- **Delta column** — numeric difference; red = under, green = over
- **Reflection panel** — activities missed for 3+ consecutive weeks are flagged as patterns, with a suggested corrective action

To generate an adjusted schedule:

1. Review the Vimarśa panel
2. Click **Create Adjusted Version**
3. Kālsangati clones the active version and pre-applies the suggested changes
4. Review and confirm — the new version is saved but not set as active until you choose

---

## Managing Schedule Versions

Open **Weekly View → Versions** to see all saved ideal schedules.

- **Clone** — duplicate a version to use as a starting point
- **Compare two versions** — select two versions to see a side-by-side diff
- **Set as active** — make a version the current target for analytics and notifications
- **Archive** — hide a version from the active list without deleting it
- **Delete** — permanently remove a version

---

## Configuring Notifications

Go to **Settings → Notifications**:

- **Lead time** — how many minutes before a block starts to fire the alert (default: 5)
- **Enable/disable** — master toggle
- **Quiet hours** — suppresses all notifications between two times (e.g. 23:00–07:00)

See [`docs/notifications.md`](notifications.md) for platform-specific setup.

---

## Watch Mode

Watch mode monitors a folder and automatically imports any new CSV files dropped into it.

1. Go to **Settings → Watch Folder**
2. Select a folder path
3. Toggle **Enable watch mode**

Kālsangati will silently import and process new files in the background. A notification appears in the status bar when an import completes.

---

## Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| Database path | `~/.local/share/kalsangati/` | Where the SQLite database is stored |
| Watch folder | None | Folder to monitor for new CSV files |
| Refresh interval | 5 min | How often the dashboard polls for updates |
| Notify lead time | 5 min | Minutes before block start to fire notification |
| Notifications enabled | True | Master notification toggle |
| Quiet hours | None | Suppress notifications during these hours |
| Timezone | System | Timezone used for all time display |

---

## Using the Task Planner

Open **Task Planner** from the sidebar.

### Creating a task

1. Click **+ Task** in any activity group in the backlog column
2. Fill in title, estimated hours, and optional due date
3. The task appears in the backlog under its activity

### Scheduling a task into a week

1. Find the task in the left backlog column
2. Drag it into the right column under the target week and activity
3. The capacity bar updates — green means slack remaining, amber means overbooked

### Marking a task done

- From the task planner: click the checkbox next to the task
- From the stopwatch: tasks for the current activity appear below the timer; tick them off directly

### What happens at week end

Tasks not marked done by Sunday are automatically moved back to the backlog on Monday morning. A notification lists the spilled tasks. Re-schedule them by dragging them into the new week.

### Capacity bar colours

| Colour | Meaning |
|--------|---------|
| Green  | Slack remaining — room for more tasks |
| Amber  | Overbooked — assigned hours exceed available capacity |
| Dim    | No tasks assigned yet |

---

## Out-of-Block Override

When you try to start a task outside its scheduled time block, Kālsangati
shows an alert with three options:

**Continue anyway** — the session starts and is tracked normally. An optional
field lets you note why (e.g. "deadline moved up"). The time is counted in
analytics under the Unplanned layer.

**Wait / Snooze** — dismisses the alert. You will be reminded again when
the next block for that activity approaches.

**Switch activity** — opens the activity selector to log something that does
have a block right now.

Choosing "Continue anyway" does not penalise your adherence score directly,
but the session is tagged as unplanned and surfaced in the Vimarśa panel.
If you override the same activity repeatedly across weeks, Kālsangati flags it
as a structural signal that the block placement needs adjusting.

---

## Reading the Three-Layer Analytics View

Open **Compare** and look at the expanded activity rows.

Each activity shows three bars:

- **Prescribed** (dim) — hours your ideal schedule allocated this week
- **Planned** (bright) — hours you logged during scheduled blocks
- **Unplanned** (amber) — hours you logged outside scheduled blocks

A healthy week has planned close to prescribed and unplanned near zero.

**What the reflection flags mean:**

| Flag | What it means | Suggested action |
|------|--------------|-----------------|
| High unplanned % | Work is happening outside its blocks | Move the block to when you naturally do this work |
| Low planned % | Blocks exist but aren't being used | Reduce ideal allocation or check label mappings |
| Chronic override | Same activity overridden 3+ weeks | Generate an adjusted schedule version |
