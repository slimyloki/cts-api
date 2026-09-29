<#
.SYNOPSIS
    Install or update the TAC Vista alarm bot on the CTS server from the cts-api repo.

.DESCRIPTION
    The cts-api repo lives in C:\cts-api on the CTS server (see C:\cts-api\INSTALL.md);
    this script is C:\cts-api\cts-alarms\install.ps1. Easiest: double-click install.cmd
    next to it. Or, in PowerShell started as Administrator:

        powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\cts-alarms\install.ps1

    What it does, in order. It stops at the first problem and says what to do:
      1. Reads the scheduled task (\TACVistaLogs\TACVista_Alarm_Bot) to find the
         Python it runs and the account it runs as. Checks that Python has 'requests'.
      2. Disables the task and waits until no run is in progress.
      3. Backs up the bot folder's files (not logs\ or csv\, not secrets.json)
         to C:\priorityalarmsapi-backups\<date-time>\.
      4. Copies main.py, shipper.py, config.json and secrets.example.json into
         C:\priorityalarmsapi. It never touches objects.csv, exceptions.csv,
         the state files, logs\ or csv\ -- the server's copies are the live ones.
      5. Creates secrets.json if it is missing (asks for the MainManager username
         and password), writes it without a byte-order mark, and locks it with
         icacls to the task's account, Administrators and SYSTEM.
      6. Deletes mm_token.json so the next check really logs in.
      7. Runs 'main.py --dry-run' and checks its output: the new version in the
         banner, "[DRY] MainManager credentials OK", "[DRY] nothing written",
         and no errors. A dry run writes nothing.
      8. Asks, then enables the task, starts one real run, waits for it and
         shows that run's summary from today's log.

    If anything fails after step 2, the task is left DISABLED and the script
    prints how to fix it or roll back.

.PARAMETER Download
    Bring the cts-api repo folder (the parent of this script's folder, normally
    C:\cts-api) up to date first, then run the updated install.ps1:
    'git pull' when the folder is a Git clone and git.exe is on PATH; otherwise
    download https://github.com/slimyloki/cts-api (branch main) as a ZIP and
    copy it over the folder. update.cmd runs this.

.PARAMETER ResetSecrets
    Ask for the MainManager username and password again and rewrite
    secrets.json (keeps its "vps" section if it has one).

.PARAMETER NoEnable
    Stop after a good dry run and leave the task disabled.

.PARAMETER Yes
    Do not ask before enabling the task and starting the real run.

.PARAMETER Rollback
    Restore main.py, shipper.py and config.json from the newest backup,
    then enable the task again. Nothing else changes.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\cts-alarms\install.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\cts-alarms\install.ps1 -Download

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\cts-alarms\install.ps1 -ResetSecrets
#>
[CmdletBinding()]
param(
    [string]$Target     = "C:\priorityalarmsapi",
    [string]$TaskPath   = "\TACVistaLogs\",
    [string]$TaskName   = "TACVista_Alarm_Bot",
    [string]$Repo       = "slimyloki/cts-api",
    [string]$Branch     = "main",
    [switch]$Download,
    [switch]$ResetSecrets,
    [switch]$NoEnable,
    [switch]$Yes,
    [switch]$Rollback
)

Set-StrictMode -Version 1
$ErrorActionPreference = "Stop"

# Files this script installs. Everything else in the bot folder is left alone.
$InstallFiles = @("main.py", "shipper.py", "config.json", "secrets.example.json")
$BackupRoot   = "$Target-backups"
$script:TaskDisabled = $false

function Say([string]$msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok([string]$msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }

function Fail([string]$msg) {
    Write-Host ""
    Write-Host "FAILED: $msg" -ForegroundColor Red
    if ($script:TaskDisabled) {
        Write-Host ""
        Write-Host "The scheduled task is DISABLED. When fixed, run this script again, or:" -ForegroundColor Yellow
        Write-Host "  roll back:      powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Rollback"
        Write-Host "  just re-enable: Enable-ScheduledTask -TaskPath '$TaskPath' -TaskName '$TaskName'"
    }
    exit 1
}

function Get-BotTask {
    $t = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Fail "Scheduled task $TaskPath$TaskName not found." }
    return $t
}

function Disable-BotTask {
    Disable-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName | Out-Null
    $script:TaskDisabled = $true
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-BotTask).State -eq "Running") {
        if ((Get-Date) -gt $deadline) { Fail "A run of the task is still in progress after 3 minutes." }
        Start-Sleep -Seconds 5
    }
    Ok "task disabled, no run in progress"
}

function Enable-BotTask {
    Enable-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName | Out-Null
    $script:TaskDisabled = $false
    Ok "task enabled (runs every 5 minutes)"
}

