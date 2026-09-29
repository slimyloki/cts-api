# Reference: `config.json`, `secrets.json`, CLI flags and exit codes

All non-secret runtime settings of the bot live in `config.json`, loaded once per run by `load_config()`
(`main.py:55`). Credentials live in `secrets.json` or environment variables
([ADR-0021](../adr/0021-secrets-in-secrets-json.md)). Line references are to `main.py` v2.1.0.

Related: [state-files.md](state-files.md) · [mainmanager-api.md](mainmanager-api.md) · [shipper.md](shipper.md) ·
[log-format.md](log-format.md) · [arc42 §7 deployment view](../arc42/07-deployment-view.md) · [TODO.md](../TODO.md)

> **Secrets.** `config.json` contains **no** credential (and `tests/test_repo_hygiene.py` fails if one is added).
> The MainManager pair and the VPS ingest secret are in `C:\priorityalarmsapi\secrets.json` on the CTS server,
> which is git-ignored and ACL-restricted. This repo is **public**: never paste secret values into docs, issues,
> commits or chat.

## Where the files are

| | `config.json` | `secrets.json` |
|---|---|---|
| Default path | `config.json` **in the folder of `main.py`** since v2.1.1 (`DEFAULT_CONFIG_PATH`): on the CTS server `C:\cts-api\cts-alarms\config.json` (the bot runs in place, ADR-0024). Every relative path under `paths` is relative to that folder. Before v2.1.1 it was always `C:\priorityalarmsapi\config.json` with absolute paths. | `paths.secrets_file`, `"secrets.json"` in the committed config: a relative path is relative to the folder of `config.json` (v2.1.1). `DEFAULT_SECRETS_PATH` (`"secrets.json"`, also next to `config.json`) is used only when a config names none. |
| Override | `--config <path>` | the `paths.secrets_file` key, or environment variables (below) |
| In the repo | yes (the deployed copy; no secrets) | **no** — `secrets.example.json` shows the shape |
| Encoding | UTF-8, a BOM is tolerated (`utf-8-sig`) | same |

As launched by Task Scheduler there is no `--config` argument and the working directory is
`C:\priorityalarmsapi` (`TACVista_Alarm_Bot.xml`), so the default path is used.

Loading `config.json` fails (unreadable, invalid JSON) → `Config load failed (<path>): <error>` on stderr,
exit code 2, **no log file written** (logging is set up after the config is loaded, `main.py:1365`).

## `secrets.json` and environment variables

```json
{
  "mainmanager": {"username": "", "password": ""},
  "vps":         {"ingest_secret": ""}
}
```

| Secret | Environment override (wins) | File key | Used by |
|---|---|---|---|
| MainManager username / password | `MM_USERNAME` + `MM_PASSWORD` (both needed) | `mainmanager.username` / `.password` | `load_secrets()` `main.py:61` → token request |
| VPS ingest secret (HMAC key) | `CTS_ALARMS_INGEST_SECRET` | `vps.ingest_secret` | `shipper.read_ingest_secret()`; only when `config.json` has a `"vps"` section |

Precedence for MainManager: environment → `secrets.json` → a legacy `mainmanager.username`/`.password` pair
in `config.json` (still accepted for one deploy, logs `Credentials: from config.json — DEPRECATED …`) →
none (`WARNING No MainManager credentials configured`; the API is treated as unusable that run, alarms are
still tracked). The log says where the credentials came from: `Credentials: from environment …` /
`Credentials: from C:\priorityalarmsapi\secrets.json`.

