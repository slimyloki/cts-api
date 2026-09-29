# Reference: log files (`logs/YYYY-MM-DD.log`)

The bot's own run log on the CTS server. Line references are to `main.py` v2.1.0 and `shipper.py`. The logs
stay on the CTS server; the 163 files of 2026-04-17 … 2026-09-28 that were committed for the migration
([ADR-0014](../adr/0014-commit-runtime-data-for-migration.md), superseded) are archived privately on the VPS and
were imported into the digibuild `cts-alarms` database. From the shipper's first run on, every run is also
recorded there as a structured run summary, so the log is no longer the only durable history.

Related: [config-reference.md](config-reference.md) · [mainmanager-api.md](mainmanager-api.md) · [shipper.md](shipper.md) ·
[arc42 §6 runtime view](../arc42/06-runtime-view.md) · [arc42 §11 risks](../arc42/11-risks-and-technical-debt.md) · [TODO.md](../TODO.md)

## Mechanics (`setup_logging()`, `main.py:92`)

| Aspect | Value |
|---|---|
| Folder | `config.json -> paths.log_folder` = `C:\priorityalarmsapi\logs` |
| File | `<YYYY-MM-DD>.log`, chosen once per process from the local date at start-up; a run crossing midnight keeps writing to the old file |
| Encoding | UTF-8 |
| Handlers | `FileHandler` (append) + `StreamHandler(stdout)` — a manual `python main.py --dry-run` shows the same lines in the console; stdout is discarded under Task Scheduler |
| Logger | `logging.getLogger("alarmbot")`, level `INFO` → `log.debug()` calls are never written |
| Line format | `%Y-%m-%d %H:%M:%S [LEVEL] message` (local time, no zone), e.g. `2026-09-28 00:00:04 [INFO] Parsed 177 total alarm rows` |
| Rotation / retention | none — one file per day, never deleted ([TODO T-023](../TODO.md)) |
| Failures before logging exists | config load failure is printed to **stderr** only (`main.py:1362`) |
| Secrets | never logged: credentials, the token, and the ingest secret (scrubbed from shipper errors) |

Grep-friendly anchors: every run starts with a line of 70 `=` characters and `Alarm bot started (v…` and ends with
`Run complete: …` (or `BOOTSTRAP complete.` / `[DRY] BOOTSTRAP not saved (dry-run).`, or an `[ERROR]`/traceback),
optionally followed by `VPS:` lines from the shipper.

## Every message the current code can emit

Grouped by phase, in emission order. `{…}` are placeholders. "v1 observed" is the count over the 163 files of
2026-04-17 … 2026-09-28 (written by v1.x) after normalising numbers/IDs; "new" marks messages introduced in
v2.0.0 … v2.1.0, which the historical logs cannot contain.

### Start-up (`main()`, `main.py:1351`) and credentials (`load_secrets()`, `main.py:61`)

| Level | Message | Line | Meaning / v1 observed |
|---|---|---|---|
| stderr | `Config load failed ({path}): {e}` | 1362 | config unreadable → exit 2. 0 |
| INFO | `======================================================================` | 1366 | run separator. 45,612 |
| INFO | `Alarm bot started (v{version}, config={path}, dry_run={b}, parse_only={b}, no_bootstrap={b})` | `main()` | `config=` since v2.1.1: the config file actually used (in place `C:\cts-api\cts-alarms\config.json`, or the copied one). v2.0.x/2.1.0 had no `config=`; v1: `Alarm bot started (dry_run=…, parse_only=…, no_bootstrap=…)`, 45,610. |
| ERROR | `MainManager API unusable this run: MainManager login failed: {error} — alarms are tracked, nothing is sent, state left untouched` | `run()` | v2.1.1: the one login check per real run failed; every action is deferred, none is abandoned. |
| WARNING | `MainManager rejected the login — no new attempt for 30 min unless the credentials change (mm_auth_failed.json)` | `record_auth_rejection()` | v2.1.1, HTTP 400/401/403 only. |
| ERROR | `MainManager API unusable this run: MainManager login skipped: rejected {n} min ago (…); next try in {m} min, or at once when the credentials change — …` | `run()` | v2.1.1 backoff; see `reference/state-files.md` → `mm_auth_failed.json`. |
| INFO | `MainManager login OK again — backoff cleared` | `run()` | v2.1.1 |
| INFO | `Credentials: from environment (MM_USERNAME/MM_PASSWORD)` / `Credentials: from {secrets_file}` | 70 / 78 | new |
| WARNING | `{secrets_file}: mainmanager.username/password missing` | 80 | new — file exists but has no pair |
| WARNING | `Credentials: from config.json — DEPRECATED, move them to {secrets_file}` | 84 | new — legacy pair still in `config.json` |
| WARNING | `No MainManager credentials configured` | 88 | new |
| ERROR | `Unhandled exception` + traceback | 1373 | any uncaught exception → exit 1. 0 |

