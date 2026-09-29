<#
.SYNOPSIS
    Install or update the TAC Vista alarm bot on the CTS server. The bot runs in
    place from this folder (C:\cts-api\cts-alarms); everything it needs lives here.

.DESCRIPTION
    Easiest: double-click install.cmd next to this file. Or, in PowerShell started
    as Administrator:

        powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\cts-alarms\install.ps1

    What it does, in order. It stops at the first problem and says what to do:
      1. Finds the scheduled task that runs the bot (by its action: a main.py in
         C:\cts-api\cts-alarms or C:\priorityalarmsapi -- the task's name does not
         matter), makes sure it runs THIS folder's main.py (repoints it if it can),
         and checks that its Python has 'requests'.
      2. Disables the task and waits until no run is in progress.
      3. Backs up the code, config and state files of this folder (not logs\ or
         csv\, never secrets.json) to C:\cts-api-backups\cts-alarms\<date-time>\.
      4. ONCE: moves the bot's data out of the old folder C:\priorityalarmsapi into
         this folder: alarms_state.json, csv_state.json, csv\, logs\ and
         secrets.json. The old folder is renamed to C:\priorityalarmsapi.retired-<date>
         only after the test run in step 7 has passed.
      5. Keeps secrets.json here if it holds a MainManager username and password,
         otherwise asks for them; writes it without a byte-order mark and locks it
         with icacls to the task's account, Administrators and SYSTEM.
      6. Deletes mm_token.json and mm_auth_failed.json so the test really logs in.
      7. Runs 'main.py --dry-run' and checks: this version in the banner, this
         folder's config.json, "[DRY] MainManager credentials OK", "[DRY] nothing
         written", no errors. A dry run changes nothing.
      8. Asks, then enables the task, starts one real run and shows its summary.

.PARAMETER Download
    Update first, then install: 'git pull' when C:\cts-api is a Git clone,
    otherwise the cts-api ZIP from GitHub copied over C:\cts-api. If the updated
    version fails the test run, the previous code is put back. update.cmd runs this.

.PARAMETER ResetSecrets
    Ask for the MainManager username and password again (keeps a "vps" section).

.PARAMETER IngestSecret
    Ask for the digibuild ingest secret again (vps.ingest_secret in secrets.json).
    It is also asked for, once, when config.json has a "vps" section and
    secrets.json has no ingest secret yet. Press Enter to skip: the runs are then
    queued on this server until it is set.

.PARAMETER NoEnable
    Stop after a good test run and leave the task disabled.

.PARAMETER Yes
    Do not ask before enabling the task.

.PARAMETER Rollback
    Put back the code of the newest backup, then enable the task. State, logs and
    secrets.json are left as they are.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\cts-alarms\install.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\cts-api\cts-alarms\install.ps1 -ResetSecrets
#>
[CmdletBinding()]
param(
    [string]$OldFolder  = "C:\priorityalarmsapi",
    [string]$BackupRoot = "C:\cts-api-backups\cts-alarms",
    [string]$TaskPath   = "",     # optional: normally the task is found by what it runs
    [string]$TaskName   = "",
    [string]$Repo       = "slimyloki/cts-api",
    [string]$Branch     = "main",
    [switch]$Download,
    [switch]$ResetSecrets,
    [switch]$IngestSecret,
    [switch]$NoEnable,
    [switch]$Yes,
    [switch]$Rollback,
    [string]$RevertTo   = "",     # set by -Download: git commit to return to if the test fails
    [string]$RevertFrom = ""      # set by -Download: code backup to return to (ZIP updates)
)

Set-StrictMode -Version 1
$ErrorActionPreference = "Stop"

$App      = $PSScriptRoot
$RepoRoot = Split-Path $App -Parent
$IsGit    = Test-Path (Join-Path $RepoRoot ".git")
$GitCmd   = Get-Command git.exe -ErrorAction SilentlyContinue
# Files that are code or config (tracked in the repo); everything else here is data.
$CodePatterns = @("*.py", "config.json", "secrets.example.json", "objects.csv", "exceptions.csv", "*.ps1", "*.cmd", "*.md", "*.xml")
$StateFiles   = @("alarms_state.json", "csv_state.json")
$script:TaskDisabled = $false
$script:WasEnabled   = $true

