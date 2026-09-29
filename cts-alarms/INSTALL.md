# Install or update the alarm bot on the CTS server

Everything happens on the CTS server itself. Nothing needs to be copied or pasted from another machine.

The bot runs **in place from `C:\cts-api\cts-alarms`**. Its config, `secrets.json`, state, CSV audit trail
(`csv\`) and logs (`logs\`) all live in that folder. The old folder `C:\priorityalarmsapi` is not used any more.
The first install moves its data over and renames it to `C:\priorityalarmsapi.retired-<date>`.

**First, the repo must be in `C:\cts-api`.** If it is not there yet, follow [../INSTALL.md](../INSTALL.md): a
`git clone`, or a ZIP download and rename.

## The three files you double-click

| File | What it does |
|---|---|
| **`install.cmd`** | Asks for administrator rights, then runs `install.ps1` (below). Use it the first time, and after changing the password. |
| **`update.cmd`** | Gets the newest version (`git pull`, or the ZIP when Git is not used), then does the same as `install.cmd`. If the new version fails its test run, it puts the previous code back. |
| **`status.cmd`** | Read-only: shows the task, the files and today's log, and ends with **`VERDICT: OK`** or the problem and its fix. |

## First install (once)

1. Open **`C:\cts-api\cts-alarms`** and double-click **`install.cmd`**. Answer **Yes** when Windows asks for
   administrator rights.
2. Answer the questions in the window:
   - **Username:** press Enter to keep the one shown.
   - **Password:** type the **new** MainManager password twice. It is not shown while you type.
   - **"Enable the task and start one real run now? [Y/n]":** press Enter for yes.
3. Read the end of the window. Success is the line **`OK  real run complete, nothing deferred, no errors`**, then
   **`Done. v2.1.1 runs from C:\cts-api\cts-alarms and the task is enabled.`**

On this first run it also moves `alarms_state.json`, `csv_state.json`, `csv\`, `logs\` and an existing
`secrets.json` from `C:\priorityalarmsapi` into `C:\cts-api\cts-alarms`. After the test run has passed, it renames
the old folder to `C:\priorityalarmsapi.retired-<date>`. Delete that folder whenever you like.

The script finds the scheduled task by what it runs, so its name does not matter. On this server it is
**`\TacVistaMails\Alarm_Bot`**.

If anything is wrong, the script stops, says what failed, and leaves the scheduled task **disabled**. The window
then shows the next step. It is usually one of the commands under [Other things](#other-things).

## Later

- **Is it working?** Double-click **`status.cmd`**.
- **New version?** Double-click **`update.cmd`**.

## Other things

Open **Command Prompt (Admin)**: right-click the Start button, then **Command Prompt (Admin)**. Type:

```
cd /d C:\cts-api\cts-alarms
```

Then one of these:

| Type this | When |
|---|---|
| `install.cmd -ResetSecrets` | The password was changed again, or the window or `status.cmd` said the login was rejected. |
| `install.cmd -Rollback` | Go back to the code from before the last install. State, logs and `secrets.json` stay. |
| `install.cmd -NoEnable` | Install and test, but leave the task switched off. |
| `install.cmd -TaskPath \TacVistaMails -TaskName Alarm_Bot` | The window said it found no task, or more than one. This names the task by hand. |

## What stays out of Git

`secrets.json`, `alarms_state.json`, `csv_state.json`, `csv\`, `logs\`, `mm_token.json` and
`mm_auth_failed.json` are git-ignored: a `git pull` never touches them and nothing can push them. Backups of code
and state go to `C:\cts-api-backups\cts-alarms\<date-time>\`, outside the repo.

The details are in [docs/reference/install-script.md](docs/reference/install-script.md).
