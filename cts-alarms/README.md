# cts-alarms — TAC Vista alarm bot (CTS server)

The alarm bot that runs on the CTS server (Windows Server 2016, TAC Vista 5.1.9). Every 5 minutes it reads
Vista's live alarm list and appends every change to a CSV audit trail. For priority 1–2 alarms it creates and
updates incidents in MainManager (Ramboll FM, v3 API). If configured, it also posts each run to the digibuild
sub-project `cts-alarms`, signed with HMAC.

This folder of the [cts-api](../README.md) repo holds only what runs on the CTS server, plus its tests and docs. It was
imported on 2026-09-29 from `slimyloki/cts-alarms` at commit `907d91f`, without that repo's history
([ADR-0023](docs/adr/0023-moved-into-cts-api.md)).

## Files

| File | What it is |
|---|---|
| `main.py` | The bot (v2.2.0). Run by Task Scheduler every 5 minutes. |
| `shipper.py` | Optional: posts each run to `api.digibuild.dk`; SQLite outbox on failure. Off without a `"vps"` section in `config.json`. |
| `backfill.py` | One-off, run by hand: sends the CSV audit events of a past time window that the shipper never sent (report first, `--send` to send). Not part of the scheduled run. See [shipper.md](docs/reference/shipper.md#backfill-events-that-were-never-shipped-backfillpy). |
| `config.json` | Paths, thresholds, MainManager URL and incident defaults. **No credentials.** |
| `secrets.example.json` | Template for `secrets.json`, which lives only on the CTS server. |
| `objects.csv` | Maps an alarm object to a MainManager MainID (empty today, so the fallback MainID is used). |
| `exceptions.csv` | Vista directories that never create incidents. |
| `TACVista_Alarm_Bot.xml` | Old export of the scheduled task. Outdated: on the server the task is `\TacVistaMails\Alarm_Bot` and runs `C:\cts-api\cts-alarms\main.py` ([TODO T-114](docs/TODO.md)). |
| `install.cmd`, `update.cmd`, `status.cmd` (+ `.ps1`) | Double-click on the CTS server: install (moves old data once, `secrets.json`, dry-run gate, enable), update (pull + gate + way back), health check. See [INSTALL.md](INSTALL.md). |
| `tests/` | Unit tests; no network, no CTS server needed. |
| `docs/` | arc42, ADRs, C4, reference docs and the backlog. Start at [docs/README.md](docs/README.md). |
| `Alarm_bot_build_reference.md` | The original design document; partly outdated, kept for history. |

Runtime data (`logs/`, `csv/`, `alarms_state.json`, `csv_state.json`, `alarm_snapshot.alr`, `mm_token.json`,
`mm_auth_failed.json`, `outbox.sqlite`) and `secrets.json` are written next to `main.py` on the CTS server and are
git-ignored ([ADR-0024](docs/adr/0024-run-in-place-from-cts-api.md)). The data as of 2026-09-28 is archived privately on the VPS.

## Tests

```
python -m unittest                 # tests/test_main.py and tests/test_repo_hygiene.py
python -m pytest -q tests          # everything, including the shipper and event tests
```

The bot itself needs only Python 3.13 and `requests`. The pytest-style files (`test_shipper.py`,
`test_main_events.py`, `test_backfill.py`) also need `pytest`, which a developer machine needs but the CTS
server does not.

## Deploy

On the CTS server the repo is `C:\cts-api` and the bot runs in place from `C:\cts-api\cts-alarms`
([ADR-0024](docs/adr/0024-run-in-place-from-cts-api.md)). Double-click `install.cmd` there once (it also moves the
data out of the old `C:\priorityalarmsapi`), `update.cmd` for new versions, `status.cmd` to check; see
[INSTALL.md](INSTALL.md). Turning the shipper on is described in
[docs/reference/shipper.md](docs/reference/shipper.md).

## Secrets

This repository is **public**. Never commit a credential, token or key, and never put one in `config.json`.

- MainManager credentials and the ingest secret live in `secrets.json` on the CTS server, or in environment
  variables ([ADR-0021](docs/adr/0021-secrets-in-secrets-json.md)).
- `secrets.json` is git-ignored, and `tests/test_repo_hygiene.py` fails if `config.json` ever carries a
  credential again.
- cts-api carries none of the old history. The old repository `slimyloki/cts-alarms` still has, in older
  commits, a MainManager password that was rotated on 2026-09-29, plus operator names. Archiving it or making
  it private no longer affects any deploy ([T-102](docs/TODO.md)).

## The web side

The history database, the pages and the friendly alarm names are the **digibuild** sub-project
`cts-alarms`, in the digibuild repo. This repo only defines what the bot sends: see
[docs/reference/shipper.md](docs/reference/shipper.md) and
[ADR-0020](docs/adr/0020-hmac-ingest-to-digibuild.md).
