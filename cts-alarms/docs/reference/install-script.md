# `install.ps1`, `update.cmd`, `status.cmd`: running the bot on the CTS server

Since v2.1.1 the bot runs **in place** from the cts-api repo folder `C:\cts-api\cts-alarms`
([ADR-0024](../adr/0024-run-in-place-from-cts-api.md)). Code, `config.json`, `secrets.json`, state, the CSV audit
trail and the logs all live there. The old runtime folder `C:\priorityalarmsapi` is retired. The owner-facing
steps are in [INSTALL.md](../../INSTALL.md); this page is the reference.

## The files

| File | Role |
|---|---|
| `install.cmd` | Double-click launcher: asks for administrator rights (`fltmc` check, `Start-Process -Verb RunAs`), then runs `install.ps1` with `-ExecutionPolicy Bypass` and passes on any switches. CRLF line endings (`*.cmd -text` in the repo's `.gitattributes`). |
| `install.ps1` | The install, below. Windows PowerShell 5.1, ASCII only. |
| `update.cmd` | Runs `install.cmd -Download`. |
| `status.cmd` / `status.ps1` | Read-only health check ending in `VERDICT: OK` or a list of problems, each with its fix. |

## What `install.ps1` does

| Step | Action | Stops when |
|---|---|---|
| 1 | Reads the task `\TACVistaLogs\TACVista_Alarm_Bot`. Its action must run this folder's `main.py`; if not, it tries `Set-ScheduledTask` and otherwise prints the Task Scheduler click steps. Checks the task's Python imports `requests`. | task or Python missing, task cannot be repointed |
| 2 | Disables the task and waits until no run is in progress. | a run lasts over 3 minutes |
| 3 | Backs up code, config and state to `C:\cts-api-backups\cts-alarms\<date-time>\`, with the Git commit in `commit.txt`. Never `secrets.json`, not `logs\`/`csv\`. The folder is readable only by the task account, Administrators and SYSTEM. | icacls fails |
| 4 | **Once:** if `C:\priorityalarmsapi\alarms_state.json` exists and this folder has none, it copies `alarms_state.json`, `csv_state.json`, `csv\`, `logs\` and `secrets.json`. If the server's `objects.csv` or `exceptions.csv` differ from the repo's, the server's copy wins and a warning says so. The state files are compared by hash. | a state file did not copy identically, and the old folder is untouched |
| 5 | Keeps `secrets.json` if it holds a MainManager username and password; otherwise asks for them. The username is offered from the old config. The file is written as UTF-8 without a byte-order mark, and an existing `vps` section is kept. Then icacls: task account read, Administrators and SYSTEM full, by SID. | no password; icacls fails |
| 6 | Deletes `mm_token.json` and `mm_auth_failed.json`, so the test really logs in. | — |
| 7 | `main.py --dry-run` must show: exit 0, `Alarm bot started (v<version>, config=C:\cts-api\cts-alarms\config.json, …`, `[DRY] MainManager credentials OK`, `[DRY] nothing written`, and none of `credential check FAILED`, `no MainManager credentials`, `DEPRECATED`, `Unhandled exception`, `Traceback`, `install.cmd once`. | any of those |
| 8 | Renames `C:\priorityalarmsapi` to `C:\priorityalarmsapi.retired-<yyyyMMdd>`, only after step 7 passed and only if data came from there. It is never deleted. | — (warns if the rename fails) |
| 9 | Asks, then enables the task, starts one real run, waits up to 10 minutes, and shows that run's `CREATED`/`UPDATED`/`RESOLVED`/`ERROR`/`Run complete` lines from `logs\<today>.log`. | — (warns on `deferred≠0` or ERROR lines) |

## Switches

| Switch | Effect |
|---|---|
| `-Download` | `update.cmd`. Disables the task, then either runs `git pull --ff-only` (`-c safe.directory=*`) when `C:\cts-api` is a clone, or copies the cts-api ZIP over it after saving the code to `C:\cts-api-backups\cts-alarms\code-<date-time>\`. Then it runs the **new** `install.ps1`. If that one's test run fails, the previous code comes back (`git reset --hard <old commit>`, or the saved copy), the task is re-enabled if it was enabled, and the window ends with FAILED. |
| `-ResetSecrets` | Ask for the MainManager username and password again. |
| `-NoEnable` | Stop after a good test run; the task stays disabled. |
| `-Yes` | Do not ask before enabling the task. |
| `-Rollback` | Put back the code of the newest backup (`git reset --hard` to its `commit.txt`, or copy its files), then enable the task. State, logs and `secrets.json` are not touched. |

## The guard in `main.py`

Until step 4 has run, a scheduled run of the new code (for example right after a `git pull`) finds no state in
`C:\cts-api\cts-alarms` while `C:\priorityalarmsapi\alarms_state.json` still exists. It then logs
`… double-click install.cmd once to move it. Nothing was done this run.` and exits 2. It does **not** bootstrap, which
would drop the link to every open ticket (`LEGACY_STATE_FILE`, `main.py`).

## Checks

`tests/test_install_script.py` pins the rules:
- in place, no code copied elsewhere;
- the old folder only renamed, and only after the test run;
- backups never hold `secrets.json`;
- BOM-less secrets, locked by SID;
- the dry-run gate strings still exist in `main.py`;
- the update keeps a way back;
- `status.ps1` changes nothing;
- `.ps1` files are ASCII and `.cmd` files are CRLF.

When `pwsh` is on PATH it also parse-checks both scripts. On 2026-09-29 both passed PSScriptAnalyzer's Windows
PowerShell 5.1 syntax rules and its Server 2016 command-compatibility rules. Neither has run on the CTS server yet.
