# ADR-0009: `objects.csv` MainID mapping with fallback

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

`CreateIncident` in MainManager requires a `MainID` — the asset/location the incident is attached
to. Vista alarms identify a point by `alarm_object` (field 2, e.g. `320-01-07930-0101_AL`,
suffix `_AL` low / `_AH` high / `_A` generic), which has no relation to MainManager IDs. Somebody
has to map Vista points to MainManager assets, and the mapping will be incomplete for a long time.

## Decision

Two-level resolution in `resolve_main_id()` (`main.py:735-741`):

1. Look up `alarm_object` in `objects.csv` (`load_objects_csv()`, `main.py:209-232`: `<alarm_object>,<main_id>`,
   comma or semicolon, `#` comments, `utf-8-sig`, non-integer MainID skipped with a WARNING).
2. Otherwise use `config.json -> mainmanager.default_main_id` (currently **14228**) and log
   `WARNING No objects.csv mapping for '<alarm_object>' — using fallback MainID <n>`.

The chosen `main_id` and a `main_id_fallback_used` boolean are stored in the state entry
(`make_state_entry()`, `main.py:717-732`) so a later mapping does not retroactively move an
existing incident.

## Current reality

`objects.csv` contains **only comments** — 0 mappings. Every incident ever created used the
fallback: `alarms_state.json` shows 119 entries on MainID 14228 and 3 on 9756 (the older default
from the build reference, still present on entries first seen 2026-04-17). The WARNING fires on
every create; "Loaded 0 object mappings" appears in every run since 2026-04-17 11:19 (the first two
runs that morning, 10:57 and 10:59, loaded 3 mappings, which were removed before any incident was
created — all 469 CREATED lines carry `[fallback]`).

## Consequences

### Positive
- Incidents are always created; a missing mapping degrades to "attach to the default asset" rather
  than failing.
- The warning lists exactly which objects need mapping ("grep the logs", build reference `:351-354`).
- Mapping is data, editable without a deployment.

### Negative
- Six months in, all 469 incidents sit on one or two generic assets; MainManager cannot report per
  building/system. The mapping table was never populated — it needs a source of truth (the FM
  asset register) that the bot does not have.
- Mapping key is `alarm_object`, not `directory`; two controllers with the same object name would
  collide. Unverified whether that occurs among the 290 observed objects.
- The default MainID changed (9756 -> 14228) with no record of when or why; the build reference
  is stale on this point.
- Because `main_id` is frozen in the state entry, fixing the mapping later does not fix open
  incidents (would need a MainManager-side move, no API call for it in `MMClient`).

## Alternatives considered

| Option | Why not |
|--------|---------|
| Fail the create when unmapped | Would have blocked all ticketing from day one. |
| Map by `directory` prefix (controller / building) | More scalable (one row per building instead of per point); not built. Candidate for the registry in [ADR-0017](0017-friendly-alarm-naming-registry.md), where `MainID` becomes a column next to friendly name / building / floor. |
| Derive MainID from the object code (`320-01-...` = building 320?) | Encoding of the codes is not documented anywhere in the repo; assumption unverified. |

## Evidence

- `main.py:209-232` `load_objects_csv()`; `main.py:735-741` `resolve_main_id()`; `main.py:717-732` state entry
- `objects.csv` (comments only); `config.json -> mainmanager.default_main_id = 14228`
- `alarms_state.json`: `main_id_fallback_used: true` on all entries; 119 x 14228, 3 x 9756
- Logs: WARNING "No objects.csv mapping" on every create; "Loaded 0 object mappings" in 45,610 runs, "Loaded 3 object mappings" only in the first two runs on 2026-04-17
- `Alarm_bot_build_reference.md:114-128` (mapping spec, default 9756)