### Input loading

| Level | Message | Line | Meaning / v1 observed |
|---|---|---|---|
| WARNING | `objects.csv not found at {path} — all alarms use fallback` | 261 | 0 |
| WARNING | `objects.csv:{n}: no delimiter — skipping` / `objects.csv:{n}: MainID not integer` | 270 / 278 | 0 |
| INFO | `Loaded {n} object mappings from {path}` | 279 | 45,612 (n = 0 since 2026-04-17 11:19) |
| INFO | `exceptions.csv not found at {path} — no exceptions` | 289 | 0 |
| INFO | `Loaded {n} exception entries from {path}` | 300 | 45,610 (n = 1) |

### Snapshot + parse

| Level | Message | Line | Meaning / v1 observed |
|---|---|---|---|
| WARNING | `snapshot attempt {i}/5 failed: {e}` | 214 | file locked; retried after 0.5 s. 0 |
| ERROR | `Could not snapshot alarm file: {e}` | 1037 | all 5 attempts failed → exit 2 (the run is still shipped). 0 |
| WARNING | `line {n}: expected 36 fields, got {m} — skipping` / `line {n}: parse error ({e}) — skipping` | 228 / 247 | 0 |
| INFO | `Parsed {n} total alarm rows` | 1044 | 45,612 |

### CSV audit (`run_csv_logging()`)

| Level | Message | Line | Meaning / v1 observed |
|---|---|---|---|
| INFO | `CSV audit: logged {n} events across alarm directories` | 611 | only when n > 0. 10,434 runs (max 143) |
| INFO | `CSV audit: [DRY] would log {n} events across alarm directories` | 611 | new — read-only run; nothing written |
| ERROR | `CSV audit logging failed (non-fatal): {e}` | 1056 | 0 |

### Filter + status table (`log_alarm_status_table()`, `main.py:882`)

| Level | Message | Line | Meaning / v1 observed |
|---|---|---|---|
| DEBUG | `skip (exception): {directory}` | 1066 | never written (level INFO) |
| INFO | `{k} alarms after priority<={p} + exception filter (removed {r})` | 1069 | 45,610 |
| INFO | `--- Alarm status table ---`, column header, one line per kept alarm, `--- Total: {k} alarms — {label}: {n}, … ---` | 887–912 | per run; `tracking` ∈ `NEW — will create incident` / `incident #{id}` / `bootstrapped — no incident yet` |

Example block (2026-09-28 00:00:04, abridged):

```
2026-09-28 00:00:04 [INFO] 68 alarms after priority<=2 + exception filter (removed 109)
2026-09-28 00:00:04 [INFO] --- Alarm status table ---
2026-09-28 00:00:04 [INFO]   Vista ID                       Pri Status                   User                         Alarm Text                                    Tracking
2026-09-28 00:00:04 [INFO]   VISTA_SERVER#6A9A5C9F            2 NORMAL                   No user                      Lav Temperatur                                incident #36630
…
2026-09-28 00:00:04 [INFO] --- Total: 68 alarms — ACTIVE: 1, ACTIVE + ACKNOWLEDGED: 6, NORMAL: 61 ---
```

