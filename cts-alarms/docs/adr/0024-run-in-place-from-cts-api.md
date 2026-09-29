# ADR-0024: The bot runs in place from C:\cts-api\cts-alarms; C:\priorityalarmsapi is retired

- **Status:** Accepted
- **Date:** 2026-09-29
- **Decided by:** the owner ("remove this priority alarm, I'm not going to use it anymore")
- **Supersedes:** decision 3 of [ADR-0023](0023-moved-into-cts-api.md) ("runtime folder unchanged")

## Context

After the code moved into cts-api (ADR-0023), the owner repointed the scheduled task to run
`C:\cts-api\cts-alarms\main.py` directly. Version 2.1.0 still read `C:\priorityalarmsapi\config.json`, with the old,
rotated password in it. The copy-based install still wrote into `C:\priorityalarmsapi`. Two folders held
half of the bot each.

## Decision

1. The bot runs **in place** from `C:\cts-api\cts-alarms`. `main.py` reads the `config.json` next to it, and
   every relative path in that config is relative to that folder (v2.1.1). The committed config has only
   relative paths, except Vista's own alarm file:
   - `secrets.json`
   - `alarms_state.json`, `csv_state.json`
   - `csv\`, `logs\`
   - `mm_token.json`, `mm_auth_failed.json`
2. All runtime files are git-ignored in that folder, so a `git pull` never touches them and nothing can push them.
3. `install.cmd` moves the data out of `C:\priorityalarmsapi` **once**. After a clean dry run it renames the
   folder to `C:\priorityalarmsapi.retired-<date>`. It never deletes it.
4. Until that has happened, `main.py` refuses to start from scratch (`LEGACY_STATE_FILE` guard, exit 2). This
   keeps the links to open tickets.
5. Backups of code and state go outside the repo, to `C:\cts-api-backups\cts-alarms\`.
6. Updates keep a gate: `update.cmd` disables the task, pulls, dry-runs the new code, and puts the previous
   commit back if that fails.

## Consequences

- One folder holds the whole bot. `status.cmd` reports its health.
- A `git pull` changes the running code at the next 5-minute run, without the gate. `update.cmd` is the gated
  way, and the docs point to it.
- `git clean -x` in `C:\cts-api` would delete the runtime data. Never run it there.
