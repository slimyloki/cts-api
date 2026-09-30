# 2026-09-30: send the alarm bot's missing 44 hours to digibuild

## What it does, and why

From 28 September 17:55 to 30 September 13:42, about 44 hours, the alarm bot kept running on this server, but its
link to digibuild (the shipper) was not switched on yet. Everything the bot saw in those hours (alarms coming in,
clearing, being acknowledged) is only in its CSV audit files here: digibuild never got it. That is why **147 alarms
still show as open on digibuild**: what happened to them in those hours, clearing included, never reached it.

This runbook has the bot's `backfill.py` read those events back from the CSV files and send them to digibuild. It
shows a report first, then asks, and sends only when you answer **y**. The window is **2026-09-28 17:30:00 to
2026-09-30 13:42:10**, server time, both ends included. It starts 25 minutes before the gap, to be safe: digibuild
skips the events it already has, so **running it twice is safe**.

## What it changes

**On this server: nothing.**

- It reads the scheduled task that runs the bot (on this server `\TacVistaMails\Alarm_Bot`) to find the bot's
  Python and folder, normally `C:\cts-api\cts-alarms`.
- In that folder it reads `config.json`, `secrets.json` (the digibuild ingest secret, never shown), the CSV files in
  `csv\`, and the daily logs `logs\2026-09-27.log` to `logs\2026-10-01.log`.
- It writes no file (no `__pycache__`, no outbox, no `names.json`) and changes no permission. It does not stop,
  start or change the scheduled task: the bot keeps running every 5 minutes meanwhile.
- It needs administrator rights only because `secrets.json` is readable just by the task's account,
  Administrators and SYSTEM.

**On digibuild, only after you answer y:** the events of the window are added, and the events digibuild already
has are skipped. digibuild then rebuilds the alarms those events belong to.

## How to run it

Run it after two things have happened:

- you have double-clicked `cts-alarms\update.cmd` (step 1 below), which brings `backfill.py` and this runbook;
- **Claude has told you that the digibuild server is updated** to take backfill batches.

Running it too early does no harm. It stops at the first batch with `FAILED: the digibuild server does not accept
backfill batches yet (HTTP 422). Nothing was changed.` Run it again once Claude says digibuild is updated.

1. Double-click **`C:\cts-api\cts-alarms\update.cmd`** and answer **Yes** to the administrator question. Answer
   its own questions as usual, and wait for the line that starts with `==> Done.` (A plain `git pull` in
   `C:\cts-api` also brings the files, but `update.cmd` also checks the bot afterwards.)
2. Double-click **`C:\cts-api\runbooks\2026-09-30-cts-alarms-backfill.cmd`** and answer **Yes** to the
   administrator question.
3. Read the report. Look at `events to send`, at `distinct alarms: ... of them RESOLVED in the window`, and at
   `Log cross-check`, where every run should match. The report ends with
   `OK: report only, nothing was sent. N events in B batches would be sent; add --send to send them.`
   You do not type `--send`: the window asks you instead.
4. At **`Send these events to digibuild now? [y/N]:`** type **y** and press Enter. Anything else, or just Enter,
   stops without sending.
5. It prints the report once more, then one line per batch. Wait for the last line.

**Only the report, without the question:** open **Command Prompt (Admin)** (right-click the Start button, then
**Command Prompt (Admin)**) and type:

```
cd /d C:\cts-api\runbooks
2026-09-30-cts-alarms-backfill.cmd -CheckOnly
```

## What success looks like

The last lines of the window (N, I, D and R are numbers):

```
==> Sending the events to digibuild
    | Backfill 2026-09-28 17:30:00 .. 2026-09-30 13:42:10 (server local time, UTC+02:00, both ends included)
    |   ... (the report again) ...
    | Sending N events in B batches to https://api.digibuild.dk
    |   batch 1/B: ... events, ... inserted, ... already there, ... alarms rebuilt
    |   ...
    |   total: N events, I inserted, D already there, R alarms rebuilt
