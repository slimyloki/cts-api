# Architecture Decision Records (ADRs)

This directory holds the architecture decisions for the TAC Vista alarm bot (`main.py`) and its
VPS shipper (`shipper.py`) — the CTS-server side of the alarm pipeline. One decision per file, numbered,
never deleted. Since 2026-09-29 the web application, history database and reports are the digibuild
sub-project `cts-alarms`; their decisions live in the digibuild repo ([ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md)).

Why ADRs: the existing system was built without a decision log. The only design document,
`Alarm_bot_build_reference.md`, has drifted from `main.py` (cadence, paths, threshold, default
MainID, status vocabulary — see [arc42 §11](../arc42/11-risks-and-technical-debt.md)). The next
phase (VPS web app, SQL storage, friendly names) involves several open choices that the owner has
explicitly deferred. ADRs record what was decided, what is still open, and why.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-scheduled-python-script-on-cts-server.md) | Scheduled Python script on the CTS server | Accepted |
| [0003](0003-vista-id-as-primary-key.md) | Vista ID as primary key for an alarm instance | Accepted |
| [0004](0004-state-signature-diff.md) | State-signature diff to detect transitions | Accepted |
| [0005](0005-snapshot-then-parse.md) | Snapshot `$this.alr` before parsing | Accepted |
| [0006](0006-bootstrap-on-first-run.md) | Bootstrap on first run without creating incidents | Accepted |
| [0007](0007-priority-threshold-filter.md) | Priority-threshold filter for the incident pipeline | Accepted |
| [0008](0008-directory-based-exceptions.md) | Directory-based exception list | Accepted |
| [0009](0009-objects-csv-mainid-mapping-with-fallback.md) | `objects.csv` MainID mapping with fallback | Accepted |
| [0010](0010-prepend-description-never-change-status.md) | Prepend to incident description, never change status | Accepted |
| [0011](0011-per-directory-csv-audit-log.md) | Per-directory CSV audit log for all priorities | Accepted |
| [0012](0012-json-state-files-atomic-writes.md) | JSON state files with atomic writes | Accepted |
| [0013](0013-secrets-in-config-json.md) | Secrets in `config.json` | Superseded by [ADR-0021](0021-secrets-in-secrets-json.md) |
| [0014](0014-commit-runtime-data-for-migration.md) | Commit runtime data to the repo for migration | Superseded by [ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md) |
| [0015](0015-sql-storage-for-alarm-history.md) | SQL storage for alarm history | Accepted (2026-09-29) — SQLite in the digibuild worker |
| [0016](0016-cts-server-vs-vps-responsibility-split.md) | CTS server vs VPS responsibility split | Accepted (2026-09-29) — Option A |
| [0017](0017-friendly-alarm-naming-registry.md) | Friendly alarm naming registry | Accepted (2026-09-29) — registry in digibuild |
| [0018](0018-alarm-web-application-on-vps.md) | Alarm web application on the VPS | Superseded by [ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md) |
| [0019](0019-mainmanager-v3-incident-api.md) | MainManager v3 incident API | Accepted (2026-09-28) |
| [0020](0020-hmac-ingest-to-digibuild.md) | Ship each run to digibuild `cts-alarms` over HTTPS POST + HMAC | Accepted (2026-09-29) |
| [0021](0021-secrets-in-secrets-json.md) | Secrets only in `secrets.json` or the environment | Accepted (2026-09-29) |
| [0022](0022-cts-side-only-repo-web-app-in-digibuild.md) | This repo holds the CTS side only; the web app is the digibuild sub-project | Accepted (2026-09-29) |
| [0023](0023-moved-into-cts-api.md) | The alarm bot's code moves into the cts-api repository | Accepted |

ADRs 0002–0013 document decisions that predate this log; they were reconstructed from `main.py`,
`config.json`, `TACVista_Alarm_Bot.xml` and `Alarm_bot_build_reference.md` on 2026-09-28. Their
`main.py:<line>` references are to v1.x; ADR-0019 … 0022 cite v2.1.0.

## Status vocabulary

| Status | Meaning |
|--------|---------|
| **Proposed** | Options laid out, no decision taken. The owner (Georgi) decides. Each Proposed ADR lists the questions that must be answered first. |
| **Accepted** | In force. Implemented in `main.py` / config, or agreed for the next phase. |
| **Deprecated** | Still in effect in the running system, but recognised as wrong; must be replaced. Names the follow-up work. |
| **Superseded by ADR-NNNN** | Replaced by a later decision. The file stays; a link points forward. |

Status changes are edits to the `Status` line plus a dated note in the file. Do not rewrite
history; add a new ADR when the decision changes.

## Conventions

- Filename: `NNNN-<kebab-slug>.md`, four-digit sequence, never reused.
- Layout: MADR-style (template below). 30–80 lines; link to [arc42](../arc42/) or
  [reference](../reference/) docs rather than repeating them.
- Every factual claim points at evidence: `main.py:<line>` or a function name (`run_csv_logging()`),
  a config key, a data file, or a log observation. Unknowns are written as "unknown"/"undecided".
- Never write credential values. The repo is **public**. Refer to secrets by location: "the
  MainManager service-account credentials in `secrets.json` (`mainmanager.username` /
  `mainmanager.password`)", "the ingest secret (`vps.ingest_secret`)".
- English text; Danish alarm texts and Vista terms stay verbatim (e.g. "Høj Temperatur", kvitteret).
- Open work items derived from an ADR go to [docs/TODO.md](../TODO.md), not into the ADR.

## Template

```markdown
# ADR-NNNN: <Title>

- **Status:** Proposed | Accepted | Deprecated | Superseded by ADR-NNNN
- **Date:** YYYY-MM-DD
- **Deciders:** Georgi (owner)

## Context
What situation forces a decision. Facts, constraints, numbers.

## Decision
What was (or is to be) decided, in one or two sentences, then details.

## Consequences
### Positive
### Negative

## Alternatives considered
| Option | Why not (or: still open) |

## Evidence
- `main.py:<line>` / function / file / observation
```

## Related

- [arc42 §09 Architecture decisions](../arc42/09-architecture-decisions.md) — summary view
- [C4 §05 Target architecture](../c4/05-target-architecture-proposal.md) — the proposal and how it was realised in digibuild
- [docs/TODO.md](../TODO.md) — the work list the Proposed/Deprecated ADRs feed
