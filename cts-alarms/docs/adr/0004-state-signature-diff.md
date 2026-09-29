# ADR-0004: State-signature diff to detect transitions

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

`$this.alr` is a snapshot of *current* alarm rows, not an event stream. TAC Vista exposes an
alarm's condition on two axes: condition (`state1`: 0 = ACTIVE, 1 = NORMAL, 6 = system) and
operator (`ack_flag`: 0/1, plus `user`: "No user" or a named operator). The code treats
`(state1=6, state2=2)` as info/system events (`main.py:75-76`, `146-147`), but no such row has been
observed in the snapshot or in five months of CSV history; the `$EE_Mess` system messages actually
seen carry `state2=0`. The bot polls every 5 minutes and must decide, per known `vista_id`, whether
anything happened since the last poll — and describe it in Vista terms in the MainManager incident.

## Decision

Reduce each alarm row to a **state signature** tuple `(state1, state2, ack_flag, user.strip())`
(`Alarm.state_signature()`, `main.py:149-150`) and store the last seen signature per `vista_id`:

- `alarms_state.json -> alarms[vid].last_state_sig` for the incident pipeline (`main.py:729,928`)
- `csv_state.json[vid].sig` for the CSV audit (`main.py:480`)

On each run: equal signature = nothing to do (`main.py:887-889`, `main.py:469-470`); different
signature = a transition. Two independent classifiers turn `(prev_sig, current)` into text:

| Consumer | Function | Output |
|----------|----------|--------|
| MainManager description | `describe_transitions()` `main.py:628-658` | "alarm returned to NORMAL (...)", "alarm ACTIVE again (...)", "ACKNOWLEDGED by <user>", "re-acknowledged by <user>", catch-all "status changed to ... (state1 a->b, ack c->d)" |
| CSV audit | `classify_csv_event()` `main.py:393-427` | `NORMAL`, `ACTIVE`, `STATE1_x_TO_y`, `ACKNOWLEDGED`, `UNACKNOWLEDGED`, `USER_CHANGED`, `STATE_CHANGED` |

The human-readable status label (`classify_alarm_status()`, `main.py:108-117`) is derived from the
same fields and is informational only: `ACTIVE`, `ACTIVE + ACKNOWLEDGED`, `NORMAL`,
`NORMAL + ACKNOWLEDGED`; `RESOLVED` is the bot's own term for "row gone".

`priority`, `alarm_text`, `count`, `date2` are **not** part of the signature: a change in alarm
text alone (e.g. "ATV21 Fejl" -> "ATV21 OK" accompanies state1 0->1) is not itself a transition.

## Consequences

### Positive
- Deterministic and cheap; no event log from Vista is required.
- Both pipelines share one notion of "changed", so incident updates and CSV rows agree.
- Multiple changes between polls (NORMAL + ACKNOWLEDGED in one step) produce multiple lines/rows.

### Negative
- Transitions that go there and back within one 5-minute interval are invisible. `count`
  (field 18, re-trigger count) would reveal them but is not in the signature; only recorded in CSV.
- The `user` string is part of the signature, so a re-acknowledgement by a different operator is a
  transition (`USER_CHANGED`); by design, but it means personal names drive diff logic.
- The two classifiers are written twice with slightly different rules (e.g. CSV `ACKNOWLEDGED`
  ignores the "No user" check that `describe_transitions()` applies at `main.py:645`).
- Legacy status values exist: 2 entries in `alarms_state.json` still carry `"status": "ACKNOWLEDGED"`
  from an older vocabulary (build reference `:220`). Harmless because flow is signature-driven.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Compare whole rows | `date2`, `count` and text change too often; would spam incident updates. |
| Vista event/alarm history log | Not available as a readable source in this setup (unverified). |
| Include `count` in signature | Would record every re-trigger as a transition; rejected implicitly, but worth revisiting for analytics ([ADR-0015](0015-sql-storage-for-alarm-history.md)). |

## Evidence

- `main.py:91-99` comment block on the two-axis model; `main.py:149-150` `state_signature()`
- `main.py:884-892` signature compare in `run()`; `main.py:467-472` in `run_csv_logging()`
- CSV totals Apr–Sep 2026: NORMAL 9,455; ACTIVE 8,644; ACKNOWLEDGED 148; UNACKNOWLEDGED 1 — 8.6k ACTIVE transitions for 1.2k instances shows heavy flapping
- `Alarm_bot_build_reference.md:241-257` (state machine table; older wording "condition cleared")
