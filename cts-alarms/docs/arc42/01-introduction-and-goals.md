# 1. Introduction and Goals

Part of the [arc42 documentation](../README.md) for the **cts-alarms** repository (the "TAC Vista alarm bot").
Next: [2. Constraints](02-constraints.md) · [3. Context and Scope](03-context-and-scope.md) · [12. Glossary](12-glossary.md)

## What the system is

`main.py` (v2.1.0, ~1380 lines) plus the optional module `shipper.py` (~470 lines) — Python 3, only third-party dependency `requests` — run every 5 minutes on the **CTS server**, the Windows Server 2016 host of the building-management system (Schneider Electric TAC Vista 5.1.9). Each run:

1. Copies Vista's live alarm list `$this.alr` to a snapshot and parses it (`snapshot_alarm_file()` `main.py:205`, `parse_alarm_file()` `:219`).
2. Writes an **audit trail of every alarm state change** (all priorities) to one semicolon-CSV per Vista directory (`run_csv_logging()`, `main.py:515`).
3. Filters alarms to priority ≤ `thresholds.max_priority_number` (currently **2**) and not listed in `exceptions.csv` (`main.py:1059-1068`).
4. Diffs the filtered alarms against `alarms_state.json` and mirrors the result into **MainManager** (Ramboll FM, the facility-management ticketing system) as incidents through the **v3 incident API**: create on new alarm, prepend a dated line on every state transition, prepend a "RESOLVED" line when the alarm leaves the list (`run()`, `main.py:983`; [ADR-0019](../adr/0019-mainmanager-v3-incident-api.md)).
5. If `config.json` has a `"vps"` section: ships the run (summary, CSV events, snapshot, alarm→ticket links) over HTTPS + HMAC to the digibuild `cts-alarms` worker at `api.digibuild.dk`, which holds the history database behind the web pages (`shipper.py`; [ADR-0020](../adr/0020-hmac-ingest-to-digibuild.md)).

The bot never closes an incident (`StatusID` is never changed after creation); ticket closure stays a human decision. See [ADR-0010](../adr/0010-prepend-description-never-change-status.md).

This repository holds **the CTS-server side only**: the code, `config.json` (no secrets), `secrets.example.json`, `objects.csv`, `exceptions.csv`, the scheduler task, tests and these docs ([ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)). Runtime data (logs, CSV audit, state files, snapshot) stays on the CTS server; the copy committed on 2026-09-28 for the migration is archived privately on the VPS and leaves the repo. Credentials live in `secrets.json` on the CTS server ([ADR-0021](../adr/0021-secrets-in-secrets-json.md)); the MainManager password that was in the public history was rotated on 2026-09-29. The repository is **public**.

## 1.1 Requirements overview

### Functional requirements (as implemented)