This table is the bulk of the log volume (≈80 %) and is redundant with the state files and the shipped snapshot.

### Bootstrap (`main.py:1080-1098`)

| Level | Message | Line | v1 observed |
|---|---|---|---|
| INFO | `BOOTSTRAP: first run — recording {n} existing alarms. No incidents created now. …` | 1082 | 0 with this wording |
| INFO | `BOOTSTRAP complete.` / `[DRY] BOOTSTRAP not saved (dry-run).` | 1096 | 0 / new |

### MainManager availability and the dry-run credential check

| Level | Message | Line | Meaning |
|---|---|---|---|
| ERROR | `MainManager API unusable this run: no MainManager credentials configured — alarms are tracked, nothing is sent` | 1113 | new — no credentials in a real run |
| ERROR | `[DRY] no MainManager credentials configured` | 1120 | new |
| INFO | `[DRY] MainManager credentials OK` | 1124 | new — the token request succeeded (or a cached token was used, [TODO T-108](../TODO.md)) |
| ERROR | `[DRY] MainManager credential check FAILED: {e}` | 1126 | new — wrong password, network, … |
| WARNING | `{vista_id}: incident #{id} not found in MainManager during {what} — marked incident_missing, no further updates for it` | 1137 | new — one ticket gone, the v3 list answers |
| ERROR | `MainManager API unusable this run: HTTP 404 from the v3 incident API during {what} — API change or lost permission? State left untouched; will retry next run` | 1142 | new — endpoint-wide failure; no further calls this run |
| ERROR | `MainManager API was unusable this run ({reason}); {n} action(s) deferred to the next run` | 1339 | new — end-of-run summary of the above |

### Incident pipeline (`resolve_main_id()`, `create_incident_for_alarm()`, `run()`)

| Level | Message | Line | Meaning / v1 observed |
|---|---|---|---|
| WARNING | `No objects.csv mapping for '{alarm_object}' — using fallback MainID {id}` | 941 | every create (`objects.csv` empty). 457 |
| INFO | `[DRY] Would create incident: main_id={id} name='{name}'` | 959 | 0 |
| ERROR | `CreateIncident FAILED for {vista_id}: {e}` | 968 | non-404 create failure; entry saved with `incident_id: null`. v1: 8 (all 404 — in v2 a 404 is classified instead) |
| INFO | `CREATED incident {id} for {vista_id} ({alarm_object}) main_id={id} [fallback]` | 973 | 469 |
| WARNING | `{vista_id} reappeared after RESOLVED — ignoring` | 1193 | 0 |
| INFO | `{vista_id}: [{lines}] (incident #{id} missing in MainManager — not sent)` | 1209 | new — transition on a ticket marked missing |
| INFO | `{vista_id} bootstrapped alarm changed: {old} -> {new} — creating incident` | 1217 | "THE FIX" path |
| INFO | `[DRY] Would update incident {id}: [{lines}]` | 1239 | 0 |
| ERROR | `UpdateIncident FAILED for {vista_id} (attempt {n}/3): {e}` | 1153 | new wording (v1: `UpdateIncident FAILED for {vista_id}: {e}`, 4,408 — retried forever) |
| ERROR | `UpdateIncident FAILED for {vista_id} 3 times — giving up on this transition, tracking continues: {e}` | 1157 | new — the state advances |
| INFO | `UPDATED incident {id} for {vista_id}: ['{line}', …]` | 1256 | 7,377 |

### Resolution

| Level | Message | Line | Meaning / v1 observed |
|---|---|---|---|
| INFO | `[DRY] Would mark {vista_id} RESOLVED; incident {id}` | 1294 | 0 |
| ERROR | `Resolution update FAILED for {vista_id} (attempt {n}/3): {e}` / `… 3 times — giving up …` | 1153 / 1157 | new wording (v1: 2,151 — 2,150 of them one 404 loop) |
| INFO | `RESOLVED incident {id} for {vista_id} ({alarm_object})` | 1317 | 406 |
| INFO | `RESOLVED (incident #{id} missing in MainManager) {vista_id} ({alarm_object})` | 1320 | new |
| INFO | `RESOLVED (no incident) {vista_id} ({alarm_object})` | 1323 | 53 |

