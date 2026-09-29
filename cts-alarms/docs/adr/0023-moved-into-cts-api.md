# ADR-0023: The alarm bot's code moves into the cts-api repository

- **Status:** Accepted; decision 3 superseded by [ADR-0024](0024-run-in-place-from-cts-api.md) (2026-09-29)
- **Date:** 2026-09-29
- **Refines:** [ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md). The code still holds only the
  CTS side; only its location changes.
- **Repo-wide decision:** cts-api [ADR-0001](../../../docs/adr/0001-one-repo-for-the-cts-server.md)

## Context

The owner decided that one repository, `slimyloki/cts-api`, holds everything that has to be on the CTS
server, one folder per application. The alarm bot is the first application in it. The old repository
`slimyloki/cts-alarms` is public, and its history carries a (since rotated) MainManager password and operator
names from committed logs and CSV files.

## Decision

1. The bot's tree moves to `cts-api/cts-alarms/`. It was imported from `slimyloki/cts-alarms` at commit
   `907d91f` as one commit, **without** the old history.
2. On the CTS server the repo lives in `C:\cts-api`, and this folder is `C:\cts-api\cts-alarms`.
   - `install.cmd` and `update.cmd` install from there.
   - `install.ps1 -Download` updates `C:\cts-api` itself, by `git pull` when it is a clone, otherwise by
     downloading the cts-api ZIP.
3. The bot's **runtime folder is unchanged**: `C:\priorityalarmsapi`. The scheduled task, state files, logs,
   CSV audit trail and `secrets.json` stay there. Moving it would be a separate runbook.
4. The backlog agent `cts-todo-keeper` is no longer in the repo. It is now a user-level agent on the
   development box: agent definitions are not needed on the CTS server.

## Consequences

- `slimyloki/cts-alarms` is history only. Archiving it or making it private affects no deploy, which
  settles the main reason behind T-102.
- Links and paths in these docs that name `slimyloki/cts-alarms` describe the past. The current home is
  `slimyloki/cts-api`, folder `cts-alarms/`.
