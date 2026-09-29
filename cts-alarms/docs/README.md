# cts-alarms — documentation index

Documentation for the **TAC Vista alarm bot**: a scheduled Python script (`main.py` v2.1.0) on the CTS
server (Windows Server 2016, Schneider Electric TAC Vista 5.1.9 building-management system). Every 5 minutes it
reads Vista's live alarm list and writes a CSV audit trail. It creates and updates incidents in MainManager
(Ramboll FM, v3 API) for priority 1–2 alarms. Its optional shipper (`shipper.py`) posts each run to the digibuild
sub-project `cts-alarms` over HTTPS POST + HMAC.

**Scope of this repo (since 2026-09-29):** what runs on the CTS server, its tests and these docs
([ADR-0022](adr/0022-cts-side-only-repo-web-app-in-digibuild.md)). The web application (history database,
pages, friendly names, reports) is the digibuild sub-project `cts-alarms`; its architecture is documented in the
**digibuild repo**. Runtime data (`logs/`, `csv/`, state files) stays on the CTS server; the data as of
2026-09-28 is archived privately on the VPS. The repo is **public**: no secret values, ever
([ADR-0021](adr/0021-secrets-in-secrets-json.md)).

The docs were verified against `main.py` v2.1.0, `shipper.py`, `config.json`, `TACVista_Alarm_Bot.xml` and the
runtime data as of 2026-09-28. Open work is in [TODO.md](TODO.md).

> `Alarm_bot_build_reference.md` at the repository root is the **original design document**.
> It is kept for history but is **partly outdated** (3-minute cadence, `exceptions.txt`,
> `C:\ctsapi\alarms`, default MainID 9756, threshold 3, status values
> OPEN/CLEARED/ACKNOWLEDGED/BOOTSTRAPPED, the v1 MainManager endpoints). Where it contradicts `main.py`,
> `main.py` wins; the drift is listed in [arc42 §11](arc42/11-risks-and-technical-debt.md) and
> [TODO.md](TODO.md) (T-021).

## Start here

A newcomer should read in this order (about 30 minutes):

1. [arc42 §1 Introduction and goals](arc42/01-introduction-and-goals.md) — what the bot is for, who uses it, where the system went.
2. [C4 level 1 — System context](c4/01-system-context.md) — the one-picture overview (Vista → bot → MainManager and digibuild).
3. [arc42 §6 Runtime view](arc42/06-runtime-view.md) — what one 5-minute run does, step by step.
4. [Reference: `$this.alr` file format](reference/alr-file-format.md) — the input everything depends on.
5. [ADR index](adr/README.md) — skim the 22 decisions; read 0003, 0004, 0019 and 0022 in full.
6. [TODO.md](TODO.md) — the backlog, open decisions and the secrets still to handle.

To change the bot: [C4 level 3 — Components](c4/03-component.md), then [C4 level 4 — Code](c4/04-code.md), then
[arc42 §11 Risks and technical debt](arc42/11-risks-and-technical-debt.md) for known issues.

