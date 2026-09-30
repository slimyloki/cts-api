<#
.SYNOPSIS
    One-off: send the alarm bot's events of the 44-hour gap (2026-09-28 17:30:00
    to 2026-09-30 13:42:10, server time) to digibuild. It shows a report first
    and sends only when you answer y.

.DESCRIPTION
    Double-click 2026-09-30-cts-alarms-backfill.cmd next to this file.

    From 2026-09-28 17:55 to 2026-09-30 13:42 the alarm bot ran before its
    digibuild shipper was switched on. Its events of those hours exist only in
    its CSV audit files on this server, so digibuild never got them, and alarms
    that cleared in that time still show as open there. The bot's backfill.py
    reads those events back and sends them.

    What it does, in order. It stops at the first problem and says what to do:
      1. Finds the scheduled task that runs the alarm bot by what it runs (a
         main.py in C:\cts-api\cts-alarms or C:\priorityalarmsapi; the task's
         name does not matter), and takes the bot's Python and folder from the
         task's action. The folder is normally C:\cts-api\cts-alarms.
      2. Checks that backfill.py is in that folder (update.cmd brings it).
      3. Runs the report in that folder:
             python backfill.py --from "2026-09-28 17:30:00" --to "2026-09-30 13:42:10"
         It reads the CSV files and daily logs only: it sends nothing and
         writes nothing. It stops with FAILED if the report fails, or if the
         report says sending is not ready (no ingest secret, for example).
      4. Asks "Send these events to digibuild now? [y/N]". Anything but y
         ends here with "OK: nothing sent".
      5. Runs the same command with --send, shows its output, and ends with
         its OK or FAILED line and exit code.

    It does not stop, start or change the scheduled task, and it writes no
    file. digibuild skips the events it already has, so running it twice is
    safe. Administrator rights are needed because secrets.json (which holds
    the ingest secret) is readable only by the task's account, Administrators
    and SYSTEM.

.PARAMETER CheckOnly
    The report only (steps 1 to 3): no question, nothing is sent.

.PARAMETER TaskPath
    Only with -TaskName, when the task cannot be found by what it runs: the
    task's folder in Task Scheduler, for example \TacVistaMails.

.PARAMETER TaskName
    The name of the task that runs the alarm bot, for example Alarm_Bot.

.EXAMPLE
    2026-09-30-cts-alarms-backfill.cmd
    Double-clicked: the report, the question, then the sending.

.EXAMPLE
    2026-09-30-cts-alarms-backfill.cmd -CheckOnly
    Typed in Command Prompt (Admin) in C:\cts-api\runbooks: the report only.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\runbooks\2026-09-30-cts-alarms-backfill.ps1 -TaskPath \TacVistaMails -TaskName Alarm_Bot
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$TaskPath = "",
    [string]$TaskName = ""
)
Set-StrictMode -Version 1
$ErrorActionPreference = "Stop"

# The window: both ends included, in the CTS server's local time. It starts 25
# minutes before the gap; events digibuild already has are skipped.
$From = "2026-09-28 17:30:00"
$To   = "2026-09-30 13:42:10"

$Stem       = "2026-09-30-cts-alarms-backfill"
$RepoRoot   = Split-Path $PSScriptRoot -Parent
$RepoApp    = Join-Path $RepoRoot "cts-alarms"
$UpdateCmd  = Join-Path $RepoApp "update.cmd"
$InstallCmd = Join-Path $RepoApp "install.cmd"