# ---------------------------------------------------------------------------
# 0. Administrator, TLS 1.2
# ---------------------------------------------------------------------------
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Start PowerShell with 'Run as administrator' and run this script again."
}
# PowerShell 5.1 on Server 2016 does not offer TLS 1.2 by default; GitHub needs it.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# ---------------------------------------------------------------------------
# -Download: bring the cts-api folder up to date, then run ITS install.ps1
# ---------------------------------------------------------------------------
if ($Download) {
    $appDir   = $PSScriptRoot
    $appName  = Split-Path $appDir -Leaf
    $repoRoot = Split-Path $appDir -Parent
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ((Test-Path (Join-Path $repoRoot ".git")) -and $git) {
        Say "Updating $repoRoot with git pull"
        $ErrorActionPreference = "Continue"
        # safe.directory: the clone may belong to another account than the elevated one.
        $pullOut = & $git.Source -c "safe.directory=*" -C $repoRoot pull --ff-only 2>&1
        $pullCode = $LASTEXITCODE
        $ErrorActionPreference = "Stop"
        $pullOut | ForEach-Object { Write-Host "    | $_" }
        if ($pullCode -ne 0) { Fail "git pull failed in $repoRoot (see above). Nothing was installed." }
    } else {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $tmp   = Join-Path $env:TEMP "cts-api-$stamp"
        $zip   = "$tmp.zip"
        $url   = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"
        Say "Downloading $url (no Git clone at $repoRoot)"
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
        if (-not (Test-Path (Join-Path $inner.FullName "$appName\install.ps1"))) {
            Fail "The download has no $appName\install.ps1."
        }
        Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $repoRoot -Recurse -Force
        Remove-Item $zip, $tmp -Recurse -Force
        Ok "copied the newest $Branch over $repoRoot"
    }
    $next = Join-Path $appDir "install.ps1"
    $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $next,
              "-Target", $Target, "-TaskPath", $TaskPath, "-TaskName", $TaskName)
    if ($ResetSecrets) { $argv += "-ResetSecrets" }
    if ($NoEnable)     { $argv += "-NoEnable" }
    if ($Yes)          { $argv += "-Yes" }
    & powershell.exe @argv
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# -Rollback: newest backup back in place
# ---------------------------------------------------------------------------
if ($Rollback) {
    $last = Get-ChildItem $BackupRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -First 1
    if (-not $last) { Fail "No backup found in $BackupRoot." }
    Say "Rolling back to $($last.FullName)"
    [void](Get-BotTask)
    Disable-BotTask
    foreach ($f in @("main.py", "shipper.py", "config.json")) {
        $from = Join-Path $last.FullName $f
        $to   = Join-Path $Target $f
        if (Test-Path $from) { Copy-Item $from $to -Force; Ok "restored $f" }
        elseif ($f -eq "shipper.py" -and (Test-Path $to)) { Remove-Item $to -Force; Ok "removed shipper.py (not in that backup)" }
    }
    Enable-BotTask
    Say "Rolled back. secrets.json was left as it is."
    exit 0
}

# ---------------------------------------------------------------------------
# 1. Source files, task, Python
# ---------------------------------------------------------------------------
$src = $PSScriptRoot
Say "Source: $src"
foreach ($f in $InstallFiles) {
    if (-not (Test-Path (Join-Path $src $f))) { Fail "$f is missing next to install.ps1 -- run the script from C:\cts-api\cts-alarms." }
}
$vm = Select-String -Path (Join-Path $src "main.py") -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $vm) { Fail "Could not read __version__ from main.py." }
$version = $vm.Matches[0].Groups[1].Value
Ok "version to install: v$version"