OK: backfill sent — N events, I inserted, D already there, R alarms rebuilt
```

- **N** is the number of events sent, the same as `events to send` in the report.
- **I inserted** are events digibuild did not have. **D already there** are events it had already.
- **R alarms rebuilt** is how many alarms digibuild rebuilt from their events.

Tell Claude the `OK: backfill sent` line: it then checks the 147 open alarms on digibuild.

**A second run** is safe and ends with `OK: backfill sent — N events, 0 inserted, N already there, ...`. digibuild
knows each event by its alarm, time and type, and skips the ones it has.

Other good endings:

| Last line | Meaning |
|---|---|
| `OK: nothing sent` | You did not answer y. Nothing happened; double-click it again when you are ready. |
| `OK: report only (-CheckOnly), nothing was sent.` | The report only, as asked with `-CheckOnly`. |

## If it fails

The last line starts with `FAILED:`. **This server is never changed**, whatever the line says. Nothing is sent to
digibuild before you answer y, or after a failure in the report.

| Last line | Meaning | What to do |
|---|---|---|
| `FAILED: the digibuild server does not accept backfill batches yet (HTTP 422). Nothing was changed. Run this again once the server is updated.` | It was run before digibuild was updated. | Wait until Claude says digibuild is updated, then double-click it again. |
| `FAILED: backfill.py is missing in C:\cts-api\cts-alarms -- double-click C:\cts-api\cts-alarms\update.cmd first` | The update that brings `backfill.py` has not reached this server. | Double-click `update.cmd`, then this again. |
| `FAILED: sending is not ready: no ingest secret: ... Nothing was sent.` | `secrets.json` has no digibuild ingest secret. The line above it says how to set it. | In Command Prompt (Admin) type `cd /d C:\cts-api\cts-alarms`, then `install.cmd -IngestSecret`. Then run this again. |
| `FAILED: sending is not ready: ...` with another reason | `config.json` has no working `vps` section. | Tell Claude the line. |
| `FAILED: N malformed rows in the window (listed above). Nothing was sent.` | Some CSV rows in the window cannot be read back. The `MALFORMED <file>.csv:<line>: <reason>` lines above name them (no alarm text is shown). | Tell Claude those lines. |
| `FAILED: batch k/B was refused: HTTP 401 (check vps.ingest_secret and that this server's clock is within 300 s). ...` | digibuild did not accept the signature: the ingest secret is wrong, or the clock is off. | Double-click `C:\cts-api\runbooks\2026-09-29-internet-address.cmd` to check the clock, and tell Claude. |
| `FAILED: batch k/B was refused: HTTP 429 ...` | Too many requests. | Wait a few minutes and run it again. |
| `FAILED: batch k/B was refused: HTTP 404 ...` or another code | The route or the request was refused. | Tell Claude the line. |
| `FAILED: batch k/B could not be sent: ..., 4 attempts. ...` | Network or digibuild trouble; each batch is tried four times. | Run it again later. |
| `FAILED: batch k/B: the server received X of Y events. ...` | digibuild answered, but not as agreed. | Tell Claude the line. |
| `FAILED: No scheduled task runs the alarm bot's main.py ...` or `FAILED: More than one scheduled task runs the alarm bot: ...` | It could not tell which task runs the bot. | In Command Prompt (Admin) type `cd /d C:\cts-api\runbooks`, then `2026-09-30-cts-alarms-backfill.cmd -TaskPath \TacVistaMails -TaskName Alarm_Bot`. |
| `FAILED: Start it with administrator rights: ...` | The administrator question was answered No. | Double-click it again and answer Yes. |
| Any other `FAILED:` line, for example `FAILED: the report stopped with exit code ...` or `FAILED: unexpected error: ...` | Something the script did not expect. The lines above say what. | Tell Claude the line and the lines above it. |

If batches had been accepted before a failure, the line above `FAILED:` says
`batches 1-k (... events) were accepted before this one`. Running it again is safe: those events come back as
`already there`.

**How to undo.** There is nothing to undo on this server, because nothing was changed here. What it sends to
digibuild are the bot's own events: the same ones the shipper would have sent live in those 44 hours. No script
takes them back. If they ever have to go, that is done on the digibuild side (ask Claude).