function Say([string]$msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok([string]$msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }

function Fail([string]$msg) {
    Write-Host ""
    Write-Host "FAILED: $msg" -ForegroundColor Red
    if ($script:TaskDisabled) {
        Write-Host ""
        Write-Host "The scheduled task is DISABLED. Fix the problem and double-click install.cmd again, or:" -ForegroundColor Yellow
        Write-Host "  go back to the previous code: install.cmd -Rollback"
        Write-Host "  just re-enable the task:      Enable-ScheduledTask -TaskPath '$TaskPath' -TaskName '$TaskName'"
    }
    exit 1
}

function Find-BotTask {
    # The task is found by WHAT it runs (the alarm bot's main.py in C:\cts-api\cts-alarms
    # or the old C:\priorityalarmsapi), not by its name: the name differs between servers.
    if ($script:TaskName) {
        # Given by hand: accept "TacVistaMails", "\TacVistaMails" or "\TacVistaMails\".
        $tp = "\" + ($script:TaskPath + "").Trim().Trim('\')
        if ($tp -ne "\") { $tp += "\" }
        return @(Get-ScheduledTask -TaskPath $tp -TaskName $script:TaskName -ErrorAction SilentlyContinue)
    }
    return @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        $hit = $false
        foreach ($a in @($_.Actions)) {
            $s = "" + $a.Execute + " " + $a.Arguments + " " + $a.WorkingDirectory
            if (($s -match '(?i)main\.py') -and ($s -match '(?i)cts-api\\cts-alarms|priorityalarmsapi')) { $hit = $true }
        }
        $hit
    })
}

function Get-BotTask {
    $t = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Fail "Scheduled task $TaskPath$TaskName not found." }
    return $t
}

function Disable-BotTask {
    $script:WasEnabled = ((Get-BotTask).State -ne "Disabled")
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

function Invoke-Native([string]$exe, [string[]]$argList) {
    $ErrorActionPreference = "Continue"
    $out = & $exe @argList 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    return @{ Code = $code; Out = $out }
}

function Invoke-Git([string[]]$argList) {
    # safe.directory: the clone may belong to another account than the elevated one.
    return Invoke-Native $GitCmd.Source (@("-c", "safe.directory=*", "-C", $RepoRoot) + $argList)
}

function Lock-Path([string]$path, [string]$rights) {
    $r = Invoke-Native "icacls.exe" @($path, "/inheritance:r", "/grant:r",
        "${script:RunAs}:$rights", "*S-1-5-32-544:$rights", "*S-1-5-18:$rights")
    if ($r.Code -ne 0) { Fail "icacls could not set permissions on ${path}: $($r.Out -join ' ')" }
}

function Save-Code([string]$dest) {
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    foreach ($p in $CodePatterns) {
        Get-ChildItem -Path $App -Filter $p -File -ErrorAction SilentlyContinue |
            Copy-Item -Destination $dest -Force
    }
}

function Restore-Code([string]$fromDir, [string]$commit) {
    if ($commit -and $IsGit -and $GitCmd) {
        $r = Invoke-Git @("reset", "--hard", $commit)
        if ($r.Code -ne 0) { Warn "git reset to $commit failed: $($r.Out -join ' ')"; return $false }
        Ok "code back at commit $commit"
        return $true
    }
    if ($fromDir -and (Test-Path $fromDir)) {
        Get-ChildItem $fromDir -File | Where-Object { $StateFiles -notcontains $_.Name -and $_.Name -ne "commit.txt" } |
            Copy-Item -Destination $App -Force
        Ok "code restored from $fromDir"
        return $true
    }
    Warn "no previous code to go back to"
    return $false
}

# ---------------------------------------------------------------------------
# 0. Administrator, TLS 1.2, the task
# ---------------------------------------------------------------------------
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Start it with administrator rights: double-click install.cmd and answer Yes."
}
# PowerShell 5.1 on Server 2016 does not offer TLS 1.2 by default; GitHub needs it.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$found = Find-BotTask
if ($found.Count -eq 0) {
    Fail ("No scheduled task runs the alarm bot's main.py (in C:\cts-api\cts-alarms or C:\priorityalarmsapi). " +
          "If it has another action, name it: install.cmd -TaskPath \<folder>\ -TaskName <name>")
}
if ($found.Count -gt 1) {
    Fail ("More than one scheduled task runs the alarm bot: " + (($found | ForEach-Object { $_.TaskPath + $_.TaskName }) -join ", ") +
          ". Disable the extra one(s) in Task Scheduler, or name the right one with -TaskPath/-TaskName.")
}
$task     = $found[0]
$TaskPath = $task.TaskPath
$TaskName = $task.TaskName
Ok "scheduled task: $TaskPath$TaskName"
$action = $task.Actions | Select-Object -First 1
$py     = $action.Execute.Trim('"')
$script:RunAs = $task.Principal.UserId
if (-not $script:RunAs) { Fail "The task has no user account (Principal.UserId is empty)." }

