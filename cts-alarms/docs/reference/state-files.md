# Reference: state files (`alarms_state.json`, `csv_state.json`, `mm_token.json`, `outbox.sqlite`)

Line references are to `main.py` v2.1.0. All four files live on the CTS server in the working folder
(`C:\priorityalarmsapi`) and are **not** in git (runtime data stays on the CTS server,
[ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)); the counts and examples below come from the
copy of 2026-09-28 that was archived privately on the VPS for the historical import.

Two JSON files carry the bot's memory between runs. Both are keyed by `vista_id`, both store the alarm's last `(state1, state2, ack_flag, user)` signature, and both are written atomically. They serve two independent pipelines and are never cross-referenced by the code.

| File | Owner | Scope | Purpose |
|---|---|---|---|
| `alarms_state.json` | incident pipeline (`run()`, `main.py:983-1344`) | alarms after priority + exception filter | remember which MainManager incident belongs to which alarm, and detect transitions/resolution |
| `csv_state.json` | CSV audit (`run_csv_logging()`, `main.py:515`) | all alarms except `(6,2)` system events | detect transitions/resolution for the CSV audit |
| `mm_token.json` | `MMClient._get_token()` (`main.py:665`) | – | cached MainManager bearer token + expiry ([below](#mm_tokenjson)) |
| `outbox.sqlite` | `shipper.py` | – | batches not yet accepted by the digibuild worker ([below](#outboxsqlite)) |

Related: [alr-file-format.md](alr-file-format.md) · [csv-audit-format.md](csv-audit-format.md) · [config-reference.md](config-reference.md) · [ADR-0012 JSON state files, atomic writes](../adr/0012-json-state-files-atomic-writes.md) · [ADR-0006 bootstrap on first run](../adr/0006-bootstrap-on-first-run.md) · [ADR-0004 state-signature diff](../adr/0004-state-signature-diff.md)

## Common mechanics

| Aspect | Behaviour | Code |
|---|---|---|
| Path | `config.json -> paths.state_file` = `C:\priorityalarmsapi\alarms_state.json`; `paths.csv_state_file` = `C:\priorityalarmsapi\csv_state.json` | `main.py:992`, `main.py:1048` |
| Format | JSON, UTF-8, `indent=2`, `ensure_ascii=False` (Danish text stored readably) | `save_state()` `main.py:324`, `save_csv_state()` `main.py:486` |
| Atomic write | write to `<path>.tmp`, then `os.replace(tmp, path)` — a crash mid-write leaves the old file intact; a stray `.tmp` may remain | same |
| Missing file | treated as empty state; **no error** | `load_state()` `main.py:312`, `load_csv_state()` `main.py:478` |
| Corrupt JSON | `alarms_state.json`: `json.load` raises → `Unhandled exception`, exit 1, no changes. `csv_state.json`: caught in `run()`, logged as `ERROR CSV audit logging failed (non-fatal): …`, the CSV step is skipped for that run (no events shipped) and the incident pipeline continues with exit 0 | `main.py:992`; `main.py:1049-1057` |
| Read-only runs | `--dry-run` and `--parse-only` save **neither** file (v2.0.1+: `read_only`, `_save()`, `run_csv_logging(write=False)`) | `main.py:996-1000` |
| Concurrency | not handled in code: no lock file. Task Scheduler `MultipleInstancesPolicy=IgnoreNew` prevents overlapping *scheduled* runs, but the task has `AllowStartOnDemand=true` and `python main.py` can be run from a console, so a manual run can overlap a scheduled one (both write `<path>.tmp` then `os.replace`) | `TACVista_Alarm_Bot.xml` |
| In git | no — git-ignored since 2026-09-29 ([ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md); they were committed 2026-09-28 … 29 as a migration source, ADR-0014) | `.gitignore` |

## `alarms_state.json`

### Top-level schema

```jsonc
{
  "meta": {
    "bootstrapped": true,                // bool — first-run bootstrap done
    "last_run_iso": "2026-09-28T17:55:12" // ISO-8601 local time, seconds; null before first run
  },
  "alarms": {
    "VISTA_SERVER#6A9A5C9F": { /* entry, see below */ },
    "...": {}
  }
}
```

`empty_state()` (`main.py:308`) defines the shape. `load_state()` also accepts a legacy file that is a bare `{vista_id: entry}` map and wraps it into `{"meta": …, "alarms": …}` — a compatibility shim for the pre-`meta` format described in the build reference.

### Entry schema (`make_state_entry()`, `main.py:919`, plus fields added by `run()`)

| Field | Type | Set at | Updated at | Meaning |
|---|---|---|---|---|
| `incident_id` | int or `null` | creation | when a bootstrapped alarm gets its incident later ("THE FIX", `main.py:1214`) | MainManager incident id. `null` = bootstrapped, or `CreateIncident` failed; in both cases an incident is created on the alarm's next signature change. (A dry run no longer records anything.) |
| `main_id` | int | creation | never | MainManager `MainID` used/chosen for this alarm (`objects.csv` mapping or `default_main_id`). Frozen at first sight even if `config.json` changes later — hence 3 entries still carry `9756` while `default_main_id` is now `14228`. |
| `main_id_fallback_used` | bool | creation | never | `true` when `default_main_id` was used (currently all 122 entries, because `objects.csv` is empty). |
| `alarm_object` | string | creation | never | `.alr` field 2 |
| `directory` | string | creation | never | `.alr` field 22 |
| `priority` | int | creation | never | `.alr` field 7 |
| `initial_alarm_text` | string | creation | never | `.alr` field 13 at first sight |
| `first_seen_iso` | ISO local time | creation | never | when the bot first saw the alarm (bootstrap time for bootstrapped entries, e.g. `2026-04-17T10:57:58`) |
| `last_state_sig` | `[state1, state2, ack_flag, user]` | creation | every applied transition | the diff key; list form of `Alarm.state_signature()` |
| `last_update_iso` | ISO local time | creation | every applied transition | |
| `status` | string | creation | every applied transition, resolution | see status values |
| `resolved_iso` | ISO local time | resolution only | – | present only on RESOLVED entries (50 of 122 on 2026-09-28); drives pruning |
| `update_failures` | int, optional (v2.0.0+) | first non-404 failure of an update/resolution | +1 per failed run; reset to 0 on success or after the 3rd failure (`_give_up()`, `main.py:1147`) | counts consecutive failures of the **current** transition; at `MAX_API_FAILURES` = 3 the transition is abandoned with an ERROR and the state advances |
| `incident_missing` | bool, optional (v2.0.0+) | a 404 on this ticket while the v3 list endpoint answers (`_on_not_found()`, `main.py:1128`) | never cleared | the ticket no longer exists in MainManager; no further calls for it; transitions and the resolution are still recorded locally (log: `… missing in MainManager — not sent`) |
| `incident_missing_iso` | ISO local time, optional | with `incident_missing` | never | when the ticket was found missing |

Example (RESOLVED, from the committed file):

```json
"VISTA_SERVER#6A5D0E0F": {
  "incident_id": 34571,
  "main_id": 14228,
  "main_id_fallback_used": true,
  "alarm_object": "320-01-07931-0101TT_AH",
  "directory": "VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-0208_01-32001_07931.0101TT_AH",
  "priority": 2,
  "initial_alarm_text": "Høj Temperatur",
  "first_seen_iso": "2026-07-19T19:55:04",
  "last_state_sig": [0, 0, 1, "<INITIALS> (<operator name> (ISS))"],
  "last_update_iso": "2026-08-20T08:00:13",
  "status": "RESOLVED",
  "resolved_iso": "2026-09-06T03:10:14"
}
```

### `status` values

Produced by `classify_alarm_status()` (`main.py:156`) plus the bot's own `RESOLVED`. Only `RESOLVED` has behavioural meaning (reappearance check and resolution loop in `run()`, `prune_state()`); the rest are informational and the actual diff is on `last_state_sig`.

| Value | Meaning | Entries (2026-09-28) |
|---|---|---:|
| `ACTIVE` | condition present, unacknowledged | 2 |
| `ACTIVE + ACKNOWLEDGED` | condition present, acknowledged | 5 |
| `NORMAL` | cleared, unacknowledged | 63 |
| `NORMAL + ACKNOWLEDGED` | cleared and acknowledged (transient) | 0 |
| `RESOLVED` | row gone from `$this.alr`; pruned after `prune_resolved_after_days` | 50 |
| `ACKNOWLEDGED` (**legacy**) | written by the earlier code version whose status set was `OPEN / CLEARED / ACKNOWLEDGED / RESOLVED / BOOTSTRAPPED` (build reference §6). 2 entries from the 2026-04-17 bootstrap still carry it (`VISTA_SERVER#684AB94B`, `VISTA_SERVER#687894B1`; both `incident_id: null`, `main_id: 9756`). Harmless: the value is only compared against `RESOLVED`; it will be overwritten on the next signature change. | 2 |

Not observed in the file: `OPEN`, `CLEARED`, `BOOTSTRAPPED` (also legacy names from the build reference).

### Lifecycle of an entry

```mermaid
flowchart LR
    A[not in state] -->|bootstrap run| B["entry, incident_id = null"]
    A -->|later run: new vista_id| C["CreateIncident → entry with incident_id"]
    A -->|CreateIncident fails (non-404)| B
    A -->|API down: create 404| A
    B -->|signature changes| C
    C -->|signature changes| C2["prepend lines to incident, update sig/status"]
    C2 -->|ticket 404, list OK| M["incident_missing = true (local tracking only)"]
    M -->|row gone| D
    C2 -->|row gone from .alr| D["status = RESOLVED, resolved_iso set"]
    B -->|row gone| D
    D -->|resolved_iso older than prune_resolved_after_days| E[deleted]
    D -.->|same vista_id reappears| W[WARNING reappeared — ignored]
```

### Pruning rules (`prune_state()`, `main.py:332`)

- Runs at the end of every successful run (`main.py:1332`; in a read-only run the pruned state is not saved) with `days = config.prune_resolved_after_days` (30; `<= 0` disables).
- Deletes entries where `status == "RESOLVED"` **and** `resolved_iso` parses **and** is older than `now - days`. Entries without `resolved_iso` are never pruned.
- Logs `INFO Pruned N resolved entries older than 30 days` when N > 0 (36 such lines in `logs/`, largest N = 64).
- Non-resolved entries are never pruned, so alarms that stay in `$this.alr` for months (the oldest by `date1` is from 2024-09) stay in the file indefinitely.
- Because pruning deletes the only record of `vista_id → incident_id` on the CTS server, the full history lives in `logs/` (`CREATED incident … for …` lines) and — from the historical import on — in the digibuild `cts-alarms` database, which the shipper keeps current (`incidents[]` in every batch).

### Bootstrap flag (`meta.bootstrapped`)

- `false` (or file absent) → the run records every filtered alarm with `incident_id: null`, sets `bootstrapped: true`, saves, ships the run (if configured) and exits with 0 **without** calling MainManager (`main.py:1080-1098`). A dry run logs `[DRY] BOOTSTRAP not saved (dry-run).` instead. `--no-bootstrap` skips this and treats everything as NEW.
- Deleting `alarms_state.json` therefore re-bootstraps; it does *not* recreate incidents for alarms already in the list, but it also forgets all `incident_id`s, so later transitions on those alarms would open **new** incidents.
- The live file has `bootstrapped: true` since the first run on 2026-04-17 10:57.

### Save points

`_save()` (`main.py:998`) is called after every created entry, every applied transition, every counted failure, the bootstrap, every resolution and once at the end. A crash therefore loses at most the in-flight alarm. In `--dry-run` and `--parse-only` `_save()` does nothing (v2.0.1+). v2.0.0 and earlier **did** save in a dry run — which on the live folder would have advanced the state past the backlog stuck since 2026-09-19.

### Counts in the archived copy (2026-09-28)

122 entries: 113 with an `incident_id`, 9 with `null`; priority 2: 99, priority 1: 23; `main_id` 14228: 119, 9756: 3.

## `csv_state.json`

Flat map `{vista_id: entry}`, no `meta`. Written by `run_csv_logging()` at the end of every normal run (`main.py:607`; not with `write=False`).

### Entry schema (`main.py:557-563`)

| Field | Type | Meaning |
|---|---|---|
| `sig` | `[state1, state2, ack_flag, user]` | last observed signature (diff key) |
| `directory` | string | used to find the CSV file when the alarm disappears |
| `alarm_object` | string | copied into RESOLVED rows |
| `initial_alarm_text` | string | despite the name, **overwritten on every signature change** (`main.py:560`), so it is the text at the last change |
| `priority` | int | copied into RESOLVED rows |
| `vista_id` | string | duplicate of the key, copied into RESOLVED rows |
| `resolved` | bool | set to `true` when the alarm disappears (`main.py:598`) — never persisted, see pruning |

Example:

```json
"VISTA_SERVER#67969A95": {
  "sig": [0, 0, 1, "SYSTEM (User Profile SYSTEM)"],
  "directory": "VISTA_SERVER-LOYTEC_PORT-RHQ-34551_02_ET7_XENTA-ZM_OMR5_ET6_7-APP.32090_0721ForcLuk_A",
  "alarm_object": "345-51-Zonemaster_7_SAL-32090_0721ForcLuk_A",
  "initial_alarm_text": "Konvektor ventiler forceret lukket af bruger",
  "priority": 3,
  "vista_id": "VISTA_SERVER#67969A95"
}
```

### Pruning rules

Entries flagged `resolved` are deleted **in the same run** (`main.py:600-604`; the comment now says "Drop resolved entries right away" — it used to claim "older than 7 days"). Net effect: `csv_state.json` always mirrors the current `$this.alr` minus `(6,2)` rows (180 entries ↔ 180 snapshot rows on 2026-09-28; 0 entries flagged `resolved`).

Consequence: if `$this.alr` were briefly empty or unreadable-but-copyable (e.g. Vista restart writing an empty file), every alarm would be logged RESOLVED and then FIRST_SEEN again. Not observed so far, but worth a guard in the new system ([arc42 §11](../arc42/11-risks-and-technical-debt.md)).

### No bootstrap

The CSV pipeline has no bootstrap: on its first run (2026-04-20/21) every existing alarm produced a `FIRST_SEEN` row.

## Differences between the two files at a glance

| | `alarms_state.json` | `csv_state.json` |
|---|---|---|
| Alarms covered | priority ≤ 2, not in exceptions | all, minus `(6,2)` |
| Has `meta` / bootstrap | yes | no |
| Keeps resolved entries | 30 days | 0 days |
| Stores incident link | yes | no |
| Saved | after each change + end of run | once at end of `run_csv_logging()` |
| Entries 2026-09-28 | 122 | 180 |

## `mm_token.json`

Written by `MMClient._get_token()` (`main.py:665`) into the working folder on every successful token request:

```json
{"access_token": "<bearer token>", "exp": 1790000000.0}
```

- `exp` = unix time of expiry (request time + `expires_in`, default 7200 s); the cache is used until 300 s before
  it (`TOKEN_MARGIN_S`), then a new token is requested and the file rewritten.
- It holds a **live credential** for up to two hours: git-ignored (`mm_token*.json`), and it should carry the same
  ACL as `secrets.json`.
- A dry run also writes it (the credential check is a real token request). A second dry run within the token's
  lifetime logs `API AUTH: using cached token` and does **not** re-check the password — delete the file before a
  dry run that must prove a rotated password ([TODO T-108](../TODO.md)).
- Unreadable/corrupt file → ignored, a new token is requested; unwritable → `WARNING API AUTH: token cache not writable`.

## `mm_auth_failed.json`

Written in the working folder (`paths.working_folder`, `C:\priorityalarmsapi`) since v2.1.1, only when MainManager
**rejects** the login with HTTP 400, 401 or 403. A network error or HTTP 5xx never writes it. It holds no secret:

| Key | Meaning |
|---|---|
| `at` | Unix time of the rejection |
| `error` | The error text, at most 200 characters, e.g. `400 Client Error: Bad Request for url: …/restapi/token` |
| `source_kind`, `source_path`, `source_mtime` | Where the rejected credentials came from (`file` / `config` / `env`) and that file's modification time |

While it is younger than 30 minutes (`AUTH_BACKOFF_S`) **and** the credential file's modification time is
unchanged, every real run skips the login. It logs `MainManager API unusable this run: MainManager login skipped: …`,
sends nothing and leaves the state untouched (`deferred=N`). A changed `secrets.json` is tried on the very next
run. A successful login deletes the file (`MainManager login OK again — backoff cleared`). `install.ps1` deletes it
before its dry run. Git-ignored.

Why: before v2.1.1 a wrong password made every pending transition try its own login, and after three runs abandon
the transition. That was many failed logins every 5 minutes on an account the Indeklima bot shares.

## `outbox.sqlite`

The shipper's queue of batches the digibuild worker has not accepted yet (`vps.outbox_file`, default
`<working_folder>\outbox.sqlite`); created on the first non-dry run with a `"vps"` section. Tables `outbox`
(queued, retried oldest-first each run) and `dead` (rejected as malformed, never retried automatically). Schema,
classification and hand-inspection commands: [shipper.md](shipper.md#outbox-and-failure-behaviour). It is
unbounded ([TODO T-106](../TODO.md)) and git-ignored. Deleting it loses only batches that were never delivered —
the CSV audit and `alarms_state.json` on the CTS server remain the source of truth.