### End of run

| Level | Message | Line | v1 observed |
|---|---|---|---|
| INFO | `Pruned {n} resolved entries older than {d} days` | 350 | 36 |
| INFO | `[DRY] nothing written (state, CSV and MainManager untouched)` | 1336 | new — every `--dry-run` |
| INFO | `Run complete: created={c}, updated={u}, resolved={r}, unchanged={s}, deferred={d}` | 1341 | **`deferred=` is new** (v1 ended at `unchanged=`; 45,610). `deferred` counts actions skipped because the API was unusable; they are retried next run. |

### MainManager client (`MMClient`, `main.py:624`)

| Level | Message | Line | Meaning / v1 observed |
|---|---|---|---|
| WARNING | `incident_defaults.{key} not set — MainManager will derive it` | 661 | new — `LocationID`/`ReportedByID`/`ReportedByOrganisationID` missing |
| INFO | `API AUTH: using cached token` | 675 | new — `mm_token.json` still valid |
| INFO | `API AUTH: requesting token from {base}/restapi/token` / `API AUTH: HTTP {code}` / `API AUTH: token obtained OK` | 683 / 689 / 700 | 8,258 / 8,254 / 8,251 |
| WARNING | `API AUTH: token cache not writable: {e}` | 699 | new |
| DEBUG | `API PROBE failed: {e}` | 746 | never written |
| INFO | `API CREATE: POST {base}/api/v3/incidents` / `API CREATE: MainID={id}, Name='{name}'` / `API CREATE: response={item}` | 775 / 776 / 779 | new endpoint (v1: `/restapi/Incident/CreateIncident`, 477; the v3 response item echoes the write model) |
| WARNING | `API CREATE: incident {id} StatusID reads {x}, wanted {y}` / `API CREATE: status check for {id} failed: {e}` | 794 / 797 | new |
| INFO | `API UPDATE: PUT {base}/api/v3/incidents ID={id}` / `API UPDATE: verified incident {id}` | 811 / 817 | new (v1: `API UPDATE: POST …/UpdateIncident IncidentID={id}`, 7,824) |

The `{e}` in `FAILED` messages is `str(exception)`; it may embed a URL with the incident id, never credentials.

### VPS shipper (`shipper.py`; only with a `"vps"` section in `config.json`)

| Level | Message | Line | Meaning |
|---|---|---|---|
| WARNING | `VPS shipper disabled — bad "vps" config: {e}` | 456 | e.g. no scheme in `base_url`, obsolete `api_key_file` |
| INFO | `[DRY] Would ship {n} events to {base_url} (run {run_id}, {m} snapshot rows, {k} incidents)` | 463 | dry run |
| INFO / WARNING | `[DRY] VPS ingest secret: present` / `[DRY] VPS ingest secret: MISSING — {reason}` | 469 / 471 | dry run; value never logged |
| INFO | `[DRY] VPS reachability: GET {base_url}/api/cts-alarms/healthz -> {HTTP code \| unreachable (…)}` | 472 | dry run; answers "can the CTS server reach api.digibuild.dk" |
| ERROR | `VPS: no ingest secret ({reason}) — run {run_id} queued, {n} batches waiting` | 382 | batch queued without a request |
| INFO | `VPS: resent queued run {run_id} (HTTP 200)` | 401 | |
| WARNING | `VPS: resend of queued run {run_id} failed (attempt {n}): {detail} — stopping` | 412 | no further request this run |
| WARNING | `VPS: time budget exhausted — resend stopped` | 394 | |
| WARNING | `VPS: run {run_id} queued without attempt (server unreachable this run)` | 421 | |
| INFO | `VPS: shipped run {run_id} — {n} events, {m} snapshot rows, {k} incidents (HTTP 200)` | 427 | the normal case |
| WARNING | `VPS: ship of run {run_id} failed: {detail} — queued` | 436 | `detail` for 401 ends `(check vps.ingest_secret and that this server's clock is within 300 s)` |
| ERROR | `VPS: run {run_id} rejected as malformed — moved to dead table ({detail})` / `VPS: queued run {run_id} rejected as malformed …` | 431 / 403 | other 4xx |
| INFO | `VPS: outbox has {q} queued, {d} dead batches ({path})` | 440 | when anything is waiting |
| ERROR | `VPS shipper failed (non-fatal): {e}` | `main.py:1027` | unexpected shipper fault; never changes the exit code |