# ---------------------------------------------------------------------------
# -Download: update C:\cts-api, then run the NEW install.ps1 (with a way back)
# ---------------------------------------------------------------------------
if ($Download) {
    Say "Updating $RepoRoot"
    Disable-BotTask                      # no scheduled run on half-updated code
    $wasEnabled = $script:WasEnabled
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $back  = @()
    if ($IsGit -and $GitCmd) {
        $old = ((Invoke-Git @("rev-parse", "HEAD")).Out -join "").Trim()
        $r = Invoke-Git @("pull", "--ff-only")
        $r.Out | ForEach-Object { Write-Host "    | $_" }
        if ($r.Code -ne 0) {
            if ($wasEnabled) { Enable-BotTask }
            Fail "git pull failed (see above). Nothing was changed."
        }
        $back = @("-RevertTo", $old)
    } else {
        $codeBackup = Join-Path $BackupRoot "code-$stamp"
        Save-Code $codeBackup
        $tmp = Join-Path $env:TEMP "cts-api-$stamp"
        $zip = "$tmp.zip"
        $url = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"
        Say "Downloading $url (C:\cts-api is not a Git clone)"
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
        if (-not (Test-Path (Join-Path $inner.FullName "cts-alarms\install.ps1"))) {
            if ($wasEnabled) { Enable-BotTask }
            Fail "The download has no cts-alarms\install.ps1. Nothing was changed."
        }
        Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $RepoRoot -Recurse -Force
        Remove-Item $zip, $tmp -Recurse -Force
        Ok "copied the newest $Branch over $RepoRoot"
        $back = @("-RevertFrom", $codeBackup)
    }
    $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $App "install.ps1")) + $back
    if ($ResetSecrets) { $argv += "-ResetSecrets" }
    if ($IngestSecret) { $argv += "-IngestSecret" }
    if ($NoEnable)     { $argv += "-NoEnable" }
    if ($Yes)          { $argv += "-Yes" }
    # The same task again. No backslashes at the ends (a trailing one would escape the
    # closing quote) and no empty argument (Windows PowerShell drops it).
    $tpArg = $TaskPath.Trim('\')
    if ($tpArg) { $argv += @("-TaskPath", $tpArg) }
    $argv += @("-TaskName", $TaskName)
    & powershell.exe @argv
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# -Rollback: the newest backup's code back in place
# ---------------------------------------------------------------------------
if ($Rollback) {
    $last = Get-ChildItem $BackupRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d{8}-\d{6}$' } |
            Sort-Object Name -Descending | Select-Object -First 1
    if (-not $last) { Fail "No backup found in $BackupRoot." }
    Say "Rolling back the code to $($last.FullName)"
    Disable-BotTask
    $commit = ""
    $cf = Join-Path $last.FullName "commit.txt"
    if (Test-Path $cf) { $commit = (Get-Content $cf -Raw).Trim() }
    if (-not (Restore-Code $last.FullName $commit)) { Fail "Rollback failed." }
    Enable-BotTask
    Say "Rolled back. State, logs and secrets.json were left as they are."
    exit 0
}

# ---------------------------------------------------------------------------
# 1. This folder, the task, Python
# ---------------------------------------------------------------------------
Say "Bot folder: $App"
foreach ($f in @("main.py", "config.json")) {
    if (-not (Test-Path (Join-Path $App $f))) { Fail "$f is missing in $App." }
}
$vm = Select-String -Path (Join-Path $App "main.py") -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $vm) { Fail "Could not read __version__ from main.py." }
$version = $vm.Matches[0].Groups[1].Value
Ok "version: v$version"

