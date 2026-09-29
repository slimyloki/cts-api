# ADR-0011: Per-directory CSV audit log for all priorities

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code; not in `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

The incident pipeline only sees priority ≤ 2 alarms outside the exception list, and the state file
prunes RESOLVED entries after 30 days. That leaves no durable record of priority-3 alarms, excepted
points, or anything older than a month. The owner wants to "follow all the alarms that have been
created" and eventually analyse recurrence — which requires an append-only history of *every*
alarm transition. The build reference does not mention this feature; it was added shortly after
go-live: the first `CSV audit` log line and the earliest CSV rows are from 2026-04-20 23:37
(`logs/2026-04-20.log`; e.g. `VISTA_SERVER#67969A95`).

## Decision

A second, independent pipeline — `run_csv_logging()` (`main.py:446-528`) — runs **before** the
priority/exception filter, on all parsed rows except `(state1=6, state2=2)` system events:

- Keeps its own signature state in `csv_state.json` ([ADR-0012](0012-json-state-files-atomic-writes.md)).
- On first sight or signature change, appends one row per event (`classify_csv_event()`) to a CSV
  file **per Vista directory**: `csv/<sanitize_filename(directory)>.csv`
  (`sanitize_filename()`, `main.py:324-334`: replaces `/ \ : " ' space # $`, truncates at 150 chars).
- Semicolon-delimited, UTF-8, header written when the file is created:
  `datetime;event;vista_id;alarm_object;directory;alarm_text;priority;user;ack_flag;state1;state2;date1_hex;date1_human;date2_hex;date2_human;count;status_label`
  (`CSV_HEADER`, `main.py:309-313`). `;` in alarm text is replaced by `,`.
- When a `vista_id` disappears: if last seen ACTIVE, a synthetic `NORMAL` row is written first
  (`main.py:502-511`), then a `RESOLVED` row with empty user/state fields (`csv_resolved_row()`).
- Wrapped in `try/except` at the call site (`main.py:804-808`): failure is logged and never stops
  the incident pipeline.

Full column reference: [docs/reference/csv-audit-format.md](../reference/csv-audit-format.md).

## Consequences

### Positive
- Complete transition history for all priorities since the feature went live: 431 files, 20,584 rows,
  ~5.3 MB of data (6.3 MB on disk) — small and greppable.
- Gives the numbers the owner asks for today (FIRST_SEEN 1,258; ACTIVE 8,644; NORMAL 9,455;
  RESOLVED 1,078; ACKNOWLEDGED 148) and is the import source for
  [ADR-0015](0015-sql-storage-for-alarm-history.md).
- Decoupled from MainManager: works even when the API is down.

### Negative
- One file per directory (431 and growing) is awkward for cross-alarm queries; every analysis
  starts with "concatenate all CSVs".
- Timestamps are local-time `dd-mm-yyyy HH:MM:SS` strings with no timezone; `date1/date2` are
  duplicated as hex and human text. Fine for humans, needs parsing for SQL.
- `sanitize_filename()` truncation at 150 chars could merge two directories into one file if their
  first 150 characters coincide; not observed.
- Personal data (operator names in `user`) is written to every row and now committed to GitHub
  ([ADR-0014](0014-commit-runtime-data-for-migration.md)).
- Stale comment: `main.py:519-520` says resolved entries are pruned "older than 7 days"; the code
  deletes them immediately (`main.py:521-523`).
- No events for `count` (re-trigger) changes or priority changes; those are only visible as
  column values when another event fires.

## Alternatives considered

| Option | Why not |
|--------|---------|
| One CSV for everything | Would have been easier to query; per-directory chosen (reason not recorded — probably to browse a single point's history by opening one file). |
| Write to SQLite from the start | Not chosen; now proposed in ADR-0015. |
| Rely on MainManager as history | Only priority ≤ 2, only ticketed alarms, no structured transitions. |

## Evidence

- `main.py:306-528` whole CSV section; `main.py:801-808` call site (before filter)
- `csv/` 431 files; sample `csv/325-02-03901-Indblæsning-Alarm-Bit0_Fejl.csv` (FIRST_SEEN 24-06-2026 -> NORMAL -> RESOLVED 13-07-2026)
- `config.json -> paths.csv_folder`, `paths.csv_state_file`
- Log line "CSV audit: logged N events across alarm directories"
