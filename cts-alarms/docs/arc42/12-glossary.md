# 12. Glossary

Part of the [arc42 documentation](../README.md).
Previous: [11. Risks and Technical Debt](11-risks-and-technical-debt.md) · Back to [1. Introduction and Goals](01-introduction-and-goals.md)

Terms are grouped by domain. Danish words are marked *(da)* and translated. Where a term maps to code, the identifier or line is given.

## Building management / TAC Vista

| Term | Meaning |
|------|---------|
| **CTS** | *(da)* Danish term for a building-management system (commonly expanded as "Central Tilstandskontrol og Styring"). In this project "the CTS server" is the Windows Server 2016 host running TAC Vista, i.e. where the bot runs. Incidents are named `CTS Alarm - …`. |
| **BMS** | Building Management System — the English equivalent of CTS. Controls and monitors HVAC, dampers, pumps, fire dampers, etc. |
| **TAC Vista** | Schneider Electric TAC Vista 5.1.9, the BMS server software installed on the CTS server. Its database directory `…\TAC Vista 5.1.9\DB\$thisdb\` contains the alarm list. |
| **Vista client / operator** | A person logged into the Vista UI who can acknowledge (kvittere) alarms. Appears in the alarm list as `"<LOGIN> (<Full name>)"`, e.g. the owner's `GPST (Georgi ISS)`; also as a bare login, or as `LON-OP` / `SYSTEM (User Profile SYSTEM)`. Other operators' names are not reproduced in this public repo. |
| **`$this.alr`** | The live alarm list file, `C:\ProgramData\Schneider Electric\TAC Vista 5.1.9\DB\$thisdb\$this.alr`. Tab-delimited, ISO-8859-1, 36 fields, no header (CRLF on the server per the build reference; the committed snapshot has LF, the parser accepts both); rewritten in place by Vista as alarms come and go. See [ALR format](../reference/alr-file-format.md). |
| **alarm_snapshot.alr** | The bot's copy of `$this.alr` in `C:\priorityalarmsapi`, made with `shutil.copy2` before parsing (`snapshot_alarm_file()` `main.py:157`). |
| **Vista ID** | Field 1 of `$this.alr`: `VISTA_SERVER#xxxxxxxx`, where the hex suffix is a Unix-epoch-like timestamp assigned at creation (close to, but not always equal to, `date1`: identical in 68 of the 180 snapshot rows). Identifies one **alarm instance** and is the primary key in state files, CSV rows and incident tracking (the incident itself carries no Vista ID) ([ADR-0003](../adr/0003-vista-id-as-primary-key.md)). |
| **Alarm object** (`alarm_object`) | Field 2: the short name of the alarming point, e.g. `320-01-07930-0101_AL`, `325-10-04806-Udsugning-Alarm-Bit0_Fejl`. Key of `objects.csv`. Not unique across controllers. |
| **Directory** (`directory`) | Field 22: the full Vista object path, e.g. `VISTA_SERVER-LOYTEC_PORT-RHQ-34551_02_ET5_XENTA-5A193_10-APP.I_O_FORC_A`. Unambiguous per physical point; used for exception matching and as the CSV file name (sanitized). Sometimes identical to `alarm_object` for non-LON objects. |
| **Alarm text** (`alarm_text`) | Field 13: the Danish human-readable message, e.g. `Høj Temperatur`, `Lav Temperatur`, `Røgspjæld er åbne`, `Brand fra ABA`, `I/O Punkt forceret`, `Lavt tryk`, `Pumpefejl`. May change when the condition clears (e.g. `ATV21 Fejl` → `ATV21 OK`). |
| **Alarm instance** | One row in `$this.alr`, one Vista ID. When Vista removes the row and the point alarms again later, that is a new instance with a new Vista ID. |
| **Priority** (`priority`) | Field 7, TAC Vista priority **1–9 where 1 is most urgent**. Observed: 1 (e.g. `Brand fra ABA`, `Høj Vandstand …`), 2 (`Høj Temperatur`, `Røgspjæld er åbne`, `I/O Punkt forceret`), 3 (`Lavt tryk`, most `Høj rumtemperatur` — alarm text is not bound to a priority; the same text also occurs at pri 1/2), 9 (`$EE_Mess` system messages and a few `nvoAlarmStatus_A0` status points). The config threshold `max_priority_number: 2` means "priority 1 and 2 become tickets". |
| **state1** | Field 4, the *condition* axis: `0` = ACTIVE (condition present), `1` = NORMAL (condition gone / reset), `6` = system/info event. |
| **state2** | Field 5: `0` for ordinary alarms, `2` for system/info events. `(state1, state2) == (6, 2)` is treated as a system event and excluded from CSV auditing (`Alarm.is_system_event`, `main.py:145`). |
| **ack_flag** | Field 11, the *operator* axis: `0` = unacknowledged, `1` = acknowledged. The bot only counts an acknowledgement when `ack_flag == 1` **and** `user != "No user"` (`classify_alarm_status()` `main.py:108`). |
| **user** | Field 10: `"No user"` or the acknowledging operator. Part of the state signature so a re-acknowledgement by a different person is detected. |
| **count** | Field 18: how many times this alarm instance has re-triggered while in the list. Recorded in CSV, not used for diffing. |
| **date1 / date2** | Fields 3 and 6: Unix epochs in hex; first occurrence and last state transition. Written to CSV as `date1_hex`/`date1_human` etc. |
| **ACTIVE / NORMAL** | Vista's condition states (state1 0/1). "Alarm returned to NORMAL" = condition cleared; "alarm ACTIVE again" = re-triggered. |
| **ACKNOWLEDGED / UNACKNOWLEDGED** | Vista's operator states (ack_flag 1/0). |
| **Status label** | The bot's combined label: `ACTIVE`, `ACTIVE + ACKNOWLEDGED`, `NORMAL`, `NORMAL + ACKNOWLEDGED`, `RESOLVED` (`main.py:101-117`). `ACKNOWLEDGED` alone is a legacy value still present in two `alarms_state.json` entries. |
| **RESOLVED** | The bot's own term (not Vista's): the Vista ID has disappeared from `$this.alr`, which Vista does only once the alarm is NORMAL **and** acknowledged. Triggers the final incident line and `resolved_iso` in state. |
| **kvitteret / kvittere / kvittering** *(da)* | "acknowledged / to acknowledge / acknowledgement". Used verbatim in the incident line `RESOLVED — alarm was acknowledged (kvitteret) and removed from Vista alarm list` (`main.py:958`). |
| **resat** *(da)* | "reset" — sometimes appears in alarm text when a condition clears. |
| **$EE_Mess** | Vista system/event messages (alarm object `VISTA_SERVER-$EE_Mess`), priority 9, observed with ordinary states `(1, 0)` / `(0, 0)`. Excluded from tickets by the priority threshold but still recorded in the CSV audit trail (309 `FIRST_SEEN` rows — the most frequent "alarm" in the data). The `(6, 2)` system-event skip in `Alarm.is_system_event` has never matched a row in the committed data. |
| **LON / LonWorks** | The field-bus protocol connecting controllers to Vista. `LON-OP` appears as an acknowledging-user value in the data (field 10); its meaning is not documented in the repo. |
| **LOYTEC** | Vendor of the LON/IP gateways (`LOYTEC_PORT` in directory paths) that connect XENTA controllers to the Vista server. |
| **XENTA** | TAC Xenta programmable controllers (e.g. `XENTA-0201_01`, `XENTA-5A193_10`) — the devices whose points raise alarms. Appears in directory paths. |
| **Zonemaster / Etage N** *(da)* | Zone controllers and floor names used in object names and directories (`Etage` = floor; `ET5` presumably = 5th floor — inferred from the names, unconfirmed, see [T-051](../TODO.md)). |
| **`_AL` / `_AH` / `_A` suffix** | Alarm-point naming convention: `_AL` = low-limit alarm (e.g. `Lav Temperatur`), `_AH` = high-limit alarm (`Høj Temperatur`), `_A` = generic/binary alarm (`Manuel_A`, `I_O_FORC_A`, `ForcLuk_A`). The same sensor can have both `_AL` and `_AH` objects. |
| **Object code** (e.g. `320-01-07930-0101`) | Building–system–controller–point identifiers used as `alarm_object`. Hard to read for humans; the motivation for the friendly-name registry ([ADR-0017](../adr/0017-friendly-alarm-naming-registry.md)). |
| **ABA** *(da)* | "Automatisk Brandalarmanlæg" — automatic fire-alarm system; `Brand fra ABA` = fire alarm from ABA (priority 1). |
| **Røgspjæld** *(da)* | Smoke damper; `Røgspjæld er åbne` = smoke dampers are open. |
| **I/O Punkt forceret** *(da)* | I/O point forced (manually overridden) — typical candidate for `exceptions.csv`. |
| **Frequent alarm texts** *(da)* | `Høj/Lav Temperatur` = high/low temperature; `Høj/Lav rumtemperatur` = high/low room temperature; `Lavt tryk` = low pressure; `Temperatur ved legionellabekæmpelse ikke opnået` = temperature for legionella control not reached; `Drifttimer overskredet` = running hours exceeded; `Pumpefejl` = pump fault; `Sprinkler Fejl` = sprinkler fault; `Spjæld i Fejlstilling` = damper in fault position; `Udsugning` / `Indblæsning` = exhaust / supply air. |

