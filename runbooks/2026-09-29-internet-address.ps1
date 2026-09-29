<#
.SYNOPSIS
    Read-only: which internet address does this server use, can it reach
    api.digibuild.dk, and is its clock close enough for the shipper's signature?

.DESCRIPTION
    Double-click 2026-09-29-internet-address.cmd next to this file. Nothing is
    changed. The address is only shown on the screen: tell it to the person
    setting up digibuild (it goes into the VPS's allow-list, never into this
    public repo).
#>
[CmdletBinding()]
param(
    [string]$Api = "https://api.digibuild.dk"
)
Set-StrictMode -Version 1
$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$failed = @()
function Show([string]$k, [string]$v) { Write-Host ("  {0,-22} {1}" -f $k, $v) }

Write-Host ""
Write-Host "Internet address check  $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor Cyan

# 1. The public address, as two independent services see it. One may answer
#    with an IPv6 address: both kinds are shown, the allow-list needs each one used.
$seen = @()
foreach ($u in @("https://api.ipify.org", "https://icanhazip.com")) {
    try {
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 15
        $cand = ("" + $r.Content).Trim()
        if ($cand -match '^[0-9A-Fa-f:.]{3,45}$') { $seen += $cand; Show "seen by $(([Uri]$u).Host)" $cand }
        else { Show "seen by $(([Uri]$u).Host)" "unexpected answer" }
    } catch {
        Show "seen by $(([Uri]$u).Host)" ("no answer (" + $_.Exception.Message + ")")
    }
}
$seen = @($seen | Select-Object -Unique)
$ip = $seen -join "  and  "
if (-not $ip) { $failed += "no service could tell the internet address (is outbound HTTPS blocked?)" }

# 2. api.digibuild.dk: reachable? (404 before the CTS part is deployed is fine)
$date = $null
$code = $null
try {
    $r = Invoke-WebRequest -Uri "$Api/api/cts-alarms/healthz" -UseBasicParsing -TimeoutSec 15
    $code = [int]$r.StatusCode
    $date = $r.Headers["Date"]
} catch {
    $resp = $_.Exception.Response
    if ($resp) {
        $code = [int]$resp.StatusCode
        $date = $resp.Headers["Date"]
    } else {
        $failed += "api.digibuild.dk cannot be reached: $($_.Exception.Message)"
    }
}
if ($code) {
    $meaning = switch ($code) { 200 { "ready" } 404 { "reachable; the CTS part is not deployed yet" } default { "reachable" } }
    Show "api.digibuild.dk" "HTTP $code ($meaning)"
}

# 3. The clock: the shipper's signature is refused beyond 300 seconds.
if ($date) {
    try {
        $server = [DateTime]::Parse($date).ToUniversalTime()
        $skew = [int][Math]::Round(((Get-Date).ToUniversalTime() - $server).TotalSeconds)
        Show "clock difference" "$skew s (must stay within 300 s)"
        if ([Math]::Abs($skew) -gt 240) { $failed += "this server's clock is $skew s off; fix the time (w32tm /resync) before the shipper is switched on" }
    } catch { Show "clock difference" "could not read the Date header" }
}

Write-Host ""
if ($ip) {
    Write-Host "  THIS SERVER'S INTERNET ADDRESS:  $ip" -ForegroundColor Green
    if ($seen.Count -gt 1) { Write-Host "  (two addresses: tell both; the server may use either one)" }
    Write-Host "  Tell it to the person setting up digibuild. Do not commit it to the repo."
}
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "OK internet address found, api.digibuild.dk reachable, clock fine." -ForegroundColor Green
    exit 0
}
$failed | ForEach-Object { Write-Host "  * $_" -ForegroundColor Yellow }
Write-Host "FAILED: $($failed.Count) problem(s), see above." -ForegroundColor Red
exit 1
