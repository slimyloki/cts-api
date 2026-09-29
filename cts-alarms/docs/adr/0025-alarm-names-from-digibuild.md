# ADR-0025: New tickets carry the alarm point's name from digibuild

- **Status:** Accepted
- **Date:** 2026-09-29
- **Decided by:** the owner, answering [T-053](../TODO.md)
- **Relates to:** [ADR-0010](0010-prepend-description-never-change-status.md) (only prepend lines),
  [ADR-0017](0017-friendly-alarm-naming-registry.md) (the naming registry), [ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md)
  (the web half lives in digibuild); digibuild's ADR-0059 is the other side of the same contract.

## Context

A ticket reads "CTS Alarm - 320-01-07930-0101_AL - Lav temperatur", and nobody at the desk knows where that low
temperature is. The digibuild catalogue lets a person name each alarm point (name, building, floor, system), but
the name stayed on the website. The owner, 2026-09-29:

> "what we need to do is have names for these alarms and this name should also go to the incident … these names
> will be from the website. It should go to the CTS server. From the CTS server next time it should be created with
> the name that is there … The system is made so we don't know what this is until and unless we give it a name,
> which we do from the website."

Nothing on the internet can reach the CTS server, so it has to fetch the names itself.

## Decision

1. **The names ride back on the ingest answer.** Every accepted batch is answered with
   `names: {version, points: [{directory, name?, building?, floor?, system?}]}`. The shipper writes it to
   **`names.json`** next to `main.py` (`paths.names_file`; git-ignored; atomic; only when `version` changes). No
   new route, no new secret, no extra request.
2. **The key is the Vista directory**, the string the bot already ships and the catalogue's unique key.
3. **The bot reads `names.json` at the start of every run** (`load_names()`). A missing file means no names yet;
   a broken one is logged and ignored. The bot never waits for digibuild.
4. **A new ticket** is titled `CTS Alarm - <name> - <alarm text>` (still 100 characters at most), and its
   description lists `Name:`, `Building:`, `Floor:` and `System:` above `Object:` and `Directory:`. Without a
   name, nothing changes.
5. **An open ticket** whose point gets a name, or a changed one, gets one line, once:
   `<time> Alarm bot - alarm point named on digibuild.dk: "<name>" (Building: …, Floor: …, System: …)`. The state
   entry remembers what the ticket was told (`named_sig`). Titles of existing tickets are never changed, so history
   and searches keep working.

## Consequences

- Naming a point on digibuild.dk changes what the desk reads in MainManager, from the second run after the save,
  so within about ten minutes.
- The names only arrive while the shipper is on (`"vps"` in `config.json` and the ingest secret set). Until then
  there is no `names.json` and tickets look as before.
- `Run complete:` gains `named=N`; `status.cmd` shows how many named points the bot knows.
- Version 2.2.0.