## Bot concepts

| Term | Meaning |
|------|---------|
| **Alarm bot / TAC Vista alarm bot** | This system: `main.py` on the CTS server, scheduled task `\TACVistaLogs\TACVista_Alarm_Bot`. Prefix used in every incident line: `Alarm bot - …`. |
| **Run / cycle / poll** | One execution of `main.py` (every 5 minutes). Each run is a separate process. |
| **Snapshot** | The copy of `$this.alr` taken at the start of each run; the bot never parses the live file ([ADR-0005](../adr/0005-snapshot-then-parse.md)). |
| **Signature / state signature** | The tuple `(state1, state2, ack_flag, user.strip())` returned by `Alarm.state_signature()` (`main.py:149`). Stored as `last_state_sig` (alarms_state) and `sig` (csv_state). Any change between runs is "a transition" ([ADR-0004](../adr/0004-state-signature-diff.md)). |
| **Transition** | A signature change; described in Vista terms by `describe_transitions()` (`main.py:628`) for incidents and classified into CSV events by `classify_csv_event()` (`main.py:393`). |
| **Bootstrap** | The first run when `meta.bootstrapped` is `false`: all filtered alarms are recorded with `incident_id: null`, no incidents created (`main.py:831-848`, [ADR-0006](../adr/0006-bootstrap-on-first-run.md)). A "bootstrapped" alarm later gets an incident on its first transition. `--no-bootstrap` skips this. |
| **Kept alarms** | Alarms after the priority threshold and exception filter (`kept` in `run()`, `main.py:812`). Only these enter the incident pipeline. |
| **Threshold** | `thresholds.max_priority_number` in `config.json` (currently 2). |
| **Exception** | A full directory path listed in `exceptions.csv`; matching alarms are skipped entirely by the incident pipeline (still CSV-audited) ([ADR-0008](../adr/0008-directory-based-exceptions.md)). |
| **Mapping / objects.csv** | `alarm_object,MainID` lines; empty today ([ADR-0009](../adr/0009-objects-csv-mainid-mapping-with-fallback.md)). |
| **Fallback MainID** | `mainmanager.default_main_id` (14228) used when `objects.csv` has no entry; flagged `main_id_fallback_used: true` in state. |
| **State file** | `alarms_state.json` — incident tracking for kept alarms — and `csv_state.json` — signature tracking for all alarms. See [State files](../reference/state-files.md). |
| **CSV audit / audit trail** | The per-directory `csv/<sanitized directory>.csv` files with one event row per transition ([CSV audit format](../reference/csv-audit-format.md)). |
| **CSV event** | One of `FIRST_SEEN`, `ACTIVE`, `NORMAL`, `ACKNOWLEDGED`, `UNACKNOWLEDGED`, `USER_CHANGED`, `STATE1_x_TO_y`, `STATE_CHANGED`, `RESOLVED`. |
| **Prune** | Deleting RESOLVED entries from `alarms_state.json` after `prune_resolved_after_days` (30) (`prune_state()` `main.py:284`); csv_state deletes resolved entries immediately (`main.py:521-523`). |
| **Dry run / parse-only** | `--dry-run`: log intended API calls as `[DRY]`, no HTTP; `--parse-only`: snapshot, parse, CSV-audit, filter and print the status table, then exit. |
| **Status table** | The per-alarm block logged every run by `log_alarm_status_table()` (`main.py:682`): Vista ID, priority, status label, user, text, tracking (`incident #N` / `bootstrapped — no incident yet` / `NEW — will create incident`). |
| **Missed between polls** | A transition that happened and was undone (or completed) within one 5-minute interval. Detected only for ACTIVE → gone: the bot writes a synthetic NORMAL line/row before RESOLVED (`main.py:951-956`, `main.py:501-511`). |
| **404 retry loop** | Known defect: when `GetIncident` returns 404 (incident no longer retrievable — cause unknown; since 2026-09-19 it affects every id, see [11. Risks R-18](11-risks-and-technical-debt.md#r-18-mainmanager-integration-not-working-since-2026-09-19)) the update is not persisted and is retried every run. See [11. Risks](11-risks-and-technical-debt.md). |

## MainManager / FM

| Term | Meaning |
|------|---------|
| **MainManager** | Ramboll FM's facility-management platform, instance `https://rambollfm.mainmanager.dk`. Receives alarms as incidents via its REST API ([MainManager API](../reference/mainmanager-api.md)). |
| **FM** | Facility management; "FM staff" are the people working tickets in MainManager (some are also Vista operators). |
| **ISS** | The facility-services company several operators belong to (e.g. `Georgi ISS`). |
| **Incident** | A MainManager ticket. The bot creates one per kept alarm instance, named `CTS Alarm - <alarm_object> - <alarm_text>` (≤ 100 chars). |
| **IncidentID** | MainManager's numeric id returned by the create call (`items[0].id` in v3, `ID` in v1), stored as `incident_id` in state and printed in the status table as `incident #N`. |
| **Description / Remarks** | The incident's free-text body. In the v3 API the writable field is `Remarks`, which the tenant stores as the full `Description`; the `Remarks` read back is a copy truncated to ~245 characters. Hence: GET `Description` → prepend → PUT `Remarks` → GET verify. Newest line on top ([ADR-0019](../adr/0019-mainmanager-v3-incident-api.md)). v1 used `IncidentRemarks` on `UpdateIncident`. |
| **MainID** | The MainManager object (building/asset/location) an incident is attached to. Resolved from `objects.csv`, else the fallback 14228 (older incidents used 9756). |
| **IncidentTypeID** (v1: Checkword / CheckwordItemID) | MainManager's classification of an incident. This bot uses `277` ("CTS API"); the Indeklima bot uses `281` ("CTS Log - Rum …"). v1 sent the pair `CheckwordID 8 / CheckwordItemID 277`; v3 drops `CheckwordItemID` silently. |
| **Grade / GradeID** | MainManager severity/grade of the incident; fixed at `10`. |
| **StatusID** | MainManager workflow status; set to `5` at creation and never changed by the bot ([ADR-0010](../adr/0010-prepend-description-never-change-status.md)). |
| **Write model** | The 22 keys the v3 tenant accepts on POST/PUT (`MainID, IncidentTypeID, Name, Remarks, LocationID, …, ID, Inactive`); everything else is dropped silently. `ECHO_FIELDS` is this list minus `ID`/`Remarks`. |
| **v3 incident API** | `POST/GET/PUT /api/v3/incidents` — the only incident endpoints since EG removed `/restapi/Incident/*` on 2026-09-19 ([ADR-0019](../adr/0019-mainmanager-v3-incident-api.md)). |
| **Ticket missing / `incident_missing`** | State flag for an alarm whose ticket answers 404 while the v3 list works: never sent again, still tracked locally. |
| **API down / `deferred`** | A run in which MainManager answered 404 on the list too (or no credentials): no further calls, state untouched, the number of skipped actions reported as `deferred=N`. |
| **Token / password grant** | `POST /restapi/token` with `grant_type=password` and the MainManager service-account credentials from `secrets.json` returns a bearer `access_token` (~2 h); cached in `mm_token.json`. |
| **Service account** | The MainManager user identity the bot (and the Indeklima bot) authenticates as. Credentials in `secrets.json` on the CTS server; password rotated 2026-09-29 after it had been in the public repo history ([ADR-0021](../adr/0021-secrets-in-secrets-json.md)). |

## Infrastructure and project

| Term | Meaning |
|------|---------|
| **CTS server** | Windows Server 2016 running TAC Vista and the bot (`C:\priorityalarmsapi`). The machine where `$this.alr` lives and is read today; whether it could be read remotely (file share) is unknown/undecided. |
| **`C:\priorityalarmsapi`** | Working folder on the CTS server: `main.py`, `shipper.py`, `config.json`, `secrets.json`, `objects.csv`, `exceptions.csv`, snapshot, state files, `mm_token.json`, `outbox.sqlite`, `csv/`, `logs/`. (The build reference's `C:\ctsapi\alarms` is outdated.) |
| **GPST** | The Windows user the scheduled task runs as (`LeastPrivilege`), and the owner's Vista login (`GPST (Georgi ISS)`). |
| **Task Scheduler task** | `\TACVistaLogs\TACVista_Alarm_Bot`, defined in `TACVista_Alarm_Bot.xml`: every 5 min, 5-min limit, `IgnoreNew`, restart 3×. |
| **VPS** | The owner's Virtual Private Server (once called "DPS server"), which runs digibuild's background workers behind `api.digibuild.dk` — including the `cts-alarms` worker that receives this bot's runs. |
| **digibuild** | The owner's web portal: pages on Vercel (Clerk login, Danish/English), background workers on the VPS. MM-reports is sub-project #1; **`cts-alarms`** (alarm history, reports, friendly names) is sub-project #2 ([ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)). |
| **Shipper** | `shipper.py`: posts one batch per run to the digibuild `cts-alarms` worker ([ADR-0020](../adr/0020-hmac-ingest-to-digibuild.md)). |
| **IngestBatch** | The JSON the shipper posts: `schema_version`, `source`, `run`, `events`, `snapshot`, `incidents` ([reference/shipper.md](../reference/shipper.md)). |
| **HMAC / X-Signature** | digibuild's machine-hop authentication: hex HMAC-SHA256 over `timestamp.nonce.raw_body`, sent with `X-Timestamp` and `X-Nonce`; ±300 s, nonce single-use. |
| **Outbox** | `outbox.sqlite` on the CTS server: batches not yet accepted by the receiver, resent oldest-first. |
| **`secrets.json`** | Git-ignored, ACL-restricted file on the CTS server holding the MainManager pair and the ingest secret ([ADR-0021](../adr/0021-secrets-in-secrets-json.md)). |
| **Indeklima bot** *(da: indeklima = indoor climate)* | Sibling script by the same author: polls Vista temperature trend logs every 15 minutes and creates MainManager incidents (`CheckwordID 21 / 281`) for rooms outside 22–24 °C (type 281). Same MainManager account; stopped with the API removal of 2026-09-19 ([TODO T-105](../TODO.md)). Not in this repository; referenced by `main.py:4` and the build reference. |
| **Build reference** | `Alarm_bot_build_reference.md`, the original design document. Partly stale (3-min cadence, `exceptions.txt`, `C:\ctsapi\alarms`, MainID 9756, threshold 3, old status names). `main.py` wins on conflict. |
| **Friendly name** | Human-readable label for an alarm point (e.g. instead of `320-01-07930-0101_AL`), kept in the registry of the digibuild `cts-alarms` worker and edited on its pages ([ADR-0017](../adr/0017-friendly-alarm-naming-registry.md)). |
| **Recurring alarm** | Analysis concept (a digibuild report): the same alarm object / text producing many instances over time (e.g. `Høj Temperatur` 176 FIRST_SEEN events, 171 of them priority 2; `320-01-07930-0101_AL` 14 instances). |
| **arc42 / ADR / C4** | The three documentation formats used in `docs/`: arc42 (this template), Architecture Decision Records (`docs/adr`), C4 model diagrams (`docs/c4`). |
