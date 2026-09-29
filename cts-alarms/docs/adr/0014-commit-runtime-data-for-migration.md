# ADR-0014: Commit runtime data to the repo for migration

- **Status:** **Superseded by [ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md)** (2026-09-29)
- **Date:** 2026-09-28 · 2026-09-29 (superseded)
- **Deciders:** Georgi (owner)

> **2026-09-29:** superseded. The historical import is verified and its source is now a private, checksummed archive of the 2026-09-28 data on the VPS; `logs/`, `csv/`, the state files and the snapshot leave the repo tip and are git-ignored (TODO T-049). Whether they are also purged from the public history is TODO T-102. Runtime data stays on the CTS server.

## Context

The owner is planning a new system (web app on a VPS, SQL storage, alarm analytics) and wants two
things from the existing runtime data: (1) to see exactly what the bot logs today, in order to
decide what the new system should record, and (2) to migrate the accumulated history into the new
storage so analysis does not start from zero.

The data lives only on the CTS server in `C:\priorityalarmsapi`. There is no other copy. The
simplest way to get it somewhere it can be studied and processed was to commit the whole folder.

## Decision

Commit the complete working folder to the `slimyloki/cts-alarms` repository, **deliberately and
temporarily**, including:

| Path | Content | Size |
|------|---------|------|
| `logs/` | 163 daily log files, 2026-04-17 … 2026-09-28, ~45,600 runs | 356 MiB |
| `csv/` | 431 per-directory audit CSVs, 20,584 rows | ~5.3 MB of data (6.3 MB on disk) |
| `alarms_state.json`, `csv_state.json` | current state (122 / 180 entries) | small |
| `alarm_snapshot.alr` | last snapshot of `$this.alr`, 180 rows | small |
| `config.json` | full config **including credentials** | small |
| `objects.csv`, `exceptions.csv`, `TACVista_Alarm_Bot.xml`, `Alarm_bot_build_reference.md`, `main.py` | | |

Quote of intent: "I deliberately uploaded the logs to GitHub just to see what we log and just to
migrate it to the new system."

This ADR records the choice so nobody "cleans up" the data before it has been used, and so the
exit condition is explicit.

## Consequences

### Positive
- The entire history of the bot is available for analysis off the CTS server — this documentation
  set was produced from it (event counts, recurrence, the 404 loop, threshold drift).
- Import into SQL ([ADR-0015](0015-sql-storage-for-alarm-history.md)) can be developed and tested
  against real data without touching the production host.
- The log format ([docs/reference/log-format.md](../reference/log-format.md)) can be reviewed for
  what is useful vs noise before the new system defines its own.

### Negative
- **Secrets**: the commit includes the MainManager credentials — see
  [ADR-0013](0013-secrets-in-config-json.md). Rotation is required regardless of what happens to
  this repo.
- **Personal data**: operator names (`user` field, e.g. the owner's own
  "GPST (Georgi ISS)"; other operators' labels have the same shape) appear in `alarm_snapshot.alr`, both state files, every CSV row with an
  acknowledgement, and every status table in the logs. This is now in a public GitHub repository
  (visibility verified via the GitHub API on 2026-09-28). GDPR exposure for named employees; the owner should decide whether the
  repo must go private now.
- **Repo size**: ~360 MB, dominated by logs. Clones are slow; GitHub will warn. Log files are
  append-only text and compress well, but git stores each daily file whole.
- **Building data**: alarm texts such as "Brand fra ABA", "Sprinkler Fejl", "Røgspjæld er åbne",
  controller paths and building/zone codes describe the physical security state of a site.
- Once removed from the tree, the data stays in git history unless history is rewritten or the
  repository is re-created.

## Exit condition

This decision ends when **all** of the following are true; then `logs/`, `csv/`, `*_state.json`,
`alarm_snapshot.alr` and `config.json` are removed from the tree and `.gitignore`d, and the
repository history is rewritten or the repo re-created:

1. History has been imported into the new store and the import verified against the CSV counts.
2. The new system's logging/recording format has been decided.
3. Secrets have been rotated and moved (ADR-0013 follow-up).

Tracked in [docs/TODO.md](../TODO.md).

## Alternatives considered

| Option | Why not |
|--------|---------|
| Private repo | Would have reduced exposure. The repository is public (verified via the GitHub API on 2026-09-28; `https://github.com/slimyloki/cts-alarms`). Treat the credentials as compromised; making it private now is still recommended. |
| Transfer via zip / cloud drive | No history, no diffing; the owner wanted it where the tooling is. |
| Commit only `csv/` and state, not `logs/` | Logs were the point ("see what we log"); 356 MiB accepted knowingly. |
| Anonymise operator names before committing | Would have been better; not done. Can still be done in the import step. |

## Evidence

- `git -c core.quotepath=off ls-files | grep -cE '^(logs|csv)/'` = 594 tracked data files (163 logs + 431 CSVs); `du -sh logs csv` = 356M / 6.3M
- Owner's statement in the task request (quoted above)
- Scouting notes §"Files in repo" and §"Personal data"
