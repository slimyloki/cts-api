# ADR-0008: Directory-based exception list

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

Some priority-1/2 alarms are known and intentional — a manually overridden object, a forced I/O
point, a broken sensor with a pending repair. Ticketing them every time they flap wastes the FM
team's time. The priority threshold ([ADR-0007](0007-priority-threshold-filter.md)) cannot express
"this specific point, not the others".

Each `.alr` row has a `directory` field (index 22): the full Vista object path, e.g.
`VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-0201_01-340_060X.Manuel_A`. It is the most specific
identifier of a point that is stable across alarm occurrences.

## Decision

Maintain `exceptions.csv` (path from `config.json -> paths.exceptions_csv`) as a list of
`directory` values to skip. Loaded by `load_exceptions_csv()` (`main.py:235-253`):

- First column = directory, **exact string match**, no wildcards; optional second column = reason.
- Comma or semicolon delimiter; `#` comment lines and blank lines ignored; `utf-8-sig`.
- Missing file = no exceptions (INFO, not an error).

Applied in `run()` after the priority filter (`main.py:816-818`): a matching alarm is dropped from
the incident pipeline with a DEBUG log (which is not emitted at the INFO log level). The CSV audit
is **not** affected — excepted alarms are still recorded.

Current content: one entry, `...XENTA-0201_01-340_060X.Manuel_A`.

## Consequences

### Positive
- Operators can silence a point without code changes or a restart; picked up on the next run.
- Exceptions never hide data from the audit trail.
- Reason column allows documenting *why* (though the single current entry has none).

### Negative
- Exact match on a 23–86 character path (the current entry is 71 characters) is error-prone to
  type; no pattern support (e.g. all
  `*.Manuel_A` forced points).
- No expiry: a "temporary" exception stays until someone remembers to remove it.
- Skips are logged at DEBUG only; the run log does not show which alarms were excepted, only the
  combined "removed M" count.
- Adding an exception for an alarm that already has an open incident does not resolve or annotate
  that incident; the state entry will eventually be marked RESOLVED when the row disappears, but
  since the alarm is now filtered out of `kept`, it is treated as "gone" **on the next run** and
  the incident receives a misleading "RESOLVED — acknowledged (kvitteret)" line (`main.py:935-985`).
- Naming drift: the file is `exceptions.csv` but its own header comment (`exceptions.csv:1`) and
  the build reference (`:129`, `:157`) call it `exceptions.txt`.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Exceptions by `alarm_object` | Less specific than `directory`; the same object name can exist under several controllers. |
| Regex / glob patterns | Not built; simpler exact match chosen. Worth adding when the list grows. |
| Manage exceptions in MainManager | No known API surface for it; the file is local and immediate. |
| Exceptions in the future DB / web UI | Proposed as part of the naming registry ([ADR-0017](0017-friendly-alarm-naming-registry.md)): a `suppress_incident` flag per point. |

## Evidence

- `main.py:83` `F_DIRECTORY = 22`; `main.py:235-253` loader; `main.py:816-818` filter
- `exceptions.csv` (1 active entry, header comments)
- `config.json -> paths.exceptions_csv = C:\priorityalarmsapi\exceptions.csv`
- Log line every run: "Loaded 1 exception entries from C:\priorityalarmsapi\exceptions.csv"
- `Alarm_bot_build_reference.md:129-145`
