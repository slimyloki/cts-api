# Alarm bot — complete build reference

TAC Vista → MainManager incident pipeline for the `$this.alr` alarm list.
Sibling bot to the Indeklima bot: same MainManager API, same state-machine
philosophy, but reacts to **events** in the alarm list instead of polling
temperature logs.

---

## 1. Purpose

Monitor the live alarm list of a Schneider Electric TAC Vista 5.1.9 BMS.
When a new alarm of priority ≤ 3 appears, create an incident in MainManager.
As the alarm changes state (condition clears, operator acknowledges), append
a dated line to the incident description. When the alarm leaves the list
entirely (Vista removes it after reset + ack), append a final "resolved"
line and stop tracking it.

Does **not** change the incident StatusID — operators close tickets manually.

---

## 2. Architecture overview

```
TAC Vista $this.alr (live alarm list, constantly rewritten)
        │
        ▼
   snapshot copy to working folder
        │
        ▼
   main.py parser ◄── objects.csv (alarm_object → MainID, optional header)
        │          ◄── exceptions.txt (Vista directory paths to ignore)
        │          ◄── config.json
        ▼
   Filter: priority ≤ max_priority_number AND directory not in exceptions
        │
        ▼
   Diff against alarms_state.json (keyed by Vista ID)
        │
        ├── NEW Vista ID         → CreateIncident
        ├── Signature changed    → UpdateIncident (prepend status line)
        ├── Signature unchanged  → do nothing
        └── Gone from file       → UpdateIncident ("fully resolved"), mark RESOLVED
```

Runs via Windows Task Scheduler every 3 minutes (configurable — see config).
Unlike Indeklima, there are **no office-hours checks** — real alarms can
happen at any time, including weekends.

---

## 3. Source data: `$this.alr` alarm list

### Location
```
C:\ProgramData\Schneider Electric\TAC Vista 5.1.9\DB\$thisdb\$this.alr
```

### File format
- Encoding: **ISO-8859-1** (Danish: æ, ø, å, °)
- Separator: **tab** (`\t`)
- Line endings: **CRLF**
- **36 fields per row**, consistent across all observed data
- No header line — every line is an alarm row
- File is rewritten in-place by Vista as alarms enter/leave

### Field map (the ones the bot actually uses)

| Index | Name          | Notes                                                                                 |
|------:|---------------|---------------------------------------------------------------------------------------|
| 1     | `vista_id`    | `VISTA_SERVER#xxxxxxxx` — the trailing hex is the creation Unix epoch. **Primary key.** |
| 2     | `alarm_object`| Short alarm point name, e.g. `320-01-03901-0106_AL`. Used as key in `objects.csv`.    |
| 3     | Date 1 (hex)  | First-occurrence Unix epoch.                                                          |
| 4     | `state1`      | `0` = active, `1` = reset/returned-to-normal, `6` = info/system event                 |
| 5     | `state2`      | `0` normally, `2` for `(6,2)` info events                                             |
| 6     | Date 2 (hex)  | Last state-transition Unix epoch.                                                     |
| 7     | `priority`    | TAC Vista priority: **LOWER = MORE urgent**. 1-3 real, 9 = system noise.              |
| 10    | `user`        | "No user" or named operator e.g. `GPST (Georgi ISS)`.                                 |
| 11    | `ack_flag`    | `0` unacknowledged, `1` acknowledged.                                                 |
| 13    | `alarm_text`  | Human text. May flip to "OK" / "resat" when condition clears (not guaranteed).        |
| 18    | `count`       | How many times this exact alarm has re-triggered.                                     |
| 22    | `directory`   | Full Vista object path. **Used for exception matching — unambiguous across controllers.** |

Other fields (colours, bitfield, internal handles) are ignored but preserved
in the file; the parser just reads by index.

### Alarm lifecycle in the file

Standard TAC Vista A/B/Ack semantics map to these `(state1, state2, ack_flag, user)` signatures:

| Signature                        | Meaning                                            |
|----------------------------------|----------------------------------------------------|
| `(0, 0, 0, "No user")`           | Active, unacknowledged — the main alarm state     |
| `(0, 0, 1, "<name>")`            | Active, acknowledged                               |
| `(1, 0, 0, "No user")`           | Condition cleared (reset), not yet acknowledged   |
| `(1, 0, 1, "<name>")`            | Cleared AND acknowledged — Vista removes it soon  |
| `(6, 2, *, *)`                   | Info / system event (`$EE_Mess`) — noise, priority 9 |

When an alarm is reset AND acknowledged, Vista deletes the row. The bot
detects that as "gone from file" → fully resolved.

### Snapshot-before-read

Vista rewrites the file in-place. To avoid reading mid-write, the bot copies
the file to `working_folder\alarm_snapshot.alr` first using `shutil.copy2`
(Windows `FILE_SHARE_READ`), with up to 5 retries on `PermissionError`.
Then it parses the snapshot.

---

## 4. Mapping files

### `objects.csv` — alarm object → MainManager MainID

```
320-01-03901-0106_AL,10
320-01-03901-0106_AH,10
320-01-07931-1102_A,12
```

- **Comma-delimited, no header.** (Semicolons also accepted.)
- Lines starting with `#` and blank lines are ignored.
- Matches **column 2** of `$this.alr` (the short alarm object name).
- Any alarm whose `alarm_object` is not in this file falls back to
  `mainmanager.default_main_id` (currently `9756`). Every fallback use is
  logged with a warning so you can see what to add.

### `exceptions.txt` — ignore these alarms entirely

```
VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-0201_01-340_060X.Manuel_A
```

- **One full directory path per line** (column 22 of `$this.alr`).
- Exact match — no wildcards.
- Lines starting with `#` and blank lines are ignored.
- Alarms matching an exception are skipped entirely — no state entry
  created, no incident.

Why directory instead of alarm_object? Short names like `Manuel_A` can
appear on many controllers — matching on the full path pins the exception
to one specific physical point.

---

## 5. Config file (`config.json`)

```json
{
    "thresholds": {
        "max_priority_number": 3
    },
    "paths": {
        "vista_alarm_file": "C:\\ProgramData\\Schneider Electric\\TAC Vista 5.1.9\\DB\\$thisdb\\$this.alr",
        "objects_csv":      "C:\\ctsapi\\alarms\\objects.csv",
        "exceptions_file":  "C:\\ctsapi\\alarms\\exceptions.txt",
        "working_folder":   "C:\\ctsapi\\alarms",
        "state_file":       "C:\\ctsapi\\alarms\\alarms_state.json",
        "log_folder":       "C:\\ctsapi\\alarms\\logs"
    },
    "mainmanager": {
        "base_url": "https://rambollfm.mainmanager.dk",
        "username": "YOUR_USERNAME",
        "password": "YOUR_PASSWORD",
        "default_main_id": 9756,
        "incident_defaults": {
            "IncidentMode": "imIncident",
            "CheckwordID": 8,
            "CheckwordItemID": 277,
            "GradeID": 10,
            "StatusID": 5
        }
    },
    "log_encoding": "iso-8859-1",
    "prune_resolved_after_days": 30
}
```

### Key settings
- `max_priority_number`: fire on priorities `≤ this`. `3` means 1, 2, 3 trigger;
  4-9 are ignored. Raise to `2` to narrow, lower to `4+` to widen.
- `default_main_id`: used when an alarm's `alarm_object` isn't in `objects.csv`.
- `CheckwordID` / `CheckwordItemID`: `8` / `277` for alarms
  (Indeklima uses `21` / `281`).
- `prune_resolved_after_days`: resolved state entries older than this are
  deleted from `alarms_state.json` to keep the file bounded.

---

## 6. State file (`alarms_state.json`) — auto-generated

```json
{
  "meta": {
    "bootstrapped": true,
    "last_run_iso": "2026-04-16T13:24:11"
  },
  "alarms": {
    "VISTA_SERVER#69DF6ED9": {
      "incident_id": 26627,
      "main_id": 9756,
      "main_id_fallback_used": true,
      "alarm_object": "325-15-04011-0205-0101_AL",
      "directory": "VISTA_SERVER-LOYTEC_PORT-RHQ-34501_02_ET0_XENTA-32515_04011_0205-ZoneType06.0101_AL",
      "priority": 3,
      "initial_alarm_text": "Lavt tryk",
      "first_seen_iso": "2026-04-16T13:24:11",
      "last_state_sig": [0, 0, 1, "GPST (Georgi ISS)"],
      "last_update_iso": "2026-04-16T13:27:14",
      "status": "ACKNOWLEDGED"
    }
  }
}
```

