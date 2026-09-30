# Reference: VPS shipper (`shipper.py`)

The CTS-side half of [ADR-0016](../adr/0016-cts-server-vs-vps-responsibility-split.md) option A and
[ADR-0020](../adr/0020-hmac-ingest-to-digibuild.md): the bot stays on the CTS server and, at the end
of every run, POSTs one batch (run summary, the CSV audit events of that run, the full current alarm
list and the alarm→incident links) to the digibuild `cts-alarms` worker at `api.digibuild.dk`.
Standard library + `requests` only, so it runs on the 32-bit Python 3.13 already installed on the CTS
host.

Related: [config-reference.md](config-reference.md) · [csv-audit-format.md](csv-audit-format.md) ·
[state-files.md](state-files.md) · [log-format.md](log-format.md) · the receiving side (schema,
verifier, storage, pages) is documented in the digibuild repo, sub-project `cts-alarms`
([ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)).

## Behaviour in one paragraph

`main.run()` calls `shipper.ship_run(cfg, run_meta, events, alarms, state, log)` after the final
state save (also on the bootstrap run and on the snapshot-failure exit 2; **not** in `--parse-only`,
which returns before). Without a `"vps"` section in `config.json`, or with `"enabled": false`, that
call returns immediately and the bot behaves exactly as without a shipper; the call is wrapped in
`try/except` (`_ship()` in `main.run()`), so a shipper bug is logged as
`ERROR VPS shipper failed (non-fatal): …` and never changes the exit code. With a section, the
shipper first resends queued batches from the local outbox, then posts the current run, each request
freshly signed. Anything that is not a 2xx goes to the outbox and is retried on the following runs;
the receiver is idempotent (dedup by `run_id` and `(vista_id, ts, event)`), so re-sending is always
safe. `main.py` imports `shipper` optionally: if the file is absent, `shipper = None` and nothing is
shipped.

## Authentication — digibuild's machine-hop HMAC

The same contract digibuild uses between Vercel and its VPS worker (digibuild ADR-0022,
`apps/worker/lib/hmac.js`). No bearer key, no key file.

| Item | Value |
|---|---|
| Route | `POST https://api.digibuild.dk/internal/cts-alarms/v1/ingest` (`INGEST_PATH`, `shipper.py:59`) |
| Headers | `X-Timestamp` = unix seconds; `X-Nonce` = a fresh uuid4; `X-Signature` = lower-case hex HMAC-SHA256 |
| Signed bytes | `f"{X-Timestamp}.{X-Nonce}."` (UTF-8) followed by the **raw request body bytes** — the receiver hashes exactly what arrived, never a re-serialised object (`signed_headers()`, `shipper.py:143`) |
| Key | the shared secret as UTF-8 |
| Freshness | the receiver rejects `|now − X-Timestamp| > 300 s` and any nonce it has seen in the last 600 s |
| Per request | a new timestamp and nonce for **every** POST, resends included — a queued batch is re-signed when it is resent, never replayed |
| Secret source | env `CTS_ALARMS_INGEST_SECRET`, else `vps.ingest_secret` in the secrets file (`paths.secrets_file`, default `C:\priorityalarmsapi\secrets.json`) — the same file that holds the MainManager credentials ([ADR-0021](../adr/0021-secrets-in-secrets-json.md)); read with `utf-8-sig`, so a Notepad BOM is harmless |
| Receiver side | the same value as `CTS_ALARMS_INGEST_SECRET` in the worker's environment file on the VPS (never in git, never in Vercel) |

`tests/test_shipper.py::test_signature_matches_digibuild_golden_vector` pins one signature produced by
digibuild's own `sign()` (non-ASCII in body and secret on purpose): if it fails, the two sides no
longer hash the same bytes.

**Clock:** because of the 300-second window, the CTS server's clock must be NTP-synchronised
(`w32tm /query /status`). A drifting clock shows up as `HTTP 401 … (check vps.ingest_secret and that
this server's clock is within 300 s)` and the batches queue until it is fixed.

