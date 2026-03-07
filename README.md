# Kālsangati — Coherence with Time

[![CI](https://github.com/your-username/kalsangati/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/kalsangati/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-cyan.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

> **Plan the cycle. Live the line. Close the gap.**

Kālsangati is a local-first, open-source Python desktop application for people who plan their weeks intentionally and want to know, with precision, how well reality matched the plan.

It bridges three things that typically live in separate tools — a time tracker, a schedule planner, and an analytics dashboard — into a single offline system. Define versioned weekly schedules (Niyam), import logged sessions from a time tracker CSV export or record them via the built-in stopwatch, and Kālsangati maps everything together through a label system that normalises inconsistent naming into a clean activity hierarchy.

The result: a three-layer analytics view (Vimarśa) that distinguishes between prescribed time, planned time, and unplanned time — surfacing not just how much time you spent, but whether it happened when you intended it to.

**No cloud. No subscriptions. No account. Everything lives in a local SQLite database.**

---

## The Naming

Kālsangati uses Sanskrit names internally — in the interface, the database, and the codebase. Each name carries the meaning of the layer it represents:

| Sanskrit | Meaning | The Layer |
|----------|---------|-----------|
| **Kālsangati** | Coherence with Time | The app itself |
| **Niyam** | Personal discipline, the rule you set for yourself | Your blueprint schedule |
| **Kālrekhā** | The line of time — the trace | The session log; time as actually lived |
| **Kālachakra** | The wheel of time | The weekly cycle that completes and returns |
| **Vimarśa** | The mind examining itself | The reflection engine |
| **Pariṇāma** | Transformation | The adjusted schedule generated from reflection |

> *Kālachakra turns. You set your Niyam. Time moves. The Kālrekhā takes shape. Vimarśa surfaces the gap. Pariṇāma adjusts the Niyam. The wheel turns again.*

---

## Features

- **Niyam** — define multiple named weekly blueprint schedules; switch the active target at any time; compare any two Niyam side by side
- **CSV ingest pipeline** — import sessions from any time tracker that exports CSV; fragmented sessions automatically aggregated
- **Built-in stopwatch** — log sessions directly; same database, same analytics
- **Label system** — map raw CSV labels to canonical activity names; define a prefix-based group hierarchy
- **Live analytics dashboard** — today view, week progress, pacing alerts, streak indicators, adherence score
- **Vimarśa (three-layer analytics)** — prescribed vs planned vs unplanned; chronic override detection; Pariṇāma generation
- **Task planner** — capacity-aware weekly scheduling; tasks time-gated to their activity blocks; three-option override alert
- **Desktop notifications** — configurable lead-time alerts before each Niyam block starts
- **Watch mode** — auto-ingest new CSVs dropped into a configured folder

---

## Installation

### Requirements

- Python 3.10 or higher
- Linux (primary); Windows and macOS support planned

### From source

```bash
git clone https://github.com/your-username/kalsangati.git
cd kalsangati
pip install -e ".[dev]"
```

### Run

```bash
kalsangati
```

---

## Quickstart

1. **Import your first CSV** — go to *File → Import CSV* and select your time tracker export
2. **Map labels** — open *Label Manager*, resolve any flagged unmapped labels
3. **Create your Niyam** — open *Weekly View*, click *New Niyam*, fill in your time blocks
4. **Set it as active** — right-click the Niyam and select *Set as Active*
5. **Check your dashboard** — the live analytics screen now shows today's Kālrekhā against your Niyam

For a full walkthrough see [`docs/user_guide.md`](docs/user_guide.md).

---

## Project Structure

```
kalsangati/
├── pyproject.toml                  # hatchling backend, deps, tool config
├── README.md                       # English-first external surface
├── LICENSE                         # MIT
├── CONTRIBUTING.md                 # dev setup, conventions, PR process
├── CHANGELOG.md                    # conventional-commits changelog
├── ROADMAP.md                      # versioned feature plan (v0.1–v0.4)
├── CODE_OF_CONDUCT.md
├── SECURITY.md
│
├── .github/
│   ├── workflows/ci.yml            # ruff + mypy + pytest on 3.10/3.11/3.12
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   └── PULL_REQUEST_TEMPLATE.md
│
├── data/
│   ├── sample_export-01.csv        # real Toggl-style export (257 sessions)
│   └── sample_export-02.csv        # recent clean export (24 sessions)
│
├── docs/
│   ├── naming.md                   # Sanskrit → ASCII → meaning mapping
│   ├── architecture.md             # system design, data flow, threading
│   ├── niyam_format.md             # CSV + JSON schema for schedules
│   ├── label_system.md             # prefix hierarchy, converter logic
│   ├── task_planner.md             # capacity model, spillover, override
│   ├── notifications.md            # platform setup, configuration
│   ├── user_guide.md               # end-user walkthrough
│   └── faq.md                      # design decisions explained
│
├── kalsangati/                     # main package
│   y├── __init__.py                 # version only
│   y├── db.py                       # schema, migrations, connection
│   y├── labels.py                   # converter + group hierarchy
│   y├── niyam.py                    # Niyam CRUD, JSON time-block I/O
│   y├── ingest.py                   # CSV parse → kalrekha + aggregates
│  	y├── analytics.py                # today/week metrics, pacing, streaks
│   y├── vimarsha.py                 # three-layer reflection engine
│   y├── notifications.py            # background scheduler, plyer
│   y├── projects.py                 # project CRUD, activity binding
│   y├── tasks.py                    # task CRUD, capacity, spillover
│   y├── tracker.py                  # window detection stub (future)
│   └── gui/
│       ├── __init__.py
│       y├── main_window.py          # tab hub, menu, auto-refresh
│       ├── stopwatch.py            # always-on-top timer widget
│       ├── niyam_editor.py         # visual grid (48 rows × 7 columns)
│       ├── niyam_compare.py        # side-by-side A vs B diff
│       ├── label_manager.py        # converter table + tree editor
│       ├── analytics_dashboard.py  # health score, tables, alerts
│       ├── task_planner.py         # backlog + capacity + scheduling
│       ├── override_dialog.py      # three-option alert
│       └── settings.py             # notifications, watch folder, prefs
│
└── tests/
    ├── __init__.py
    ├── conftest.py                 # shared fixtures (conn, sample CSVs)
    ├── test_db.py
    ├── test_labels.py
    ├── test_niyam.py
    ├── test_ingest.py
    └── test_tasks.py
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [User Guide](docs/user_guide.md) | How to use Kālsangati day-to-day |
| [Niyam Format](docs/niyam_format.md) | CSV schema for importing blueprint schedules |
| [Architecture](docs/architecture.md) | System design, data flow, module responsibilities |
| [Label System](docs/label_system.md) | Prefix hierarchy, converter logic, grouping |
| [Task Planner](docs/task_planner.md) | Capacity-aware weekly task scheduling |
| [Notifications](docs/notifications.md) | Platform setup, configuration |
| [FAQ](docs/faq.md) | Design decisions explained |
| [Roadmap](ROADMAP.md) | What is planned and what is out of scope |
| [Contributing](CONTRIBUTING.md) | Dev setup, conventions, PR process |

---

## Philosophy

> *This is not a productivity system. It is the practice of moving through time as a complete, undivided self — where the person who planned the week and the person who lived it are always moving toward each other, cycle by cycle, until the gap between them closes not by force but by design.*
>
> *Sva kālrekhā. Sva niyam. Sva pariṇāma.*
> *Your timeline. Your discipline. Your transformation.*

---

## Contributing

Contributions are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

---

## License

MIT — see [`LICENSE`](LICENSE).
