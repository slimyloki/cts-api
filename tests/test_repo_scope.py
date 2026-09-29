"""Repo-wide guards for cts-api (ADR-0001). The repo is PUBLIC and holds only
what must be on the CTS server. These checks run on every commit; the
cts-api-scope-guard agent runs them before each push and adds the judgement
calls a test cannot make."""
import hashlib
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
VPS_PATHS = re.compile(r"/home/admin|/etc/(caddy|systemd|cts-alarms|mm-reports)|systemctl |"
                       r"\b[\w@-]+\.(service|timer|socket)\b|localhost:\d+|127\.0\.0\.1:\d+|"
                       r"EnvironmentFile|/var/lib/|/srv/|/opt/|vps-docs/")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}(?![\w:])")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")
EMAIL_OK = re.compile(r"@(example\.(com|org|net|dk)|[\w.-]+\.test|users\.noreply\.github\.com|anthropic\.com)$")
TOKEN_FORMATS = re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|\bsk-[A-Za-z0-9-]{20,}|"
                           r"xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
SECRET_VALUE = re.compile(r"(?i)[\"']?(password|passwd|pwd|secret|ingest_secret|token|api_?key)[\"']?\s*[:=]\s*"
                          r"[\"']([^\"'\s]{4,})[\"']")
SECRET_VALUE_OK = re.compile(r"^(YOUR_[A-Z_]+|<[^>]+>|\$\{?\w+\}?|p|cp|ep|fp)$")
# "LOGIN (Full Name Org)": the shape of a Vista operator label (personal data).
OPERATOR_LABEL = re.compile(r"\b[A-Z][A-Z0-9-]{1,9} \([A-Z\u00c6\u00d8\u00c5][a-z\u00e6\u00f8\u00e5]+(?: [A-Za-z\u00c6\u00d8\u00c5\u00e6\u00f8\u00e5]+)+\)")
LABEL_OK = {"OPR1 (Operator One FM)", "SYSTEM (User Profile SYSTEM)",
            "LOGIN (Name Organisation)", "INITIALS (Name Organisation)"}   # placeholders
# Owner's own label: allowed until the owner decides (see the scope-guard audit of 2026-09-29).
LABEL_OK_HASHES = {"cfd466044c0110f70851257b16ff2bd5729601bb146857ddc6a49bc388fd60c4"}
# SHA-256 of lower-cased words that must never appear (internal hostnames, third-party
# operator names). Only hashes are stored, so this file publishes nothing.
DENY_HASHES = {
    "1c84dbee3d356670db21e045b05c431d414ce019b5984854d28161f6b2aee40b",
    "e99b6632e6e8689e3c8fe63072493ad7130921c5d51593c4fae2af12899b7c37",
    "eae8e3404182477f008479b38a6629ee86b23cefe2c418c5dc5c2f0d02af5475",
    "f6c4b2f6ffe3b021b378c40abc75cfd32cac97903cecd60c6c3f363c4fb70f64",
}
FORBIDDEN_FILES = re.compile(
    r"(^|/)(\.claude/|logs/|csv/|bin/|obj/)|\.(log|alr|sqlite|db|dll|exe|pdb)$|_state\.json$|(^|/)outbox[^/]*$")


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

    def test_no_forbidden_files(self):
        self.assertEqual([p for p in self.files if FORBIDDEN_FILES.search(p)], [])

    def test_contents_no_secrets_emails_labels_or_denied_words(self):
        bad = []
        for p in self.files:
            f = ROOT / p
            if not f.is_file() or f.stat().st_size > 2_000_000:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            is_guard = p.startswith("tests/")
            for m in TOKEN_FORMATS.finditer(text):
                if not is_guard:
                    bad.append(f"{p}: token-like string at offset {m.start()}")
            for m in SECRET_VALUE.finditer(text):
                if not SECRET_VALUE_OK.match(m.group(2)) and p.endswith((".json", ".xml", ".ps1", ".cmd", ".config")):
                    bad.append(f"{p}: value for '{m.group(1)}'")
            for m in EMAIL.finditer(text):
                if not EMAIL_OK.search(m.group(0)):
                    bad.append(f"{p}: e-mail address")
            for m in IPV6.finditer(text):
                try:
                    addr = ipaddress.ip_address(m.group(0))
                except ValueError:
                    continue
                if not (addr.is_loopback or addr in ipaddress.ip_network("2001:db8::/32")):
                    bad.append(f"{p}: IPv6 address")
            for m in ([] if is_guard else OPERATOR_LABEL.finditer(text)):
                label = m.group(0)
                if label in LABEL_OK or hashlib.sha256(label.lower().encode()).hexdigest() in LABEL_OK_HASHES:
                    continue
                bad.append(f"{p}: operator-style label")
            if not is_guard:
                for w in set(re.findall(r"[A-Za-z0-9\u00c6\u00d8\u00c5\u00e6\u00f8\u00e5]{3,}", text)):
                    if hashlib.sha256(w.lower().encode()).hexdigest() in DENY_HASHES:
                        bad.append(f"{p}: a denied word (internal hostname or third-party name)")
        self.assertEqual(sorted(set(bad)), [])

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
