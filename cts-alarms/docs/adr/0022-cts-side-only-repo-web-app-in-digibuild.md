# ADR-0022: This repo holds the CTS side only; the web application is the digibuild sub-project `cts-alarms`

- **Status:** Accepted
- **Date:** 2026-09-29
- **Deciders:** Georgi (owner)
- **Supersedes:** [ADR-0014](0014-commit-runtime-data-for-migration.md) (runtime data in the repo), [ADR-0018](0018-alarm-web-application-on-vps.md) (standalone web app on the VPS)

## Context

On 2026-09-29 this repo held three things: the bot that runs on the CTS server; a standalone FastAPI web app
(`server/`, pages + API + importer, PostgreSQL in production, nginx/Docker files) built by the cloud session;
and ~360 MB of runtime data committed on purpose for the migration (ADR-0014). The same day the owner decided
that the VPS side is a **digibuild sub-project** (decision D-C in the
design record (2026-09-28, kept in the old repo `slimyloki/cts-alarms` under `docs/design/`; not carried into cts-api because it describes the VPS side)), and their standing rule for new web apps applies:
pages and on-demand APIs on **Vercel**, only background processes on the **VPS**, **Clerk** login, Danish and
English. `server/` contradicted that rule (pages served from the VPS, its own auth, nginx/Docker). The owner
asked to "leave only what I need on the CTS server" in this repo.

## Decision

1. **This repo = what runs on the CTS server**, plus its tests and docs: `main.py`, `shipper.py`, `config.json`,
   `secrets.example.json`, `objects.csv`, `exceptions.csv`, `TACVista_Alarm_Bot.xml`, `tests/`, `docs/`,
   `Alarm_bot_build_reference.md` (historical). Nothing else is deployed from it.
2. **Runtime data stays on the CTS server.** `logs/`, `csv/`, `alarms_state.json`, `csv_state.json`,
   `alarm_snapshot.alr` leave the repo tip and are git-ignored. The data as of 2026-09-28 is **archived privately
   on the VPS** (checksummed) as the source of the one-off historical import. Supersedes ADR-0014.
3. **The web application is the digibuild sub-project `cts-alarms`** (digibuild ADR-0042 shape: registry entry,
   route group, own worker, own env file, own docs):
   - a Python worker on the VPS (ported from `server/`: models, ingest, queries, importer) with its own SQLite
     (WAL) database, reachable only through `api.digibuild.dk` — the HMAC ingest route
     ([ADR-0020](0020-hmac-ingest-to-digibuild.md)) and a read API for Vercel with a bearer token;
   - a read-only join to digibuild's MainManager mirror for live ticket status (never written);
   - pages on Vercel (overview, history + detail, recurring, catalogue) behind Clerk, Danish and English;
   - digibuild's Vercel watchdog probes the worker's health.
   `server/`'s Jinja pages, nginx/Docker files, PostgreSQL default and API-key table are dropped; `server/` is
   removed from this repo. Supersedes ADR-0018.
4. **MainManager writes stay on the CTS server** (ADR-0016, [ADR-0019](0019-mainmanager-v3-incident-api.md)); the
   digibuild side never writes to MainManager, which keeps digibuild's "read-only towards MainManager" rule.
5. The receiving side's architecture (arc42, ADRs, C4, UI docs) is documented **in the digibuild repo**; this
   repo documents the CTS side and the wire contract (`reference/shipper.md`).

## Consequences

### Positive
- The repo contains only what is deployed to the CTS server; reviewing a deploy means reviewing this repo.
- No operational data or personal data enters the repo from now on.
- The web app inherits digibuild's login, languages, hosting split, backups and watchdog instead of inventing them.

### Negative
- Two repos for one pipeline; the ingest contract is the coupling point (golden-vector test on this side).
- The public **history** still holds the runtime data (operator names) and the old password until the owner
  decides T-102 ([TODO](../TODO.md)).
- Anything that needs the history (analysis, re-import) now needs access to the private archive or to digibuild.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep `server/` here and deploy it standalone on the VPS | Conflicts with the owner's hosting/auth rule (pages on Vercel, Clerk); a second login and a second web stack. |
| Keep the runtime data in the repo until the dual-write period ends | Public repo; the data is already archived privately and lives on the CTS server anyway. |
| Monorepo: move the bot into digibuild | The bot is Windows/Python on a building server with its own deploy path; mixing it into a Vercel/Node monorepo buys nothing. |

## Evidence

- Owner, 2026-09-29: "leave only what I need on the CTS server … make ADR, arc42 and C4 for everything for the
  documentation on the VPS"
- Design record (2026-09-28, kept in the old repo `slimyloki/cts-alarms` under `docs/design/`; not carried into cts-api because it describes the VPS side) decisions D-A … D-E
- `git log`: `server/` added in `aa27f0d`, `ab617a4`, `963fcd4` (2026-09-29)