$mainPy = Join-Path $App "main.py"
$args0  = ($action.Arguments + "").Trim().Trim('"')
$wd0    = ($action.WorkingDirectory + "").Trim().Trim('"').TrimEnd('\')
$inPlace = ($args0 -eq $mainPy) -or (($args0 -eq "main.py") -and ($wd0 -eq $App))
if (-not $inPlace) {
    Warn "the task runs '$($action.Arguments)' -- repointing it to $mainPy"
    try {
        $newAction = New-ScheduledTaskAction -Execute $action.Execute -Argument "`"$mainPy`"" -WorkingDirectory $App
        Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $newAction | Out-Null
        Ok "task now runs $mainPy"
    } catch {
        Fail ("Windows did not let the script change the task ($($_.Exception.Message)). Change it by hand: " +
              "Task Scheduler > Task Scheduler Library" + ($TaskPath.TrimEnd('\') -replace '\\', ' > ') + " > $TaskName > Properties > Actions > Edit: " +
              "'Add arguments' = `"$mainPy`", 'Start in' = $App. Then double-click install.cmd again.")
    }
} else {
    Ok "task runs $mainPy as '$($script:RunAs)'"
}
if (-not (Test-Path $py)) { Fail "Python not found at $py (taken from the task)." }
$r = Invoke-Native $py @("-c", "import requests, sqlite3, hmac, uuid")
if ($r.Code -ne 0) { Fail "Python cannot import 'requests'. Install it: & '$py' -m pip install requests" }
Ok "Python has requests"

# ---------------------------------------------------------------------------
# 2. Stop the task
# ---------------------------------------------------------------------------
Say "Disabling the task"
Disable-BotTask

# ---------------------------------------------------------------------------
# 3. Backup of code, config and state (never secrets.json, not logs\ or csv\)
# ---------------------------------------------------------------------------
$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $BackupRoot $stamp
Say "Backing up to $backup"
if (-not (Test-Path $BackupRoot)) {
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    Lock-Path $BackupRoot "(OI)(CI)(F)"
}
Save-Code $backup
foreach ($f in $StateFiles) {
    $p = Join-Path $App $f
    if (Test-Path $p) { Copy-Item $p $backup -Force }
}
if ($IsGit -and $GitCmd) {
    ((Invoke-Git @("rev-parse", "HEAD")).Out -join "").Trim() | Set-Content (Join-Path $backup "commit.txt") -Encoding ASCII
}
Ok "$((Get-ChildItem $backup -File).Count) files backed up"

# ---------------------------------------------------------------------------
# 4. Once: move the data out of the old folder C:\priorityalarmsapi
# ---------------------------------------------------------------------------
$migrated = $false
$oldState = Join-Path $OldFolder "alarms_state.json"
$oldUser  = $null
if (Test-Path (Join-Path $OldFolder "config.json")) {
    try {
        $oc = Get-Content (Join-Path $OldFolder "config.json") -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($oc.mainmanager -and $oc.mainmanager.username) { $oldUser = [string]$oc.mainmanager.username }
    } catch { Write-Verbose "old config.json unreadable" }
}
if ((Test-Path $oldState) -and -not (Test-Path (Join-Path $App "alarms_state.json"))) {
    Say "Moving the bot's data from $OldFolder into $App (once)"
    foreach ($f in $StateFiles) {
        $p = Join-Path $OldFolder $f
        if (Test-Path $p) { Copy-Item $p $App -Force; Ok "copied $f" }
    }
    foreach ($d in @("csv", "logs")) {
        $from = Join-Path $OldFolder $d
        if (Test-Path $from) {
            $to = Join-Path $App $d
            New-Item -ItemType Directory -Path $to -Force | Out-Null
            Copy-Item -Path (Join-Path $from "*") -Destination $to -Recurse -Force
            Ok "copied $d\ ($((Get-ChildItem $to -Recurse -File).Count) files)"
        }
    }
    $oldSecrets = Join-Path $OldFolder "secrets.json"
    if ((Test-Path $oldSecrets) -and -not (Test-Path (Join-Path $App "secrets.json"))) {
        Copy-Item $oldSecrets $App -Force
        Ok "copied secrets.json"
    }
    foreach ($f in @("objects.csv", "exceptions.csv")) {
        $o = Join-Path $OldFolder $f
        $n = Join-Path $App $f
        if ((Test-Path $o) -and (Test-Path $n) -and ((Get-FileHash $o).Hash -ne (Get-FileHash $n).Hash)) {
            Copy-Item $o $n -Force
            Warn "$f on the server differed from the repo; the server's version is kept (git will show it as changed -- report it so the repo gets the same file)"
        }
    }
    foreach ($f in $StateFiles) {
        $o = Join-Path $OldFolder $f
        if ((Test-Path $o) -and ((Get-FileHash $o).Hash -ne (Get-FileHash (Join-Path $App $f)).Hash)) {
            Fail "$f was not copied correctly. $OldFolder is untouched."
        }
    }
    $migrated = $true
} elseif (Test-Path $oldState) {
    Ok "data already in $App; $OldFolder will be retired after the test"
    $migrated = $true
} else {
    Ok "nothing to move ($OldFolder has no state)"
}

# ---------------------------------------------------------------------------
# 5. secrets.json (in this folder, git-ignored)
# ---------------------------------------------------------------------------
$secretsPath = Join-Path $App "secrets.json"
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
        if (-not $p1)        { Warn "empty password, try again" }
        elseif ($p1 -ne $p2) { Warn "the two passwords differ, try again" }
        else                 { $plain = $p1 }
        $p1 = $null; $p2 = $null
    }
    if (-not $plain) { Fail "No password set." }
    $obj = [ordered]@{ mainmanager = [ordered]@{ username = $u; password = $plain } }
    if ($existing -and $existing.vps) { $obj["vps"] = $existing.vps }
    $json = $obj | ConvertTo-Json -Depth 5
    # UTF-8 WITHOUT a byte-order mark.
    [IO.File]::WriteAllText($secretsPath, $json, (New-Object Text.UTF8Encoding($false)))
    $plain = $null; $json = $null; $obj = $null
    Ok "secrets.json written"
} else {
    Ok "secrets.json present, kept (use install.cmd -ResetSecrets to change it)"
}
# The digibuild ingest secret: asked for when config.json turns the shipper on
# ("vps" section) and secrets.json has none yet, or with -IngestSecret. It is
# typed by hand (the owner cannot paste into this server): 32 characters 0-9 a-f,
# spaces and dashes ignored, asked twice. Never shown, never logged.
$cfgObj = $null
try { $cfgObj = Get-Content (Join-Path $App "config.json") -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $cfgObj = $null }
$shipOn = [bool]($cfgObj -and $cfgObj.vps -and ($cfgObj.vps.enabled -ne $false))
$cur = $null
try { $cur = Get-Content $secretsPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $cur = $null }
$haveIngest = [bool]($cur -and $cur.vps -and $cur.vps.ingest_secret)
if ($IngestSecret -or ($shipOn -and -not $haveIngest)) {
    Say "digibuild ingest secret (stored only in $secretsPath)"
    Write-Host "  Type the 32 characters shown on the VPS. Spaces and dashes are ignored."
    Write-Host "  Press Enter to skip: the runs are then kept on this server until it is set."
    $val = $null
    $skipped = $false
    for ($i = 0; $i -lt 3 -and -not $val -and -not $skipped; $i++) {
        $s1 = Read-Host "  Ingest secret" -AsSecureString
        $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1)
        try { $n1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b1) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1) }
        $n1 = ($n1 -replace '[\s-]', '').ToLowerInvariant()
        if (-not $n1) { $skipped = $true; break }
        $s2 = Read-Host "  Ingest secret again" -AsSecureString
        $b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2)
        try { $n2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b2) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2) }
        $n2 = ($n2 -replace '[\s-]', '').ToLowerInvariant()
        if ($n1 -ne $n2)                       { Warn "the two differ, try again" }
        elseif ($n1 -notmatch '^[0-9a-f]{32,}$') { Warn "that is not it: 32 or more characters, only 0-9 and a-f; try again" }
        else                                   { $val = $n1 }
        $n1 = $null; $n2 = $null
    }
    if ($val) {
        $obj = [ordered]@{}
        if ($cur) { foreach ($pr in $cur.PSObject.Properties) { if ($pr.Name -ne "vps") { $obj[$pr.Name] = $pr.Value } } }
        $vpsObj = [ordered]@{}
        if ($cur -and $cur.vps) { foreach ($pr in $cur.vps.PSObject.Properties) { $vpsObj[$pr.Name] = $pr.Value } }
        $vpsObj["ingest_secret"] = $val
        $obj["vps"] = $vpsObj
        $json = $obj | ConvertTo-Json -Depth 5
        [IO.File]::WriteAllText($secretsPath, $json, (New-Object Text.UTF8Encoding($false)))
        $val = $null; $json = $null; $obj = $null
        Ok "ingest secret written to secrets.json"
    } else {
        Warn "no ingest secret set: runs are queued on this server until install.cmd -IngestSecret"
    }
} elseif ($shipOn) {
    Ok "ingest secret present (use install.cmd -IngestSecret to change it)"
}