Create the file BOM-free and restrict it on the CTS server — see [arc42 §7](../arc42/07-deployment-view.md#secretsjson-on-the-cts-server).

## `config.json` keys

Types are as used by the code; "Current" is the committed value. Windows paths are JSON-escaped (`\\`) in
the file.

### `thresholds`

| Key | Type | Current | Effect | Read at |
|---|---|---|---|---|
| `thresholds.max_priority_number` | int | `2` | Alarms with `priority > this` are excluded from the incident pipeline (**not** from the CSV audit or the shipped snapshot). `2` = only priority 1 and 2 create incidents. | `main.py:1060`, echoed in `… alarms after priority<=2 …` |
| `thresholds._comment` | string | "Trigger incidents for Vista priority 1, 2, and 3. …" | ignored; **stale** (describes the former value 3) — [TODO T-083](../TODO.md) | – |

History: 3 from 2026-04-17 until 2026-04-21 16:57, then 2 ([ADR-0007](../adr/0007-priority-threshold-filter.md)).

### `paths`

| Key | Type | Current | Effect | Read at |
|---|---|---|---|---|
| `paths.vista_alarm_file` | string | `C:\ProgramData\Schneider Electric\TAC Vista 5.1.9\DB\$thisdb\$this.alr` | source alarm list, copied every run ([alr-file-format.md](alr-file-format.md)) | `main.py:1035` |
| `paths.objects_csv` | string | `C:\priorityalarmsapi\objects.csv` | `alarm_object → MainID` mapping; missing file → WARNING, empty mapping | `main.py:990` |
| `paths.exceptions_csv` | string | `C:\priorityalarmsapi\exceptions.csv` | directories to ignore in the incident pipeline | `main.py:991` |
| `paths.working_folder` | string | `C:\priorityalarmsapi` | created if missing; holds `alarm_snapshot.alr`, `mm_token.json` and (default) `outbox.sqlite` | `main.py:1031`, `:1108` |
| `paths.state_file` | string | `C:\priorityalarmsapi\alarms_state.json` | incident-pipeline state ([state-files.md](state-files.md#alarms_statejson)); saved via `_save()` (never in `--dry-run`/`--parse-only`) | `main.py:992`, `:1000` |
| `paths.csv_folder` | string, optional | `C:\priorityalarmsapi\csv` | per-directory CSV audit files. If this **or** `csv_state_file` is missing/empty the CSV audit step is skipped silently (and no events are shipped) | `main.py:1047` |
| `paths.csv_state_file` | string, optional | `C:\priorityalarmsapi\csv_state.json` | CSV-audit state ([state-files.md](state-files.md#csv_statejson)) | `main.py:1048` |
| `paths.log_folder` | string | `C:\priorityalarmsapi\logs` | daily log files; created if missing | `main.py:1365` → `setup_logging()` |
| `paths.secrets_file` | string, optional | `secrets.json` (relative: next to `config.json`, v2.1.1) | where the credentials and the ingest secret are read from; absolute paths are used as they are | `load_secrets()`/`resolve_path()`, `shipper.load_shipper_config()` |
| `paths.names_file` | string, optional | `names.json` (relative: next to `config.json`, v2.2.0) | the alarm names from digibuild: written by the shipper from the ingest answer, read by the bot at the start of each run ([ADR-0025](../adr/0025-alarm-names-from-digibuild.md)) | `load_names()`, `shipper.save_names()` |

### `mainmanager`

| Key | Type | Current | Effect | Read at |
|---|---|---|---|---|
| `mainmanager.base_url` | string | `https://rambollfm.mainmanager.dk` | prefix for the token and v3 incident endpoints; trailing `/` stripped | `main.py:651` |
| `mainmanager.default_main_id` | int | `14228` | `MainID` for any alarm whose `alarm_object` is not in `objects.csv` — i.e. every alarm today | `main.py:1088`, `:1175`, `:1222` |
| `mainmanager.incident_defaults.IncidentTypeID` | int | `277` | "CTS API"; sent as `IncidentTypeID` (and as the dropped legacy `CheckwordItemID`). Falls back to a `CheckwordItemID` key if absent. | `create_incident()` `main.py:756` |
| `mainmanager.incident_defaults.GradeID` | int | `10` | required | same |
| `mainmanager.incident_defaults.StatusID` | int | `5` | "Awaits handling"; required; set and **enforced** on create, never changed afterwards ([ADR-0010](../adr/0010-prepend-description-never-change-status.md)) | same |
| `mainmanager.incident_defaults.LocationID` | int, optional | `6` | building; if absent: WARNING `incident_defaults.LocationID not set — MainManager will derive it` | `main.py:661`, create |
| `mainmanager.incident_defaults.ReportedByID` | int, optional | `748` | "TacVista Automated Services"; same warning if absent | same |
| `mainmanager.incident_defaults.ReportedByOrganisationID` | int, optional | `11` | Rambøll Danmark A/S; same warning if absent | same |
| ~~`mainmanager.username` / `.password`~~ | — | *(absent)* | legacy location, DEPRECATED — move to `secrets.json` | `load_secrets()` |
| ~~`IncidentMode`, `CheckwordID`~~ | — | *(absent)* | v1 only; ignored | – |

What the IDs mean inside MainManager: [mainmanager-api.md](mainmanager-api.md#incident_defaults).

### Top-level

| Key | Type | Current | Effect | Read at |
|---|---|---|---|---|
| `log_encoding` | string, optional | `"iso-8859-1"` | encoding used to read the `.alr` snapshot (not the bot's own logs, which are UTF-8) | `main.py:1042` |
| `prune_resolved_after_days` | int, optional | `30` | RESOLVED entries in `alarms_state.json` older than this are deleted; `0`/negative disables | `main.py:1332` → `prune_state()` |
| `vps` | object, optional | *(absent until [TODO T-092](../TODO.md))* | enables the shipper: `base_url` (`https://api.digibuild.dk`), `outbox_file`, `timeout_seconds`, `max_resend_per_run`, `time_budget_seconds`, `enabled`. Absent or `enabled: false` = no-op. `api_key_file` is **obsolete** and rejected. The secret is not here. See [shipper.md](shipper.md). | `shipper.load_shipper_config()` |

### Missing-key behaviour

- `paths`, `thresholds`, `mainmanager` and their required keys are accessed with `[]` → `KeyError` →
  `[ERROR] Unhandled exception` + traceback, exit 1.
- `paths.log_folder` is read *before* the `try` (`main.py:1365`): a missing key there is a raw traceback on
  stderr, exit 1, no log line.
- `mainmanager.base_url` and `incident_defaults` are only read when `MMClient` is first created, so runs that
  make no API call never fail on them. `mainmanager.default_main_id` is read for every bootstrapped and every
  new alarm (also in `--dry-run`).
- A broken `vps` section never stops the bot: `WARNING VPS shipper disabled — bad "vps" config: …`.

## Other input files

| File | Format | Current content | Loader |
|---|---|---|---|
| `objects.csv` | `<alarm_object>,<main_id>` (or `;`), no header, `#` comments, UTF-8 with optional BOM | **comments only — 0 mappings** ([TODO T-020](../TODO.md)) | `load_objects_csv()` `main.py:257` |
| `exceptions.csv` | `<directory>[,<reason>]`, `#` comments | 1 entry: `VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-0201_01-340_060X.Manuel_A` (no reason recorded; the header still calls the file `exceptions.txt`) | `load_exceptions_csv()` `main.py:283` |

Both are read fresh on every run, so edits take effect within 5 minutes.

## CLI (`main()`, `main.py:1351`)

```
python main.py [--config PATH] [--dry-run] [--parse-only] [--no-bootstrap]
```

| Flag | Effect |
|---|---|
| `--config PATH` | use another config file |
| `--dry-run` | **read-only** (v2.0.1+): snapshot, parse and diff run; the CSV events are computed but not written; **no** state save, **no** CSV, **no** outbox. One MainManager call: the token (`[DRY] MainManager credentials OK` / `… FAILED`). Logs `[DRY] Would create/update/mark …`, `[DRY] Would ship …` (+ secret/reachability lines with a `"vps"` section), and ends with `[DRY] nothing written (state, CSV and MainManager untouched)`. A cached `mm_token.json` skips the password check ([TODO T-108](../TODO.md)). |
| `--parse-only` | stop after the status table; writes nothing (no state, no CSV), no API, no shipping. |
| `--no-bootstrap` | skip the first-run bootstrap; every current alarm is treated as NEW and gets an incident. Only meaningful when `meta.bootstrapped` is false or the state file is absent. |

The scheduled task passes no flags. `--parse-only` wins over the others since it returns first.

## Exit codes

| Code | When |
|---|---|
| `0` | normal run (also when the MainManager API was unusable — see `deferred=` and the ERROR lines), bootstrap run, `--parse-only` |
| `1` | uncaught exception inside `run()` (logged as `Unhandled exception` with traceback) |
| `2` | `config.json` could not be loaded, or the `.alr` snapshot failed after 5 attempts (`Could not snapshot alarm file`) |

Task Scheduler restarts a failed task up to 3 times at 1-minute intervals and has a 5-minute execution
limit; a run normally takes 1–3 s, longer when MainManager is slow (30 s token timeout, 60 s per incident call)
or the shipper resends a backlog (capped by `vps.time_budget_seconds`, default 120 s).

## Scheduler facts that behave like configuration

From `TACVista_Alarm_Bot.xml` (not read by the code, but part of the effective configuration):

| Setting | Value |
|---|---|
| Task | `\TACVistaLogs\TACVista_Alarm_Bot` |
| Interval | every 5 min (`PT5M`), 24/7 |
| Runs as | user `GPST`, least privilege, password logon |
| Interpreter | `C:\Users\GPST\AppData\Local\Programs\Python\Python313-32\python.exe` (32-bit Python 3.13) |
| Arguments / working dir | `"C:\priorityalarmsapi\main.py"` / `C:\priorityalarmsapi` |
| Multiple instances | `IgnoreNew` |
| Execution limit / restart | 5 min / 3 × 1 min |
