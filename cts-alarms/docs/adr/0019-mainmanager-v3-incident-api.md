# ADR-0019: MainManager v3 incident API

- **Status:** Accepted
- **Date:** 2026-09-28 (v2.0.0 coded and live-tested) · 2026-09-28 (v2.0.1 read-only dry run) · 2026-09-29 (recorded)
- **Deciders:** Georgi (owner)

## Context

From **Sat 2026-09-19 between 00:40 and 02:55 CEST** every call to `/restapi/Incident/GetIncident` and
`UpdateIncident` answered HTTP 404, and later `CreateIncident` too; the token endpoint `POST /restapi/token`
kept answering 200. The last successful write was `2026-09-19 00:40:06 UPDATED incident 36693`; no ticket was
created after 2026-09-14 13:35 (37058). The tickets themselves still existed (digibuild's MainManager mirror
held 34570, 36692, 36693, 37058 as live rows), and the tenant's other clients on the owner's VPS were working
against `/api/v3/incidents`. Conclusion: **EG removed the `/restapi/Incident/*` routes without notice.** The
Indeklima bot, same MainManager account, stopped the same weekend (last incident Fri 2026-09-18).

v1.x handled a 404 by `continue` without touching state, so the same call was retried every 5 minutes forever
(one id 2,150 times) and a token was fetched on every run ([arc42 §11 R-01, R-18](../arc42/11-risks-and-technical-debt.md)).

## Decision

`MMClient` (`main.py:624`) talks to the v3 incident API only; the token endpoint is unchanged.

| Operation | Call |
|---|---|
| Token | `POST /restapi/token` password grant; cached in `mm_token.json` with its expiry, 300 s margin (`_get_token()`, `main.py:665`) |
| Create | `POST /api/v3/incidents {"items":[item]}` → `items[0].success`, `items[0].id`; then `GET /api/v3/incidents/{id}`; if `StatusID` did not stick, `PUT` it and `GET` again (`create_incident()`, `main.py:756`) |
| Read | `GET /api/v3/incidents/{id}` → `items[0]` |
| Update | `GET` → `PUT /api/v3/incidents {"items":[{…echoed write model…, "ID": id, "Remarks": new_line + "\n" + Description}]}` → `GET` and verify (`prepend_description_line()`, `main.py:807`) |
| Probe | `GET /api/v3/incidents?pageNumber=1&pageSize=1` — tells "API down" from "ticket missing" (`probe()`, `main.py:738`) |

**Write `Remarks`, read `Description`.** On this tenant the write model has no `Description`; `Remarks` is
stored as the full description, and the `Remarks` read back is a copy **truncated to ~245 characters** (440 of
453 bot tickets). Reading `Remarks` as the source and writing it back cuts the ticket's history — this
happened once in the live test on ticket 34570 (13 lines → 5); it was restored from the mirror copy the same
minute and verified identical. `_text()` (`main.py:801`) therefore reads `Description` (falling back to
`Remarks` only when empty) and the update is verified against `Description`.

**Echo the write model on every PUT.** The tenant's write model has 22 keys; `ECHO_FIELDS` (`main.py:640`) is
that list minus `ID`/`Remarks`, copied from the current item so an update cannot blank them. Keys outside the
model (`Description`, `DateReported`, `CheckwordItemID`) are dropped silently — `CheckwordItemID` is still sent
on create as the pre-2026-09 spelling of `IncidentTypeID` and ignored.

**Incident defaults** from `config.json -> mainmanager.incident_defaults`: `IncidentTypeID` 277 ("CTS API"),
`LocationID` 6, `ReportedByID` 748, `ReportedByOrganisationID` 11, `GradeID` 10, `StatusID` 5 ("Awaits
handling") — the values every bot ticket since 2026-04 carries.

**404 classification** (`_on_not_found()`, `main.py:1128`): a 404 raises `MMNotFound`; if the probe answers,
only this ticket is gone → the entry gets `incident_missing` + `incident_missing_iso`, is never sent again, is
still tracked and resolved locally; if the probe fails too → **API down**: one ERROR, no further calls this run,
state untouched, `deferred=N` in the run summary, retried next run. Non-404 failures retry at most
`MAX_API_FAILURES` = 3 runs per transition (`update_failures`, `_give_up()`), then the transition is abandoned
with an ERROR. A 404 never marks an alarm RESOLVED.

**Read-only dry run (v2.0.1).** `--dry-run` and `--parse-only` persist nothing (`read_only`, `main.py:996`):
no state, no CSV, no outbox; a dry run makes exactly one MainManager call — the token — and logs
`[DRY] MainManager credentials OK`. v2.0.0's dry run saved state and would have swallowed the backlog of
~30 updates/resolutions stuck since 2026-09-19.

## Consequences

### Positive
- Tickets and trail lines flow again once deployed ([TODO T-103](../TODO.md)); the backlog is sent on the first
  real run because state was never advanced during the outage.
- An endpoint-wide change now costs one ERROR per run instead of thousands of retries, and is visible as
  `deferred=` in every run summary (and in digibuild once shipped).
- ADR-0010 still holds: the bot prepends lines and never changes `StatusID` after creation.

### Negative
- Every update is GET → PUT → GET (three calls); acceptable at the observed volume.
- The trail still lives in one text field that is rewritten on every update; a comment-based trail
  (`/api/v1/app/incidentcomment`, used by other clients of the tenant) would avoid that — not adopted.
- The Indeklima bot needs the same port ([TODO T-105](../TODO.md)).
- Test ticket 37378 exists in the tenant and must be cancelled by hand ([TODO T-104](../TODO.md)).

## Alternatives considered

| Option | Why not |
|---|---|
| Wait for EG to restore `/restapi/Incident/*` | No notice was given and the tenant's other clients had already moved; tickets were not being created. |
| Comment trail instead of description prepend | Changes how operators read tickets mid-history; not asked for. |
| Mark alarms RESOLVED on 404 | Would permanently detach live tickets from their alarms during an API outage. |

## Evidence

- `main.py` v2.1.0: `MMNotFound` `:619`, `MMClient` `:624`, `run()` `:983`; tests `tests/test_main.py`
  (`MMClientTests`, `RunTests`)
- Live test 2026-09-28 (owner-approved): `GET /api/v3/incidents/37058` 200 vs `/restapi/Incident/GetIncident` 404;
  update on 34570; create of 37378 (`CTS Alarm - TEST - v2.0.0 endpoint test (ignore)`) identical to 37058 on
  every derived field — [design record](../design/2026-09-28-vps-branch-design.md)
- Logs up to 2026-09-28 (archived privately on the VPS): last `UPDATED incident 36693` 2026-09-19 00:40:06,
  404s from 02:55:13
- [reference/mainmanager-api.md](../reference/mainmanager-api.md)
