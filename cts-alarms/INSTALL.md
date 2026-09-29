# Install or update the alarm bot on the CTS server

Everything happens on the CTS server itself. Nothing needs to be copied or pasted from another machine.

**First, the repo must be in `C:\cts-api`.** If it is not there yet, follow [../INSTALL.md](../INSTALL.md): a
`git clone`, or a ZIP download and rename.

The bot itself keeps running from **`C:\priorityalarmsapi`**. That folder holds its state, logs, CSV audit
trail and `secrets.json`. `C:\cts-api\cts-alarms` is only the source that the install copies from.

## First install

1. Open **`C:\cts-api\cts-alarms`** and double-click **`install.cmd`**. Answer **Yes** when Windows asks
   for administrator rights.
2. Answer the questions in the window:
   - **Username:** press Enter to keep the one shown.
   - **Password:** type the **new** MainManager password twice. It is not shown while you type.
   - **"Enable the task and start one real run now? [Y/n]":** press Enter for yes.
3. Read the end of the window. Success is the line **`OK  real run complete, nothing deferred, no errors`**,
   followed by **`Done. v2.1.0 is installed and the task is enabled.`**

If anything is wrong, the script stops, says what failed, and leaves the scheduled task **disabled**. The
window then shows the next step. It is usually one of the commands under [Other things](#other-things).

## Later updates

Double-click **`C:\cts-api\cts-alarms\update.cmd`**. It brings `C:\cts-api` up to date (`git pull`, or the
ZIP when Git is not used) and runs the same install, with the same backup and dry-run check.

## Other things

Open **Command Prompt (Admin)**: right-click the Start button, then **Command Prompt (Admin)**. Type:

```
cd /d C:\cts-api\cts-alarms
```

Then one of these:

| Type this | When |
|---|---|
| `install.cmd -ResetSecrets` | The password was changed again, or the window said `credential check FAILED`. |
| `install.cmd -Rollback` | Go back to the files from before the last install. |
| `install.cmd -NoEnable` | Install and test, but leave the task switched off. |

## What the install does

The details are in [docs/reference/install-script.md](docs/reference/install-script.md). In short:
1. Stops the scheduled task `\TACVistaLogs\TACVista_Alarm_Bot`.
2. Backs up `C:\priorityalarmsapi` to `C:\priorityalarmsapi-backups\<date-time>\`.
3. Copies in `main.py`, `shipper.py`, `config.json` and `secrets.example.json`.
4. Writes `secrets.json` with the password, readable only by the task's account and administrators.
5. Runs a test that changes nothing (`--dry-run`), then switches the task back on.

It never changes `objects.csv`, `exceptions.csv`, the state files, `logs\` or `csv\`.
