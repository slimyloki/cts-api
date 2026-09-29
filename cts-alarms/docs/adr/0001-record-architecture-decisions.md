# ADR-0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** Georgi (owner)

## Context

The alarm bot has been in production on the CTS server since 2026-04-17 (first log file
`logs/2026-04-17.log`) with no decision log. The single design document,
`Alarm_bot_build_reference.md`, describes an earlier version: 3-minute cadence, `exceptions.txt`,
`C:\ctsapi\alarms`, default MainID 9756, threshold 3, status vocabulary
`OPEN/CLEARED/ACKNOWLEDGED/BOOTSTRAPPED`. The deployed `main.py` and `config.json` differ on every
one of those points. Nobody can tell from the repo which choices were deliberate and which were
drift.

The owner has asked for documentation first (arc42, ADRs, C4) and then a set of changes: secrets
rotation, a web application on a VPS, SQL storage, friendly alarm names, alarm analytics. Several
of those are explicitly undecided ("we're going to decide this later"). Without a record, the
next phase would repeat the same drift.

## Decision

Keep Architecture Decision Records in `docs/adr/`, one file per decision, MADR-style layout,
numbered sequentially, with the status vocabulary defined in [README.md](README.md)
(Proposed / Accepted / Deprecated / Superseded).

- Decisions already embodied in code are reconstructed as ADRs 0002–0013 and marked
  "decision predates this record; reconstructed from code and `Alarm_bot_build_reference.md`".
- Decisions the owner has deferred are written as **Proposed** ADRs (0015–0018) that list options,
  trade-offs and the questions to answer, and name the owner as decider.
- A decision recognised as wrong but still in effect is marked **Deprecated** (0013) with a link to
  the follow-up in [docs/TODO.md](../TODO.md).
- Where `Alarm_bot_build_reference.md` contradicts `main.py`, `main.py` wins and the ADR notes the drift.

## Consequences

### Positive
- The reasoning behind each behaviour (bootstrap, prepend-only updates, per-directory CSV) is
  findable without reading 1,030 lines of `main.py`.
- Open decisions have a home; the TODO list can reference them by number.
- Future contributors (or agents) can see what was rejected and why.

### Negative
- ADRs must be maintained; an unmaintained ADR log drifts the same way the build reference did.
- Reconstructed ADRs record the *effect* in code; the original motivation is inferred where the
  build reference is silent, and those inferences are flagged in the text.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep everything in `Alarm_bot_build_reference.md` | Already stale; a single long document has no status per decision. |
| Only arc42 §09 | arc42 is a snapshot of the whole; per-decision files are easier to supersede individually. |
| GitHub issues as the decision log | Issues close and disappear from view; decisions need to remain readable in the repo. |

## Evidence

- `Alarm_bot_build_reference.md:47` ("every 3 minutes") vs `TACVista_Alarm_Bot.xml` `<Interval>PT5M</Interval>`
- `Alarm_bot_build_reference.md:152` (`max_priority_number: 3`) vs `config.json -> thresholds.max_priority_number = 2`
- `Alarm_bot_build_reference.md:126,166` (default MainID 9756) vs `config.json -> mainmanager.default_main_id = 14228`
- `Alarm_bot_build_reference.md:220` (`OPEN/CLEARED/...`) vs `main.py:101-105` (`ACTIVE`, `ACTIVE + ACKNOWLEDGED`, ...)
- `main.py:7` docstring still says "default every 3 min"
