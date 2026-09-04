# Kālsangati frontend

Empty on purpose.

Kālsangati is moving toward a hybrid shape: the desktop process stays
the owner of the data and hosts a small local API, and the
screens that benefit from richer layout move to HTML and JavaScript
panels. This directory is where those panels will live. It is created
ahead of them so the boundary exists before anything is built across it.

The first screen to migrate will be Vimarśa (the reflection view).
Nothing here yet.

## The one rule

**No cross-imports, in either direction.** `backend/` never imports from
`frontend/`. `frontend/` never imports from `backend/`. They talk over
HTTP on a local port, and nothing else.

This costs almost nothing to hold now and is expensive to retrofit
later. It is what makes the two halves separable into their own
repositories if their release cadences ever diverge. That split is not
planned, and the discipline is worth keeping whether or not it ever
happens.

Related: no shared tooling at the repository root. No root
`package.json`, no root `pyproject.toml`. Each side owns its own
ecosystem.

## What lands here

- A JavaScript stack, likely plain JavaScript with CDN libraries first
  and a bundler only once it earns one.
- Panels for the reflection view, the schedule editor grid, the label
  manager, the task planner, and multi-week trend charts.
- A startup check against the backend's reported API version, so a
  mismatched pair refuses to run rather than failing in some confusing
  way later.

The stopwatch and desktop notifications are staying native. Session
writes go straight to SQLite through the backend's service layer and
never travel over HTTP.
