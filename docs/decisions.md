# Architecture Decision Records

This file is the log of significant technical decisions for Kālsangati. Entries are append-only and reverse-chronological. Each decision follows a lightweight [ADR](https://adr.github.io/) format: Context, Decision, Consequences, Alternatives Considered.

An entry lands here when a decision has structural or convention-level consequences for future code — not for every judgement call. Small implementation choices live in code comments; unit-level scoping decisions live in unit notes; the parking lot of deferred decisions with reopen conditions lives in the project's `SKILL-state.md §14`.

---

## ADR-0001 — Retain mypy strict in a solo codebase

**Status:** Accepted · 2026-04-22
**Deciders:** Chidansh (maintainer)
**Related pitfalls:** #14 (`Optional[X]` vs `X | None`), #29 (`fetchone()` returns `Any`)

### Context

Kālsangati is a solo-maintained codebase with roughly 8,700 lines of code and 149 tests as of Unit 4 (extended further by subsequent units). The project uses `mypy --strict` on every module, with `python_version = "3.10"` in `pyproject.toml`. All new code must be fully annotated; `Any` requires explicit justification.

Static type checking is often defended on collaborative grounds — "helps the team stay on the same page." A one-person codebase weakens that argument by construction. The question this ADR settles: given no team, does the ongoing maintenance cost of mypy strict earn its keep?

Recurring costs already observed:

- Per-unit budget of 1–2 rounds of type-error cleanup after feature code lands.
- Sandbox-authored code frequently produces mypy diagnostics that don't reproduce locally, requiring a second-pass reconciliation.
- Some third-party stubs are incomplete (e.g., `sqlite3.Cursor.fetchone()` returns `Any` — pitfall #29), forcing explicit casts or annotations that would otherwise be inferred.
- `Optional[X]` vs `X | None` convention drift catches new code out of habit (pitfall #14).

### Decision

Retain mypy strict. Every function and method carries complete parameter and return annotations. No `Any` without a written justification (either in a comment or in the surrounding docstring).

Three reasons, in priority order:

1. **Annotations serve as first-class documentation.** In a solo codebase, the future maintainer is the same person, but not the same *instance* of that person. Six months from now, the type signature of a function is often the fastest way to remember what it does — faster than reading its body, faster than looking up its tests. Annotations displace docstring boilerplate ("Args: conn (sqlite3.Connection): the database connection") without losing information.

2. **Mypy enforces the service-layer boundary contract.** The six-service plan (see `../SKILL-state.md §9`) hinges on services being reachable by both PyQt5 and (eventually) FastAPI without either layer knowing about the other. That property is only enforceable if the service signatures are precise and machine-checked. `def commit_stopwatch_session(conn: sqlite3.Connection, activity: str, start_time: datetime, end_time: datetime, ...) -> CommitResult` is a contract in a way `def commit_stopwatch_session(conn, activity, start_time, end_time)` cannot be. Under Read A productization, this contract is what makes the backend architecturally installable-without-the-frontend even before that install path exists.

3. **Contributor readiness.** Kālsangati is MIT-licensed and open-source. Contributor onboarding cost drops materially when the type checker can catch a wrong argument order or a missing keyword before code review has to. A contributor floor set at "code passes mypy strict" is high enough to keep quality up and low enough that any experienced Python developer already clears it.

### Consequences

- **1–2 rounds of type-error cleanup per unit is baked into the unit budget.** Not a defect to eliminate; a category of work to plan for.
- **New code convention: `sqlite3.Row | None` returned from single-row queries requires an explicit local variable annotation before return**, not a bare `return cursor.fetchone()`. Documented as pitfall #29.
- **PEP 604 unions (`X | None`) are canonical**; `Optional[X]` is disallowed by convention. Documented as pitfall #14. Some `Optional[X]` still exists in older code — grandfathered until the next touch of the containing module.
- **All dataclasses use keyword arguments at construction sites.** `@dataclass(slots=True)` does not enforce type hints at runtime; positional construction can silently swap fields of the same runtime type. Documented as pitfall #25.
- **CI runs mypy strict on Python 3.10, 3.11, and 3.12.** A pass on one version does not guarantee a pass on the others; the matrix catches version-specific stub differences.
- **Sandbox-vs-local mypy divergence is a known friction.** When Claude authors code in a sandbox without mypy available, the code returns for a local three-tool check (ruff + mypy + pytest) before merge. This is the origin of the "1–2 cleanup rounds per unit" line item.

### Alternatives Considered

- **Drop mypy entirely.** Considered on solo-codebase grounds. Rejected because the annotations-as-documentation value stands alone even without the type checker running.
- **Keep annotations, drop `--strict`.** Considered as a middle ground — accept the docs value, skip the enforcement cost. Rejected because unenforced annotations rot: they drift from truth and become worse than no annotations at all. If annotations matter, the checker matters.
- **Move to `pyright` / `pylance`.** Considered as a possible speed improvement. Deferred: mypy's ecosystem integration (pre-commit hooks, GitHub Actions, IDE plugins) is currently better-established for our workflow. Revisit if mypy performance becomes a real blocker; not now.
- **Introduce `mypy --strict` only on `core/` and `services/`, leave `gui/` and tests on plain mypy.** Considered as a scope reduction. Rejected because `gui/` calls into `services/` and the whole point of the service-layer boundary is that both sides of the call are typed. Half-typed enforcement invites the drift it's supposed to prevent.

### Related

- `SKILL-state.md §9` — service layer plan (the primary beneficiary of strict typing).
- `SKILL-core.md §5` — Code Conventions Checklist, where the mypy strict requirement is enshrined per-file.
- `SKILL-state.md §15` — Pitfalls log, entries #14, #25, #29 (all mypy-related).
