# Migration into cts-api

Every application that runs on the CTS server moves into this repo as one folder ([ADR-0001](adr/0001-one-repo-for-the-cts-server.md)).
This file tracks the order and, per application, the checklist. `cts-api-keeper` maintains it.

## Order

| # | Application | Source | Folder here | Status |
|---|---|---|---|---|
| 1 | Alarm bot | `slimyloki/cts-alarms` @ `907d91f` (2026-09-29) | `cts-alarms/` | **Migrated 2026-09-29.** Runs in place from `C:\cts-api\cts-alarms` since v2.1.1; `install.cmd` moves the old data once and retires `C:\priorityalarmsapi`. |
| 2 | Indoor-climate bot | `slimyloki/vista-opc` → `indeklima-bot/` (v3 port merged as `185b98f`) | `indeklima-bot/` | Planned. Its v3 port is **live** on the CTS server since 2026-09-29 12:15 UTC, from `C:\vista-opc\indeklima-bot\`; the ticket mirror shows its first new tickets. Import after vista-opc PR #2 lands, so the import carries that fix. |
| 3 | Vista OPC .NET API | `slimyloki/vista-opc` → `dotnetapi/` | `vista-opc/` | Planned. Any credential in its code moves into a git-ignored secrets file before import (checklist step 3). |
| 4 | The rest | Anything else found running on the CTS server | as named | To be inventoried. |

## Checklist per application

1. **Take only the CTS-server part.** Leave VPS and Vercel code where it is.
2. **Import the current tree without Git history** as one commit, named `Import <app> from <repo>@<sha>`.
3. **Remove secrets, IP addresses and personal data.** Move credentials into a git-ignored `secrets.json`
   and commit a `secrets.example.json` in their place.
4. **Git-ignore the runtime files** in the application's own `.gitignore`.
5. **Write the application's `README.md` and `INSTALL.md`, plus `install.ps1`, `install.cmd` and
   `update.cmd`.** The install must back up, install, gate on a dry run or self-test, then enable. Nothing
   may need pasting.
6. **Keep its tests passing.** Keep `tests/test_repo_scope.py` passing.
7. **Update the root README's application table and this file.** Point the source repository at the new
   home.
8. **Update the box documentation and the general TODO.** The source repository's backlog says where the
   work continues.