# Every time: readable only by the task account, Administrators and SYSTEM (SIDs
# work on a Danish-language Windows too).
$r = Invoke-Native "icacls.exe" @($secretsPath, "/inheritance:r", "/grant:r", "${script:RunAs}:(R)", "*S-1-5-32-544:(F)", "*S-1-5-18:(F)")
if ($r.Code -ne 0) { Fail "icacls could not lock secrets.json: $($r.Out -join ' ')" }
Ok "secrets.json readable only by $($script:RunAs), Administrators and SYSTEM"

foreach ($f in @("mm_token.json", "mm_auth_failed.json")) {
    $p = Join-Path $App $f
    if (Test-Path $p) { Remove-Item $p -Force; Ok "removed $f" }
}

# ---------------------------------------------------------------------------
# 6. Test run (--dry-run): reads everything, writes nothing, one login
# ---------------------------------------------------------------------------
Say "Test run (--dry-run)"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { Write-Verbose "console encoding unchanged" }
Push-Location $App
$r = Invoke-Native $py @($mainPy, "--dry-run")
Pop-Location
$r.Out | ForEach-Object { Write-Host "    | $_" }
$text = $r.Out -join "`n"
$cfgPath = Join-Path $App "config.json"

$problems = @()
if ($r.Code -ne 0) { $problems += "main.py exited with code $($r.Code)" }
if ($text -notlike "*Alarm bot started (v$version,*") { $problems += "the banner does not show v$version" }
if ($text -notlike "*config=$cfgPath,*") { $problems += "main.py did not use $cfgPath" }
if ($text -notlike "*[[]DRY] MainManager credentials OK*") { $problems += "the MainManager login was not confirmed" }
if ($text -notlike "*[[]DRY] nothing written*") { $problems += "no '[DRY] nothing written' line" }
foreach ($bad in @("credential check FAILED", "no MainManager credentials", "DEPRECATED", "Unhandled exception", "Traceback", "install.cmd once")) {
    if ($text -like "*$bad*") { $problems += "output contains '$bad'" }
}
if ($problems.Count -gt 0) {
    $hint = ""
    if ($text -like "*credential check FAILED*") { $hint = " If the password is wrong, run install.cmd -ResetSecrets." }
    if ($RevertTo -or $RevertFrom) {
        Warn "the updated code failed the test -- putting the previous code back"
        [void](Restore-Code $RevertFrom $RevertTo)
        if ($script:WasEnabled -and -not $NoEnable) { Enable-BotTask }
        Fail ("test run not clean after the update: " + ($problems -join "; ") + ". The previous code is back." + $hint)
    }
    Fail ("test run not clean: " + ($problems -join "; ") + "." + $hint)
}
Ok "test run clean"
foreach ($l in @($r.Out | Where-Object { $_ -like "*[[]DRY] VPS *" })) {
    $fact = ($l -replace '^.*\[DRY\] ', '')
    if ($fact -like "*MISSING*" -or ($fact -like "*reachability*" -and $fact -notlike "*HTTP 200*")) { Warn $fact } else { Ok $fact }
}

