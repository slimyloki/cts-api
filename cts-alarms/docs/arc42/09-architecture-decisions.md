# 9. Architecture Decisions

All decisions are recorded as Architecture Decision Records (ADRs) in [`docs/adr/`](../adr/README.md), one file per decision, numbered sequentially and never renumbered. This chapter is the index; the rationale, alternatives and consequences live in the individual records.

Related: [04 – Solution Strategy](04-solution-strategy.md) · [08 – Crosscutting Concepts](08-crosscutting-concepts.md) · [11 – Risks and Technical Debt](11-risks-and-technical-debt.md) · [docs/TODO.md](../TODO.md)

## 9.1 ADR index

Status legend: **Accepted** = in force (implemented in `main.py`/`shipper.py`, or — for 0015/0017 — in the digibuild sub-project) · **Superseded by NNNN** = replaced; the file stays as the record. No ADR is Proposed or Deprecated as of 2026-09-29. Line references are to `main.py` v2.1.0.

| # | Title | Status | Summary | Link |
|---|---|---|---|---|
| 0001 | Record architecture decisions | Accepted | Use lightweight ADRs (context / decision / consequences) under `docs/adr/`; this documentation set is the first application. | [0001](../adr/0001-record-architecture-decisions.md) |
| 0002 | Scheduled Python script on the CTS server | Accepted | Run a single `main.py` every 5 minutes via Windows Task Scheduler (`\TACVistaLogs\TACVista_Alarm_Bot`) on the Windows Server 2016 CTS host, reading Vista's `$this.alr` directly; no service, no daemon, no separate host. | [0002](../adr/0002-scheduled-python-script-on-cts-server.md) |
| 0003 | Vista ID as primary key for an alarm instance | Accepted | Use field 1 (`VISTA_SERVER#<hex>`) as the identity of an alarm instance in both state files; `alarm_object`/`directory` are attributes. | [0003](../adr/0003-vista-id-as-primary-key.md) |
| 0004 | State-signature diff to detect transitions | Accepted | Detect change by comparing the tuple `(state1, state2, ack_flag, user)` with the stored copy; identical tuple = no event (`Alarm.state_signature()` `main.py:197`; compared in `run()` `:1200` and `run_csv_logging()` `:544`). | [0004](../adr/0004-state-signature-diff.md) |
| 0005 | Snapshot `$this.alr` before parsing | Accepted | Copy `$this.alr` to `alarm_snapshot.alr` (5 retries, 0.5 s) and parse the copy, to avoid reading a file Vista is writing (`snapshot_alarm_file()`, `main.py:205`). | [0005](../adr/0005-snapshot-then-parse.md) |
| 0006 | Bootstrap on first run without creating incidents | Accepted | First run records all current alarms with `incident_id = null` and creates no incidents; incidents are created on the first later change (bootstrap block `main.py:1080`, "THE FIX" `:1214`). | [0006](../adr/0006-bootstrap-on-first-run.md) |
| 0007 | Priority-threshold filter for the incident pipeline | Accepted | Only alarms with `priority <= thresholds.max_priority_number` (currently 2) reach the incident pipeline; the CSV audit sees all priorities (filter in `run()`, `main.py:1059-1068`). | [0007](../adr/0007-priority-threshold-filter.md) |
| 0008 | Directory-based exception list | Accepted | `exceptions.csv` lists full Vista `directory` paths to ignore, exact match, no wildcards (`load_exceptions_csv()` `main.py:283`, check `:1065`). | [0008](../adr/0008-directory-based-exceptions.md) |
| 0009 | `objects.csv` MainID mapping with fallback | Accepted | Resolve the MainManager `MainID` per `alarm_object` from `objects.csv`; unmapped objects use `mainmanager.default_main_id` with a WARNING (`resolve_main_id()`, `main.py:937`). File is currently empty. | [0009](../adr/0009-objects-csv-mainid-mapping-with-fallback.md) |
| 0010 | Prepend to incident description, never change status | Accepted | Every transition is prepended as a timestamped line to the incident text; since v2.0.0 via the v3 API (GET `Description` → PUT `Remarks` → GET verify, [0019](../adr/0019-mainmanager-v3-incident-api.md)); `StatusID` is only set (and enforced) on create — humans close tickets (`prepend_description_line()`, `main.py:807`). | [0010](../adr/0010-prepend-description-never-change-status.md) |
| 0011 | Per-directory CSV audit log for all priorities | Accepted | Independently of the incident pipeline, append every transition of every non-system alarm to one semicolon CSV per Vista directory in `csv/` (`run_csv_logging()`, `main.py:515`). | [0011](../adr/0011-per-directory-csv-audit-log.md) |
| 0012 | JSON state files with atomic writes | Accepted | Persist `alarms_state.json` and `csv_state.json` as pretty-printed UTF-8 JSON written via temp file + `os.replace()` (`save_state()` `main.py:324`, `save_csv_state()` `:486`). | [0012](../adr/0012-json-state-files-atomic-writes.md) |
| 0013 | Secrets in `config.json` | Superseded by 0021 | Until v1.1.0 the MainManager credentials were plain text in `config.json`, which was pushed to a public repo. Replaced by `secrets.json`/environment (v2.0.0); the password was rotated on 2026-09-29. | [0013](../adr/0013-secrets-in-config-json.md) |
| 0014 | Commit runtime data to the repo for migration | Superseded by 0022 | `logs/`, `csv/`, state files and the snapshot were deliberately committed for review and migration; the import is verified, the data is archived privately on the VPS and leaves the repo tip. | [0014](../adr/0014-commit-runtime-data-for-migration.md) |
| 0015 | SQL storage for alarm history | Accepted | The history database is the digibuild `cts-alarms` worker's own SQLite (WAL) on the VPS (points, instances, events, incident links, runs), fed by the shipper; CTS keeps CSV + JSON state (dual write). | [0015](../adr/0015-sql-storage-for-alarm-history.md) |
| 0016 | CTS server vs VPS responsibility split | Accepted | Option A: the bot stays on the CTS server (snapshot, parse, CSV, MainManager) and ships every run to the VPS via `shipper.py`; the VPS side is the digibuild sub-project `cts-alarms`. | [0016](../adr/0016-cts-server-vs-vps-responsibility-split.md) |
| 0017 | Friendly alarm naming registry | Accepted | The registry (friendly name, building, floor, system, notes per alarm point) lives in the digibuild worker and is edited on the Vercel pages; who may edit is open there. | [0017](../adr/0017-friendly-alarm-naming-registry.md) |
| 0018 | Alarm web application on the VPS | Superseded by 0022 | `server/` (FastAPI + Jinja2 pages + JSON API) was built, then ported into digibuild and removed from this repo. | [0018](../adr/0018-alarm-web-application-on-vps.md) |
| 0019 | MainManager v3 incident API | Accepted | EG removed `/restapi/Incident/*` on 2026-09-19; `MMClient` uses `/api/v3/incidents` (write `Remarks`, read/verify `Description`, echo the write model, enforce `StatusID`); 404s classified as API-down vs ticket-missing; max 3 retries per transition; dry run is read-only. | [0019](../adr/0019-mainmanager-v3-incident-api.md) |
| 0020 | Ship each run to digibuild over HTTPS POST + HMAC | Accepted | `POST https://api.digibuild.dk/internal/cts-alarms/v1/ingest` signed with digibuild's X-Signature/X-Timestamp/X-Nonce contract (±300 s, nonce 600 s); outbox retries; dry run probes `healthz`. | [0020](../adr/0020-hmac-ingest-to-digibuild.md) |
| 0021 | Secrets only in `secrets.json` or the environment | Accepted | MainManager pair and ingest secret in the git-ignored, ACL-restricted `secrets.json` (env overrides), read BOM-tolerant; guard test in `tests/test_repo_hygiene.py`. | [0021](../adr/0021-secrets-in-secrets-json.md) |
| 0022 | CTS side only in this repo; web app in digibuild | Accepted | The repo holds what runs on the CTS server; runtime data stays there; the web app, database and reports are the digibuild sub-project `cts-alarms` (Vercel pages, VPS worker). | [0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md) |

