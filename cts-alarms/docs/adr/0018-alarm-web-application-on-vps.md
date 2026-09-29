# ADR-0018: Alarm web application on the VPS

- **Status:** **Superseded by [ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md)** (2026-09-29)
- **Date:** 2026-09-28 (proposed) · 2026-09-29 (accepted, provisional) · 2026-09-29 (superseded)
- **Deciders:** Georgi (owner)

> **2026-09-29:** superseded. The web application is the digibuild sub-project `cts-alarms`: pages on Vercel (Clerk, Danish/English), a Python worker + SQLite on the VPS; `server/` was ported there and removed from this repo. The text below records the standalone design that was built and replaced.

## Context

The owner wants "a website … on a VPS" that lets them (and presumably the FM team):

1. See and follow **all** alarms ever created — not only the priority ≤ 2 subset that becomes
   MainManager incidents.
2. Answer "what exactly are we doing, how many alarms do we have", and detect alarms that "appear
   all the time, constantly" — recurrence / trend analysis.
3. Produce reports for the alarms ("it's going to make some sort of reports … we need to see how
   exactly").
4. Show alarms under friendly names ([ADR-0017](0017-friendly-alarm-naming-registry.md)).
5. Sit on top of SQL storage ([ADR-0015](0015-sql-storage-for-alarm-history.md)).

It is explicitly "not going to be read-only". The owner also mentioned a "DPS server", which is
read as the VPS. No stack, hosting provider, authentication or audience has been stated.

What the data already supports (from the CSV audit, Apr–Sep 2026): 1,258 alarm instances,
8.6k ACTIVE and 9.5k NORMAL transitions (heavy flapping), FIRST_SEEN by priority
pri3 456 / pri2 384 / pri9 336 / pri1 82, top texts "Høj Temperatur" (171), "Høj rumtemperatur"
(132), "Lavt tryk" (114), "Lav Temperatur" (69), 469 incidents created / 459 resolved.

## Proposal (scope, not stack)

| Area | Minimum | Later |
|------|---------|-------|
| Alarm list | all `alarm_instance` rows with status, priority, friendly name, building, first seen, resolved, incident ID; filter/search | live "current alarms" view (needs a feed from CTS, ADR-0016) |
| Alarm detail | event timeline (`alarm_event`), link to MainManager incident | annotations / comments |
| Recurrence | per point: instances per week, ACTIVE flaps per instance, mean time to NORMAL, mean time to kvittering; "top 20 noisiest points" | anomaly flags (point alarms > N x its baseline) |
| Reports | weekly/monthly summary per building and system type: new, resolved, open, by priority, by text; export CSV | scheduled email/PDF |
| Registry (write) | edit friendly name, building, floor, system type, MainID, suppress flag | bulk import, change history |
| Bot health | last run time, runs/day, FAILED counts (surfaces the 404 loop), parse warnings | alerts when no run for > 15 min |
| Auth | single admin login at minimum; TLS | FM team accounts / SSO |

"Not read-only" is interpreted as: writing to the **registry** and to the app's own data
(reports, annotations). Writing to MainManager from the web (close/comment incidents) and any
write toward Vista are **out of scope until the owner says otherwise** — they change the
credentials and network story ([ADR-0016](0016-cts-server-vs-vps-responsibility-split.md)).

## Options

| Dimension | Options | Notes |
|-----------|---------|-------|
| Language / framework | Python (FastAPI/Django/Flask) reusing `main.py` parsing and vocabulary; or anything else | Python keeps one language across bot, importer and app; `Alarm` class and status labels can be shared |
| Rendering | server-rendered HTML + light JS; or SPA + JSON API | Reports and tables favour server-rendered; fewer moving parts for one operator |
| Database | per ADR-0015 | |
| Deployment | single VPS, reverse proxy (Caddy/nginx) with TLS, systemd or Docker | backups of the DB are the critical ops task |
| Data feed from CTS | per ADR-0016 (A: push events; B: push raw rows; C: file sync) | determines whether "current alarms" can be live |
| Exposure | public with login; VPN/IP-restricted; both | alarm texts reveal building fire/sprinkler status — restrict |

## Questions to answer first

1. Who are the users besides the owner? Does the FM team need accounts?
2. Which reports, concretely? (A weekly summary per building is the obvious first one; confirm.)
3. Live current-alarm view required, or is 5-minute-delayed / daily-synced history enough?
4. Does "not read-only" include acting on MainManager incidents from the web?
5. Hosting: which VPS provider, who pays, who patches, where are backups?
6. Personal data policy: show operator names (kvitteret by …) in the UI or not?
7. Retention and export obligations, if any.

## Consequences

### Positive
- Turns 5 months of committed data into something the owner can look at; replaces grepping logs.
- Gives the registry a home, which is the precondition for friendly names and correct MainIDs.
- Bot health page would have exposed the 404 loop and the empty `objects.csv` months ago.

### Negative
- A second system to operate (VPS, TLS, backups, updates) for a one-person team.
- Feature creep risk: scope above is already large; the minimum column should ship first.
- Security surface: building security state on the internet; must not be public without auth.

## Evidence

- Owner's request (quoted above)
- `main.py:101-117` status vocabulary reusable in the app; `main.py:120-150` `Alarm` model
- CSV audit statistics (scouting notes, Apr–Sep 2026)
- Logs: 531 FAILED lines on 2026-09-28 — the kind of signal a health page would show
