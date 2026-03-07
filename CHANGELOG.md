# Changelog

All notable changes to Kālsangati are documented here.

This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.
Kālsangati uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- Initial project scaffold
- DB schema: niyam, kalrekha, weekly_aggregates, label_mappings, label_groups, settings
- CSV ingest pipeline with timezone normalization and session aggregation
- Label converter and group hierarchy system with auto-suggest
- Versioned ideal schedule management (JSON column in SQLite)
- Live analytics dashboard: today view, week progress, pacing alerts, streak indicators
- Ideal vs actual comparison view with Vimarśa panel
- Stopwatch widget with quick-switch and live session logging
- Desktop notifications via plyer with configurable lead time
- Watch mode for auto-ingest on CSV folder drop
- PyQt5 GUI: main window, schedule editor, label manager, analytics dashboard, settings
- Cross-platform notification abstraction (Linux primary)
- Full test suite with pytest and coverage
- GitHub Actions CI pipeline
- Pre-commit hooks: ruff + mypy

---

## [0.1.0] - TBD

> First public release. Core functionality complete.

### Added
- Everything listed under Unreleased above.

---

<!-- Links -->
[Unreleased]: https://github.com/your-username/kalsangati/compare/HEAD
[0.1.0]: https://github.com/your-username/kalsangati/releases/tag/v0.1.0

### Task system (added to Unreleased)
- tasks table: title, project, canonical_activity, estimated_hours, due_date, status, week_assigned, spilled_from, notes
- tasks.py module: CRUD, capacity calculation, spillover handling
- gui/task_planner.py: two-column backlog + week planner with drag-and-drop
- Capacity bar: ideal hours vs logged vs assigned, slack indicator
- Stopwatch integration: active tasks shown during session, mark done inline
- Notification integration: task count included in block-start notification
- Auto-spillover: incomplete tasks moved to backlog on week boundary

### Override & three-layer analytics (added to Unreleased)
- projects table: name, canonical_activity, color, notes
- tasks.project_id FK → projects
- kalrekha gains: unplanned, override_reason, block_classified fields
- weekly_aggregates gains: planned_hours, unplanned_hours columns
- gui/override_dialog.py: three-option alert (Continue / Wait / Switch)
- Session classification: planned vs unplanned at write time + retroactive for imports
- Three-layer analytics view: prescribed / planned / unplanned per activity and project
- Reflection flags: high unplanned %, low planned %, chronic override (3+ weeks)
- projects.py module: CRUD + get_by_activity lookup
- Chronic override detection query (8-week rolling window)
