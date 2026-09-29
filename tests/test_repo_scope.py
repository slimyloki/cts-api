"""Repo-wide guards for cts-api (ADR-0001). The repo is PUBLIC and holds only
what must be on the CTS server. These checks run on every commit; the
cts-api-scope-guard agent runs them before each push and adds the judgement
calls a test cannot make."""
import ipaddress
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = {".gitattributes", ".gitignore", "README.md", "INSTALL.md"}
ROOT_DIRS = {"docs", "runbooks", "tests"}

# Files that only make sense on the VPS / Vercel side (never on the CTS server).
VPS_ONLY_NAMES = re.compile(
    r"(^|/)(package\.json|pnpm-lock\.yaml|package-lock\.json|next\.config\.[a-z]+|vercel\.json|"
    r"Caddyfile|[^/]*\.caddy|Dockerfile|docker-compose\.ya?ml|[^/]*\.service|[^/]*\.timer|"
    r"[^/]*\.socket)$")
SECRET_NAMES = re.compile(
    r"(^|/)(secrets\.json|secrets\.[^/]*\.json|[^/]*token[^/]*\.json|\.env(\.[^/]*)?|[^/]*\.env|"
    r"[^/]*\.(pem|key|pfx|p12))$")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
IP_ALLOWED = {"127.0.0.1", "0.0.0.0"}
VPS_PATHS = re.compile(r"/home/admin|/etc/(caddy|systemd|cts-alarms|mm-reports)|systemctl ")


def tracked():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                         capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


class RepoScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.files = tracked()
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise unittest.SkipTest("not a git checkout")
        # A fresh file that is staged counts too: include untracked-but-not-ignored files.
        extra = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--others",
                                "--exclude-standard"], capture_output=True).stdout
        cls.files += [p for p in extra.decode("utf-8").split("\0") if p]

    def test_top_level_is_apps_plus_repo_files(self):
        tops = {p.split("/", 1)[0] for p in self.files}
        apps = tops - ROOT_FILES - ROOT_DIRS
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for app in apps:
            self.assertTrue((ROOT / app).is_dir(), f"unexpected top-level file {app}")
            self.assertIn(f"`{app}/`", readme, f"{app}/ missing from the README application table")
            for need in ("README.md", "INSTALL.md"):
                self.assertTrue((ROOT / app / need).is_file(), f"{app}/{need} missing")

    def test_no_secret_files(self):
        leaked = [p for p in self.files if SECRET_NAMES.search(p)
                  and not p.endswith(("secrets.example.json", ".env.example"))]
        self.assertEqual(leaked, [])

    def test_no_vps_only_files(self):
        self.assertEqual([p for p in self.files if VPS_ONLY_NAMES.search(p)], [])

    def test_no_ip_addresses_or_vps_paths(self):
        bad = []
        for p in self.files:
            f = ROOT / p
            if not f.is_file() or f.stat().st_size > 2_000_000:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for m in IPV4.finditer(text):
                ip = m.group(0)
                try:
                    addr = ipaddress.ip_address(ip)
                except ValueError:
                    continue                      # e.g. a version number like 10.0.14393.0
                if ip in IP_ALLOWED or ipaddress.ip_network("192.0.2.0/24").supernet_of(
                        ipaddress.ip_network(f"{ip}/32")):
                    continue                      # loopback / documentation range
                bad.append(f"{p}: {ip}")
            if p.startswith("tests/"):
                continue                          # this file names the patterns it forbids
            for m in VPS_PATHS.finditer(text):
                bad.append(f"{p}: {m.group(0)}")
        self.assertEqual(bad, [])

    def test_windows_scripts_are_safe_for_windows(self):
        for p in self.files:
            f = ROOT / p
            if p.endswith((".cmd", ".bat")):
                raw = f.read_bytes()
                self.assertTrue(all(b < 128 for b in raw), f"{p} must be ASCII")
                self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), f"{p} must be CRLF only")
            elif p.endswith(".ps1"):
                self.assertTrue(all(b < 128 for b in f.read_bytes()), f"{p} must be ASCII")

    def test_every_runbook_is_a_complete_set(self):
        stems = {}
        for p in self.files:
            if p.startswith("runbooks/") and p.count("/") == 1 and p != "runbooks/README.md":
                stem, _, ext = p[len("runbooks/"):].rpartition(".")
                stems.setdefault(stem, set()).add(ext)
        index = (ROOT / "runbooks" / "README.md").read_text(encoding="utf-8")
        for stem, exts in stems.items():
            self.assertEqual(exts, {"md", "ps1", "cmd"}, f"runbooks/{stem}: need .md, .ps1 and .cmd")
            self.assertRegex(stem, r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+$", stem)
            self.assertIn(stem, index, f"runbooks/{stem} missing from runbooks/README.md index")


if __name__ == "__main__":
    unittest.main()
