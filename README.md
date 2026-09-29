# cts-api — everything that runs on the CTS server

This is **the** repository for the CTS server: the Windows Server 2016 machine that runs TAC Vista 5.1.9, the
building management system. Every application, script and config file that has to be on that server lives
here, **one folder per application**. On the server, the repo lives in **`C:\cts-api`**; see
[INSTALL.md](INSTALL.md).

Anything that runs on the VPS or on Vercel does **not** belong here. That includes the digibuild portal, its
workers, systemd units and Caddy config. Those parts live in their own repositories and talk to the CTS
server over the network.

## Applications

| Folder | What it does on the CTS server | Status |
|---|---|---|
| [`cts-alarms/`](cts-alarms/README.md) | TAC Vista alarm bot. It reads Vista's alarm list every 5 minutes, creates and updates MainManager incidents, and can ship each run to digibuild. | Migrated 2026-09-29 from `slimyloki/cts-alarms` |
| `indeklima-bot/` | Indoor-climate bot. It reports room temperature logs to MainManager. | Planned; source is `slimyloki/vista-opc` → `indeklima-bot/` |
| `vista-opc/` | The Vista OPC bridge: the .NET API (`dotnetapi/`) and its Windows service setup. | Planned; source is `slimyloki/vista-opc` |

The migration order and the per-application checklist are in [docs/MIGRATION.md](docs/MIGRATION.md).

## Rules

1. **Only what must be on the CTS server.** Code, config, the scheduled-task and service definitions for that
   server, its install and update scripts, and each application's docs and tests. Nothing that runs
   elsewhere, and no copies of VPS or Vercel code.
2. **Public repository.** Never a secret, token, key or password. No IP address, and no personal data such as
   operator names in logs or state files. Secrets live only on the server, in each application's
   `secrets.json`, which is git-ignored, or in environment variables.
3. **Every action on the CTS server is a script in this repo.** Each one is a `.ps1` with a `.md` that
   explains it, plus a `.cmd` that asks for administrator rights and runs it, so it can be double-clicked.
   One-off, server-wide actions go in [`runbooks/`](runbooks/README.md). Install and update scripts go in
   the application's own folder. Nothing has to be pasted into the server.
4. **Runtime data stays on the server.** Logs, state files, CSV audit trails, outboxes and token caches are
   git-ignored.
5. **Applications are imported without their old Git history.** Old histories hold credentials and personal
   data. The source repository is named in each application's README and in
   [docs/MIGRATION.md](docs/MIGRATION.md).

The decision behind this layout is [ADR-0001](docs/adr/0001-one-repo-for-the-cts-server.md).

## Layout

```
cts-api/
├── README.md            this file
├── INSTALL.md           getting the repo onto the CTS server (C:\cts-api), and updating it
├── docs/                repo-wide decisions and the migration tracker
├── runbooks/            one-off CTS-server actions: <date>-<name>.ps1 + .md + .cmd
├── tests/               repo-wide guards (scope, secrets, line endings)
└── cts-alarms/          one folder per application, each with README, INSTALL, docs/, tests/
```

## Checks

```
python -m unittest discover -s tests            # repo-wide guards: scope, secrets, IPs, CRLF, ASCII
cd cts-alarms && python -m pytest -q tests     # an application's own tests
```