function Say([string]$msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok([string]$msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }

function Fail([string]$msg) {
    Write-Host ""
    Write-Host "FAILED: $msg" -ForegroundColor Red
    exit 1
}

trap {
    # Anything unexpected still ends with a FAILED line and exit code 1.
    Write-Host ""
    Write-Host ("FAILED: unexpected error: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

function Find-BotTask([string]$path, [string]$name) {
    # The task is found by WHAT it runs (the alarm bot's main.py in
    # C:\cts-api\cts-alarms or the old C:\priorityalarmsapi), not by its name:
    # the name differs between servers. The same rule as cts-alarms\install.ps1.
    if ($name) {
        # Given by hand: accept "TacVistaMails", "\TacVistaMails" or "\TacVistaMails\".
        $tp = "\" + ($path + "").Trim().Trim('\')
        if ($tp -ne "\") { $tp += "\" }
        return @(Get-ScheduledTask -TaskPath $tp -TaskName $name -ErrorAction SilentlyContinue)
    }
    return @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        $hit = $false
        foreach ($a in @($_.Actions)) {
            $s = "" + $a.Execute + " " + $a.Arguments + " " + $a.WorkingDirectory
            if (($s -match '(?i)main\.py') -and ($s -match '(?i)cts-api[\\/]cts-alarms|priorityalarmsapi')) { $hit = $true }
        }
        $hit
    })
}

function Get-BotFolder($act) {
    # The folder of the main.py the task runs: from the action's arguments, and
    # from its working folder when the arguments name main.py without a folder.
    $wd = ("" + $act.WorkingDirectory).Trim().Trim('"')
    $m = [regex]::Match(("" + $act.Arguments), '(?i)"([^"]*main\.py)"|(\S*main\.py)')
    if (-not $m.Success) { return $wd }
    $p = $m.Groups[1].Value
    if (-not $p) { $p = $m.Groups[2].Value }
    if (-not [IO.Path]::IsPathRooted($p)) {
        if (-not $wd) { return "" }
        $p = Join-Path $wd $p
    }
    return (Split-Path $p -Parent)
}

function Invoke-Backfill([switch]$Send) {
    # python backfill.py --from ... --to ... [--send], run in the bot's folder.
    # Every line is shown as it comes: backfill.py's own OK or FAILED line as
    # it is, the other lines indented. Returns the exit code, the lines and
    # the last line that is not empty.
    $argList = @($script:Backfill, "--from", $script:From, "--to", $script:To)
    if ($Send) { $argList += "--send" }
    $lines = New-Object System.Collections.Generic.List[string]
    $code = $null
    Push-Location -LiteralPath $script:Bot
    $ErrorActionPreference = "Continue"      # a line on stderr must not stop this script
    try {
        & $script:Py @argList 2>&1 | ForEach-Object {
            $line = ("" + $_).TrimEnd()
            $lines.Add($line)
            if ($line -like "OK:*")         { Write-Host $line -ForegroundColor Green }
            elseif ($line -like "FAILED:*") { Write-Host $line -ForegroundColor Red }
            else                            { Write-Host "    | $line" }
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = "Stop"
        Pop-Location
    }
    $last = ""
    foreach ($l in $lines) { if ($l.Trim()) { $last = $l } }
    return @{ Code = $code; Lines = $lines; Last = $last }
}

# ---------------------------------------------------------------------------
# 0. Administrator (secrets.json is readable only by the task's account,
#    Administrators and SYSTEM)
# ---------------------------------------------------------------------------
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Start it with administrator rights: double-click $Stem.cmd and answer Yes."
}

Write-Host ""
Say "Backfill: the alarm bot's events of $From .. $To (server time) to digibuild"

# ---------------------------------------------------------------------------
# 1. The scheduled task that runs the bot: the bot's Python and folder
# ---------------------------------------------------------------------------
$found = @(Find-BotTask $TaskPath $TaskName)
if ($found.Count -eq 0) {
    Fail ("No scheduled task runs the alarm bot's main.py (in C:\cts-api\cts-alarms or C:\priorityalarmsapi). " +
          "If it has another action, name it: $Stem.cmd -TaskPath \<folder> -TaskName <name>")
}
if ($found.Count -gt 1) {
    Fail ("More than one scheduled task runs the alarm bot: " + (($found | ForEach-Object { $_.TaskPath + $_.TaskName }) -join ", ") +
          ". Name the right one: $Stem.cmd -TaskPath \<folder> -TaskName <name>")
}
$task    = $found[0]
$actions = @($task.Actions)
$action  = $actions | Where-Object { ("" + $_.Execute + " " + $_.Arguments + " " + $_.WorkingDirectory) -match '(?i)main\.py' } |
           Select-Object -First 1
if (-not $action) { $action = $actions | Select-Object -First 1 }
if (-not $action) { Fail "The task $($task.TaskPath)$($task.TaskName) has no action." }
Ok ("scheduled task: " + $task.TaskPath + $task.TaskName + " (runs as " + $task.Principal.UserId + ", " +
    $task.State + "); it is left as it is")

$Py = [Environment]::ExpandEnvironmentVariables(("" + $action.Execute).Trim().Trim('"'))
if (-not $Py) { Fail "The task's action names no program." }
if ($Py -match '(?i)pythonw\.exe$') {
    # pythonw.exe shows no output; python.exe of the same installation does.
    $alt = $Py -replace '(?i)pythonw\.exe$', 'python.exe'
    if (Test-Path -LiteralPath $alt -PathType Leaf) { $Py = $alt }
}
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) {
    $cmd = Get-Command $Py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { Fail "Python not found: $Py (taken from the task's action)." }
    $Py = $cmd.Path
}
Ok "Python: $Py"

$Bot = Get-BotFolder $action
if (-not $Bot -or -not (Test-Path -LiteralPath $Bot -PathType Container)) {
    Fail ("Could not find the bot's folder from the task's action (arguments: " + $action.Arguments +
          "; start in: " + $action.WorkingDirectory + ").")
}
$Bot = (Resolve-Path -LiteralPath $Bot).ProviderPath
Ok "bot folder: $Bot"
if ($Bot -ne $RepoApp) { Warn "the task runs the bot from $Bot, not from $RepoApp" }

# ---------------------------------------------------------------------------
# 2. backfill.py
# ---------------------------------------------------------------------------
$Backfill = Join-Path $Bot "backfill.py"
if (-not (Test-Path -LiteralPath $Backfill -PathType Leaf)) {
    Fail "backfill.py is missing in $Bot -- double-click $UpdateCmd first"
}
Ok "backfill.py found"

# backfill.py writes UTF-8 (its OK line has a long dash) and must leave no
# __pycache__ behind.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { Write-Verbose "console encoding unchanged" }

# ---------------------------------------------------------------------------
# 3. The report: reads the CSV files and daily logs, sends nothing
# ---------------------------------------------------------------------------
Write-Host ""
Say "The report: reads the CSV files and daily logs; sends nothing, writes nothing"
$r = Invoke-Backfill
if ($r.Code -ne 0) {
    if ($r.Last -like "FAILED:*") { exit 1 }     # backfill.py's FAILED line above is the last line
    Fail "the report stopped with exit code $($r.Code) (see above). Nothing was sent."
}
if ($r.Last -notlike "OK:*") { Fail "the report ended without its OK line (see above). Nothing was sent." }
$notReady = ""
foreach ($l in $r.Lines) {
    if ($l -match '^\s*sending:\s+NOT READY:\s*(.+)$') { $notReady = $Matches[1].Trim() }
}
if ($notReady) {
    if ($notReady -like "*ingest secret*") {
        Warn "to set the ingest secret, type in Command Prompt (Admin):  $InstallCmd -IngestSecret"
    }
    Fail "sending is not ready: $notReady. Nothing was sent."
}
if ($CheckOnly) {
    Write-Host ""
    Write-Host "OK: report only (-CheckOnly), nothing was sent." -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# 4. Ask
# ---------------------------------------------------------------------------
Write-Host ""
$answer = Read-Host "Send these events to digibuild now? [y/N]"
if (("" + $answer).Trim() -notmatch '^y$') {
    Write-Host "OK: nothing sent" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# 5. Send: the same command with --send; its OK or FAILED line ends the run
# ---------------------------------------------------------------------------
Write-Host ""
Say "Sending the events to digibuild"
$r = Invoke-Backfill -Send
if (($r.Code -eq 0) -and ($r.Last -like "OK:*")) { exit 0 }
if (($r.Code -ne 0) -and ($r.Last -like "FAILED:*")) { exit 1 }
if ($r.Code -eq 0) { Fail "backfill.py --send ended without its OK line (see above). Running this again is safe." }
Fail ("backfill.py --send stopped with exit code $($r.Code) (see above). Some batches may have been sent; " +
      "running this again is safe.")
