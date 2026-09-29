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

    def test_installs_exactly_the_code_files(self):
        m = re.search(r'\$InstallFiles\s*=\s*@\(([^)]*)\)', self.text)
        self.assertIsNotNone(m)
        files = re.findall(r'"([^"]+)"', m.group(1))
        self.assertEqual(files, ["main.py", "shipper.py", "config.json", "secrets.example.json"])
        for f in files:
            self.assertTrue((ROOT / f).is_file(), f)

    def test_never_overwrites_live_server_files(self):
        m = re.search(r'\$InstallFiles\s*=\s*@\(([^)]*)\)', self.text)
        for live in ("objects.csv", "exceptions.csv", "secrets.json", "alarms_state.json",
                     "csv_state.json"):
            self.assertNotIn(live, m.group(1))

    def test_secrets_written_without_bom_and_locked_with_sids(self):
        self.assertIn("New-Object Text.UTF8Encoding($false)", self.text)
        self.assertIn('"*S-1-5-32-544:(F)"', self.text)      # Administrators, any language
        self.assertIn('"*S-1-5-18:(F)"', self.text)          # SYSTEM
        self.assertIn("/inheritance:r", self.text)

    def test_backup_skips_secrets_and_token(self):
        self.assertIn('@("secrets.json", "mm_token.json") -notcontains $_.Name', self.text)

    def test_dry_run_gate_matches_main_py(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        for needle in ("[DRY] MainManager credentials OK", "[DRY] nothing written",
                       "credential check FAILED", "Alarm bot started (v"):
            self.assertIn(needle, main, needle)
        self.assertIn('"*[[]DRY] MainManager credentials OK*"', self.text)
        self.assertIn('"*[[]DRY] nothing written*"', self.text)
        self.assertIn('Alarm bot started (v$version,', self.text)

    def test_tls12_for_github(self):
        self.assertIn("[Net.SecurityProtocolType]::Tls12", self.text)

    def test_download_updates_the_cts_api_folder(self):
        self.assertIn('[string]$Repo       = "slimyloki/cts-api"', self.text)
        self.assertIn("pull --ff-only", self.text)
        self.assertIn('"safe.directory=*"', self.text)
        self.assertIn("$repoRoot = Split-Path $appDir -Parent", self.text)
        root_install = (ROOT.parent / "INSTALL.md")
        if root_install.is_file():                   # inside the cts-api checkout
            txt = root_install.read_text(encoding="utf-8")
            self.assertIn("https://github.com/slimyloki/cts-api/archive/refs/heads/main.zip", txt)
            self.assertIn("git clone https://github.com/slimyloki/cts-api.git C:\\cts-api", txt)

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
        for needle in ("C:\\cts-api\\cts-alarms", "../INSTALL.md",
                       "install.cmd", "update.cmd", "-ResetSecrets", "-Rollback", "-NoEnable",
                       "real run complete, nothing deferred, no"):
            self.assertIn(needle, md)
        for needle in ("real run complete, nothing deferred, no errors", "-ResetSecrets",
                       "-Rollback", "-NoEnable"):
            self.assertIn(needle, self.text)

    def test_parses_in_powershell_if_available(self):
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh not installed")
        cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::"
               f"ParseFile('{PS1}',[ref]$t,[ref]$e);$e.Count")
        out = subprocess.run([pwsh, "-NoProfile", "-Command", cmd],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.stdout.strip(), "0", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