To deploy a new version: [arc42 §7.5](arc42/07-deployment-view.md#75-deploying-a-new-version-owners-hands) and,
for the shipper, [reference/shipper.md](reference/shipper.md).

To work on the web side: the digibuild repo, sub-project `cts-alarms`. This repo only holds the wire contract
([reference/shipper.md](reference/shipper.md), [ADR-0020](adr/0020-hmac-ingest-to-digibuild.md)) and the
record of how the split was decided ([C4 §05](c4/05-target-architecture-proposal.md),
[design record](design/2026-09-28-vps-branch-design.md)).

## Layout

```
docs/
├── README.md            this index
├── TODO.md              project backlog (owned by the cts-todo-keeper agent, a user-level agent on the development box)
├── arc42/               architecture documentation, arc42 template, 12 sections
├── adr/                 architecture decision records, 0001–0022
├── c4/                  C4 model diagrams (context, container, component, code) + target architecture as realised
├── design/              dated design records (the VPS branch design and its decisions)
└── reference/           exact file/API formats for the data the bot reads, writes and sends
```

### Backlog

| File | Contents |
|---|---|
| [TODO.md](TODO.md) | Backlog with stable ids (T-nnn), priorities P0–P3, statuses (items moved to digibuild are marked so, never deleted), open owner decisions, secrets to handle (locations only), changelog. |

### arc42

| File | Contents |
|---|---|
| [01-introduction-and-goals.md](arc42/01-introduction-and-goals.md) | Purpose, repo scope, functional requirements, quality goals, stakeholders, where the system went. |
| [02-constraints.md](arc42/02-constraints.md) | Windows Server 2016, 32-bit Python 3.13, Vista file access, MainManager v3 API, Task Scheduler, HMAC clock tolerance, public repo. |
| [03-context-and-scope.md](arc42/03-context-and-scope.md) | Business and technical context: `$this.alr` in; MainManager incidents and digibuild batches out; files on disk. |
| [04-solution-strategy.md](arc42/04-solution-strategy.md) | Snapshot-then-parse, signature diff against JSON state, priority filter, prepend-only updates, defer on API failure, ship to digibuild. |
| [05-building-block-view.md](arc42/05-building-block-view.md) | Decomposition of `main.py` and `shipper.py`: CSV audit, incident pipeline, VPS shipper. |
| [06-runtime-view.md](arc42/06-runtime-view.md) | One run; bootstrap; new / changed / resolved alarms; 404 and API-down paths; dry run; shipping. |
| [07-deployment-view.md](arc42/07-deployment-view.md) | CTS server layout (`C:\priorityalarmsapi`), scheduled task, `secrets.json` and its ACL, deploy and rollback steps, what runs elsewhere. |
| [08-crosscutting-concepts.md](arc42/08-crosscutting-concepts.md) | Vista status model, priorities, encoding, logging, error handling, secrets, read-only runs, testability. |
| [09-architecture-decisions.md](arc42/09-architecture-decisions.md) | Summary table pointing at the ADRs. |
| [10-quality-requirements.md](arc42/10-quality-requirements.md) | Quality tree and concrete scenarios, with what is met today. |
| [11-risks-and-technical-debt.md](arc42/11-risks-and-technical-debt.md) | Risk register with 2026-09-29 status: secrets in the public history, empty `objects.csv`, clock skew, unbounded outbox, Indeklima bot on the removed API. |
| [12-glossary.md](arc42/12-glossary.md) | CTS, Vista, `$this.alr`, Vista ID, kvitteret, MainID, IncidentTypeID, v3 API, digibuild, HMAC, outbox, etc. |

### ADR

| File | Contents |
|---|---|
| [README.md](adr/README.md) | ADR index, statuses, template. |
| [0001-record-architecture-decisions.md](adr/0001-record-architecture-decisions.md) | Use ADRs. |
| [0002-scheduled-python-script-on-cts-server.md](adr/0002-scheduled-python-script-on-cts-server.md) | Single-file Python script run by Windows Task Scheduler on the CTS server. |
| [0003-vista-id-as-primary-key.md](adr/0003-vista-id-as-primary-key.md) | `VISTA_SERVER#<8 hex>` identifies one alarm instance. |
| [0004-state-signature-diff.md](adr/0004-state-signature-diff.md) | Detect transitions by `(state1, state2, ack_flag, user)`, not by alarm text. |
| [0005-snapshot-then-parse.md](adr/0005-snapshot-then-parse.md) | Copy `$this.alr` to `alarm_snapshot.alr` before reading. |
| [0006-bootstrap-on-first-run.md](adr/0006-bootstrap-on-first-run.md) | First run records existing alarms without creating incidents. |
| [0007-priority-threshold-filter.md](adr/0007-priority-threshold-filter.md) | Only `priority <= max_priority_number` (currently 2) reaches MainManager. |
| [0008-directory-based-exceptions.md](adr/0008-directory-based-exceptions.md) | Ignore list keyed on the full Vista directory path (`exceptions.csv`). |
| [0009-objects-csv-mainid-mapping-with-fallback.md](adr/0009-objects-csv-mainid-mapping-with-fallback.md) | `objects.csv` maps alarm_object → MainID; fallback `default_main_id`. |
| [0010-prepend-description-never-change-status.md](adr/0010-prepend-description-never-change-status.md) | Transitions are prepended to the incident description; the bot sets StatusID only at creation. |
| [0011-per-directory-csv-audit-log.md](adr/0011-per-directory-csv-audit-log.md) | One semicolon CSV per Vista directory, all priorities. |
| [0012-json-state-files-atomic-writes.md](adr/0012-json-state-files-atomic-writes.md) | `alarms_state.json` / `csv_state.json`, written via tmp + `os.replace`. |
| [0013-secrets-in-config-json.md](adr/0013-secrets-in-config-json.md) | **Superseded by 0021** — credentials used to live in `config.json`. |
| [0014-commit-runtime-data-for-migration.md](adr/0014-commit-runtime-data-for-migration.md) | **Superseded by 0022** — runtime data was committed to plan the migration. |
| [0015-sql-storage-for-alarm-history.md](adr/0015-sql-storage-for-alarm-history.md) | **Accepted** — SQLite in the digibuild worker. |
| [0016-cts-server-vs-vps-responsibility-split.md](adr/0016-cts-server-vs-vps-responsibility-split.md) | **Accepted** — Option A: the bot stays on the CTS server and ships runs. |
| [0017-friendly-alarm-naming-registry.md](adr/0017-friendly-alarm-naming-registry.md) | **Accepted** — the registry lives in digibuild. |
| [0018-alarm-web-application-on-vps.md](adr/0018-alarm-web-application-on-vps.md) | **Superseded by 0022** — the standalone web app on the VPS. |
| [0019-mainmanager-v3-incident-api.md](adr/0019-mainmanager-v3-incident-api.md) | MainManager v3 incident API: write `Remarks`, read `Description`, echo on PUT, 404 classification, read-only dry run. |
| [0020-hmac-ingest-to-digibuild.md](adr/0020-hmac-ingest-to-digibuild.md) | One HMAC-signed batch per run to `api.digibuild.dk`; SQLite outbox. |
| [0021-secrets-in-secrets-json.md](adr/0021-secrets-in-secrets-json.md) | Credentials only in `secrets.json` or the environment; rotation after the public exposure. |
| [0022-cts-side-only-repo-web-app-in-digibuild.md](adr/0022-cts-side-only-repo-web-app-in-digibuild.md) | This repo holds the CTS side only; the web app is the digibuild sub-project. |

### C4

| File | Contents |
|---|---|
| [README.md](c4/README.md) | How the C4 levels map to this system; notation. |
| [01-system-context.md](c4/01-system-context.md) | Level 1: operators, TAC Vista, alarm bot, MainManager, digibuild, portal users. |
| [02-container.md](c4/02-container.md) | Level 2: scheduled task, script, shipper, files on disk, MainManager v3 API, digibuild ingest. |
| [03-component.md](c4/03-component.md) | Level 3: components inside `main.py` and `shipper.py`. |
| [04-code.md](c4/04-code.md) | Level 4: classes, state schemas, the signature, `MMClient`, `IngestBatch`, outbox, with line references. |
| [05-target-architecture-proposal.md](c4/05-target-architecture-proposal.md) | Option A as realised in digibuild, plus the original A/B/C proposal for the record. |

### Design records

| File | Contents |
|---|---|
| [2026-09-28-vps-branch-design.md](design/2026-09-28-vps-branch-design.md) | The design of the VPS branch, the owner's decisions D-A … D-E, and where each one lives now. |

### Reference

| File | Contents |
|---|---|
| [alr-file-format.md](reference/alr-file-format.md) | `$this.alr`: tab-delimited, ISO-8859-1, 36 fields; the 12 fields the bot uses; state and priority semantics. |
| [csv-audit-format.md](reference/csv-audit-format.md) | `csv/*.csv` header, event names, synthetic NORMAL/RESOLVED rows, filename sanitising. |
| [state-files.md](reference/state-files.md) | `alarms_state.json`, `csv_state.json`, `mm_token.json` and `outbox.sqlite`: schemas, statuses, pruning. |
| [log-format.md](reference/log-format.md) | `logs/YYYY-MM-DD.log` line format, message catalogue, per-run status table, volumes. |
| [config-reference.md](reference/config-reference.md) | Every `config.json` and `secrets.json` key, CLI flags, exit codes (credential values omitted). |
| [shipper.md](reference/shipper.md) | The optional `vps` config section, HMAC signing, `IngestBatch`, outbox, failure behaviour, install steps. |
| [mainmanager-api.md](reference/mainmanager-api.md) | Token and the v3 incident endpoints as used by `MMClient`; write model, defaults, errors; the v1 history. |
| [install-script.md](reference/install-script.md) | `install.ps1`: first install and `-Download` updates on the CTS server, what each step checks, switches, rollback. |

## Conventions

- Code references are written as `main.py:<line>` / `shipper.py:<line>` or as function names
  (`run_csv_logging()`), against `main.py` v2.1.0 unless a page says otherwise.
- Danish alarm texts and Vista terms are quoted verbatim (e.g. "Høj Temperatur", *kvitteret*).
- Credential **values** are never written in these docs; they are referred to by location
  (`secrets.json → mainmanager.username` / `mainmanager.password`, `vps.ingest_secret`). The same goes for
  VPS addresses, internal ports and VPS filesystem paths: the public route names on `api.digibuild.dk` are the
  only VPS-side locations named here.
- Diagrams are Mermaid fenced blocks; GitHub renders them.
- Backlog changes go through [TODO.md](TODO.md) and the `cts-todo-keeper` agent
  (a user-level agent on the development box, not in this repo; ADR-0023), which owns that file.
