# `install.ps1` — installing and updating the bot on the CTS server

`install.ps1` is in the `cts-alarms/` folder of the [cts-api](../../../README.md) repo. It installs the
bot on the CTS server from `C:\cts-api\cts-alarms`, the source, into `C:\priorityalarmsapi`, where the
bot runs. It follows the steps of [arc42 §7](../arc42/07-deployment-view.md) in order and stops at the
first problem. It needs Windows PowerShell 5.1, which Windows Server 2016 has, run as Administrator
([ADR-0023](../adr/0023-moved-into-cts-api.md)).

## First install

1. **Get the repo into `C:\cts-api`** as described in the repo root's
   [INSTALL.md](../../../INSTALL.md). There are two ways:
   - `git clone https://github.com/slimyloki/cts-api.git C:\cts-api`, in Command Prompt (Admin).
   - The ZIP `https://github.com/slimyloki/cts-api/archive/refs/heads/main.zip`, unblocked, extracted to
     `C:\`, and `C:\cts-api-main` renamed to `C:\cts-api`.

   The repo is public, so no GitHub login is needed.
2. **Double-click `C:\cts-api\cts-alarms\install.cmd`.** The click-only steps are in
   [INSTALL.md](../../INSTALL.md).
   - `install.cmd` asks for administrator rights and runs `install.ps1` with `-ExecutionPolicy Bypass`.
     That lets the unsigned script run for this one call only; it does not change the machine's policy.
   - `install.cmd` and `update.cmd` keep CRLF line endings through the repo's `.gitattributes`, because
     `cmd.exe` misparses LF-only batch files.

The same from a PowerShell prompt started with *Run as administrator*:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\cts-alarms\install.ps1
```

## Later updates

Double-click `C:\cts-api\cts-alarms\update.cmd`. It runs `install.ps1 -Download`, which first brings
`C:\cts-api` up to date, then runs the **updated** `install.ps1`, so the newest install logic installs the
newest files.

| `C:\cts-api` is | `-Download` does |
|---|---|
| a Git clone, with `git.exe` on PATH | `git -c safe.directory=* -C C:\cts-api pull --ff-only`. `safe.directory` is needed because the clone may belong to another account than the elevated one. It stops without installing if the pull fails. |
| not a clone (ZIP) | Sets TLS 1.2, downloads the cts-api ZIP of `main`, and checks that it contains `cts-alarms\install.ps1`. Copies it over `C:\cts-api`, then removes the download. |

TLS 1.2 is set because PowerShell 5.1 on Server 2016 does not use it by default, and GitHub refuses older
versions.

## What it does

| Step | Action | Stops when |
|---|---|---|
| 1 | Reads the task `\TACVistaLogs\TACVista_Alarm_Bot` for the Python it runs and the account it runs as; checks `requests` imports | task or Python missing |
| 2 | Disables the task and waits until no run is in progress | a run lasts over 3 minutes |
| 3 | Copies the bot folder's top-level files to `C:\priorityalarmsapi-backups\<date-time>\`, except `secrets.json` and `mm_token.json`; the backup is readable only by the task account, Administrators and SYSTEM | icacls fails |
| 4 | Copies `main.py`, `shipper.py`, `config.json`, `secrets.example.json` into `C:\priorityalarmsapi` | — |
| 5 | Keeps `secrets.json` if it holds a MainManager username and password; otherwise asks for them, writes the file as UTF-8 **without** a byte-order mark, keeps an existing `vps` section. Then restricts the file with icacls every time | no password given; icacls fails |
| 6 | Deletes `mm_token.json`, so the check below really logs in (T-108) | — |
| 7 | Runs `main.py --dry-run` and requires the new version in the banner, `[DRY] MainManager credentials OK`, `[DRY] nothing written`, exit code 0, and none of `credential check FAILED`, `DEPRECATED`, `Unhandled exception`, `Traceback` | any of those |
| 8 | Asks, then enables the task, starts one real run, waits up to 10 minutes and shows that run's `CREATED`/`UPDATED`/`RESOLVED`/`ERROR`/`Run complete` lines from today's log | — (warns on `deferred≠0` or ERROR lines) |

It never writes `objects.csv`, `exceptions.csv`, the state files, `logs\` or `csv\`: the server's
copies are the live ones.

If it stops after step 2, the task stays **disabled** and the script prints both ways out: fix
and run again, or `-Rollback`.

## Switches

| Switch | Effect |
|---|---|
| `-Download` | Bring `C:\cts-api` up to date first (`git pull`, or the cts-api ZIP), then run the updated `install.ps1`. `update.cmd` uses this. |
| `-ResetSecrets` | Ask for the MainManager username and password again, for example after a rotation or when the dry run reports `credential check FAILED`. |
| `-NoEnable` | Stop after a good dry run and leave the task disabled. |
| `-Yes` | Do not ask before enabling the task. |
| `-Rollback` | Restore `main.py`, `shipper.py` and `config.json` from the newest backup, then enable the task. `secrets.json` is left as it is. |
| `-Target`, `-TaskPath`, `-TaskName` | Other folder or task names. The defaults are the production ones. |

## Guards

`tests/test_install_script.py` pins the script's safety rules: it must be ASCII only, install
exactly the four code files, never the live CSVs, secrets or state, write `secrets.json` without a
BOM, and lock it with well-known SIDs. The SIDs are `S-1-5-32-544` for Administrators and
`S-1-5-18` for SYSTEM, so this works on a Danish-language Windows too. The test also checks that
the dry-run strings it waits for still exist in `main.py`, and that `-Download` targets
`slimyloki/cts-api` with `pull --ff-only` and `safe.directory`. The cts-api root's
`tests/test_repo_scope.py` checks, repo-wide, that every `.cmd` is CRLF and ASCII and every `.ps1` is
ASCII. When a `pwsh` binary is on PATH it
parse-checks the script. On 2026-09-29 the script also passed PSScriptAnalyzer's
Windows PowerShell 5.1 syntax and Server 2016 command-compatibility rules. It has not yet run on
the CTS server itself.