# ---------------------------------------------------------------------------
# 7. Retire the old folder (renamed, not deleted)
# ---------------------------------------------------------------------------
if ($migrated -and (Test-Path $OldFolder)) {
    $retired = "$OldFolder.retired-" + (Get-Date -Format "yyyyMMdd")
    try {
        Rename-Item -Path $OldFolder -NewName (Split-Path $retired -Leaf)
        Ok "$OldFolder renamed to $retired (not used any more; delete it when you like)"
    } catch {
        Warn "could not rename $OldFolder ($($_.Exception.Message)); it is not used any more, rename or delete it by hand"
    }
}

# ---------------------------------------------------------------------------
# 8. Enable and one real run
# ---------------------------------------------------------------------------
if ($NoEnable) {
    Say "Stopping here (-NoEnable). The task is DISABLED; double-click install.cmd to go live."
    exit 0
}
if (-not $Yes) {
    $a = Read-Host "Test OK. Enable the task and start one real run now? [Y/n]"
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
if ($state -eq "Running") { Warn "the run is still going after 10 minutes; check with status.cmd later" }
else { Ok "run finished, task result code $($info.LastTaskResult) (0 = OK)" }

$logFile = Join-Path (Join-Path $App "logs") ((Get-Date -Format "yyyy-MM-dd") + ".log")
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
        if (-not $done) { Warn "no 'Run complete' line yet -- double-click status.cmd in a minute" }
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
Say "Done. v$version runs from $App and the task is enabled."
Write-Host "    Backup of the previous code and state: $backup"
Write-Host "    Is it working?   double-click status.cmd"
Write-Host "    Later updates:   double-click update.cmd"
exit 0