- Keyed by `vista_id` (the full `VISTA_SERVER#xxxxxxxx` string).
- `last_state_sig` is the tuple the bot diffs on: `(state1, state2, ack_flag, user)`.
  Any change in this signature triggers a description update.
- `status` values: `OPEN`, `CLEARED`, `ACKNOWLEDGED`, `RESOLVED`,
  `BOOTSTRAPPED`. Purely informational — flow is driven by the signature diff.
- Delete this file and re-run to start fresh; the next run will bootstrap
  again.

### Bootstrap behaviour (first run)

On first run, `bootstrapped` is `false`. The bot loads all currently-active
alarms into the state file **without creating any incidents**, then sets
`bootstrapped: true` and exits. This prevents the bot from opening 66
tickets the instant it's deployed. From the next run onward, only
**new** Vista IDs (alarms that appear after deployment) create incidents.

To override (useful for first real deployment after testing):
```
python main.py --no-bootstrap
```
This treats every alarm in the current file as NEW.

---

## 7. State machine — per-alarm lifecycle

State per alarm is the `(state1, state2, ack_flag, user)` tuple. The bot
diffs run-over-run. Here are the transitions and corresponding actions:

| Prev signature             | Current signature            | Description line appended                              |
|----------------------------|------------------------------|--------------------------------------------------------|
| *(not in state)*           | any                          | **CreateIncident** with NEW alarm description          |
| `(0, 0, 0, "No user")`     | `(0, 0, 1, "<name>")`        | `acknowledged by <name>`                               |
| `(0, 0, *, *)`             | `(1, 0, *, *)`               | `condition cleared ("<current text>")`                 |
| `(1, 0, *, *)`             | `(0, 0, *, *)`               | `alarm re-activated ("<current text>")`                |
| any                        | unchanged                    | *nothing*                                              |
| any                        | *(not in file)*              | `fully resolved (removed from Vista alarm list)` + mark RESOLVED |

If multiple things change in one run (e.g. condition cleared *and* acked
between two runs), multiple lines are prepended — newest first, same as
Indeklima.

### Description format (newest on top)

```
16-04-2026 15:30 Alarm bot - fully resolved (removed from Vista alarm list)
16-04-2026 14:02 Alarm bot - acknowledged by GPST (Georgi ISS)
16-04-2026 13:45 Alarm bot - condition cleared ("ATV21 OK")
16-04-2026 13:20 Alarm bot - NEW alarm (pri 3): "ATV21 Fejl"
Object: 325-10-04806-Udsugning-Alarm-Bit0_Fejl
Directory: VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-...
```

### Incident naming

```
CTS Alarm - <alarm_object> - <alarm_text>
```

Truncated at 100 characters if needed. Example:
`CTS Alarm - 325-10-04806-Udsugning-Alarm-Bit0_Fejl - ATV61 Fejl`

---

## 8. MainManager API

Identical to the Indeklima bot except for the checkword pair and the
absence of CSV uploads. See the Indeklima reference for field-level details
on each endpoint.

### 8.1 Authentication
`POST /restapi/token` — username/password/grant_type, returns bearer
token valid for ~24 hours. Token is fetched once per run, only when the
first API call is needed (lazy init).

### 8.2 Create incident
```
POST /restapi/Incident/CreateIncident
Authorization: Bearer {token}

{
  "IncidentMode":    "imIncident",
  "MainID":          10,
  "Name":            "CTS Alarm - 320-01-03901-0106_AL - Lav Temperatur på Varmeveksler 3",
  "Description":     "16-04-2026 13:20 Alarm bot - NEW alarm (pri 2): \"Lav Temperatur på Varmeveksler 3\"\nObject: 320-01-03901-0106_AL\nDirectory: ...",
  "CheckwordID":     8,
  "CheckwordItemID": 277,
  "GradeID":         10,
  "StatusID":        5
}
```

