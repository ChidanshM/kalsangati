# Naming Guide

Kālsangati uses Sanskrit names internally — in the GUI, the database, and
the codebase. This document is the single reference for every term, its
meaning, and how it maps to a technical concept.

---

## The Architecture

| Sanskrit | Transliteration | Meaning | Technical Layer |
|----------|----------------|---------|----------------|
| कालसंगति | Kālsangati | Coherence with Time | The application itself |
| नियम | Niyam | Personal discipline; the rule you set for yourself | Blueprint schedule — the weekly plan you design and hold yourself to |
| कालरेखा | Kālrekhā | The line of time; the trace | The session log — time as it was actually lived |
| कालचक्र | Kālachakra | The wheel of time | The weekly cycle that completes and returns |
| विमर्श | Vimarśa | The mind examining itself | The reflection engine — analytics, gap detection, pattern surfacing |
| परिणाम | Pariṇāma | Transformation; the result of process | The adjusted Niyam generated from Vimarśa findings |

---

## Usage Rules

### External-facing surfaces (English leads)

README headline, GitHub repository description, tagline, App Store listing,
social media, and any surface a first-time visitor sees before engaging:

```
Kālsangati — Coherence with Time
Plan the cycle. Live the line. Close the gap.
```

Introduce Sanskrit terms with their English meaning on first use:
```
"your Niyam (blueprint schedule)"
"the Vimarśa panel (reflection)"
```

### Internal surfaces (Sanskrit primary)

GUI labels, sidebar navigation, dialog titles, DB table names,
module names, and documentation for contributors:

```
Sidebar:   Niyam  |  Kālrekhā  |  Vimarśa  |  Task Planner
DB tables: niyam, kalrekha, kalachakra, projects, tasks, settings
Modules:   niyam.py, vimarsha.py, analytics.py, notifications.py
```

### Code conventions

Use ASCII transliterations in all file names, variable names,
function names, and SQL identifiers:

| Display (GUI/docs) | Code / DB |
|--------------------|-----------|
| Kālsangati | kalsangati |
| Niyam | niyam |
| Kālrekhā | kalrekha |
| Kālachakra | kalachakra |
| Vimarśa | vimarsha |
| Pariṇāma | parinama |

---

## The Philosophy in One Flow

Kālachakra turns — the week begins again.
You set your Niyam — the blueprint of how this cycle should be lived.
Time moves. Sessions are logged.
The Kālrekhā takes shape — the actual line of your lived week, traced against your plan.
Kālsangati is measured — how coherent was the line with the intention?
Vimarśa surfaces the gap — not as failure, but as signal.
Pariṇāma adjusts the Niyam — and the Kālachakra turns again.

---

## Motto

> *Sva kālrekhā. Sva niyam. Sva pariṇāma.*
> Your timeline. Your discipline. Your transformation.

---

## Pronunciation Guide

| Term | Approximate pronunciation |
|------|--------------------------|
| Kālsangati | kaal-san-GAH-tee |
| Niyam | NEE-yum |
| Kālrekhā | kaal-REK-haa |
| Kālachakra | kaal-AH-chuck-ruh |
| Vimarśa | vih-MAR-shuh |
| Pariṇāma | puh-rih-NAA-muh |