## Older wording in early logs

Historical (v1.x and earlier). A parser for the archived logs must accept both forms.

The first two runs (2026-04-17 10:57 and 10:59) were made by an earlier code version and use messages that no longer exist in `main.py`: `alarm bot run started (…)`, `Loaded 1 exception directory entries from …\exceptions.txt`, `74 alarms after priority<=3 + exception filter` (no `removed` part), `First run detected — bootstrapping state with 74 existing alarms (no incidents will be created for these)`, `Bootstrap complete. Subsequent runs will treat new Vista IDs as NEW.`, `Run summary: created=0 updated=0 resolved=0 unchanged=74`. From 11:19 the same day the current wording appears. The priority threshold in the filter line was `3` until 2026-04-21 16:57 and `2` from 2026-04-22 onwards (current). A parser for the historical logs must accept both forms.

## Volumes

| Metric | Value |
|---|---:|
| Files | 163 daily files, 2026-04-17 … 2026-09-28; only 2026-04-18 and 2026-04-19 are missing (the bot was not scheduled until 2026-04-20/21) |
| Total size | 356 MiB (372 MB; 371,990,839 bytes, ≈2.29 million lines) |
| Per day | min 16.9 KB (first day), median 2.29 MB, max 4.13 MB |
| Runs | 45,612 (288/day = every 5 min since 2026-04-21) |
| Lines per run (2026-09-28) | ≈84: 16 fixed + one per kept alarm (68) + API/event lines |
| By level | INFO 2,287,695 · WARNING 457 · ERROR 6,567 |

## Recurring FAILED / WARNING messages in the v1 logs (as of 2026-09-28)