## 9.2 Decisions by concern

| Concern | ADRs |
|---|---|
| Runtime and hosting | 0002, 0016, 0022 (0018 superseded) |
| Alarm identity and change detection | 0003, 0004, 0005, 0006 |
| Which alarms become incidents | 0007, 0008, 0009 |
| MainManager integration | 0009, 0010, 0019 |
| Security and secrets | 0020, 0021 (0013 superseded) |
| Persistence and audit | 0011, 0012, 0015 (0014 superseded) |
| Shipping to the VPS | 0016, 0020 |
| Usability of alarm data | 0017 (in digibuild) |

## 9.3 ADR process

ADRs 0002–0013 were written retroactively in September 2026 from the code, `config.json`, the scheduler XML and the observed logs; they document decisions already embodied in `main.py` rather than decisions taken at documentation time (0013 is marked Deprecated). ADR 0001 is a new decision taken on 2026-09-28. Where `Alarm_bot_build_reference.md` disagrees with `main.py`, the ADR follows `main.py` and notes the drift (see [11](11-risks-and-technical-debt.md#r-07-build-reference-drift)).

Process going forward (from [ADR 0001](../adr/0001-record-architecture-decisions.md)):

1. A new decision gets the next free number and a kebab-case slug; numbers are never reused.
2. Each record has **Status**, **Context**, **Decision**, **Consequences**; alternatives considered go in Context.
3. Status lifecycle: `Proposed` → `Accepted` → (`Deprecated` | `Superseded by NNNN`). A superseding ADR links back; the old one is edited only to update its status line.
4. ADRs 0015, 0016 and 0018 were accepted provisionally on 2026-09-29 when the owner asked to start building (Option A of 0016). Later the same day the VPS side was placed in digibuild: 0015/0016/0017 became Accepted, 0013/0014/0018 Superseded, and 0019–0022 recorded the v3 API, the HMAC transport, the secrets file and the repo scope. The receiving side's decisions (worker, storage details, pages, auth) are ADRs in the digibuild repo. Open questions: [docs/TODO.md](../TODO.md).
5. Code changes that alter a documented decision (e.g. the polling cadence, the threshold, the storage format) should update the relevant ADR in the same commit.
