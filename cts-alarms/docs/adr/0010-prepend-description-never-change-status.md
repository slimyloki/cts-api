# ADR-0010: Prepend to incident description, never change status

- **Status:** Accepted
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`)
- **Deciders:** Georgi (owner)

> **2026-09-29:** The decision stands. Since v2.0.0 it is carried out through the v3 incident API (GET
> `Description` → PUT `Remarks` with the echoed write model → GET verify), and `StatusID` is set and enforced only
> at creation ([ADR-0019](0019-mainmanager-v3-incident-api.md)). The `/restapi/Incident/*` endpoints and
> `main.py:<line>` references below are v1.x.

## Context

Once an incident exists in MainManager, the FM team works it: assigns, comments, closes. The bot
sees the alarm flap ACTIVE/NORMAL, get acknowledged (kvitteret), and finally leave the Vista list.
The question is how much of the incident the bot is allowed to own. The MainManager REST API used
here (`POST /restapi/Incident/CreateIncident`, `GET /restapi/Incident/GetIncident`,
`POST /restapi/Incident/UpdateIncident`) can change status, but the build reference notes that a
newer `PUT /api/v1/incidents` route does not work for this purpose (`:324`).

## Decision

The bot writes an incident **once** with defaults from `config.json -> mainmanager.incident_defaults`
(`IncidentMode imIncident`, `CheckwordID 8`, `CheckwordItemID 277`, `GradeID 10`, `StatusID 5`)
and afterwards **only prepends timestamped lines to the Description** — newest on top. It never
changes `StatusID`, never closes, never deletes.

Mechanics (`MMClient`, `main.py:535-617`):

1. `GET GetIncident?IncidentID=<id>` -> read current `Description`.
2. `POST UpdateIncident {IncidentID, IncidentRemarks: "<new line>\n<old description>"}`.
3. Repeated per line (`prepend_description_line()`, `main.py:613-617`); resolution lines are sent
   in reverse so the newest ends up on top (`main.py:970-972`).

Line vocabulary (`describe_transitions()`, `main.py:628-658`; resolution `main.py:950-960`):
`alarm returned to NORMAL ("<text>")`, `alarm ACTIVE again ("<text>")`, `ACKNOWLEDGED by <user>`,
`re-acknowledged by <user>`, `alarm returned to NORMAL (missed between polls)`,
`RESOLVED — alarm was acknowledged (kvitteret) and removed from Vista alarm list`, all prefixed
`dd-mm-yyyy HH:MM Alarm bot - `.

Incident name: `CTS Alarm - <alarm_object> - <alarm_text>`, truncated to 100 chars
(`incident_name()`, `main.py:672-675`).

## Consequences

### Positive
- Humans keep full ownership of the ticket lifecycle; the bot cannot close a ticket on a flapping
  sensor that still needs a visit.
- The description becomes a chronological trail of what Vista showed, in Vista terms.
- Only two write endpoints are needed; no status-ID semantics to get wrong.

### Negative
- Two HTTP round-trips per line; a NORMAL+ACK in one poll costs four calls. Lifetime: 7,377 updates.
- Read-modify-write is not atomic: a human editing the description between the bot's GET and POST
  loses their edit (window = one GET + one POST round-trip; unmeasured, not observed, not guarded).
- **404 retry loop (open bug):** if `GetIncident` returns 404 for any reason (observed for every
  incident id since 2026-09-19, cause unknown — arc42 §11 R-18), `prepend_description_line()` raises, the caller logs `... FAILED` and `continue`s **without
  updating state** (`main.py:918-923`, `main.py:968-975`). The same update or resolution is retried
  every 5 minutes forever. Observed: `IncidentID=34570` failed 2,150 times; 30 distinct IDs affected in all
  (11 more 51–947 times, 18 more 1–40 times); 531 FAILED lines on 2026-09-28 alone. Since
  2026-09-19 this is an endpoint-wide failure (every id, `CreateIncident` too — see arc42 §11 R-18),
  not individual deleted incidents. Side effect: `_mm()` is instantiated and a token
  fetched on every run (8,258 auth calls) even when nothing else changes.
- Resolved incidents stay open in MainManager until a human closes them; the bot's RESOLVED line is
  the only signal.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Bot sets StatusID to closed on RESOLVED | Vista "resolved" (row removed after kvittering) does not mean the fault is fixed; FM team decides. |
| Append instead of prepend | Newest-on-top reads better in the MainManager UI (build reference `:259-268`). |
| One update per run with all lines joined | Fewer calls; not done. Worth doing alongside the 404 fix. |
| Post comments/remarks instead of rewriting Description | `IncidentRemarks` is what `UpdateIncident` accepts here; whether it maps to a comment thread or the description field is only known empirically ("works"). |

## Evidence

- `main.py:535-617` `MMClient`; `main.py:566-589` create body and defaults; `main.py:613-617` prepend
- `main.py:912-926` update path; `main.py:933-985` resolution path; `continue` without state update at `:923` and `:975`
- `config.json -> mainmanager.incident_defaults`
- Logs: "Resolution update FAILED for VISTA_SERVER#6A5D0D16: 404 ... GetIncident?IncidentID=34570" x 2,150
- `Alarm_bot_build_reference.md:259-279` (description format, naming), `:281-330` (API)
