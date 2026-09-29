# ADR-0016: CTS server vs VPS responsibility split

- **Status:** Accepted — 2026-09-29: **Option A**. The bot stays on the CTS server and keeps creating MainManager incidents ([ADR-0019](0019-mainmanager-v3-incident-api.md)); each run is shipped to the digibuild sub-project `cts-alarms` on the VPS ([ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md)) over HTTPS POST + HMAC ([ADR-0020](0020-hmac-ingest-to-digibuild.md)).
- **Date:** 2026-09-28 (proposed) · 2026-09-29 (accepted, provisional) · 2026-09-29 (accepted: placement digibuild, transport HMAC)
- **Deciders:** Georgi (owner)

> **2026-09-29, final:** the open questions are answered — Digibuild is the owner's portal (Vercel pages + VPS workers), the VPS is the owner's box behind `api.digibuild.dk`, and whether the CTS server can reach it is answered by the v2.1.x dry run's reachability line (TODO T-077). The "Decision (2026-09-29)" text below describes the first `server/` implementation (bearer key, `/api/v1/ingest`, PostgreSQL); those details are superseded by ADR-0020 and ADR-0022, the split itself stands.

## Decision (2026-09-29)

**Option A — the bot stays on the CTS server and ships events to the VPS.**

- `main.py` keeps snapshot → parse → CSV audit → MainManager exactly as today. A new module
  `shipper.py` POSTs each run's audit events, the current alarm snapshot, the incident links and
  the run summary to the VPS (`POST /api/v1/ingest`, bearer API key read from a file outside
  git). Failed batches go to a local SQLite outbox and are resent on the next run.
- The VPS runs the `server/` application: FastAPI + SQLAlchemy, PostgreSQL in production
  (SQLite for development), a JSON API for Digibuild and server-rendered report pages
  ([ADR-0015](0015-sql-storage-for-alarm-history.md), [ADR-0018](0018-alarm-web-application-on-vps.md)).
- MainManager credentials and calls stay on the CTS server for now; moving them (Option B) is
  not excluded later but is not part of this decision.
- History is loaded once from the committed `csv/`, state files and `logs/` by `server/app/importer`.

Owner statements this rests on: "On my VPS, I would like to have maybe the database of the logs,
and then on my website, which is probably Digibuild, we will need to have some overview of the
alarms, like the incident reports."

Still open (answers may adjust details, not the split): what Digibuild is (own stack on the same
VPS, or a platform that can only call the JSON API / embed pages); whether the CTS server can open
outbound HTTPS to the VPS; who operates the VPS and whether operator names may be stored there.
Tracked in [docs/TODO.md](../TODO.md).

## Context

Fixed facts that constrain any split:

- The bot as built reads `$this.alr` **locally** on the CTS server (Windows Server 2016): Vista
  rewrites it in place; the bot copies it with retries ([ADR-0005](0005-snapshot-then-parse.md)).
  Whether Vista offers any API/export that the VPS could use instead is unverified
  ([ADR-0002](0002-scheduled-python-script-on-cts-server.md)); until shown otherwise, nothing on
  the VPS can read Vista directly.
- The CTS server currently has outbound HTTPS to `rambollfm.mainmanager.dk` (8,258 token calls
  prove it). Whether it can reach an arbitrary VPS, and whether inbound connections to it are
  allowed, is **unknown** — it is a BMS host, likely behind the building's firewall.
- The MainManager credentials must live wherever the MainManager client runs
  ([ADR-0013](0013-secrets-in-config-json.md)).
- The owner wants a website on a VPS that shows all alarms, analysis and reports, and says it is
  "not going to be read-only" — read here (undecided, see TODO T-002) as at least editing friendly
  names / registry data ([ADR-0017](0017-friendly-alarm-naming-registry.md)) and producing reports;
  whether it includes acting on incidents is open. Whether "not read-only" includes writing back to
  MainManager or to Vista is likewise undecided
  (writing to Vista is out of scope for all options below).
- A sibling bot (Indeklima) by the same author uses the same pattern (Python + Task Scheduler +
  MainManager REST) and reads Vista trend logs, so it probably runs on the same CTS server —
  unverified; if so, changes to the CTS side should not break it.

## Candidate splits

### A — Bot stays on CTS; ship events to the VPS

CTS keeps `main.py` as is (snapshot, parse, CSV, MainManager). A small addition posts each new
CSV event (or the run summary) to an HTTPS endpoint on the VPS, or syncs the `csv/` folder. The
VPS holds the SQL database and the web app.

