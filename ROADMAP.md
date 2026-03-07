# Roadmap

This document describes what is planned for Kālsangati, what is being considered, and what is explicitly out of scope.

---

## Current: v0.1.0 — Core System

The first release establishes the full core loop:

- [x] SQLite database with hybrid JSON schema
- [x] CSV ingest pipeline with label conversion
- [x] Label system: converter + group hierarchy + auto-suggest
- [x] Versioned ideal schedule management
- [x] Live analytics dashboard (today view, week progress, pacing, streaks)
- [x] Comparison & reflection view
- [x] Built-in stopwatch widget
- [x] Desktop notifications with configurable lead time
- [x] Watch mode for auto CSV ingest
- [x] PyQt5 GUI with all screens
- [x] pytest + CI pipeline
- [x] MIT license, full documentation

---

## v0.2.0 — Cross-Platform + Tracker

**Goal:** Windows and macOS support, plus passive activity tracking.

- [x] Task management: backlog, weekly scheduling, capacity-aware planner
- [x] Project entity: projects table linking tasks to canonical_activity
- [x] Override alert: three-option dialog (Continue / Wait / Switch)
- [x] Session classification: planned vs unplanned tagging
- [x] Three-layer analytics: prescribed / planned / unplanned per activity
- [x] Reflection flags: high unplanned, low planned, chronic override
- [x] tasks.py + gui/task_planner.py
- [ ] `tracker.py` — Linux active window detection (ewmh + Xlib)
- [ ] `tracker.py` — Windows active window detection (pygetwindow)
- [ ] `tracker.py` — macOS active window detection (AppKit)
- [ ] App-to-activity mapping via label system
- [ ] Windows installer (NSIS or Inno Setup)
- [ ] macOS app bundle (.app)
- [ ] Platform-specific notification backends (winotify, osascript)

---

## v0.3.0 — Analytics Depth

**Goal:** Richer historical analysis.

- [ ] Multi-week trend charts (matplotlib or pyqtgraph)
- [ ] Custom date range reports
- [ ] Export comparison results as CSV or PDF
- [ ] Activity heatmap (calendar view of hours per day)
- [ ] Configurable adherence score formula (allow weighting by activity priority)
- [ ] Week-over-week delta view (not just vs ideal, but vs last week)

---

## v0.4.0 — Notifications & Automation

**Goal:** Smarter scheduling feedback.

- [ ] Notification snooze (delay by N minutes)
- [ ] Block overrun alert (you have been on this activity X minutes past end time)
- [ ] Daily summary notification at end of day
- [ ] Weekly review prompt on Sunday evening
- [ ] Smart rescheduling suggestion (if you missed a block, propose a makeup slot)

---

## Considering (not scheduled)

These are ideas that may be added depending on community interest:

- **Multiple databases** — support for separate databases per life context (work vs personal)
- **CSV format presets** — built-in parsers for popular time trackers beyond the default schema
- **Plugin system** — allow third-party importers and notification backends
- **Mobile companion** — read-only dashboard view on phone (would require a local HTTP server)
- **Sync between machines** — optional, via a user-provided SQLite sync solution (e.g. Syncthing)

---

## Out of Scope

The following will not be added to Kālsangati:

- **Cloud storage or accounts** — Kālsangati is explicitly local-first; adding a cloud backend contradicts the design
- **Social or sharing features** — schedule data is personal
- **AI-generated schedules** — Kālsangati helps you analyse and adjust; it does not generate schedules on your behalf
- **Calendar integration** (Google Calendar, Outlook) — too many sync edge cases; use the CSV import instead
- **Mobile app** — native mobile is a separate project with different constraints

---

## Contributing to the Roadmap

If you want to work on a roadmap item, open an issue referencing the item and discuss your approach before starting. This prevents duplicate effort and ensures the implementation fits the architecture.

If you have a feature idea not on this list, open a [Feature Request](.github/ISSUE_TEMPLATE/feature_request.md) first.
