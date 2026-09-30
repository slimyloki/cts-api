# Runbooks: one-off actions on the CTS server

Anything that has to be **done** on the CTS server comes as a script in this folder. That includes moving a
folder, changing a scheduled task, setting a permission, or checking something. The owner never pastes
commands into the server. They pull the repo, read the `.md`, and double-click the `.cmd`.

`cts-server-runbook-writer` writes these. Install and update scripts for an application live in that
application's folder, not here.

## Naming

Three files per runbook, same stem, `<yyyy-mm-dd>-<short-name>`:

| File | Content |
|---|---|
| `<stem>.md` | What it does and why, what it changes, how to run it, what success looks like, how to undo it. |
| `<stem>.ps1` | The script. Windows PowerShell 5.1, **ASCII only**. |
| `<stem>.cmd` | Double-click launcher. It asks for administrator rights and runs the `.ps1` with `-ExecutionPolicy Bypass`. **CRLF line endings.** |

## Rules for the script

- **Look before changing.** Print what it found, change only what it must, and verify afterwards.
- **Be safe to run twice.** A second run finds the work done and says so.
- **Take a `-WhatIf`-style switch where it makes sense:** report without changing.
- **Back up anything it overwrites** to a dated folder next to the original.
- **Never print a secret. Never write one outside the application's `secrets.json`.**
- **End with a clear `OK ...` or `FAILED: ...` line,** and exit 0 or 1 to match.

## Index

| Runbook | Purpose | Status |
|---|---|---|
| [2026-09-29-internet-address](2026-09-29-internet-address.md) | Read-only: the CTS server's internet address (for digibuild's allow-list), whether `api.digibuild.dk` answers, and the clock difference | ready |
| [2026-09-30-cts-alarms-backfill](2026-09-30-cts-alarms-backfill.md) | Sends the alarm bot's events of the 44-hour gap (2026-09-28 17:30:00 to 2026-09-30 13:42:10) to digibuild, where 147 alarms still show as open. Report first, then it asks; changes nothing on the server; safe to run twice | ready: run after `cts-alarms\update.cmd`, once Claude says digibuild takes backfill batches |