| Pros | Cons |
|------|------|
| Smallest change to a working system; MainManager integration untouched | Two copies of logic/state (CSV on CTS, SQL on VPS); the 404 bug and objects.csv/exceptions.csv stay on CTS |
| Credentials for MainManager stay where they are (one place) | Needs **outbound** CTS -> VPS; requires a VPS API key on CTS (second secret on the BMS host) |
| Web app is read-mostly; "not read-only" limited to registry edits and reports | Registry changes (friendly names, MainID mapping, exceptions) made on the VPS must be pushed back to CTS files or fetched by the bot |
| VPS outage loses nothing (CTS keeps CSV; resend later) | |

### B — Collector-only on CTS; all logic on the VPS

CTS runs a thin collector: snapshot `.alr`, parse, push raw rows (or the raw snapshot) to the VPS
every 5 minutes. The VPS does signature diffing, CSV/SQL history, MainManager incidents, registry,
web app.

| Pros | Cons |
|------|------|
| One brain, one state, one database; bugs fixed in one place | Biggest rewrite; the working incident pipeline is re-implemented and must be re-validated against MainManager |
| MainManager credentials move **off** the BMS host to the VPS | VPS becomes critical: if it is down or unreachable, no incidents are created (collector can buffer, but ticket latency grows) |
| "Not read-only" is natural: the web app and the bot share the DB | Needs outbound CTS -> VPS and a reliable buffer on CTS for outages; the VPS needs outbound to MainManager (fine) |
| Collector is trivial and rarely changes | MainManager traffic now originates from a VPS IP instead of the building; check whether MainManager restricts by source |

### C — Everything on CTS; VPS hosts a read-only UI over a synced DB

CTS keeps the bot and gains a local SQLite database (bot writes events there instead of / in
addition to CSV). The database file is synced one-way to the VPS (e.g. periodic copy over
SFTP/rsync/rclone, or `litestream`-style replication). The VPS serves a **read-only** web app.

| Pros | Cons |
|------|------|
| CTS remains self-contained; VPS outage is invisible to ticketing | Does **not** satisfy "not read-only": registry edits, report generation with saved state, any action must be done on CTS or via a second sync path back |
| Simplest security story: only one outbound file sync from CTS; no inbound anything | Two-way sync of a SQLite file is fragile; effectively forces "edits on CTS" |
| SQLite is stdlib; no new service on CTS | Windows Server 2016 as the only writer to the analytics DB; every schema change is a CTS deployment |

### Comparison on the decisive questions

| Question | A | B | C |
|----------|---|---|---|
| Reads `.alr` locally | yes | yes (collector) | yes |
| CTS needs outbound to VPS | yes (events) | yes (rows) | yes (sync) |
| CTS needs inbound | no | no | no |
| MainManager credentials on | CTS | VPS | CTS |
| Second secret on CTS (VPS API key / sync key) | yes | yes | yes |
| Web app can write (registry, reports) | yes, with push-back to CTS | yes | no |
| Incidents survive VPS outage | yes | only with buffer, delayed | yes |
| Rewrite size | small | large | medium |

## Questions to answer first

1. Can the CTS server open outbound HTTPS/SSH to a VPS? Who controls that firewall? (If no:
   only C with manual export, or a VPN, works.)
2. Exactly what must the web app write? Registry only -> A or C+edit-on-CTS; MainManager actions
   (close, comment) from the web -> B, or A with credentials duplicated.
3. Is it acceptable that incident creation depends on the VPS being up (B)?
4. Does MainManager care about the source IP of API calls?
5. Who operates the VPS (backups, patches, TLS)? The owner alone?
6. Will the Indeklima bot follow the same split later? If yes, B's collector could serve both.

## Consequences

Whichever option is chosen, the ADR-0013 follow-up (rotate + move secrets) and the 404-loop fix in
`main.py` happen first; they are option-independent. Recorded in [docs/TODO.md](../TODO.md).

## Evidence

- `main.py:157-168` local snapshot requirement; `main.py:544-558` MainManager auth from CTS
- `TACVista_Alarm_Bot.xml` (`GPST`, 5-minute cadence, `RunOnlyIfNetworkAvailable=false`)
- Owner's statements in the task request (VPS, "not read-only", undecided split)
- `Alarm_bot_build_reference.md:4-8`, `:453-467` (Indeklima sibling: same API and pattern; host not stated)