### 8.3 Get incident (to read current Description before prepending)
`GET /restapi/Incident/GetIncident?IncidentID={id}`

### 8.4 Update description
```
POST /restapi/Incident/UpdateIncident

{
  "IncidentID":      26627,
  "IncidentRemarks": "<NEW LINE>\n<previous description>"
}
```

`IncidentRemarks` overwrites the full `Description` field, so the bot
does GET → prepend → POST on every update. This is the same dance as
Indeklima — `PUT /api/v1/incidents` does not work.

### What's NOT used vs. Indeklima

- **No document upload.** Alarms are discrete events; there's no daily CSV
  to attach. If you want the raw .alr row attached per incident, that can be
  added later (copy the Indeklima `POST /api/v1/documents` path).
- **No document delete.** Follows from the above.

---

## 9. Exception handling & edge cases

- **Vista still writing when we read**: mitigated by snapshot-copy with
  retries. If all retries fail, the run aborts cleanly (exit code 2) and
  the next run will retry — no state change.
- **Duplicate Vista ID in the file**: shouldn't happen, but the parser
  handles it by last-wins on state signature.
- **Parse error on a single row**: logged as a warning, that row is
  skipped, the rest proceed.
- **MainManager API failure on a specific alarm**: logged, state not
  updated for that alarm, the run continues with others. Next run retries.
