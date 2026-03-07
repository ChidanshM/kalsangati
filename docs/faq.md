# Frequently Asked Questions

---

## Why SQLite and not a document database like MongoDB?

The comparison and analytics queries are relational at their core — JOINs between sessions, label mappings, and group hierarchies are exactly what SQL is optimised for. MongoDB would turn those joins into manual application-level code.

The one genuinely document-like object in Kālsangati is the ideal schedule version, because each version has a different set of activities with variable structure. This is solved with a single JSON column in the `niyam` table, using SQLite's native `json_extract` and `json_each` functions to query into it when needed. You get document-style flexibility where you need it without sacrificing relational integrity everywhere else.

---

## Why not use PostgreSQL or MySQL?

Kālsangati is local-first. A full database server requires installation, a running process, connection management, and credentials — none of which are appropriate for a single-user desktop application. SQLite is a file. You copy it, back it up, and delete it. No server required.

---

## Why Python and not Rust or Go?

The stopwatch and notification features are simple enough that performance is not a concern — a timer loop and a system notification call are negligible in any language. Python was chosen because the ecosystem for data manipulation (Pandas), GUI toolkits (PyQt5), and cross-platform desktop tooling (plyer, watchdog) is mature and well-documented. The development overhead of Rust for a project at this scope is not justified by the performance gains.

---

## Why PyQt5 and not Tkinter or Electron?

Tkinter is included with Python but has significant limitations in terms of widget richness and styling — building a time-block grid with drag interaction would require substantial custom drawing code. Electron would work but adds a 150MB Node.js runtime and Chromium instance to a lightweight local tool.

PyQt5 offers native widgets, rich layout tools, proper threading support (essential for the background watcher and notification threads), and a design that scales well. The LGPL licence allows distribution in open source projects without restriction.

---

## Why is there no cloud sync?

Kālsangati is explicitly designed to not require an account, a subscription, or network access. Your time tracking data is personal and should live on your machine under your control. If you want to sync between devices, tools like Syncthing or a shared network drive can sync the SQLite file directly — this is supported and documented.

Adding a cloud backend would introduce authentication, API rate limits, data residency questions, and a dependency on an external service that could go offline or change pricing. None of these trade-offs are worth it for what is fundamentally a local productivity tool.

---

## Why not integrate with Google Calendar or Outlook?

Calendar integrations involve bidirectional sync with significant edge cases: timezone mismatches, recurring event handling, deleted/modified events, and API authentication. The complexity would dominate the codebase.

The simpler and more reliable approach: export a CSV from your time tracker and import it into Kālsangati. This is a five-second operation and works with any tool that exports CSV.

---

## Can I use Kālsangati with a time tracker other than the default?

Yes, as long as it exports a CSV with these columns:

```
Project name, Task name, Date, Start time, End time, Duration, Time zone
```

The label system handles name normalisation after import, so you can map whatever naming convention your tracker uses to Kālsangati's canonical activity names.

---

## How does the adherence score work?

The adherence score (0–100) is calculated as a weighted average of per-category completion percentages. Each category's completion is `min(actual_hours / ideal_hours, 1.0)` — going over ideal does not penalise the score, but it does not increase it beyond 100% for that category either.

Categories are equally weighted by default. A future release will allow you to weight categories by priority.

---

## Is my data ever sent anywhere?

No. Kālsangati makes no network requests. The only external calls are to the operating system's notification API (`notify-send` on Linux) and to the filesystem watcher. No telemetry, no analytics, no crash reporting.

---

## Where is the database stored?

```
Linux:   ~/.local/share/kalsangati/kalsangati.db
Windows: %APPDATA%\Kālsangati\kalsangati.db        (planned)
macOS:   ~/Library/Application Support/Kālsangati/kalsangati.db  (planned)
```

You can change the path in **Settings → Database Path**.

---

## Why does Kālsangati alert me when I work outside a scheduled block?

The alert exists because unacknowledged out-of-block work silently distorts
your analytics. If you spend 3 hours on research outside its scheduled slot
and Kālsangati doesn't know it was unplanned, the comparison view just shows
"3 hours logged" with no context about whether that was deliberate or drift.

By making you choose — Continue, Wait, or Switch — Kālsangati captures intent.
"Continue anyway" means you made a conscious decision. The session is tracked
fully, just tagged as unplanned so the Vimarśa panel can distinguish
habitual drift from deliberate flexibility.

The three-option design is deliberate: it never blocks you from doing work,
it just asks you to be aware of it.

---

## Does unplanned time count against my adherence score?

No — unplanned time still counts as logged time for the activity, so it
contributes to your actual hours in the ideal vs actual comparison. It does
not reduce the adherence score by itself. What the score reflects is how
closely your total logged hours match your prescribed hours per activity —
regardless of whether those hours were within block or not.

The unplanned flag is used separately in the three-layer analytics view and
the chronic override reflection flag.
