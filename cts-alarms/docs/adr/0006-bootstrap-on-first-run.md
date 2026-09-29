# ADR-0006: Bootstrap on first run without creating incidents

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

When the bot is deployed (or its state file is deleted), `$this.alr` already contains dozens of
alarms — the build reference mentions 66 at the time of writing; the current snapshot has 71 rows
at priority ≤ 2. Creating a MainManager incident for each of them at once would flood the FM team
with tickets for conditions they already know about.

## Decision

The state file carries `meta.bootstrapped` (`empty_state()`, `main.py:260-261`). When it is `false`
and `--no-bootstrap` is not given, the run records every alarm that passed the filter into
`alarms_state.json` with `incident_id: null` and its current `status_label`, sets
`bootstrapped: true`, saves, and exits 0 without contacting MainManager (`main.py:831-848`).

From the next run on:

- A `vista_id` not in state = new alarm -> `CreateIncident` (`main.py:865-876`).
- A bootstrapped entry (`incident_id is None`) whose signature changes -> an incident **is created
  at that moment** with the transition lines prepended to the initial description
  (`main.py:895-911`, comment "THE FIX"). Bootstrapped alarms therefore get a ticket the first time
  something happens to them.
- A bootstrapped entry that disappears -> logged as "RESOLVED (no incident)" (`main.py:978-980`).

`--no-bootstrap` (`main.py:1006`) bypasses the bootstrap and treats every current alarm as new.

## Consequences

### Positive
- Deployment does not create a ticket storm; only new or changing alarms produce work.
- The state file starts fully populated, so RESOLVED detection works from run two.
- "THE FIX" closes the gap where a pre-existing alarm that goes ACTIVE again would otherwise be
  updated against a non-existent incident.

### Negative
- Pre-existing alarms that never change and eventually clear leave no MainManager trace at all
  (only a CSV RESOLVED row). Acceptable for tickets; a gap for "see all alarms ever created",
  which the audit CSV covers instead.
- Bootstrap keys the MainID at bootstrap time (`main_id`, `main_id_fallback_used` stored in the
  entry, `main.py:839-842`); a later `objects.csv` mapping is not applied to bootstrapped entries
  (`main.py:903-904` reads the stored value).
- Deleting `alarms_state.json` silently re-bootstraps; there is no "are you sure".
- The build reference (`:225-238`) describes bootstrap but not "THE FIX"; the code is the only
  record of that behaviour.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Create incidents for all existing alarms on first run | Ticket flood (66+ incidents at once). Available on demand via `--no-bootstrap`. |
| Ignore pre-existing alarms forever | Their later transitions would be lost; "THE FIX" was added precisely to catch them. |
| Bootstrap only alarms older than N minutes | Not implemented; the simple boolean was enough. |

## Evidence

- `main.py:260-261` `empty_state()`; `main.py:831-848` bootstrap branch
- `main.py:893-911` bootstrapped-entry incident creation ("THE FIX")
- `main.py:692-698` status table shows "bootstrapped — no incident yet"
- `alarms_state.json -> meta.bootstrapped = true`; entries with `incident_id: null` (e.g. `VISTA_SERVER#684AB94B`, first seen 2026-04-17)
- `Alarm_bot_build_reference.md:225-238`
