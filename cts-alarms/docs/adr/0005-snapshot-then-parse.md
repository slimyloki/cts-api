# ADR-0005: Snapshot `$this.alr` before parsing

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

TAC Vista rewrites `$this.alr` in place whenever an alarm changes and keeps it open. Reading the
live file line by line risks seeing a half-written file (truncated rows, mixed old/new content) or
a `PermissionError` from the Windows share mode. The file is small (180 rows, 36 tab-separated
fields per row, ISO-8859-1) so copying is cheap.

## Decision

Every run first copies the live file to `working_folder\alarm_snapshot.alr` and then parses only
the snapshot (`snapshot_alarm_file()`, `main.py:157-168`; call site `main.py:786-794`):

- `shutil.copy2(src, dst)`, up to 5 attempts, 0.5 s sleep between attempts, each failure logged as
  WARNING.
- If all attempts fail: `log.error("Could not snapshot alarm file")`, **exit code 2**, no state
  change. The next scheduled run retries.
- Parsing (`parse_alarm_file()`, `main.py:171-202`) opens the snapshot with
  `encoding=cfg.log_encoding` (default `iso-8859-1`) and `errors="replace"`; rows with fewer than
  36 fields or non-integer numeric fields are skipped with a WARNING; the rest proceed.

The snapshot is overwritten each run and is not archived; the committed `alarm_snapshot.alr` in the
repo is simply the last one (180 rows: pri1 18, pri2 53, pri3 60, pri9 49).

## Consequences

### Positive
- Parsing works on an immutable file; a mid-write read cannot corrupt state.
- Abort-and-retry semantics are simple and safe: nothing is written before the snapshot succeeds.
- The snapshot doubles as a debugging artefact (`--parse-only` prints what was parsed).

### Negative
- The copy itself can still capture a partially written file if Vista's write is not atomic;
  the per-row 36-field check is the only guard. Never observed: zero "expected 36 fields" and zero
  "parse error" warnings in 45,610 logged runs (Apr–Sep 2026).
- Ties the bot to local filesystem access on the CTS server; nothing else can perform this step
  (drives the options in [ADR-0016](0016-cts-server-vs-vps-responsibility-split.md)).
- Only the latest snapshot is kept; historical `.alr` contents cannot be replayed. History exists
  only through the CSV audit ([ADR-0011](0011-per-directory-csv-audit-log.md)).
- Rows skipped for parse errors vanish silently from that run; if a tracked `vista_id` is skipped
  it will be treated as "gone" and marked RESOLVED. Potential false resolution; not observed but
  not guarded.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Read the live file directly | Mid-write reads and share-mode errors; the build reference names this as the motivating problem. |
| Open with explicit Win32 `FILE_SHARE_READ|WRITE` | More code for the same outcome; `copy2` retry is simpler. |
| Keep dated snapshots (`alarm_snapshot_<ts>.alr`) | Would have given a replayable history; not done. Could be added cheaply — tracked as [T-086](../TODO.md) as a migration aid. |

## Evidence

- `main.py:157-168` `snapshot_alarm_file()`; `main.py:789-794` exit code 2 path
- `main.py:171-202` `parse_alarm_file()`; `main.py:85` `EXPECTED_FIELDS = 36`
- `config.json -> paths.vista_alarm_file`, `paths.working_folder`, `log_encoding`
- `Alarm_bot_build_reference.md:103-109` ("Snapshot-before-read"); `:337-339`
- `alarm_snapshot.alr` in repo (last snapshot, 180 rows)