- **Alarm reappears after being marked RESOLVED**: logged as a warning
  and ignored (wouldn't normally happen — Vista reuses the creation
  timestamp as the ID, so a "reappearance" means the exact same alarm
  instance came back, which shouldn't be possible). If you want to treat
  it as a fresh alarm, delete the state entry manually.
- **alarm_object not in objects.csv**: uses `default_main_id` (9756) and
  logs a WARNING with the alarm_object name — grep the logs to see what
  to add to `objects.csv`.

---

## 10. File structure on server

```
C:\ctsapi\alarms\
    main.py                  ← the bot
    config.json              ← all settings
    objects.csv              ← alarm_object → MainID
    exceptions.txt           ← ignored directory paths
    alarms_state.json        ← auto-generated
    alarm_snapshot.alr       ← auto-generated (temp copy of $this.alr)
    logs\
        2026-04-16.log       ← daily log file
```

### Dependencies

- Python 3.9+
- `requests` (`pip install requests`)
- Nothing else.

---

## 11. Windows Task Scheduler setup

- **Task name**: `TACVista_Alarm_Bot`
- **Trigger**: Daily, repeat every **3 minutes** for duration of 1 day, indefinitely
- **Start time**: 00:00 (24/7 — alarms aren't office-hours only)
- **Action**: `C:\Users\...\Python313-32\python.exe`
- **Arguments**: `"C:\ctsapi\alarms\main.py"`
- **Working directory**: `C:\ctsapi\alarms`
- **Run whether user is logged on or not** (same gotcha as Indeklima —
  must manually set after importing from XML)
- **Execution time limit**: 5 minutes
- **Multiple instances**: Ignore new

---

## 12. CLI flags

```
python main.py                # normal run
python main.py --dry-run      # log intended actions, make no API calls,
                              #   do not update state file
python main.py --parse-only   # parse + filter + print rows, exit
python main.py --no-bootstrap # treat all current alarms as NEW
                              #   (overrides the first-run bootstrap)
python main.py --config PATH  # use a non-default config file
```

`--dry-run` is the safe way to verify behaviour before first real run.
It logs `[DRY]` on every line that would have triggered an API call.

---

## 13. Deployment checklist

1. Create `C:\ctsapi\alarms\` directory.
2. Copy `main.py`, `config.json`, `objects.csv`, `exceptions.txt` into it.
3. Edit `config.json` — set `mainmanager.username` and `password`, verify
   paths.
4. Populate `objects.csv` with as many alarm_object → MainID mappings as
   you have. Leave it empty to fall back to MainID `9756` for everything.
5. Populate `exceptions.txt` with any directory paths to ignore. (The
   `Manuel_A` example from the spec is included.)
6. Run `python main.py --parse-only` to confirm parsing works on the live
   file.
7. Run `python main.py --dry-run` to see what would happen on a real run.
8. Run `python main.py` once manually — this is the **bootstrap** run. It
   loads all currently-active alarms into state and exits without
   creating tickets. Verify `alarms_state.json` was created.
9. Register the Task Scheduler task (3-minute recurrence).
10. After 3-10 minutes, check the log file for the second run — should
    report `created=0 updated=0 resolved=0 unchanged=<N>`. From here, only
    genuinely new alarms will create incidents.

---

## 14. Troubleshooting

- **Delete `alarms_state.json`** to start fresh. Next run will re-bootstrap.
- **Too many "No mapping for..." warnings in logs** — populate `objects.csv`.
  Every unique alarm_object in the log is a candidate.
- **Incident was created for a noise alarm** — add its full directory
  path (column 22 of `$this.alr`) to `exceptions.txt`.
- **Snapshot PermissionError** — Vista has the file locked in a way that
  doesn't allow shared read. Check Vista version; fallback is to add a
  longer `retry_delay` in `snapshot_alarm_file`.
- **Priority 9 alarm got an incident** — you must have set
  `max_priority_number` to `9` or higher. Set it back to `3`.
- **Auth failure** — verify credentials in `config.json`. Token fetch
  happens once per run; there's no cached token file.
- **Log file is huge** — logs rotate daily by filename (`YYYY-MM-DD.log`)
  but aren't auto-deleted. Set up a separate retention task if needed.

---

## 15. Differences from Indeklima bot — quick reference

| Aspect                  | Indeklima                               | Alarm bot                                |
|-------------------------|------------------------------------------|------------------------------------------|
| Source                  | Per-room `*.TXT` trend logs in `$wrk\`   | Single `$this.alr` file in `$thisdb\`    |
| Data model              | Polling temperature values               | Reacting to events                       |
| Classification          | Threshold check (22-24 °C)               | Priority filter + state-diff             |
| Mapping file            | `rooms.json` + `MMcomponents.csv`        | `objects.csv` (single file)              |
| Key identifier          | `log_id` (room number)                   | Vista ID (`VISTA_SERVER#xxxxxxxx`)       |
| CSV attachment          | Yes, re-uploaded every run               | No                                       |
| Office hours            | Yes, configurable                        | No — 24/7                                |
| Incident per            | Room, per day                            | Alarm instance (each Vista ID)           |
| CheckwordID / ItemID    | 21 / 281                                 | 8 / 277                                  |
| Default MainID          | N/A (must be in CSV)                     | 9756 (fallback)                          |
| Cadence                 | 15 min                                   | 3 min                                    |
| Bootstrap               | None (creates on first run if needed)    | Yes — first run is bootstrap-only        |

---

## 16. Key decisions / lessons

1. **Vista ID as primary key** — the `VISTA_SERVER#xxxxxxxx` string is stable
   across the life of one alarm instance. The trailing hex is the creation
   Unix epoch, so it's globally unique per alarm.
2. **State signature diff, not text parsing** — alarm text changes
   inconsistently when condition clears (sometimes "OK", sometimes
   unchanged). The `(state1, state2, ack_flag, user)` tuple is the reliable
   state indicator.
3. **Directory-based exceptions, not alarm_object** — short names can
   collide across controllers.
4. **Bootstrap on first run** — without this, deploying the bot onto a
   running BMS would spam 60+ tickets for pre-existing alarms.
5. **Snapshot-then-parse** — safer than streaming the live file; cost is
   one file copy every 3 min, which is negligible.
6. **No status change on resolve** — operators keep ticket closure as a
   human decision. Flip `StatusID` manually in `UpdateIncident` if that
   changes.
7. **Priority semantics** — TAC Vista is counter-intuitive: priority 1 is
   most urgent, 9 is least. `max_priority_number: 3` filters *for* the top
   three urgent tiers.
8. **Encoding is ISO-8859-1, same as Indeklima** — don't try UTF-8,
   Danish characters will break.
