"""Repo hygiene guards. The GitHub repo is public: a credential committed here
is published. These tests fail before such a commit can pass CI or review."""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRED_KEYS = re.compile(r"(?i)^(password|passwd|pwd|secret|ingest_secret|api_key|apikey|"
                       r"token|access_token|client_secret|username)$")


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else k)
            yield f"{path}.{k}" if path else k, k, v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


class RepoHygieneTests(unittest.TestCase):
    def test_config_json_holds_no_credentials(self):
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
        bad = [p for p, k, v in walk(cfg)
               if CRED_KEYS.match(str(k)) and isinstance(v, str) and v.strip()]
        self.assertEqual(bad, [], "credentials belong in secrets.json, never config.json")

    def test_secrets_example_is_empty(self):
        ex = json.loads((ROOT / "secrets.example.json").read_text(encoding="utf-8-sig"))
        filled = [p for p, k, v in walk(ex)
                  if not str(k).startswith("_") and isinstance(v, str) and v.strip()]
        self.assertEqual(filled, [], "secrets.example.json must carry names only")

    def test_secret_files_are_ignored(self):
        try:
            out = subprocess.run(
                ["git", "-C", str(ROOT), "check-ignore", "secrets.json", "mm_token.json",
                 "vps_api_key.txt", "outbox.sqlite", "config.json.bak", ".env"],
                capture_output=True, text=True, check=False)
        except FileNotFoundError:
            self.skipTest("git not available")
        if out.returncode == 128:
            self.skipTest("not a git checkout")
        self.assertEqual(len(out.stdout.split()), 6, out.stdout)

    def test_no_secret_files_tracked(self):
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                                 capture_output=True, text=True, check=True).stdout.split("\n")
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        leaked = [f for f in out if re.search(
            r"(^|/)(secrets\.json|mm_token\.json|vps_api_key\.txt|\.env)$", f)]
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