$task   = Get-BotTask
$action = $task.Actions | Select-Object -First 1
$py     = $action.Execute.Trim('"')
$runAs  = $task.Principal.UserId
if (-not $runAs) { Fail "The task has no user account (Principal.UserId is empty); cannot set file permissions." }
Ok "task $TaskPath$TaskName runs '$py' as '$runAs'"
if (-not (Test-Path $py)) { Fail "Python not found at $py (taken from the task)." }
if ($action.WorkingDirectory -and ($action.WorkingDirectory.TrimEnd('\') -ne $Target.TrimEnd('\'))) {
    Warn "the task's working directory is '$($action.WorkingDirectory)', not '$Target'"
}
if (-not (Test-Path $Target)) { Fail "Bot folder $Target not found." }

$ErrorActionPreference = "Continue"
& $py -c "import requests, sqlite3, hmac, uuid" 2>&1 | Out-Null
$pyOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $pyOk) { Fail "Python cannot import 'requests'. Install it: & '$py' -m pip install requests" }
Ok "Python has requests"

# Old config: remember the MainManager username (older configs carried it).
$oldUser = $null
$oldCfgPath = Join-Path $Target "config.json"
if (Test-Path $oldCfgPath) {
    try {
        $oldCfg = Get-Content $oldCfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($oldCfg.mainmanager -and $oldCfg.mainmanager.username) { $oldUser = [string]$oldCfg.mainmanager.username }
    } catch { Warn "the current config.json could not be read as JSON; it is replaced anyway (backup first)" }
}

# ---------------------------------------------------------------------------
# 2. Stop the task
# ---------------------------------------------------------------------------
Say "Disabling the task"
Disable-BotTask

# ---------------------------------------------------------------------------
# 3. Backup (top-level files only; never the secrets or the token cache)
# ---------------------------------------------------------------------------
$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $BackupRoot $stamp
Say "Backing up to $backup"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
# Only the task account, Administrators and SYSTEM may read the backup (it holds
# the state files and the previous config.json). SIDs work on any Windows language.
$ErrorActionPreference = "Continue"
$aclOut = & icacls $backup /inheritance:r /grant:r "${runAs}:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "*S-1-5-18:(OI)(CI)(F)" 2>&1
$aclCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($aclCode -ne 0) { Fail "icacls could not restrict the backup folder: $aclOut" }
Get-ChildItem $Target -File |
    Where-Object { @("secrets.json", "mm_token.json") -notcontains $_.Name } |
    Copy-Item -Destination $backup
Ok "$((Get-ChildItem $backup -File).Count) files backed up"

# ---------------------------------------------------------------------------
# 4. Install the files
# ---------------------------------------------------------------------------
Say "Installing v$version into $Target"
foreach ($f in $InstallFiles) {
    Copy-Item (Join-Path $src $f) (Join-Path $Target $f) -Force
    Ok "copied $f"
}

# ---------------------------------------------------------------------------
# 5. secrets.json
# ---------------------------------------------------------------------------
$secretsPath = Join-Path $Target "secrets.json"
$existing = $null
$needSecrets = [bool]$ResetSecrets
if (Test-Path $secretsPath) {
    try { $existing = Get-Content $secretsPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Warn "secrets.json is not valid JSON -- asking for the credentials again"; $needSecrets = $true }
    if ($existing -and -not ($existing.mainmanager -and $existing.mainmanager.username -and $existing.mainmanager.password)) {
        Warn "secrets.json has no MainManager username/password -- asking for them"
        $needSecrets = $true
    }
} else {
    $needSecrets = $true
}

if ($needSecrets) {
    Say "MainManager credentials (stored only in $secretsPath)"
    $defaultUser = $oldUser
    if ($existing -and $existing.mainmanager -and $existing.mainmanager.username) { $defaultUser = [string]$existing.mainmanager.username }
    if ($defaultUser) { $u = Read-Host "  Username [$defaultUser]" } else { $u = Read-Host "  Username" }
    if (-not $u) { $u = $defaultUser }
    if (-not $u) { Fail "No username given." }
    $plain = $null
    for ($i = 0; $i -lt 3 -and -not $plain; $i++) {
        $s1 = Read-Host "  Password (the NEW one)" -AsSecureString
        $s2 = Read-Host "  Password again" -AsSecureString
        $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1)
        $b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2)
        try {
            $p1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b1)
            $p2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b2)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1)
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
        }
        if (-not $p1)          { Warn "empty password, try again" }
        elseif ($p1 -ne $p2)   { Warn "the two passwords differ, try again" }
        else                   { $plain = $p1 }
        $p1 = $null; $p2 = $null
    }
    if (-not $plain) { Fail "No password set." }
    $obj = [ordered]@{ mainmanager = [ordered]@{ username = $u; password = $plain } }
    if ($existing -and $existing.vps) { $obj["vps"] = $existing.vps }
    $json = $obj | ConvertTo-Json -Depth 5
    # UTF-8 WITHOUT a byte-order mark (Notepad on Server 2016 adds one).
    [IO.File]::WriteAllText($secretsPath, $json, (New-Object Text.UTF8Encoding($false)))
    $plain = $null; $json = $null; $obj = $null
    Ok "secrets.json written"
} else {
    Ok "secrets.json present, kept (use -ResetSecrets to change it)"
}

# Lock it down every time: task account read, Administrators + SYSTEM full.
# SIDs, so this also works on a Danish-language Windows ("Administratorer").
$ErrorActionPreference = "Continue"
$icaclsOut = & icacls $secretsPath /inheritance:r /grant:r "${runAs}:(R)" "*S-1-5-32-544:(F)" "*S-1-5-18:(F)" 2>&1
$icaclsCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($icaclsCode -ne 0) { Fail "icacls could not set permissions on secrets.json: $icaclsOut" }
Ok "secrets.json readable only by $runAs, Administrators and SYSTEM"