| ID | Requirement | Where |
|----|-------------|-------|
| F1 | Read the TAC Vista alarm list without disturbing Vista: copy `$this.alr` to a snapshot (5 retries, 0.5 s apart) and parse the snapshot, never the live file. | `snapshot_alarm_file()` `main.py:205` |
| F2 | Parse tab-delimited, ISO-8859-1, 36-field rows into `Alarm` objects; skip malformed rows with a WARNING rather than abort. | `parse_alarm_file()` `main.py:219`, [ALR format](../reference/alr-file-format.md) |
| F3 | Record **every** alarm (all priorities, no exception filter, excluding `(state1=6, state2=2)` system events) and every change of its `(state1, state2, ack_flag, user)` signature as a CSV event row (`FIRST_SEEN`, `ACTIVE`, `NORMAL`, `ACKNOWLEDGED`, `UNACKNOWLEDGED`, `USER_CHANGED`, `STATE1_x_TO_y`, `STATE_CHANGED`, `RESOLVED`). | `run_csv_logging()` `main.py:515`, [CSV audit format](../reference/csv-audit-format.md) |
| F4 | Only alarms with priority ≤ `max_priority_number` (config: 2) whose full directory path is not in `exceptions.csv` reach the MainManager pipeline. | `main.py:1059-1068`, [ADR-0007](../adr/0007-priority-threshold-filter.md), [ADR-0008](../adr/0008-directory-based-exceptions.md) |
| F5 | On the very first run (bootstrap), record all currently listed alarms in state **without** creating incidents. | `main.py:1080-1098`, [ADR-0006](../adr/0006-bootstrap-on-first-run.md) |
| F6 | For a Vista ID never seen before: create a MainManager incident named `CTS Alarm - <alarm_object> - <alarm_text>` (≤ 100 chars) with a dated text, via `POST /api/v3/incidents`; enforce `StatusID` with a PUT if it did not stick. | `create_incident_for_alarm()` `main.py:946`, `MMClient.create_incident()` `:756`, `incident_name()` `:872` |
| F7 | When an alarm's signature changes: prepend one dated Vista-terminology line per transition to the incident text (GET `Description` → PUT `Remarks` → GET verify). A bootstrapped alarm without an incident gets one created at that moment. | `describe_transitions()` `main.py:828`, `prepend_description_line()` `:807`, `run()` `:1164-1262` |
| F8 | When a tracked Vista ID disappears from the file: prepend "RESOLVED — alarm was acknowledged (kvitteret) and removed from Vista alarm list" (preceded by a "returned to NORMAL (missed between polls)" line if last seen ACTIVE, so RESOLVED ends on top); mark the state entry RESOLVED. Never change the incident status. | `run()` `main.py:1264-1329` |
| F9 | Map `alarm_object` → MainManager `MainID` via `objects.csv`; fall back to `mainmanager.default_main_id` (14228) with a WARNING. | `resolve_main_id()` `main.py:937`, [ADR-0009](../adr/0009-objects-csv-mainid-mapping-with-fallback.md) |
| F10 | Persist tracking state in JSON with atomic writes; prune RESOLVED entries after `prune_resolved_after_days` (30). | `save_state()` `main.py:324`, `prune_state()` `:332`, [State files](../reference/state-files.md) |
| F11 | Log a per-alarm status table on every run plus a run summary (`created=…, updated=…, resolved=…, unchanged=…, deferred=…`) to `logs/YYYY-MM-DD.log` and stdout; the start banner carries the version. | `log_alarm_status_table()` `main.py:882`, [Log format](../reference/log-format.md) |
| F12 | Offer `--dry-run` (read-only; checks the MainManager credentials and, with a `"vps"` section, the ingest secret and reachability), `--parse-only`, `--no-bootstrap`, `--config`. | `main()` `main.py:1351`, [config reference](../reference/config-reference.md#cli-main-mainpy1351) |
| F13 | Tell a MainManager outage from a missing ticket: on a 404 probe the v3 list; API down → no further calls, state untouched, retried next run; ticket missing → mark the entry and stop sending for it. Other failures are retried at most 3 runs per transition. | `_on_not_found()` `main.py:1128`, `_give_up()` `:1147`, [ADR-0019](../adr/0019-mainmanager-v3-incident-api.md) |
| F14 | Optionally ship every run to the digibuild `cts-alarms` worker (HMAC-signed HTTPS POST), queueing undelivered batches in a local SQLite outbox; a shipper failure never affects ticketing or the exit code. | `shipper.ship_run()`, `_ship()` `main.py:1008`, [shipper reference](../reference/shipper.md) |
| F15 | Read credentials only from the environment or `secrets.json` (BOM-tolerant), never from the committed `config.json`. | `load_secrets()` `main.py:61`, [ADR-0021](../adr/0021-secrets-in-secrets-json.md) |

### Known gaps in the implemented behaviour

These are facts observed in code and logs, not requirements; they are tracked in [11. Risks and Technical Debt](11-risks-and-technical-debt.md) and [TODO](../TODO.md):

- `objects.csv` is empty, so every incident is created on the fallback `MainID` (WARNING on every create).
- Until v2.0.0 is deployed on the CTS server ([TODO T-103](../TODO.md)), the running v1.x bot creates no tickets: EG removed the `/restapi/Incident/*` routes on 2026-09-19 and v1 retried every failing update every 5 minutes ([§11 R-18](11-risks-and-technical-debt.md#r-18-mainmanager-integration-not-working-since-2026-09-19), R-01). Fixed in code: v3 API + 404 classification.
- Logs are never rotated or deleted (~2–4 MB/day).
- The earliest logs were produced by earlier versions of the bot: `2026-04-17` uses the wording `alarm bot run started`, `exceptions.txt` and `Loaded 3 object mappings` (objects.csv was not always empty); runs up to `2026-04-21` filter with `priority<=3` (`priority<=2` from `2026-04-22`), and runs up to `2026-04-22` use fallback MainID `9756` (`14228` appears from `2026-04-23`). The committed scheduler task has `StartBoundary` 2026-04-21. Lifetime totals quoted in [3. Context and Scope](03-context-and-scope.md#volumes-observed-apr-17--sep-28-2026-from-the-data-archived-privately-on-the-vps) therefore mix code versions with different thresholds and log vocabularies — relevant when migrating the logs.
- The build reference (`Alarm_bot_build_reference.md`) is partly stale: it says 3-minute cadence, `exceptions.txt`, `C:\ctsapi\alarms`, default MainID 9756, threshold 3 and status values `OPEN/CLEARED/ACKNOWLEDGED/BOOTSTRAPPED`. `main.py`, `config.json` and `TACVista_Alarm_Bot.xml` are authoritative.

## 1.2 Quality goals

Ordered by importance, as evidenced by design choices in the code.

| # | Quality goal | Motivation / scenario | How it is addressed today |
|---|--------------|----------------------|---------------------------|
| 1 | **Do no harm to the BMS** | Vista rewrites `$this.alr` in place; a reader must never lock or corrupt it. | Snapshot-then-parse with `shutil.copy2` and retries ([ADR-0005](../adr/0005-snapshot-then-parse.md)); read-only access to Vista; runs as least-privilege user `GPST`. |
| 2 | **No ticket spam / no duplicate incidents** | Deploying on a running BMS must not open 60+ tickets; one alarm instance must map to exactly one incident. | Bootstrap on first run ([ADR-0006](../adr/0006-bootstrap-on-first-run.md)); Vista ID as primary key ([ADR-0003](../adr/0003-vista-id-as-primary-key.md)); signature diff instead of text parsing ([ADR-0004](../adr/0004-state-signature-diff.md)); priority threshold + exceptions. |
| 3 | **Traceability / auditability** | FM staff and the owner must be able to reconstruct what an alarm did and when, independent of MainManager. | Per-directory CSV audit trail for all priorities ([ADR-0011](../adr/0011-per-directory-csv-audit-log.md)); per-run status table in daily logs; dated description lines in incidents. |
| 4 | **Robustness across runs** | A crash mid-run, a locked file or an API error must not corrupt state or lose alarms. | Atomic JSON writes ([ADR-0012](../adr/0012-json-state-files-atomic-writes.md)); state saved after every change; per-alarm try/except so one failure does not stop the run; scheduler restarts 3× on failure; `IgnoreNew` prevents overlap. |
| 5 | **Humans keep control of tickets** | Alarms flap (8.6k ACTIVE transitions for ~1.2k alarm instances in the logs); auto-closing would hide real issues. | Bot never changes `StatusID` ([ADR-0010](../adr/0010-prepend-description-never-change-status.md)). |
| 6 | **Operability by one person** | The owner maintains it alongside other duties; no build pipeline, no server-side services. | Single file, single dependency, JSON config, plain-text data, Task Scheduler ([ADR-0002](../adr/0002-scheduled-python-script-on-cts-server.md)). |

Security (credential handling) was **not** met until v2.0.0: the credentials were in the committed `config.json` of a public repo ([ADR-0013](../adr/0013-secrets-in-config-json.md), superseded). Since then they live in `secrets.json`, the exposed password was rotated (2026-09-29), and a test guards `config.json` ([ADR-0021](../adr/0021-secrets-in-secrets-json.md)). What remains: the old value and operator names in the public git history ([TODO T-102](../TODO.md)).

## 1.3 Stakeholders

| Role | Who | Expectations / concerns |
|------|-----|-------------------------|
| Owner / developer / operator | Georgi (`GPST (Georgi ISS)` in Vista; task runs under Windows user `GPST`) | Keeps the bot running; wants the documentation set, a to-do list, secret rotation, and the planned web application described below. |
| BMS operators | Named Vista users seen in the data: three operators with labels of the form `LOGIN (Name Organisation)`, one bare login, `LON-OP`, `SYSTEM (User Profile SYSTEM)` — and the owner as `GPST (Georgi ISS)`. Other operators' names are not reproduced in this public repo | Acknowledge (kvittere) alarms in TAC Vista. Their acknowledgements are what the bot observes and reports; they do not interact with the bot directly. |
| FM staff / ticket handlers | Users of MainManager (Ramboll FM, `rambollfm.mainmanager.dk`) | Receive incidents named `CTS Alarm - …` with `IncidentTypeID 277` ("CTS API"), `GradeID 10`, `StatusID 5` ("Awaits handling"), reporter 748 "TacVista Automated Services"; read the prepended status lines; close tickets manually. Want readable alarm names and correct `MainID` assignment. |
| MainManager service account | The account whose credentials live in `secrets.json` on the CTS server (`mainmanager.username` / `mainmanager.password`); also used by the Indeklima bot | Technical identity for all API calls (MainManager user 747); password rotated 2026-09-29 after public exposure. |
| CTS server administrators | Whoever administers the Windows Server 2016 host and TAC Vista | Need the bot to stay read-only towards Vista, low-privilege and bounded in disk use (logs/CSV grow unbounded today). |
| Sibling system | The "Indeklima bot" (temperature-log → MainManager incidents, type 281; same author, same account; runs on the CTS server; not in this repo) | Stopped with the same API removal on 2026-09-18/19; needs the same v3 port and the new password ([TODO T-105](../TODO.md)). |
| digibuild `cts-alarms` | The owner's portal (Vercel pages with Clerk login, Danish/English; worker + SQLite on the VPS) | Receives every run over the HMAC ingest; shows all alarms ever, history, recurring alarms and friendly names; joins live ticket status from its MainManager mirror. Documented in the digibuild repo. |
| Future readers / contributors | Anyone changing the bot or the wire contract | Need the reference docs ([ALR](../reference/alr-file-format.md), [CSV](../reference/csv-audit-format.md), [state](../reference/state-files.md), [logs](../reference/log-format.md), [config](../reference/config-reference.md), [MainManager API](../reference/mainmanager-api.md), [shipper](../reference/shipper.md)) and the ADRs. |

## Where the system went (decided 2026-09-29)

The owner's plan — a website on a VPS that follows **all alarms ever**, is **not read-only**, produces
**reports**, gives alarms **friendly names**, analyses **recurring alarms**, and moves storage **from CSV to SQL**
— is realised as the digibuild sub-project `cts-alarms` ([ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)):

- the bot stays on the CTS server and keeps creating MainManager tickets ([ADR-0016](../adr/0016-cts-server-vs-vps-responsibility-split.md) option A, [ADR-0019](../adr/0019-mainmanager-v3-incident-api.md));
- every run is shipped to the digibuild worker over HTTPS + HMAC ([ADR-0020](../adr/0020-hmac-ingest-to-digibuild.md)), which stores it in its own SQLite database ([ADR-0015](../adr/0015-sql-storage-for-alarm-history.md)) together with the imported history of 2026-04-17 … 2026-09-28;
- the pages (overview, history + detail, recurring, catalogue with friendly names, [ADR-0017](../adr/0017-friendly-alarm-naming-registry.md)) are on Vercel behind Clerk, in Danish and English; live ticket status comes from digibuild's MainManager mirror.

The receiving side is documented (arc42, ADRs, C4) in the digibuild repo. Open owner questions — PII in the pages,
who may edit names, report formats, repo privacy — are in [docs/TODO.md](../TODO.md).
