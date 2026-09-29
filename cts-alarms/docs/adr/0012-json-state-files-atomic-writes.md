# ADR-0012: JSON state files with atomic writes

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

Each run is a fresh process ([ADR-0002](0002-scheduled-python-script-on-cts-server.md)) that must
know what it saw last time: per `vista_id`, the last state signature, the MainManager incident ID,
and whether the bot is bootstrapped. A crash mid-write must not leave a corrupt file, because the
next run 5 minutes later would then either fail to load or — worse — treat every alarm as new and
create a burst of incidents.

## Decision

Two JSON files in the working folder, written whole, via temp file + `os.replace`:

| File | Owner | Shape | Writer |
|------|-------|-------|--------|
| `alarms_state.json` | incident pipeline | `{"meta": {"bootstrapped", "last_run_iso"}, "alarms": {vista_id: entry}}` | `save_state()` `main.py:276-281` |
| `csv_state.json` | CSV audit | `{vista_id: {"sig", "directory", "alarm_object", "initial_alarm_text", "priority", "vista_id"}}` | `save_csv_state()` `main.py:438-443` |

Entry fields for `alarms_state.json` are fixed by `make_state_entry()` (`main.py:717-732`):
`incident_id`, `main_id`, `main_id_fallback_used`, `alarm_object`, `directory`, `priority`,
`initial_alarm_text`, `first_seen_iso`, `last_state_sig`, `last_update_iso`, `status`, plus
`resolved_iso` once RESOLVED. Full reference: [docs/reference/state-files.md](../reference/state-files.md).

Rules in code:

- `save_state()` is called after **every** mutation in the incident loop (`main.py:873,931,984,990`),
  so a crash loses at most the alarm being processed.
- `--dry-run` skips API calls but still writes `alarms_state.json`: new alarms are recorded with
  `incident_id: null` (later treated like bootstrapped entries) and vanished alarms are marked
  RESOLVED without `resolved_iso`, so they are never pruned (`main.py:868-873`, `962-966`, `990`,
  `292-294`). No dry run appears in the committed logs.
- `load_state()` tolerates an older flat layout (no `meta`) by wrapping it (`main.py:269-272`).
- RESOLVED entries are pruned after `prune_resolved_after_days` (30) (`prune_state()`, `main.py:284-302`);
  `csv_state.json` drops resolved entries immediately (`main.py:521-523`).
- `indent=2, ensure_ascii=False`: human-readable, Danish characters intact.

## Consequences

### Positive
- Atomic rename means the file is always either the old or the new complete version.
- Readable with any text editor; state can be inspected or hand-corrected (the build reference
  suggests "delete the state entry manually" for a reappearing ID).
- No database on the CTS server; zero extra dependencies.

### Negative
- Whole-file rewrite on every change: 122 entries today, trivial; would not scale to years of
  history — which is why history goes to CSV and RESOLVED entries are pruned.
- Pruning after 30 days means `alarms_state.json` is **not** a history; the MainManager incident
  ID for an alarm older than 30 days exists nowhere but in MainManager and the logs.
- Two state files with overlapping content (`csv_state` entries duplicate `alarm_object`,
  `directory`, `priority`, text) can disagree if one write fails.
- The `.tmp` file is left behind if the process dies between write and rename; harmless, overwritten next run.
- Legacy values survive: 2 entries still carry `"status": "ACKNOWLEDGED"` from the pre-refactor vocabulary.
- Running two instances concurrently would race; prevented only by Task Scheduler `IgnoreNew`.

## Alternatives considered

| Option | Why not |
|--------|---------|
| SQLite on the CTS server | Would have avoided pruning and given history; not chosen originally. Now proposed in [ADR-0015](0015-sql-storage-for-alarm-history.md). |
| In-place JSON write | Not crash-safe; rejected in favour of temp + replace. |
| Single combined state file | The CSV audit was added later as an independent feature; separate file keeps the pipelines decoupled. |

## Evidence

- `main.py:260-302` state load/save/prune; `main.py:430-443` CSV state
- `config.json -> paths.state_file`, `paths.csv_state_file`, `prune_resolved_after_days = 30`
- `alarms_state.json`: `meta = {"bootstrapped": true, "last_run_iso": "2026-09-28T17:55:12"}`, 122 entries (NORMAL 63, RESOLVED 50, ACTIVE + ACKNOWLEDGED 5, ACKNOWLEDGED 2, ACTIVE 2)
- `csv_state.json`: 180 entries
- `Alarm_bot_build_reference.md:191-223`
