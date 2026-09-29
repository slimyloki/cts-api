# ADR-0013: Secrets in `config.json`

- **Status:** **Superseded by [ADR-0021](0021-secrets-in-secrets-json.md)** (2026-09-29)
- **Date:** 2026-09-28 (decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`) · 2026-09-29 (superseded)
- **Deciders:** Georgi (owner)

> **2026-09-29:** superseded. Since `main.py` v2.0.0 the credentials come from `secrets.json` or the environment, never from `config.json` ([ADR-0021](0021-secrets-in-secrets-json.md)); the password was rotated on 2026-09-29 (TODO T-011, done); the history question is TODO T-102. The text below is kept as the historical record; its `main.py:<line>` references are to v1.x.

## Context

`MMClient` authenticates to MainManager with the OAuth2 password grant
(`POST /restapi/token`, `grant_type=password`, `main.py:544-558`). It reads the username and
password from `config.json -> mainmanager.username` / `mainmanager.password` (`main.py:538-539`).
The build reference's config template used a `"YOUR_PASSWORD"` placeholder (`:165`), i.e. the
plan was always "put the real value in the file on the server".

On 2026-09-28 the whole working folder — including `config.json` with the **live** MainManager
service-account credentials — was committed and pushed to GitHub (`slimyloki/cts-alarms`, single
"Initial commit"). The repository is **public** (verified via the GitHub API on 2026-09-28); the
credentials must be treated as compromised, whoever has actually seen them.

Additionally, the credentials are also present on the CTS server file system in plain text,
readable by anyone with access to `C:\priorityalarmsapi`, and the Task Scheduler task runs under
the `GPST` account with `LogonType=Password`.

## Decision (historical)

Store MainManager credentials as plain-text fields in `config.json` next to the non-secret
configuration; load them with `json.load`; no environment variables, no encryption, no separate
file.

## Why deprecated

1. The credentials are in a pushed git history. Rewriting history does not undo that; they must be
   **rotated** in MainManager.
2. Secrets and configuration are mixed in one file, so the file cannot be committed even after
   rotation.
3. The next phase ([ADR-0016](0016-cts-server-vs-vps-responsibility-split.md),
   [ADR-0018](0018-alarm-web-application-on-vps.md)) adds a second host and possibly a database
   password and a web-app secret; the pattern must be fixed before it multiplies.

## Required follow-up (tracked in [docs/TODO.md](../TODO.md))

| Step | Note |
|------|------|
| Rotate the MainManager service-account password | In MainManager, by whoever administers the account. Highest priority. |
| Remove the values from the repo | Replace with placeholders; consider history rewrite / repo re-creation; GitHub secret scanning alert if any. |
| Choose a mechanism | Options: (a) environment variables set on the scheduled task / user; (b) separate `secrets.json` outside the repo and `.gitignore`d; (c) Windows DPAPI-protected file (`CryptProtectData`, user-scoped to `GPST`); (d) Windows Credential Manager; (e) a secret store on the VPS if logic moves there. Undecided; the owner decides. |
| Add `.gitignore` | `config.json` (or the secrets file), `*_state.json`, `logs/`, `csv/` once [ADR-0014](0014-commit-runtime-data-for-migration.md) ends. |
| Audit other secrets | Only the MainManager pair was found in this repo; `TACVista_Alarm_Bot.xml` contains no password. Operator names in data are personal data, not secrets, handled under ADR-0014. |

## Consequences of the historical decision

### Positive
- Simple: one file, one loader.

### Negative
- Credential leak (realised). Anyone who reads the repo can create, read and update incidents in
  the MainManager tenant as the bot.
- Backups/copies of the working folder carry the secret.
- Same pattern likely exists in the sibling Indeklima bot (not in this repo; unverified).

## Alternatives considered (at the time)

| Option | Why not (then) |
|--------|----------------|
| Environment variables | Task Scheduler running the task under a stored-password logon (`LogonType=Password`) makes them slightly awkward; not attempted. |
| DPAPI | Extra code; not attempted. |

## Evidence

- `main.py:535-558` `MMClient.__init__` and `_get_token()`
- `config.json -> mainmanager.username`, `mainmanager.password` (values deliberately not reproduced here)
- `Alarm_bot_build_reference.md:161-167` config template with placeholder
- `git log`: single commit `13b09b6 Initial commit` containing `config.json`
- Logs: "API AUTH: requesting token from https://rambollfm.mainmanager.dk/restapi/token" — 8,258 times
