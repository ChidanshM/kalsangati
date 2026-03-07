# Notifications

Kālsangati fires desktop notifications before each planned time block starts, keeping you connected to your ideal schedule in real time.

---

## How It Works

A background thread starts when Kālsangati launches. Every 60 seconds it:

1. Reads today's blocks from the active `schedule_version` JSON
2. Reads `notify_lead_minutes` from the `settings` table
3. For each block whose start time is within the lead window:
   - Checks whether a notification has already been fired for this block today
   - Checks whether the stopwatch is already tracking the correct activity
   - If neither condition is met, fires a desktop notification

The notification payload contains:
- **Title:** the canonical activity name
- **Message:** the planned duration for that block

---

## Platform Setup

### Linux (primary)

Notifications use `plyer` with the `libnotify` backend. Requires `notify-send` to be available:

```bash
# Ubuntu / Debian
sudo apt install libnotify-bin

# Arch
sudo pacman -S libnotify
```

No additional configuration needed. Notifications appear in your desktop notification centre (GNOME, KDE, etc.).

### Windows (planned)

Will use `plyer` with the Windows toast backend via `win10toast` or `winotify`. No setup required beyond installation.

### macOS (planned)

Will use `plyer` with the `osascript` backend. Requires granting notification permissions to Terminal or the Kālsangati app bundle in System Preferences → Notifications.

---

## Configuration

Open **Settings** in Kālsangati to configure notifications:

| Setting | Default | Description |
|---------|---------|-------------|
| Lead time | 5 minutes | How many minutes before a block starts to fire the notification |
| Notifications enabled | True | Master toggle for all notifications |
| Quiet hours | None | Time range during which notifications are suppressed (e.g. 23:00–07:00) |

Settings are stored in the `settings` table:

```
notify_lead_minutes     = 5
notifications_enabled   = true
quiet_hours_start       = null
quiet_hours_end         = null
```

---

## Suppression Logic

A notification is **not** fired if:

- The stopwatch is already tracking the activity that is about to start
- The notification was already fired for this block today (no duplicates)
- The current time falls within the configured quiet hours
- Notifications are globally disabled in settings

---

## Troubleshooting

**Notifications not appearing on Linux:**

```bash
# test notify-send directly
notify-send "Kālsangati test" "Notifications are working"

# check plyer is installed
pip show plyer

# check Kālsangati log output for notification errors
chronos --log-level debug
```

**Notifications firing at the wrong time:**

Check that your system clock is correct and that the timezone set in Kālsangati (Settings → Timezone) matches your local timezone.

**Too many notifications:**

Increase the lead time or enable quiet hours in Settings. You can also disable notifications for specific days of the week (weekend toggle).
