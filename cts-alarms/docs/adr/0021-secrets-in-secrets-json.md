# ADR-0021: Secrets only in `secrets.json` or the environment, never in `config.json`

- **Status:** Accepted
- **Date:** 2026-09-28 (v2.0.0) · 2026-09-29 (HMAC secret added, BOM tolerance, guard test)
- **Deciders:** Georgi (owner)
- **Supersedes:** [ADR-0013](0013-secrets-in-config-json.md)

## Context

Until v1.1.0 the MainManager service-account credentials were plain-text fields of `config.json`
(ADR-0013). The whole working folder was pushed to GitHub on 2026-09-28; the repository is **public**. The
password is in `config.json` in 8 commits (`13b09b6` … `07778e9`); 7 of them are already on GitHub (`13b09b6`
on `main`, six on `claude/epic-ride-lh3lkz`) and the 8th (`6910a3d`) becomes public when the merged branch is
pushed. It had to be treated as known — the owner **rotated it on 2026-09-29 (~10:45 UTC)**, so the value in the history is dead. The shipper adds a second secret on the same host
(the HMAC ingest secret, [ADR-0020](0020-hmac-ingest-to-digibuild.md)), and the same MainManager account is used
by the Indeklima bot.

## Decision

1. **Credentials live in one git-ignored file on the CTS server**, `C:\priorityalarmsapi\secrets.json`
   (`paths.secrets_file`), shaped like `secrets.example.json`:
   `{"mainmanager": {"username", "password"}, "vps": {"ingest_secret"}}`.
2. **Environment variables win** when set: `MM_USERNAME` + `MM_PASSWORD`, `CTS_ALARMS_INGEST_SECRET`.
3. `config.json` holds **no** credential and stays tracked; a legacy pair there still works for one deploy but
   logs `Credentials: from config.json — DEPRECATED …` (`load_secrets()`, `main.py:61`).
4. **Read with `utf-8-sig`**: Notepad on Windows Server 2016 saves "UTF-8" with a byte-order mark, which plain
   `utf-8` JSON parsing rejects (`load_config()`/`load_secrets()` in `main.py`, `read_ingest_secret()` in
   `shipper.py`). Creating the file with PowerShell `[IO.File]::WriteAllText` avoids the BOM in the first place.
5. **NTFS ACL**: the file is restricted with `icacls` to the scheduled task's account and Administrators
   (inheritance removed) — see [arc42 §7](../arc42/07-deployment-view.md).
6. **Guard in the repo**: `.gitignore` covers `secrets.json` and its variants, token caches, key files, `.env`
   files and config backups (`config.*.json`, `*.bak`); `tests/test_repo_hygiene.py` fails if `config.json`
   gains a credential value, if `secrets.example.json` carries a value, if a secret file stops being ignored, or
   if one is tracked.
7. **Rotate** after exposure: done for the MainManager password on 2026-09-29 ([TODO T-011](../TODO.md)); the new
   value goes into `secrets.json` with the v2.0.1 deploy ([TODO T-103](../TODO.md)) and into the Indeklima bot when
   it is ported ([TODO T-105](../TODO.md)). History purge or making the repo private is a separate owner decision
   ([TODO T-102](../TODO.md)).

Docs and the backlog name secrets **by location only**, never by value.

## Consequences

### Positive
- `config.json` can be edited, reviewed and committed freely.
- Rotation is one edit of one file on one host (plus the Indeklima bot's own config).
- Both secrets of the BMS host sit behind one ACL.

### Negative
- The file is still plain text on the CTS server; anyone with Administrator rights there can read it (as before).
- The old (rotated, dead) password remains in public history until T-102 is decided.
- A dry run within the token lifetime uses `mm_token.json` and does not re-check the password
  ([TODO T-108](../TODO.md)) — delete the token cache before verifying a rotation.

## Alternatives considered

| Option | Why not |
|---|---|
| Environment variables only | Awkward with a stored-password Task Scheduler logon; kept as an override, not the default. |
| DPAPI-protected file / Windows Credential Manager | Ties the secret to the `GPST` profile and adds code; the ACL gives most of the benefit. |
| Git-ignored `config.json` + `config.example.json` | Keeps secrets and settings mixed; every config change becomes an untracked edit on the server. |

## Evidence

- `main.py` v2.1.0: `DEFAULT_SECRETS_PATH` `:48`, `load_config()` `:55`, `load_secrets()` `:61`
- `shipper.py`: `SECRET_ENV`, `read_ingest_secret()`
- `secrets.example.json`, `.gitignore`, `tests/test_main.py::SecretsTests`, `tests/test_repo_hygiene.py`
- Public exposure verified via the GitHub API on 2026-09-28 and 2026-09-29
