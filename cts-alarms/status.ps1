<#
.SYNOPSIS
    Is the alarm bot working? Read-only check of the task, the files in this
    folder (C:\cts-api\cts-alarms) and today's log, ending with a VERDICT.

.DESCRIPTION
    Double-click status.cmd next to this file. Nothing is changed. No secret is
    shown: for secrets.json it only says whether a username and password are set
    and whether other users could read the file.
#>
[CmdletBinding()]
param(
    [string]$TaskPath  = "\TACVistaLogs\",
    [string]$TaskName  = "TACVista_Alarm_Bot",
    [string]$OldFolder = "C:\priorityalarmsapi"
)
Set-StrictMode -Version 1
$ErrorActionPreference = "Continue"

$App = $PSScriptRoot
$problems = New-Object System.Collections.ArrayList
function Line([string]$k, [string]$v) { Write-Host ("  {0,-22} {1}" -f $k, $v) }
function Problem([string]$what, [string]$fix) { [void]$problems.Add("$what`n      -> $fix") }

Write-Host ""
Write-Host "Alarm bot status  $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor Cyan

# --- the task ---------------------------------------------------------------
$task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Problem "Scheduled task $TaskPath$TaskName not found." "Recreate it: it is needed before install.cmd can run (report it)."
} else {
    $info = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName
    $act  = $task.Actions | Select-Object -First 1
    Write-Host "Task" -ForegroundColor Cyan
    Line "state" $task.State
    Line "runs as" $task.Principal.UserId
    Line "program" $act.Execute
    Line "arguments" $act.Arguments
    Line "start in" $act.WorkingDirectory
    Line "last run" ("{0}  (result {1}, 0 = OK)" -f $info.LastRunTime, $info.LastTaskResult)
    Line "next run" $info.NextRunTime
    if ($task.State -eq "Disabled") { Problem "The task is disabled." "Double-click install.cmd (it tests, then enables)." }
    $argPath = ($act.Arguments + "").Trim().Trim('"')
    $wd = ($act.WorkingDirectory + "").Trim().Trim('"').TrimEnd('\')
    $mainPy = Join-Path $App "main.py"
    if (-not (($argPath -eq $mainPy) -or ($argPath -eq "main.py" -and $wd -eq $App))) {
        Problem "The task does not run $mainPy." "Double-click install.cmd (it repoints the task)."
    }
    if ($info.LastTaskResult -ne 0 -and $info.LastTaskResult -ne 267009 -and $info.LastTaskResult -ne 267011) {
        Problem "The last run ended with code $($info.LastTaskResult)." "See the log lines below."
    }
}

# --- this folder ------------------------------------------------------------
Write-Host "Folder $App" -ForegroundColor Cyan
$vm = Select-String -Path (Join-Path $App "main.py") -Pattern '^__version__\s*=\s*"([^"]+)"' -ErrorAction SilentlyContinue | Select-Object -First 1
$version = if ($vm) { $vm.Matches[0].Groups[1].Value } else { "?" }
Line "version" "v$version"

$sec = Join-Path $App "secrets.json"
if (-not (Test-Path $sec)) {
    Line "secrets.json" "MISSING"
    Problem "secrets.json is missing." "Double-click install.cmd (it asks for the MainManager password)."
} else {
    $ok = $false
    try {
        $j = Get-Content $sec -Raw -Encoding UTF8 | ConvertFrom-Json
        $ok = [bool]($j.mainmanager -and $j.mainmanager.username -and $j.mainmanager.password)
    } catch { $ok = $false }
    $acl = (Get-Acl $sec).Access | ForEach-Object { $_.IdentityReference.Value }
    $open = @($acl | Where-Object { $_ -match 'Everyone|Authenticated Users|\\Users$|^BUILTIN\\Users|Brugere|Alle' })
    Line "secrets.json" ("present, username+password " + $(if ($ok) { "set" } else { "NOT set" }) + $(if ($open.Count) { ", readable by other users!" } else { ", locked" }))
    if (-not $ok) { Problem "secrets.json has no MainManager username/password." "Double-click install.cmd." }
    if ($open.Count) { Problem "secrets.json can be read by other users." "Double-click install.cmd (it locks the file)." }
}
foreach ($f in @("alarms_state.json", "csv_state.json")) {
    $p = Join-Path $App $f
    Line $f $(if (Test-Path $p) { "present, changed " + (Get-Item $p).LastWriteTime.ToString("yyyy-MM-dd HH:mm") } else { "missing" })
}
$marker = Join-Path $App "mm_auth_failed.json"
if (Test-Path $marker) {
    $age = [int]((Get-Date) - (Get-Item $marker).LastWriteTime).TotalMinutes
    Line "login backoff" "ACTIVE (MainManager rejected the login $age min ago)"
    Problem "MainManager rejected the login." "The password is wrong: double-click install.cmd -ResetSecrets (type it in Command Prompt (Admin) in this folder)."
}
if (Test-Path (Join-Path $OldFolder "alarms_state.json")) {
    Line "old folder" "$OldFolder still holds the bot's state"
    if (-not (Test-Path (Join-Path $App "alarms_state.json"))) {
        Problem "The bot's data is still in $OldFolder." "Double-click install.cmd once: it moves it here."
    }
}

# --- today's log --------------------------------------------------------------
$log = Join-Path (Join-Path $App "logs") ((Get-Date -Format "yyyy-MM-dd") + ".log")
Write-Host "Log $log" -ForegroundColor Cyan
if (-not (Test-Path $log)) {
    Line "today" "no log yet"
    if ($task -and $task.State -ne "Disabled") { Problem "No log for today in $App\logs." "The bot has not run from this folder today: double-click install.cmd." }
} else {
    $lines = @(Get-Content $log -Encoding UTF8)
    $start = -1
    for ($i = $lines.Count - 1; $i -ge 0; $i--) { if ($lines[$i] -like "*Alarm bot started (v*dry_run=False*") { $start = $i; break } }
    if ($start -lt 0) {
        Line "last real run" "none today"
    } else {
        $run = $lines[$start..($lines.Count - 1)]
        $banner = $run[0]
        Line "last real run" ($banner.Substring(0, [Math]::Min(19, $banner.Length)))
        $cred = $run | Where-Object { $_ -like "*Credentials:*" } | Select-Object -First 1
        if ($cred) { Line "credentials" ($cred -replace '^.*Credentials:\s*', '') }
        $auth = @($run | Where-Object { $_ -like "*API AUTH: HTTP*" } | ForEach-Object { ($_ -replace '^.*API AUTH: HTTP\s*', '').Trim() })
        Line "MainManager login" $(if ($auth.Count) { "HTTP " + ($auth -join ", ") } else { "cached token (no new login)" })
        $errs = @($run | Where-Object { $_ -match '\bERROR\b' })
        Line "errors in that run" $errs.Count
        $done = $run | Where-Object { $_ -like "*Run complete:*" } | Select-Object -Last 1
        Line "summary" $(if ($done) { $done -replace '^.*Run complete:\s*', '' } else { "no 'Run complete' line" })
        if ($auth | Where-Object { $_ -match '^(400|401|403)' }) {
            Problem "MainManager rejected the login (HTTP $($auth -join ', '))." "The password is wrong: install.cmd -ResetSecrets."
        }
        if ($run -match 'install\.cmd once') { Problem "The bot's data is still in $OldFolder." "Double-click install.cmd once." }
        if ($errs.Count -gt 0) {
            Write-Host "  error lines:" -ForegroundColor Yellow
            $errs | Select-Object -Last 5 | ForEach-Object { Write-Host "    $_" }
            if (-not $problems.Count) { Problem "The last run logged $($errs.Count) error(s)." "Read the error lines above and report what they say." }
        }
        if ($done -and $done -notlike "*deferred=0*") { Problem "Some actions were deferred: $($done -replace '^.*Run complete:\s*', '')." "They are retried on the next run; if it stays, read the error lines." }
    }
    $gave = @($lines | Where-Object { $_ -like "*giving up*" }).Count
    if ($gave) { Line "given up today" "$gave transition(s) (those ticket lines are lost; nothing to do)" }
}

# --- verdict ------------------------------------------------------------------
Write-Host ""
if ($problems.Count -eq 0) {
    Write-Host "VERDICT: OK -- the bot runs v$version from $App." -ForegroundColor Green
    exit 0
}
Write-Host "VERDICT: $($problems.Count) problem(s)" -ForegroundColor Red
$problems | ForEach-Object { Write-Host "  * $_" -ForegroundColor Yellow }
exit 1
