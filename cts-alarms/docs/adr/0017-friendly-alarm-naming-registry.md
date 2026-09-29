# ADR-0017: Friendly alarm naming registry

- **Status:** Accepted — 2026-09-29: the registry lives in the digibuild `cts-alarms` worker database (one row per alarm point: friendly name, building, floor, system, notes) and is edited on the Vercel pages; rows are created from ingested and imported data. **Who may edit** is an open owner question in digibuild. Names are not pushed back to `objects.csv` on the CTS server.
- **Date:** 2026-09-28 · 2026-09-29 (accepted)
- **Deciders:** Georgi (owner)

> **2026-09-29:** decided by building it (first in `server/`, then carried into digibuild — [ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md)). The options below are kept as the record of what was weighed.

## Context

Alarms are identified to humans by `alarm_object` and `directory`, e.g.

| alarm_object | directory | alarm_text |
|---|---|---|
| `320-01-07930-0101_AL` | `VISTA_SERVER-LOYTEC_PORT-...` | Lav Temperatur |
| `345-51-Zonemaster_7_SAL-32090_0721ForcLuk_A` | `VISTA_SERVER-LOYTEC_PORT-RHQ-34551_02_ET7_XENTA-ZM_OMR5_ET6_7-APP.32090_0721ForcLuk_A` | Konvektor ventiler forceret lukket af bruger |
| `325-02-03901-Indblæsning-Alarm-Bit0_Fejl` | same | ATV21 Fejl |

The codes appear to encode building – system – controller – point with suffixes `_AL` (low),
`_AH` (high), `_A` (generic); the directory path carries `RHQ-345`, `ET9` (Etage 9 = floor),
`XENTA`/`LOYTEC` controller names. This decoding is an inference from the data — **no key exists in
the repo**. The owner: "the majority of the alarms are a little bit hard to find and hard to know
what exactly they are, so maybe we can give them a new name."

The same identifiers are what MainManager sees in the incident name
(`CTS Alarm - <alarm_object> - <alarm_text>`, `incident_name()`, `main.py:672-675`) and what the
future web app would list. Observed population: **290 distinct `alarm_object`** values and roughly
245 distinct initial alarm texts (336 distinct text values across all events, since text changes on
NORMAL, e.g. "ATV21 Fejl" -> "ATV21 OK") over Apr–Sep 2026 (CSV audit), 431 distinct directories.

Two existing mechanisms already key on these identifiers and would merge naturally into a
registry: `objects.csv` (alarm_object -> MainID, currently empty,
[ADR-0009](0009-objects-csv-mainid-mapping-with-fallback.md)) and `exceptions.csv`
(directory -> suppress, [ADR-0008](0008-directory-based-exceptions.md)).

## Proposal

A table `alarm_point` (see the schema sketch in [ADR-0015](0015-sql-storage-for-alarm-history.md)),
keyed by `directory` (most specific, stable), maintained through the web UI:

| Column | Source / meaning |
|--------|------------------|
| `directory` (unique) | `.alr` field 22 |
| `alarm_object` | `.alr` field 2 (denormalised for display) |
| `friendly_name` | human text, e.g. "RHQ 345, 9th floor, meeting room 0201 – radiator valve forced" (**to be written by the owner / FM team**) |
| `building` | e.g. "RHQ 345" — inferred from code prefix, to be confirmed |
| `floor` | e.g. "Etage 9" |
| `system_type` | ventilation / heating / fire (ABA) / sprinkler / zone controller / … |
| `mainmanager_main_id` | replaces `objects.csv` |
| `suppress_incident` | replaces `exceptions.csv` |
| `notes`, `updated_by`, `updated_at` | audit |

Bootstrap: import all 431 directories / 290 objects seen in `csv/` and `alarm_snapshot.alr` with
empty friendly names; the UI shows "unnamed" points sorted by alarm count so the most frequent
(e.g. `320-01-07930-0101_AL` 14x, `300-01-04904-0104_AH` 13x) get named first.

Display rule: `friendly_name` if set, else `alarm_object`. The Vista identifiers are never
replaced — they stay as the key and are shown alongside.

## Options for where the registry is authoritative

| Option | Pros | Cons |
|--------|------|------|
| DB on the VPS, web UI edits, bot on CTS **fetches** a generated `objects.csv`/`exceptions.csv` (fits ADR-0016 A) | bot code barely changes | needs CTS -> VPS pull and a cache when VPS is unreachable |
| DB on the VPS, bot on the VPS reads it directly (ADR-0016 B) | single source | requires the B rewrite |
| SQLite on CTS, edited via a small local tool (ADR-0016 C) | no cross-host dependency | editing on a BMS host over RDP; no web edits |
| Keep CSV files, edit by hand | zero build | this is the status quo that produced an empty `objects.csv` for six months |

## Questions to answer first

1. Who will write the 290+ friendly names, and from what source (Vista object descriptions?
   building drawings? FM asset register)? Does Vista itself hold a description field that could
   seed `friendly_name` (unknown — not among the 12 parsed `.alr` fields; fields 14–35 are unexplored)?
2. Is the key `directory` (431) or `alarm_object` (290)? Are there objects under several
   directories that should share a name?
3. Should friendly names propagate into MainManager incident names (changes `incident_name()`, and
   existing incidents keep the old name)?
4. Should the registry also carry a per-point priority override or "ticket yes/no" beyond
   `suppress_incident` (see [ADR-0007](0007-priority-threshold-filter.md))?
5. Naming language: Danish (matches alarm texts and operators) or English?

## Consequences

### Positive
- Humans can read the alarm list and reports without decoding controller paths.
- `objects.csv` and `exceptions.csv` stop being separate, hand-edited files.
- Grouping by building / floor / system type becomes possible for analytics.

### Negative
- A registry nobody fills is worse than none (see `objects.csv`); needs an owner and a UI that
  nags about unnamed points.
- Adds a write path to the web app, which bears on the ADR-0016 choice.

## Evidence

- `main.py:72-83` parsed fields; `main.py:672-675` incident naming; `main.py:735-741` mapping lookup
- `alarm_snapshot.alr` sample rows (object/directory forms above)
- CSV audit statistics: 290 distinct `alarm_object`, ~245 distinct initial texts (336 across all events), top recurring points
- `objects.csv` header comment describing `_AH`/`_AL`/`_A` suffixes
- Owner's request (quoted above)
