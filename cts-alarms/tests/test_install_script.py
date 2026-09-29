"""Guards for install.ps1, the CTS-server installer. The script cannot run
here (no Windows, no Task Scheduler), so these pin its safety rules; if a
`pwsh` binary is on PATH the file is also parse-checked."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "install.ps1"


class InstallScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = PS1.read_bytes()
        cls.text = cls.raw.decode("ascii")          # fails on any non-ASCII byte

    def test_ascii_only(self):
        # Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI; non-ASCII would garble.
        self.assertTrue(all(b < 128 for b in self.raw))

    def test_runs_in_place_and_never_copies_code_elsewhere(self):
        # The bot runs from this folder; the task must point at its main.py.
        self.assertIn('$App      = $PSScriptRoot', self.text)
        self.assertIn('$mainPy = Join-Path $App "main.py"', self.text)
        self.assertNotRegex(self.text, r'Copy-Item[^\n]*main\.py[^\n]*\$OldFolder')
        self.assertNotIn("$InstallFiles", self.text)

    def test_moves_old_data_once_and_only_renames_the_old_folder(self):
        self.assertIn('[string]$OldFolder  = "C:\\priorityalarmsapi"', self.text)
        for need in ('$StateFiles   = @("alarms_state.json", "csv_state.json")',
                     'foreach ($d in @("csv", "logs"))', "copied secrets.json",
                     "Rename-Item -Path $OldFolder"):
            self.assertIn(need, self.text)
        # never deleted, and retired only after the test run
        self.assertNotRegex(self.text, r"Remove-Item[^\n]*\$OldFolder")
        self.assertLess(self.text.index("Ok \"test run clean\""), self.text.index("Rename-Item -Path $OldFolder"))
        # a copy that does not match is a hard stop, the old folder untouched
        self.assertIn("was not copied correctly. $OldFolder is untouched.", self.text)

    def test_never_overwrites_live_server_files(self):
        m = re.search(r'\$CodePatterns = @\(([^)]*)\)', self.text)
        self.assertIsNotNone(m)
        for live in ("secrets.json", "alarms_state.json", "csv_state.json", "mm_token.json"):
            self.assertNotIn(f'"{live}"', m.group(1))

    def test_secrets_written_without_bom_and_locked_with_sids(self):
        self.assertIn("New-Object Text.UTF8Encoding($false)", self.text)
        self.assertIn('"*S-1-5-32-544:(F)"', self.text)      # Administrators, any language
        self.assertIn('"*S-1-5-18:(F)"', self.text)          # SYSTEM
        self.assertIn("/inheritance:r", self.text)

    def test_backup_is_code_and_state_never_secrets(self):
        self.assertIn('[string]$BackupRoot = "C:\\cts-api-backups\\cts-alarms"', self.text)
        self.assertIn("Save-Code $backup", self.text)
        self.assertIn('Lock-Path $BackupRoot "(OI)(CI)(F)"', self.text)

    def test_dry_run_gate_matches_main_py(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for needle in ("[DRY] MainManager credentials OK", "[DRY] nothing written",
                       "credential check FAILED", "Alarm bot started (v"):
            self.assertIn(needle, main, needle)
        self.assertIn('"*[[]DRY] MainManager credentials OK*"', self.text)
        self.assertIn('"*[[]DRY] nothing written*"', self.text)
        self.assertIn('Alarm bot started (v$version,', self.text)
        self.assertIn('config=$cfgPath,', self.text)         # the in-place config.json was used
        self.assertIn('"install.cmd once"', self.text)       # the legacy-state guard counts as a failure

    def test_tls12_for_github(self):
        self.assertIn("[Net.SecurityProtocolType]::Tls12", self.text)

    def test_download_updates_c_cts_api_with_a_way_back(self):
        self.assertIn('[string]$Repo       = "slimyloki/cts-api"', self.text)
        self.assertIn('Invoke-Git @("pull", "--ff-only")', self.text)
        self.assertIn('"safe.directory=*"', self.text)
        self.assertIn('$back = @("-RevertTo", $old)', self.text)
        self.assertIn('$back = @("-RevertFrom", $codeBackup)', self.text)
        self.assertIn('Invoke-Git @("reset", "--hard", $commit)', self.text)
        self.assertIn("The previous code is back.", self.text)
        # the task is disabled before the pull, so no run sees half-updated code
        self.assertLess(self.text.index("Disable-BotTask                      # no scheduled run"),
                        self.text.index('Invoke-Git @("pull", "--ff-only")'))
        root_install = (ROOT.parent / "INSTALL.md")
        if root_install.is_file():                   # inside the cts-api checkout
            txt = root_install.read_text(encoding="utf-8")
            self.assertIn("https://github.com/slimyloki/cts-api/archive/refs/heads/main.zip", txt)
            self.assertIn("git clone https://github.com/slimyloki/cts-api.git C:\\cts-api", txt)

    def test_task_is_found_by_what_it_runs_not_by_name(self):
        # The server's task is \\TacVistaMails\\Alarm_Bot, not the exported name: no name is hard-coded.
        for f in (PS1, ROOT / "status.ps1"):
            t = f.read_text("ascii")
            self.assertNotIn("TACVista_Alarm_Bot", t)
            self.assertNotIn("TACVistaLogs", t)
            self.assertIn("function Find-BotTask", t)
            self.assertRegex(t, r'\[string\]\$TaskName\s*=\s*""')
        # -Download hands the same task to the new installer
        self.assertIn('$argv += @("-TaskName", $TaskName)', self.text)

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_find_bot_task_behaviour(self):
        # Runs the real Find-BotTask from both scripts against a fake Get-ScheduledTask.
        fake = (
            "function New-T($p,$n,$e,$a,$w){ [pscustomobject]@{TaskPath=$p;TaskName=$n;"
            "Actions=@([pscustomobject]@{Execute=$e;Arguments=$a;WorkingDirectory=$w})} }; "
            "$script:all=@("
            "(New-T '\\TacVistaMails\\' 'Alarm_Bot' 'C:\\py\\python.exe' '\"C:\\cts-api\\cts-alarms\\main.py\"' 'C:\\cts-api\\cts-alarms'),"
            "(New-T '\\' 'Indeklima_Bot_v2' 'C:\\py\\python.exe' 'main.py' 'C:\\vista-opc\\indeklima-bot'),"
            "(New-T '\\Other\\' 'Backup' 'robocopy.exe' 'C:\\a C:\\b' ''));"
            "function Get-ScheduledTask { param($TaskPath,$TaskName,$ErrorAction) "
            "  if ($TaskName) { return @($script:all | ? { $_.TaskPath -eq $TaskPath -and $_.TaskName -eq $TaskName }) }; "
            "  return $script:all }; "
        )
        for f in (PS1, ROOT / "status.ps1"):
            cmd = (
                "$ast=[System.Management.Automation.Language.Parser]::ParseFile('" + str(f) + "',[ref]$null,[ref]$null); "
                "$fn=$ast.FindAll({param($x) $x -is [System.Management.Automation.Language.FunctionDefinitionAst] "
                "-and $x.Name -eq 'Find-BotTask'},$true)[0]; . ([scriptblock]::Create($fn.Extent.Text)); " + fake +
                "$TaskPath=''; $TaskName=''; $r=@(Find-BotTask); 'auto=' + ($r | % { $_.TaskPath + $_.TaskName }) + ';' + $r.Count; "
                "$TaskPath='TacVistaMails'; $TaskName='Alarm_Bot'; $r=@(Find-BotTask); 'byname=' + $r.Count; "
                "$TaskPath=''; $TaskName='Nope'; $r=@(Find-BotTask); 'missing=' + $r.Count; "
                "$TaskName=''; $script:all += (New-T '\\Old\\' 'Old_Bot' 'python.exe' 'C:\\priorityalarmsapi\\main.py' ''); "
                "$r=@(Find-BotTask); 'two=' + $r.Count"
            )
            out = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", cmd],
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(out.stdout.split(), ["auto=\\TacVistaMails\\Alarm_Bot;1", "byname=1", "missing=0", "two=2"],
                             f.name + ": " + out.stdout + out.stderr)

    def test_ingest_secret_is_asked_hidden_normalised_and_never_shown(self):
        t = self.text
        self.assertIn("[switch]$IngestSecret", t)
        self.assertIn('if ($IngestSecret) { $argv += "-IngestSecret" }', t)       # survives -Download
        self.assertIn('Read-Host "  Ingest secret" -AsSecureString', t)
        self.assertIn('Read-Host "  Ingest secret again" -AsSecureString', t)
        self.assertIn("-replace '[\\s-]', '').ToLowerInvariant()", t)
        self.assertIn("'^[0-9a-f]{32,}$'", t)
        self.assertIn('$vpsObj["ingest_secret"] = $val', t)
        # asked only when config.json turns shipping on and none is set, or on request
        self.assertIn("if ($IngestSecret -or ($shipOn -and -not $haveIngest))", t)
        # skipping never stops the install: tickets keep being created
        self.assertIn("runs are queued on this server until install.cmd -IngestSecret", t)
        for leak in ("Write-Host $val", "Write-Host $n1", "Ok $val", "$val\"", "Say $val"):
            self.assertNotIn(leak, t)

    def test_status_reports_the_shipper_and_the_alarm_names(self):
        st = (ROOT / "status.ps1").read_text("ascii")
        self.assertIn('"digibuild shipper"', st)
        self.assertIn("install.cmd -IngestSecret", st)
        self.assertIn('Join-Path $App "names.json"', st)

    def test_status_does_not_count_readable_secrets_as_a_problem(self):
        # Owner, 2026-09-29: other Windows users on the server may read secrets.json.
        st = (ROOT / "status.ps1").read_text("ascii")
        self.assertIn("other Windows users can read it (allowed)", st)
        self.assertNotRegex(st, r"Problem [^\n]*read by other users")

    def test_status_script_is_read_only_and_safe(self):
        st = (ROOT / "status.ps1").read_bytes()
        self.assertTrue(all(b < 128 for b in st))
        t = st.decode("ascii")
        for verb in ("Set-Content", "Remove-Item", "Rename-Item", "Copy-Item", "Disable-ScheduledTask",
                     "Enable-ScheduledTask", "Set-ScheduledTask", "icacls", "WriteAllText"):
            self.assertNotIn(verb, t, verb)
        self.assertIn("VERDICT: OK", t)
        self.assertNotRegex(t, r"\$j\.mainmanager\.password\b(?!\))")   # never printed
        cmd = (ROOT / "status.cmd").read_bytes()
        self.assertEqual(cmd.count(b"\n"), cmd.count(b"\r\n"))
        self.assertIn(b'-ExecutionPolicy Bypass -File "%~dp0status.ps1"', cmd)

    def test_cmd_wrappers_keep_crlf_and_call_the_script(self):
        # cmd.exe misparses LF-only batch files; .gitattributes keeps the bytes as committed.
        for name in ("install.cmd", "update.cmd"):
            raw = (ROOT / name).read_bytes()
            self.assertTrue(all(b < 128 for b in raw), name)
            self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), f"{name} must be CRLF only")
        inst = (ROOT / "install.cmd").read_text(encoding="ascii")
        self.assertIn('-ExecutionPolicy Bypass -File "%~dp0install.ps1" %*', inst)
        self.assertIn("-Verb RunAs", inst)
        self.assertIn("fltmc >nul 2>&1", inst)
        self.assertIn('install.cmd" -Download', (ROOT / "update.cmd").read_text(encoding="ascii"))
        attrs = ROOT.parent / ".gitattributes"       # repo root of cts-api
        self.assertIn("*.cmd -text", attrs.read_text(encoding="ascii"))

    def test_install_md_points_at_the_real_files(self):
        md = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        for needle in ("C:\\cts-api\\cts-alarms", "../INSTALL.md", "status.cmd",
                       "install.cmd", "update.cmd", "-ResetSecrets", "-Rollback", "-NoEnable",
                       "C:\\priorityalarmsapi.retired-", "real run complete, nothing deferred, no"):
            self.assertIn(needle, md)
        for needle in ("real run complete, nothing deferred, no errors", "ResetSecrets",
                       "Rollback", "NoEnable"):
            self.assertIn(needle, self.text)

    def test_parses_in_powershell_if_available(self):
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh not installed")
        cmd = ("$n=0; foreach ($f in @('" + str(PS1) + "','" + str(ROOT / 'status.ps1') + "')) { "
               "$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$t,[ref]$e); $n+=$e.Count }; $n")
        out = subprocess.run([pwsh, "-NoProfile", "-Command", cmd],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.stdout.strip(), "0", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
