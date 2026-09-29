# ADR-0002: Scheduled Python script on the CTS server

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

The only source of alarm data is the TAC Vista 5.1.9 live alarm list
`C:\ProgramData\Schneider Electric\TAC Vista 5.1.9\DB\$thisdb\$this.alr` on the CTS server
(Windows Server 2016, the building-management system host). Vista rewrites the file in place
(build reference §"Snapshot-before-read"). Whether TAC Vista 5.1.9 offers an alarm API, database
view or export that could replace file reading is unknown/unverified; the bot as built depends on
local file access on that machine.

The bot's job is to turn priority-1/2 alarms into MainManager incidents (Ramboll FM,
`https://rambollfm.mainmanager.dk`) and keep those incidents updated. A sibling "Indeklima bot" by
the same author already ran the same way (Python + Task Scheduler + MainManager REST), so the
pattern was known to work in this environment.

## Decision

Run the bot as a single-file Python 3 script (`main.py`, one third-party dependency: `requests`)
executed by Windows Task Scheduler on the CTS server under the `GPST` account with a stored
password (`LogonType=Password`, runs whether or not the user is logged on):

| Setting | Value | Source |
|---------|-------|--------|
| Task | `\TACVistaLogs\TACVista_Alarm_Bot` | `TACVista_Alarm_Bot.xml` |
| Cadence | every 5 minutes, 24/7, no office-hours check | `<Interval>PT5M</Interval>` |
| Interpreter | 32-bit Python 3.13 (`...\Python313-32\python.exe`) | `<Command>` |
| Working dir | `C:\priorityalarmsapi` | `<WorkingDirectory>`, `config.json -> paths` |
| Overlap | `MultipleInstancesPolicy=IgnoreNew`, `ExecutionTimeLimit=PT5M` | XML `<Settings>` |
| Failure | `RestartOnFailure` 3x at 1-minute interval | XML `<Settings>` |

Each run is stateless in memory: it loads state from disk, does one pass, saves state, exits
(exit codes 0 / 1 unhandled / 2 config or snapshot failure).

## Consequences

### Positive
- No service to install; a run that crashes is simply retried 5 minutes later.
- All persistence is on disk (JSON + CSV), so a run can be reproduced from its inputs.
- Same operational model as the Indeklima bot; one operator knows both.

### Negative
- The CTS server must have outbound HTTPS to `rambollfm.mainmanager.dk`; the MainManager
  credentials live on the BMS host (see [ADR-0013](0013-secrets-in-config-json.md)).
- A 5-minute poll misses transitions shorter than one interval; the code has to infer them
  ("returned to NORMAL (missed between polls)", `main.py:951-956`).
- Runs under a personal-style account (`GPST`, `LogonType=Password`); a password change breaks the task.
- Logging is per run with no rotation (`setup_logging()`, `main.py:44-64`): ~2–4 MB/day, 356 MiB so far.
- No test suite, no packaging; deployment is copy-the-file.
- Docstring drift: `main.py:7` says "every 3 min", the task runs every 5.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Windows service / long-running daemon | More moving parts; a scheduled task with `IgnoreNew` already gives single-instance polling. |
| Run the logic on another host and read the file over SMB | `$this.alr` is locked/rewritten by Vista; remote reads are unreliable. Snapshot must be local ([ADR-0005](0005-snapshot-then-parse.md)). |
| Vista-side integration (OPC, event scripting) | Not available/known for TAC Vista 5.1.9 in this environment; unverified. |

## Evidence

- `TACVista_Alarm_Bot.xml` (whole file)
- `main.py:1-15` module docstring; `main.py:1001-1024` `main()` and exit codes
- `main.py:36` `DEFAULT_CONFIG_PATH = r"C:\priorityalarmsapi\config.json"`
- `config.json -> paths.*` all under `C:\priorityalarmsapi`
- Logs: 288 runs/day observed, 45,612 runs from 2026-04-17 to 2026-09-28
- `Alarm_bot_build_reference.md:47-50` (3-minute cadence, no office hours) — cadence has drifted to 5 min
