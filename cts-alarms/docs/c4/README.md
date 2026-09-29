# C4 model — TAC Vista alarm bot

C4 views of the alarm bot (`main.py` v2.1.0 + `shipper.py`) on the CTS server, and — as an external
system — the digibuild `cts-alarms` sub-project it ships to. Page 05 keeps the original proposal and records
how it was realised. The receiving side's own C4 views are in the digibuild repo. Every diagram is a Mermaid C4 block that
GitHub renders; because Mermaid's C4 layout is often hard to read, each diagram is followed by a
table listing every element with its description and technology. When the diagram and the table
disagree, the table is authoritative and the diagram is the bug.

## Levels

| Level | File | Question it answers | Source of truth |
|-------|------|---------------------|-----------------|
| 1 — System context | [01-system-context.md](01-system-context.md) | Who uses the bot, which external systems it talks to | `main.py`, `shipper.py`, `config.json`, `TACVista_Alarm_Bot.xml` |
| 2 — Container | [02-container.md](02-container.md) | What runs on the CTS server, which files it reads/writes, with exact paths and formats | `config.json`, `main.py`, `shipper.py`, `TACVista_Alarm_Bot.xml` |
| 3 — Component | [03-component.md](03-component.md) | The functional blocks inside `main.py`/`shipper.py` and the data flow between them | `main.py`, `shipper.py` |
| 4 — Code | [04-code.md](04-code.md) | `Alarm`, `MMClient`, state-entry schemas, the signature tuple, the IngestBatch | `main.py`, `shipper.py` |
| Target (realised) | [05-target-architecture-proposal.md](05-target-architecture-proposal.md) | The 2026-09-28 proposal for the VPS side and how it was decided and realised in digibuild | ADR 0015–0022 |

## How these views relate to the rest of the docs

- The arc42 documentation covers the same system in prose: [03 context and scope](../arc42/03-context-and-scope.md), [05 building block view](../arc42/05-building-block-view.md), [06 runtime view](../arc42/06-runtime-view.md), [07 deployment view](../arc42/07-deployment-view.md). The C4 pages are the diagram-first companion; they do not repeat the arc42 sections, they link to them.
- Decisions that shaped what you see here are recorded as ADRs in [../adr/README.md](../adr/README.md). Levels 1–4 reflect ADRs 0002–0012 and 0019–0022; page 05 records how ADRs 0015–0018 were decided.
- Field-level formats live under [../reference/](../reference/alr-file-format.md): [alr-file-format.md](../reference/alr-file-format.md), [csv-audit-format.md](../reference/csv-audit-format.md), [state-files.md](../reference/state-files.md), [log-format.md](../reference/log-format.md), [config-reference.md](../reference/config-reference.md), [mainmanager-api.md](../reference/mainmanager-api.md), [shipper.md](../reference/shipper.md).
- Open work, including everything the owner asked for but has not decided, is in [../TODO.md](../TODO.md).

## Conventions used in the diagrams

| Convention | Meaning |
|------------|---------|
| `Person` | A human role. Named people are not modelled; the maintainer is referred to as Georgi. |
| `System` / `Container` / `Component` | Things that are part of the alarm bot. |
| `System_Ext` | Systems the bot depends on but does not own (TAC Vista, MainManager, digibuild `cts-alarms`, GitHub, Task Scheduler). |
| `ContainerDb` | A file the bot reads or writes on the CTS server. The history database is digibuild's (see [ADR 0015](../adr/0015-sql-storage-for-alarm-history.md)). |
| `Rel(a, b, "label", "technology")` | Direction is the direction of the *call* or the *write*, not of the data. |
| `main.py:<line>` | Line reference into `main.py` v2.1.0 (re-verified 2026-09-29). Function names are stable; line numbers drift. |

Danish alarm texts and Vista terms are kept verbatim where quoted (for example "Høj Temperatur",
*kvitteret*). Vista priorities are counter-intuitive: **1 is most urgent, 9 is system noise**.

## Where `main.py` and the older build reference disagree

`Alarm_bot_build_reference.md` predates the current code. Where it contradicts `main.py`, these
pages follow `main.py`. Known drift: cadence (3 min in the reference, 5 min in the deployed task),
install path (`C:\ctsapi\alarms` vs `C:\priorityalarmsapi`), exception file name (`exceptions.txt`
vs `exceptions.csv`), default MainID (9756 vs 14228), priority threshold (3 vs 2), status
vocabulary (OPEN/CLEARED/ACKNOWLEDGED/BOOTSTRAPPED vs ACTIVE / ACTIVE + ACKNOWLEDGED / NORMAL /
NORMAL + ACKNOWLEDGED / RESOLVED). See [arc42 §11](../arc42/11-risks-and-technical-debt.md).