## The `"vps"` section of `config.json`

```json
"vps": {
    "base_url":            "https://api.digibuild.dk",
    "outbox_file":         "C:\\priorityalarmsapi\\outbox.sqlite",
    "timeout_seconds":     20,
    "max_resend_per_run":  20,
    "time_budget_seconds": 120,
    "enabled":             true
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `vps.base_url` | string | required | Origin; the shipper posts to `{base_url}/internal/cts-alarms/v1/ingest` and, in a dry run, probes `{base_url}/api/cts-alarms/healthz`. Must start with `http://` or `https://`; trailing `/` stripped. |
| `vps.outbox_file` | string | `<paths.working_folder>\outbox.sqlite` | sqlite file holding undelivered batches (see below). Created on first use. |
| `vps.timeout_seconds` | number | `20` | per-request timeout (the dry-run probe uses at most 10 s). |
| `vps.max_resend_per_run` | int | `20` | how many queued batches may be resent before the current one; `0` disables resending. |
| `vps.time_budget_seconds` | number | `120` | wall-clock cap for the whole shipping step; no new request is started after it. Keeps the task's 5-minute limit safe. |
| `vps.enabled` | bool | `true` | `false` turns the shipper into a no-op without removing the section. |
| ~~`vps.api_key_file`~~ | — | — | **Obsolete** (bearer key of the first design). Its presence is rejected: `WARNING VPS shipper disabled — bad "vps" config: vps.api_key_file is obsolete …`. |

A present-but-broken section (no scheme in `base_url`, obsolete `api_key_file`) logs
`WARNING VPS shipper disabled — bad "vps" config: …` and ships nothing. The secret is **not** part of
`config.json`; see the table above.

Worst-case requests per run: `max_resend_per_run + 1`, each bounded by `timeout_seconds`, all bounded
by `time_budget_seconds`.

## What is shipped (`IngestBatch`, `schema_version` 1)

| Part | Source | Notes |
|---|---|---|
| `run` | `main.run()` bookkeeping | `run_id` = ISO start timestamp of the run (with local UTC offset), `started_at`, `finished_at`, `bot_version` (`main.__version__`), `host` (`platform.node()`), `parsed_rows`, `kept`, `csv_events`, `created`, `updated`, `resolved`, `unchanged`, `errors` (the messages the bot logged at ERROR, max 100 + one "… more errors omitted"), `exit_code`. Counters not reached on an early exit are `null`. |
| `events` | return value of `run_csv_logging()` | exactly the rows appended to `csv/*.csv` in this run, same fields; `ts` is the run's CSV timestamp as ISO with offset; synthetic NORMAL/RESOLVED rows have `null` for `user`, `ack_flag`, `state1`, `state2`, `date1_epoch`, `date2_epoch`, `count`. |
| `snapshot` | all parsed alarms | every alarm in `$this.alr`, all priorities, exception list ignored; `(6,2)` system events skipped. |
| `incidents` | `alarms_state.json` after the run | `vista_id`, `incident_id`, `main_id`, `main_id_fallback_used`, `status`, `first_seen`, `last_update`, `resolved_at` (ISO strings with offset). |

All timestamps are server local time with the UTC offset attached via `datetime.astimezone()`, so DST
is unambiguous. The schema is validated on the receiving side (digibuild `cts-alarms`); a batch it
rejects as malformed comes back as a 4xx other than 401/403/404/429 and is dead-lettered (below).

