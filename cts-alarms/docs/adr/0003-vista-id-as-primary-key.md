# ADR-0003: Vista ID as primary key for an alarm instance

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

## Context

Each row in `$this.alr` carries, in field 1 (0-based), an identifier of the form
`VISTA_SERVER#<8 hex digits>`, e.g. `VISTA_SERVER#6A3BE36B`. The hex part is a Unix epoch close to
the first-occurrence time in field 3 (`date1`), but the two are not identical: in the committed
snapshot they match in 68 of 180 rows and otherwise differ by a few seconds to ~90 s in either
direction (e.g. `VISTA_SERVER#67969A95` vs `date1 = 67969A77`), with two rows months apart. Vista keeps the
ID stable while the row lives in the file, across ACTIVE/NORMAL flapping and acknowledgements, and
removes the row only when the alarm is both NORMAL and acknowledged (kvitteret).

The bot needs a key to (a) recognise the same alarm across polls, (b) attach a MainManager
incident ID to it, and (c) detect when it disappears. The alternative natural keys — `alarm_object`
(field 2) or `directory` (field 22) — identify the *point*, not the *occurrence*: the same point
raises many alarms over time (e.g. `320-01-07930-0101_AL` seen 14 times).

## Decision

Use `vista_id` (field 1) as the primary key for an alarm instance everywhere:

- `alarms_state.json -> alarms` is a dict keyed by `vista_id` (`main.py:843,871`).
- `csv_state.json` is a dict keyed by `vista_id` (`main.py:479`).
- Incident pipeline: "new `vista_id`" = create incident; "`vista_id` gone" = RESOLVED
  (`main.py:865-876`, `main.py:934-985`).
- CSV audit: `vista_id` is column 3 of every row (`csv_row()`, `main.py:337-358`).
- `alarm_object` and `directory` are attributes of the instance, used for MainID mapping and
  exceptions, not for identity.

A `vista_id` that reappears after being marked RESOLVED is logged as a WARNING and ignored
(`main.py:879-881`) — the design assumes IDs are not reused.

## Consequences

### Positive
- One alarm occurrence maps to exactly one incident; flapping does not create duplicate tickets.
- The key is a plain string; it becomes the natural primary key of `alarm_instance` in the proposed
  SQL schema ([ADR-0015](0015-sql-storage-for-alarm-history.md)).
- The embedded epoch gives the creation time even for rows whose other fields are corrupt.

### Negative
- Identity is per *occurrence*; "how often does this point alarm?" requires grouping by
  `alarm_object`/`directory`, which is what the analytics phase needs and does not exist yet.
- Uniqueness relies on Vista never creating two alarms in the same second with the same server
  prefix; unverified, assumed. The build reference (`:340-341`) says duplicates "shouldn't happen"
  and last-wins applies.
- The ID is opaque to humans; in MainManager the incident name uses `alarm_object` + text instead
  (`incident_name()`, `main.py:672-675`), which is why friendly names are wanted
  ([ADR-0017](0017-friendly-alarm-naming-registry.md)).

## Alternatives considered

| Option | Why not |
|--------|---------|
| `alarm_object` as key | Same point alarms repeatedly; would merge distinct occurrences into one incident. |
| `directory` as key | Same problem; also an up-to-86-character path. |
| Composite `(alarm_object, date1)` | Would only approximate `vista_id` (the ID epoch and `date1` differ in most rows); the file already provides the canonical ID. |
| Bot-generated UUID per first-seen row | Would need its own lookup table to re-identify rows on the next poll. |

## Evidence

- `main.py:72` `F_VISTA_ID = 1`; `main.py:185` parsing
- `main.py:829` `current_ids = {a.vista_id for a in kept}`; `main.py:935` set difference for RESOLVED detection
- `alarm_snapshot.alr` sample row: `VISTA_SERVER#67969A95` with `date1 = 67969A77`
- `Alarm_bot_build_reference.md:340-348` (duplicate ID and reappearance handling)
- Scouting: 1,258 FIRST_SEEN instances across 290 distinct `alarm_object` values (CSV audit, Apr–Sep 2026)