$token = Join-Path $Target "mm_token.json"
if (Test-Path $token) { Remove-Item $token -Force; Ok "old mm_token.json removed" }

# ---------------------------------------------------------------------------
# 6. Dry run
# ---------------------------------------------------------------------------
Say "Dry run (reads everything, writes nothing, one login to MainManager)"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { Write-Verbose "console encoding unchanged (no console host)" }
Push-Location $Target
$ErrorActionPreference = "Continue"
$out  = & $py main.py --dry-run 2>&1 | ForEach-Object { "$_" }
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
Pop-Location
$out | ForEach-Object { Write-Host "    | $_" }
$text = $out -join "`n"

$problems = @()
if ($code -ne 0) { $problems += "main.py exited with code $code" }
if ($text -notlike "*Alarm bot started (v$version,*") { $problems += "the banner does not show v$version" }
if ($text -notlike "*[[]DRY] MainManager credentials OK*") { $problems += "the MainManager login was not confirmed" }
if ($text -notlike "*[[]DRY] nothing written*") { $problems += "no '[DRY] nothing written' line" }
foreach ($bad in @("credential check FAILED", "no MainManager credentials", "DEPRECATED", "Unhandled exception", "Traceback")) {
    if ($text -like "*$bad*") { $problems += "output contains '$bad'" }
}
if ($problems.Count -gt 0) {
    $hint = ""
    if ($text -like "*credential check FAILED*") { $hint = " If the password is wrong, run again with -ResetSecrets." }
    Fail ("dry run not clean: " + ($problems -join "; ") + "." + $hint)
}
Ok "dry run clean"

# ---------------------------------------------------------------------------
# 7. Enable and one real run
# ---------------------------------------------------------------------------
if ($NoEnable) {
    Say "Stopping here (-NoEnable). The task is DISABLED; run the script again without -NoEnable to go live."
    exit 0
}
if (-not $Yes) {
    $a = Read-Host "Dry run OK. Enable the task and start one real run now? [Y/n]"
    if ($a -match '^(n|no|nej)$') {
        Say "Stopped. The task is DISABLED. Enable it with: Enable-ScheduledTask -TaskPath '$TaskPath' -TaskName '$TaskName'"
        exit 0
    }
}

Say "Enabling the task and starting a real run"
$before = (Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName).LastRunTime
Enable-BotTask
Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
$deadline = (Get-Date).AddMinutes(10)
do {
    Start-Sleep -Seconds 5
    $info  = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName
    $state = (Get-BotTask).State
} while ((($info.LastRunTime -le $before) -or ($state -eq "Running")) -and ((Get-Date) -lt $deadline))
if ($state -eq "Running") { Warn "the run is still going after 10 minutes; check the log later" }
else { Ok "run finished, task result code $($info.LastTaskResult) (0 = OK)" }

# Show that run's lines from today's log.
$logFolder = Join-Path $Target "logs"
try {
    $cfg = Get-Content (Join-Path $Target "config.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.paths.log_folder) { $logFolder = [string]$cfg.paths.log_folder }
} catch { Write-Verbose "config.json unreadable here; using $logFolder" }
$logFile = Join-Path $logFolder ((Get-Date -Format "yyyy-MM-dd") + ".log")
if (Test-Path $logFile) {
    $lines = @(Get-Content $logFile -Encoding UTF8)
    $start = -1
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        if ($lines[$i] -like "*Alarm bot started (v*dry_run=False*") { $start = $i; break }
    }
    if ($start -ge 0) {
        $run = $lines[$start..($lines.Count - 1)]
        $run | Where-Object { $_ -match "Alarm bot started|CREATED|UPDATED|RESOLVED|ERROR|Run complete" } |
            Select-Object -Last 60 | ForEach-Object { Write-Host "    | $_" }
        $errs = @($run | Where-Object { $_ -match "\bERROR\b" }).Count
        $done = $run | Where-Object { $_ -like "*Run complete:*" } | Select-Object -Last 1
        if (-not $done) { Warn "no 'Run complete' line yet -- look at $logFile" }
        elseif ($done -notlike "*deferred=0*") { Warn "some actions were deferred: $done" }
        elseif ($errs -gt 0) { Warn "$errs ERROR line(s) in this run -- read them above" }
        else { Ok "real run complete, nothing deferred, no errors" }
    } else {
        Warn "could not find the real run in $logFile"
    }
} else {
    Warn "log file $logFile not found"
}

Write-Host ""
Say "Done. v$version is installed and the task is enabled."
Write-Host "    Backup of the previous files: $backup"
Write-Host "    Roll back if needed:          powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Rollback"
Write-Host "    Later updates:                powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Download"
exit 0
