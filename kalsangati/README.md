# Kālsangati — Coherence with Time

> Plan the cycle. Live the line. Close the gap.

Kālsangati bridges three things that typically live in separate tools — a time
tracker, a schedule planner, and an analytics dashboard — into a single
local-first desktop application.

## What It Does

- **Niyam** (blueprint schedules): define versioned weekly layouts like
  "Spring 26" or "Exam Week"
- **Kālrekhā** (session log): import logged sessions from external time-tracker
  CSV exports or record them via the built-in stopwatch
- **Vimarśa** (reflection): three-layer comparison — prescribed / planned /
  unplanned — with pacing alerts, streak tracking, and adherence scores

All data stays in a local SQLite database. No cloud, no account, no
subscription.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run
kalsangati

# Run tests
pytest
```

## Stack

- Python + SQLite (native JSON support)
- GUI: PyQt5
- Pandas — CSV parsing and aggregation
- Watchdog — folder monitoring for live ingest
- plyer — cross-platform desktop notifications

## License

MIT

---

*Kālsangati — कालसंगति*
*Sva kālrekhā. Sva niyam. Sva pariṇāma.*
*Your timeline. Your discipline. Your transformation.*
