# Niyam Format

This document specifies the CSV format for importing an ideal weekly schedule into Kālsangati, and describes the internal JSON representation used to store it.

---

## CSV Import Format

When importing an Niyam from a spreadsheet, the CSV must follow this schema:

### Required Columns

| Column | Type | Description |
|--------|------|-------------|
| `activity` | string | The canonical activity name (must match or be mappable via label system) |
| `weekly_hours` | float | Total planned hours for this activity per week |
| `monday` | float | Planned hours on Monday |
| `tuesday` | float | Planned hours on Tuesday |
| `wednesday` | float | Planned hours on Wednesday |
| `thursday` | float | Planned hours on Thursday |
| `friday` | float | Planned hours on Friday |
| `saturday` | float | Planned hours on Saturday |
| `sunday` | float | Planned hours on Sunday |

### Optional Columns

| Column | Type | Description |
|--------|------|-------------|
| `weekly_pct` | float | Percentage of waking hours (informational only, not used in calculations) |
| `notes` | string | Free text notes for the activity |

### Example

```csv
activity,weekly_hours,monday,tuesday,wednesday,thursday,friday,saturday,sunday
00-Divine,8.4,1.2,1.2,1.2,1.2,1.2,1.2,1.2
01-02-el,22.6,3.2,3.4,1.0,3.4,3.4,0.0,8.2
01-03-RshWrk,9.8,4.0,0.0,3.0,2.8,0.0,0.0,0.0
04-workout,10.0,0.0,1.8,2.2,1.8,0.0,1.8,2.4
02-kew,18.8,2.8,2.6,2.6,2.6,2.4,2.8,3.0
sleep,35.0,5.0,5.0,5.0,5.0,5.0,5.0,5.0
traveling,9.0,1.4,1.4,1.4,1.4,1.4,1.2,0.8
```

### Rules

- Activity names should match canonical labels in your label system. If they don't, Kālsangati will flag them during import and ask you to map them.
- Zero values are valid — an activity with 0 hours on a given day will not generate blocks for that day but will still appear in comparison views.
- The `weekly_hours` column must equal the sum of all daily columns. Kālsangati validates this and warns on mismatch (does not block import).
- Column names are case-insensitive.

---

## Time Block Format

The CSV above represents *daily totals*. For the **weekly grid view** and **notifications**, Kālsangati also needs to know the specific start and end time of each block.

When you import a daily-totals CSV, Kālsangati creates blocks with placeholder times that you can adjust in the schedule editor. Alternatively, you can define precise time blocks directly in the GUI, which stores them in the internal JSON format.

### Internal JSON Schema

Each schedule version stores its time blocks as a JSON blob in the `niyam` table:

```json
{
  "monday": [
    {
      "activity": "04-workout",
      "start": "06:00",
      "end": "07:48",
      "duration_h": 1.8
    },
    {
      "activity": "01-02-el",
      "start": "09:00",
      "end": "11:00",
      "duration_h": 2.0
    }
  ],
  "tuesday": [
    {
      "activity": "04-GYM",
      "start": "06:00",
      "end": "07:48",
      "duration_h": 1.8
    }
  ]
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `activity` | string | Canonical activity name |
| `start` | string | Start time in 24-hour format `HH:MM` |
| `end` | string | End time in 24-hour format `HH:MM` |
| `duration_h` | float | Duration in hours (derived from start/end, stored for convenience) |

### Notes

- Times use 24-hour format. Blocks do not span midnight — split overnight blocks into two entries.
- Overlapping blocks on the same day are allowed (the schedule editor will show them as overlapping and warn you, but will not prevent saving).
- Days with no blocks can be omitted from the JSON entirely or included as empty arrays.

---

## Exporting the Current Ideal Schedule

You can export any saved version as a CSV from **Weekly View → Versions → Export as CSV**. The export follows the daily-totals format above, with an additional `time_blocks_json` column containing the raw JSON for complete round-trip fidelity.