> **History, resolved by v2.0.0 ([ADR-0019](../adr/0019-mainmanager-v3-incident-api.md)):** the MainManager integration was effectively down from 2026-09-19 because EG removed the `/restapi/Incident/*` routes. The last successful `UPDATED incident` line is 2026-09-19 00:40:06, the last `CREATED incident` 2026-09-14 13:35:04, the last `RESOLVED incident` 2026-09-06. From 2026-09-19 02:55 every `GetIncident` (30 distinct incident IDs) and, from 2026-09-23, every `CreateIncident` (8 of 8) returns 404, while the token endpoint still answers 200. Details and interpretation: [mainmanager-api.md](mainmanager-api.md#history-the-v1-api-until-2026-09-19); follow-up in [TODO.md](../TODO.md).

| Message (normalised) | Count | Cause | Where documented |
|---|---:|---|---|
| `[ERROR] UpdateIncident FAILED for … : 404 Client Error: Not Found for url: …/GetIncident?IncidentID=N` | 4,401 | `GetIncident` returns 404 for incident N (since 2026-09-19 for **every** incident, so most likely an API/permission change rather than a deleted incident); the bot `continue`s **without** updating `last_state_sig`, so the same update is retried every 5 minutes for as long as the alarm stays in the list | [mainmanager-api.md](mainmanager-api.md#history-the-v1-api-until-2026-09-19), [arc42 §11](../arc42/11-risks-and-technical-debt.md) |
| `[ERROR] Resolution update FAILED for … : 404 … GetIncident?IncidentID=N` | 2,150 | same, for an alarm that has already left the list — retried **forever** (`IncidentID=34570` for `VISTA_SERVER#6A5D0D16` alone: 2,150 times, every run since it was resolved) | same |
| `[WARNING] No objects.csv mapping for '…' — using fallback MainID 14228` | 403 | `objects.csv` is empty | [ADR-0009](../adr/0009-objects-csv-mainid-mapping-with-fallback.md) |
| `[ERROR] CreateIncident FAILED … 404 … /CreateIncident` | 8 | the endpoint itself returns 404 (MainManager side) on every create attempt since 2026-09-23 14:40 (2026-09-23 ×2, 09-25 ×2 — the same alarm retried via the bootstrapped-alarm path — 09-27, 09-28 ×3); the 7 affected alarms have `incident_id: null` in `alarms_state.json` | [mainmanager-api.md](mainmanager-api.md#history-the-v1-api-until-2026-09-19) |
| `[ERROR] UpdateIncident FAILED … Read timed out (read timeout=30)` | 3 | `requests` timeout on token request (30 s) | |
| `… SSLEOFError` (2026-05-17), `RemoteDisconnected` (2026-08-24), `521 Server Error … /restapi/token` (2026-09-15), `522 Server Error` (2026-09-26), `401 Client Error: Unauthorized … /restapi/token` (2026-09-19 22:05) | 1 each | transient MainManager-side outages; the single 401 on the token endpoint was not repeated, so it was not a credential change | |

On 2026-09-28 alone there were 531 `FAILED` lines. The 12 most-retried incident IDs in the 404 loop (of 30 affected in total): 34570 (2,150), 36674 (947), 36696 (835), 36676 (494), 36701 (421), 36688 (389), 36683 (254), 36804 (225), 36703 (208), 36702 (178), 36693 (113), 36631 (51); the remaining 18 IDs account for 1–40 retries each. Side effect: because a failing update still needs a token, `API AUTH` runs on every one of those runs (8,258 token requests for 469 creates + 7,377 updates).

## What to keep when migrating logging into the new system (suggestion)

Not a decision — input for [TODO T-006/T-062](../TODO.md). Status 2026-09-29: points 1 and 2 are covered by the shipper (run summary with counters, `errors[]`, `exit_code`, `bot_version`; `incidents[]` with every alarm→ticket link), stored by the digibuild `cts-alarms` worker; the rest is still open.

Keep (as structured records, not free text):

1. **Run record** per execution: start time, duration, exit code, `parsed`, `kept`, `removed`, `created/updated/resolved/unchanged`, `csv_events`. Today this is 3–4 separate INFO lines; one row per run in SQL replaces them.
2. **Incident link events**: `CREATED incident {id} for {vista_id}` and `RESOLVED incident {id}` — currently the only durable `vista_id ↔ incident_id` history once state is pruned. Store `incident_id` on the alarm-instance table instead.
3. **API call outcome** per call: endpoint, HTTP status, latency, error text — enough to reproduce the 404/timeout analysis above without grepping. Never the request body with credentials (the current code never logs them; keep it that way).
4. **Every WARNING/ERROR** with the `vista_id` it concerns, plus a *first seen / last seen / count* de-duplication so a stuck 404 is one row with a counter, not 2,150 lines.
5. The **older-wording** runs from 2026-04-17 only matter for the bootstrap timestamp; they can be imported as two run records.

Drop or demote:

- The per-run **alarm status table** (≈80 % of volume): it is a snapshot of `alarms_state.json` × `alarm_snapshot.alr`; the CSV audit / SQL event table already holds the transitions. If a "current status at time T" view is needed, it can be reconstructed from the event table.
- `API AUTH: requesting token…` / `HTTP 200` / `token obtained OK` triples → one debug-level line or a counter.
- `API CREATE: response={…}` full dict → keep only `ID`, `Success`, `ErrorNumber`, `Message`.
- The `=====` separator and duplicated `Loaded … from …` lines.

Format suggestions: one JSON object per line (or direct DB inserts) with `ts` in UTC + offset, `level`, `run_id`, `vista_id`, `incident_id`, `msg`, `data`; daily rotation with a retention limit (e.g. 90 days on the CTS server, unlimited in SQL on the VPS). Transport CTS → VPS is decided: HTTPS POST + HMAC per run ([ADR-0020](../adr/0020-hmac-ingest-to-digibuild.md)).
