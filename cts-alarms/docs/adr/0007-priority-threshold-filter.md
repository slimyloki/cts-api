# ADR-0007: Priority-threshold filter for the incident pipeline

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

TAC Vista assigns each alarm a numeric priority in field 7: **lower is more urgent**. Observed
distribution in the current snapshot: priority 1 = 18 rows, 2 = 53, 3 = 60, 9 = 49. Priority 9 is
mostly `VISTA_SERVER-$EE_Mess` system messages (44 of 49 rows) plus a few `nvoAlarmStatus_A0`
points; these rows carry normal state values (`state1` 0/1, `state2` 0), so they are **not** the
`(6,2)` system events that the CSV audit skips, and they do appear in the CSV history
(309 FIRST_SEEN instances). Not every alarm justifies a
MainManager ticket; the FM team wants tickets for the urgent ones only.

## Decision

Only alarms with `priority <= thresholds.max_priority_number` enter the incident pipeline
(`main.py:811-814`). The value is read from `config.json`; it is currently **2**, i.e. priority 1
and 2 alarms create/update incidents. Priority 3 alarms are audited in CSV but never ticketed.

The threshold is applied after CSV audit logging and before the exception filter
([ADR-0008](0008-directory-based-exceptions.md)); the log line
"`N alarms after priority<=2 + exception filter (removed M)`" reports the effect every run.

The threshold has changed over time: the build reference says 3, early April logs say
"priority<=3", the current config says 2. The reason for lowering it is not recorded; the logs
show the change took effect between 2026-04-21 and 2026-04-22 (last "priority<=3" run in
`logs/2026-04-21.log`, first "priority<=2" run in `logs/2026-04-22.log`).

## Consequences

### Positive
- One number controls the ticket volume; changing it needs no code change.
- Priority-9 system noise never reaches MainManager.
- Because the CSV audit runs before the filter ([ADR-0011](0011-per-directory-csv-audit-log.md)),
  lowering the threshold does not lose history.

### Negative
- Priority 3 is the largest real-alarm class (60 of 180 rows; 456 of 1,258 FIRST_SEEN instances)
  and includes texts like "Høj rumtemperatur", "Lavt tryk"; none of it is ticketed today.
- Raising the threshold back to 3 later would make every existing priority-3 alarm look "new" and
  create a burst of incidents (no bootstrap for a threshold change).
- `config.json` `_comment` still says "Trigger incidents for Vista priority 1, 2, and 3" while the
  value is 2 — misleading.
- Vista priorities are assigned by the BMS integrator; the bot has no override per point (the only
  per-point control is the exception list).

## Alternatives considered

| Option | Why not |
|--------|---------|
| Ticket everything, let MainManager triage | Priority 3 + 9 volume (109 of 180 rows) is too much for a ticket queue. |
| Per-alarm-object priority overrides | Not built; a future friendly-name registry ([ADR-0017](0017-friendly-alarm-naming-registry.md)) could carry a "ticket yes/no" column. |
| Text-based filtering (e.g. all "Brand fra ABA") | Alarm text is free Danish text, ~245 distinct initial texts (336 across all events); brittle. |

## Evidence

- `main.py:77` `F_PRIORITY = 7`; `main.py:811-821` filter and log line
- `config.json -> thresholds.max_priority_number = 2`, with the stale `_comment`
- `Alarm_bot_build_reference.md:13,78,152,181-182` (threshold 3)
- Logs 2026-04-17: "priority<=3"; logs 2026-09-28: "priority<=2"
- `alarm_snapshot.alr` priority counts (18/53/60/49); CSV FIRST_SEEN by priority (pri3 456, pri2 384, pri9 336, pri1 82)