**Privacy note:** the `user` field carries the Vista operator name as shown in the alarm list (e.g.
`GPST (Georgi ISS)`, the owner's own label) in events and snapshot rows. The receiver stores it and exposes only the
login token part to the web pages (digibuild decision, open for the owner — [TODO T-007](../TODO.md)).
MainManager credentials are never shipped.

## Outbox and failure behaviour

sqlite file with two tables:

```
outbox(id INTEGER PRIMARY KEY, created_iso TEXT, run_id TEXT UNIQUE, payload TEXT, attempts INTEGER, last_error TEXT)
dead  (id INTEGER PRIMARY KEY, created_iso TEXT, run_id TEXT, payload TEXT, attempts INTEGER, last_error TEXT, dead_iso TEXT)
```

Per run, in order:

0. **Secret.** If neither the environment nor the secrets file has it, the current batch is queued
   without a request (`last_error` = `not attempted: …`) and `WARNING VPS: no ingest secret (…) — run …
   queued, N batches waiting (install.cmd -IngestSecret)` is logged (an ERROR before v2.2.0). Nothing is lost
   while the secret is being set up; `status.cmd` reports it as a problem.
1. **Resend** up to `max_resend_per_run` queued batches, oldest first, each re-signed. 2xx → row
   deleted, `INFO VPS: resent queued run …`. Transient failure → `attempts` +1, `last_error` set,
   `WARNING … — stopping`, and **no further request this run** (server down → do not hammer; the
   current batch is queued straight away). Malformed (see 3) → moved to `dead`, loop continues.
2. **Post the current run.** 2xx → `INFO VPS: shipped run <run_id> — N events, M snapshot rows, K
   incidents (HTTP 200)`.
   **The answer's `names`** (v2.2.0, [ADR-0025](../adr/0025-alarm-names-from-digibuild.md)): after the posts,
   the `names` of the last accepted answer are written to `names.json` when their `version` changed
   (`save_names()`), and the bot uses them from its next run.
3. **Classification** of a non-2xx (`_post()`, `shipper.py:315`):
   - connection error, timeout, 5xx, 403, 429, 3xx → `WARNING VPS: ship of run … failed: <reason> —
     queued`, retried on later runs;
   - **401** → retried, with the hint `(check vps.ingest_secret and that this server's clock is within
     300 s)`;
   - **404** → retried: it means the route is not deployed (yet), not that the batch is bad;
   - any other 4xx (400, 413, 422 …) → the receiver rejected the batch as malformed → `ERROR VPS: run …
     rejected as malformed — moved to dead table (HTTP 422: <response text, truncated>)`; dead batches
     are never retried automatically.
4. If anything is queued or dead: `INFO VPS: outbox has Q queued, D dead batches (<path>)`.

The secret is scrubbed (`***`) from every logged error and outbox `last_error`. An unusable outbox
file raises out of `ship()` and surfaces as `ERROR VPS shipper failed (non-fatal): …`.

**The outbox is unbounded** ([arc42 §11 R-20](../arc42/11-risks-and-technical-debt.md#r-20-unbounded-outbox-growth)): each batch
carries the whole incident list and snapshot, so weeks of an unreachable receiver or a missing secret
grow the file by tens of MB per day. Capping it is [TODO T-106](../TODO.md).

Inspecting or draining the outbox by hand (`sqlite3` is in the standard library):

```
python -c "import sqlite3;c=sqlite3.connect(r'C:\priorityalarmsapi\outbox.sqlite');print(c.execute('select id,run_id,attempts,last_error from outbox').fetchall());print(c.execute('select id,run_id,last_error from dead').fetchall())"
```

To retry a dead batch after the receiver has been fixed, move the row back:
`INSERT INTO outbox(created_iso,run_id,payload,attempts,last_error) SELECT created_iso,run_id,payload,0,'requeued' FROM dead WHERE id=?; DELETE FROM dead WHERE id=?;`

## `--dry-run`

Nothing is sent and the outbox is not touched (not even created). The batch is built anyway (so
conversion errors show up) and three lines are logged:

```
[DRY] Would ship N events to https://api.digibuild.dk (run <run_id>, M snapshot rows, K incidents)
[DRY] VPS ingest secret: present                       (or: WARNING … MISSING — <reason>)
[DRY] VPS reachability: GET https://api.digibuild.dk/api/cts-alarms/healthz -> HTTP 200
```

The reachability line is one anonymous GET (`probe()`, `shipper.py:344`) and answers "can the CTS
server open outbound HTTPS to api.digibuild.dk" before anything is shipped:
`unreachable (ConnectionError)` = the building firewall or DNS; `HTTP 404` = the worker route is not
deployed yet; `HTTP 200` = ready. The secret's value is never logged.

## Installing on the CTS server

Prerequisite: the digibuild `cts-alarms` worker is live and `https://api.digibuild.dk/api/cts-alarms/healthz`
answers `200` ([TODO T-092](../TODO.md)). Since v2.2.0 the switch-on is [TODO T-115](../TODO.md):

1. The ingest secret is generated once on the VPS (32 characters 0-9 a-f, so it can be typed) into the
   worker's environment file as `CTS_ALARMS_INGEST_SECRET` (digibuild install kit) — and nowhere in git.
2. The `"vps"` block above is committed to `config.json` in cts-api.
3. On the CTS server the owner double-clicks `update.cmd`. The install sees the `vps` section, finds no
   `vps.ingest_secret` in `C:\cts-api\cts-alarms\secrets.json` and asks for it (twice, hidden; spaces and
   dashes ignored). Later changes: `install.cmd -IngestSecret`.
4. The install's dry run must show `[DRY] Would ship …`, `[DRY] VPS ingest secret: present` and
   `… healthz -> HTTP 200` (echoed after `test run clean`). Then the real run logs
   `VPS: shipped run … (HTTP 200)` and, with the first answer, `VPS: alarm names updated -- …`.
   `HTTP 401` → secret mismatch or clock; `ConnectionError` → the CTS firewall blocks outbound HTTPS
   to api.digibuild.dk — batches queue in the outbox meanwhile and are resent once the route works.
6. The scheduled task needs no change; its 5-minute limit is respected by the time budget.

Rolling back: delete the `"vps"` block (or set `"enabled": false`) — `shipper.py` may stay in place;
or delete `shipper.py` — `main.py` imports it as optional.

## Backfill: events that were never shipped (`backfill.py`)

Every event is also in the CSV audit trail ([csv-audit-format.md](csv-audit-format.md)), so the events of runs
whose batch never reached digibuild can be sent later. That happened once: from 2026-09-28 17:55:05 to
2026-09-30 13:42:10 the bot ran before the shipper was switched on, and alarms cleared in that window stayed
open on digibuild. `backfill.py` sends such a window. `main.py` does not import it; the scheduled run is not
affected.

```
python backfill.py --from "2026-09-28 17:30:00" --to "2026-09-30 13:42:10"          (report only)
python backfill.py --from "2026-09-28 17:30:00" --to "2026-09-30 13:42:10" --send   (send)
```

Both ends are included and are server local time without an offset (read with `datetime.astimezone()`,
no `zoneinfo`). `--config` works as for `main.py`: `csv\`, `logs\` and `secrets.json` are found next to
`config.json`.

**Without `--send`** it only reports, and writes no file (not even `__pycache__`) and makes no network call.
It prints counts, never an alarm text, a user name, the secret or a URL query: CSV files scanned; rows in
the window; duplicates dropped (the same `(vista_id, ts, event)` twice in one file); malformed rows; events to
send; distinct alarms and how many were RESOLVED in the window; runs (one CSV timestamp each); events by type
and by day; the batches it would send; and whether `--send` would find the `vps` section and the secret.
It also cross-checks the daily logs: each run should have a `CSV audit: logged N events` line within 120 s
of its CSV time, with N equal to its rows. It prints how many runs match, how many do not and how many
have no such line, the first five of each, log lines with no CSV rows, and every
`CSV audit logging failed` line in the window as a WARNING.

A row in the window that is not what `run_csv_logging()` writes (not 17 fields, a number column that is not
a number, an unknown event, a `status_label` that does not fit the row) is listed as
`MALFORMED <file>.csv:<line>: <reason>` and the run ends with `FAILED: … Nothing was sent.` (exit 1). A row
whose time cannot be read counts as in the window unless the rows around it place it outside.

**With `--send`** it posts the batches after the report:

| Item | Value |
|---|---|
| Route, signature, secret | the live shipper's: `load_shipper_config()`, `read_ingest_secret()`, `ingest_url`, `_post()` with `signed_headers()`; a fresh `X-Timestamp` and `X-Nonce` for every attempt. The request timeout is at least 60 s. |
| Body | `{"schema_version": 1, "source": "cts-alarm-backfill", "run": null, "events": [...], "snapshot": null, "incidents": []}` |
| Events | the 15 fields the live shipper sends, rebuilt as the live path builds them (`main.event_record()`, then `event_row()`): `ts` is the row's time as ISO 8601 with the local offset; synthetic NORMAL/RESOLVED rows (empty `ack_flag`) have `null` in `user` … `count`. |
| Batches | oldest first, at most 500 events. The rows of one run are never split (one run of more than 500 goes alone). Within a run the file order is kept, so a vanished alarm's NORMAL stays before its RESOLVED. |
| Answer | `200` with `events_received`, `events_inserted`, `events_duplicate` and `instances_rebuilt` (0 if absent). `names` is ignored: `names.json` is never written. |
| Writes | nothing: no outbox, no `names.json`, no state or CSV file. |

It prints one line per batch (`batch k/n: E events, I inserted, D already there, R alarms rebuilt`), the
totals, and ends with `OK: backfill sent — N events, I inserted, D already there, R alarms rebuilt` (exit 0).
digibuild drops events it already has, keyed `(vista_id, ts, event)`, so running it again is safe: the events
come back as already there.

| Last line | Meaning |
|---|---|
| `FAILED: the digibuild server does not accept backfill batches yet (HTTP 422). Nothing was changed. …` | Any 422 stops at once: the server predates backfill batches. Run it again after the digibuild update. |
| `FAILED: batch k/n was refused: HTTP 401 (…)` (or 403, 404, 429) | Not retried; as for the shipper (secret, clock, route). |
| `FAILED: batch k/n could not be sent: … 4 attempts.` | A network error or 5xx, retried three times (2, 5, 10 s). |
| `FAILED: batch k/n: the server received X of Y events. …` | An answer that does not fit the contract; the batch may be stored. |

After a failure, the batches before it are named as accepted; running it again sends them again as duplicates.

Two things the CSV cannot give back: a `;` in an alarm text was written as `,` (`csv_row()`), so it is sent
as `,`; and in the one hour when the clocks go back (late October) a CSV time is ambiguous and is read as
summer time.

## Tests

`python -m pytest -q tests` (`tests/test_shipper.py`, `tests/test_main_events.py`): batch shape,
no-op without config, obsolete `api_key_file` rejected, HMAC golden vector and a server-side verify of
every request (resends carry fresh nonces), missing secret queues without a request, env beats file,
BOM-tolerant secrets file, outbox/resend/dead-letter, 401/403/404/429/5xx retried, secret never in
log records or outbox rows, dry run probes healthz and touches no outbox, end-to-end `--dry-run` with
and without a `"vps"` section.

`tests/test_backfill.py` covers `backfill.py`: rows written by `run_csv_logging()` come back as exactly the
live events, both window ends are included, malformed rows fail with file and line, duplicates are dropped,
batches keep the cap and whole runs, NORMAL stays before RESOLVED, the log cross-check, a report that writes
nothing (not even bytecode) and calls nothing, and sending: every attempt freshly signed, 422 stops at once,
5xx retried, other 4xx stop, no outbox and no `names.json`.
