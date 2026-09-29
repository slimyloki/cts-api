# Reference: CSV audit log (`csv/*.csv`)

Append-only event log of **every** alarm in `$this.alr`, all priorities, no exception filter. One file per Vista directory. Produced by `run_csv_logging()` (`main.py:515-612`), driven by `csv_state.json` ([state-files.md](state-files.md#csv_statejson)). The files are the main source of the one-off historical import into the digibuild `cts-alarms` database ([ADR-0015](../adr/0015-sql-storage-for-alarm-history.md), [ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)). Since v2.1.0 the same events are also returned to `run()` and shipped per run ([shipper.md](shipper.md)).

The files stay on the CTS server. The copy as of 2026-09-28 is archived privately on the VPS; the observed values and totals on this page come from it.

Related: [alr-file-format.md](alr-file-format.md) · [ADR-0011 per-directory CSV audit log](../adr/0011-per-directory-csv-audit-log.md) · [arc42 §6 runtime view](../arc42/06-runtime-view.md) · [c4/03-component.md](../c4/03-component.md)

## Location and file naming

| Item | Value |
|---|---|
| Folder | `config.json -> paths.csv_folder` = `C:\priorityalarmsapi\csv` (on the CTS server only; git-ignored) |
| File name | `sanitize_filename(directory) + ".csv"` (`main.py:372-382`, `main.py:432`) |
| Encoding | UTF-8 (`main.py:435`) — note: the source `.alr` is ISO-8859-1; the bot transcodes |
| Delimiter | `;` |
| Line ending | `\n` in code (file opened in text mode). The 2026-09-28 copies were LF-only; whether the on-server copies are CRLF (Python text-mode newline translation on Windows) is unverified |
| Header | written once when the file is created (`main.py:436-437`) |
| Rotation / retention | none — files grow forever |
| Enabled when | both `paths.csv_folder` and `paths.csv_state_file` are non-empty (`main.py:1049`). In `--dry-run` / `--parse-only` the events are computed but nothing is written (`write=False`) |
| Failure mode | whole step wrapped in `try/except`; logs `ERROR CSV audit logging failed (non-fatal): …` and the incident pipeline continues; no events are shipped for that run (`main.py:1050-1057`) |

### Filename sanitisation (`sanitize_filename()`)

Replacements, in order: `/ \ :` → `_`; `"` and `'` removed; space → `_`; `#` and `$` → `_`; then truncated to 150 characters. Everything else (including `æøå` and the non-breaking space `0xA0`) is kept.

Consequences:

- `VISTA_SERVER-LOYTEC_PORT-RHQ-34551_02_ET4_XENTA-4A271_16#MA100` → `VISTA_SERVER-LOYTEC_PORT-RHQ-34551_02_ET4_XENTA-4A271_16_MA100.csv` (the `$EE_Mess` system messages land in 142 files, 140 of them `…_MAn.csv` — one per controller — plus two server-level ones, `VISTA_SERVER_DSSLarmObj.csv` and `VISTA_SERVER_SqlSizeExceeded.csv`).
- Two different directories that differ only in a sanitised character would share a file. Not observed (431 files ↔ 431 distinct sanitised names); longest name in use is 86 characters, so truncation has never triggered.
- The `directory` column inside the file still holds the unsanitised path, so the mapping is recoverable.

## Header and columns

Exact header (`CSV_HEADER`, `main.py:357-361`):

```
datetime;event;vista_id;alarm_object;directory;alarm_text;priority;user;ack_flag;state1;state2;date1_hex;date1_human;date2_hex;date2_human;count;status_label
```

| # | Column | Type | Source | Notes |
|---:|---|---|---|---|
| 1 | `datetime` | `DD-MM-YYYY HH:MM:SS` | `datetime.now().astimezone()` at the start of `run_csv_logging()` (`main.py:527`), formatted without the offset | Server local time, inferred from the data as Europe/Copenhagen (+02:00 on 2026-06-24, see [alr-file-format.md](alr-file-format.md#hex-epochs)); not configured anywhere; no zone in the value. Same value for every row of one run. Observation time, **not** the Vista transition time (that is `date2_human`). |
| 2 | `event` | enum | `classify_csv_event()` | see [Events](#events) |
| 3 | `vista_id` | string | `.alr` field 1 | primary key of the alarm instance |
| 4 | `alarm_object` | string | field 2 | |
| 5 | `directory` | string | field 22 | unsanitised |
| 6 | `alarm_text` | string | field 13 | `;` replaced by `,` (`main.py:393`) so the delimiter is safe. **Current** text at observation time — can differ between rows of the same alarm (`ATV21 Fejl` → `ATV21 OK`). RESOLVED rows use `initial_alarm_text` from `csv_state.json`, i.e. the text at the *last signature change*, not necessarily at first sight. |
| 7 | `priority` | int | field 7 | |
| 8 | `user` | string | field 10 | empty on synthetic rows |
| 9 | `ack_flag` | 0/1 | field 11 | empty on synthetic rows |
| 10 | `state1` | 0/1 | field 4 | empty on synthetic rows |
| 11 | `state2` | 0 | field 5 | empty on synthetic rows |
| 12 | `date1_hex` | 8 hex | field 3, re-formatted `%08X` | empty on synthetic rows |
| 13 | `date1_human` | `DD-MM-YYYY HH:MM:SS` | `epoch_hex_to_human(date1)` | local time; empty on synthetic rows |
| 14 | `date2_hex` | 8 hex | field 6 | empty on synthetic rows |
| 15 | `date2_human` | local time | `epoch_hex_to_human(date2)` | empty on synthetic rows |
| 16 | `count` | int | field 18 | empty on synthetic rows |
| 17 | `status_label` | enum | `Alarm.status_label` | `ACTIVE`, `ACTIVE + ACKNOWLEDGED`, `NORMAL`, `NORMAL + ACKNOWLEDGED`, `RESOLVED` (see [alr-file-format.md](alr-file-format.md#the-state-signature)) |

No quoting is used anywhere; the only escaping is the `;`→`,` replacement in `alarm_text`. Other string columns (`user`, `directory`, `alarm_object`) are written raw — none of the observed values contain `;`.

## Events

Emitted by `classify_csv_event(prev_sig, alarm)` (`main.py:441-475`) whenever an alarm's signature `(state1, state2, ack_flag, user)` differs from the one stored in `csv_state.json`. Several events can be emitted for one alarm in one run, in the order below, each as its own row with the same `datetime`.

| `event` | Emitted when | Rows observed (to 2026-09-28) |
|---|---|---:|
| `FIRST_SEEN` | `vista_id` not in `csv_state.json` (new alarm instance, or first run after the state file was created/emptied) | 1,258 |
| `NORMAL` | `state1` went `0 → 1` (condition cleared) | 9,455 (incl. 184 synthetic, see below) |
| `ACTIVE` | `state1` went `1 → 0` (condition returned) | 8,644 |
| `STATE1_<p>_TO_<n>` | `state1` changed to/from a value other than 0/1 (e.g. `6`) | 0 |
| `ACKNOWLEDGED` | `ack_flag` went `0 → 1` (may be emitted together with NORMAL/ACTIVE) | 148 |
| `UNACKNOWLEDGED` | `ack_flag` went `1 → 0` | 1 |
| `USER_CHANGED` | nothing above matched but `user` changed (re-acknowledged by another operator) | 0 |
| `STATE_CHANGED` | catch-all: signature changed but no rule matched (in practice only a `state2` change) | 0 |
| `RESOLVED` | `vista_id` present in `csv_state.json` but absent from the file (Vista removed the row) | 1,078 |

Note: unlike the incident pipeline, the CSV `ACKNOWLEDGED` event does **not** check `user != "No user"`; it fires on the flag alone.

### Synthetic `NORMAL` + `RESOLVED` rows

When an alarm disappears from the file (`main.py:565-599`):

1. If its last stored signature had `state1 == 0` (last seen ACTIVE), a **synthetic `NORMAL` row** is written first, built as a bare f-string (`main.py:581-586`): columns 8–16 empty, `status_label = NORMAL`. This records the transition the bot missed between two polls. 184 such rows exist.
2. Then a `RESOLVED` row is written via `csv_resolved_row()` (`main.py:409-426`): columns 8–16 empty, `status_label = RESOLVED`, `alarm_text = initial_alarm_text` from state.
3. The `csv_state.json` entry is flagged `resolved` and deleted in the same run (`main.py:598-604`).

Synthetic rows can be recognised by `user == ""`. Example (from `csv/VISTA_SERVER-LOYTEC_PORT-RHQ-34501_02_ET0_XENTA-32515_04011_0205-ZoneType06.0101_AL.csv`):

```
23-04-2026 09:24:59;NORMAL;VISTA_SERVER#69E71F7D;325-15-04011-0205-0101_AL;VISTA_SERVER-LOYTEC_PORT-RHQ-34501_02_ET0_XENTA-32515_04011_0205-ZoneType06.0101_AL;Lavt tryk;3;;;;;;;;;;NORMAL
```

### A complete lifecycle (real rows, `csv/325-02-03901-Indblæsning-Alarm-Bit0_Fejl.csv`)

```
datetime;event;vista_id;alarm_object;directory;alarm_text;priority;user;ack_flag;state1;state2;date1_hex;date1_human;date2_hex;date2_human;count;status_label
24-06-2026 16:10:02;FIRST_SEEN;VISTA_SERVER#6A3BE36B;325-02-03901-Indblæsning-Alarm-Bit0_Fejl;325-02-03901-Indblæsning-Alarm-Bit0_Fejl;ATV21 Fejl;3;No user;0;0;0;6A3BE340;24-06-2026 16:01:36;6A3BE340;24-06-2026 16:01:36;1;ACTIVE
24-06-2026 16:20:04;NORMAL;VISTA_SERVER#6A3BE36B;325-02-03901-Indblæsning-Alarm-Bit0_Fejl;325-02-03901-Indblæsning-Alarm-Bit0_Fejl;ATV21 OK;3;No user;0;1;0;6A3BE340;24-06-2026 16:01:36;6A3BE59B;24-06-2026 16:11:39;1;NORMAL
13-07-2026 06:45:02;RESOLVED;VISTA_SERVER#6A3BE36B;325-02-03901-Indblæsning-Alarm-Bit0_Fejl;325-02-03901-Indblæsning-Alarm-Bit0_Fejl;ATV21 OK;3;;;;;;;;;;RESOLVED
```

Read: alarm raised 16:01:36, first noticed by the bot at 16:10:02 (up to one polling interval plus a Vista write delay), cleared 16:11:39, and the row was acknowledged and removed by Vista 19 days later.

An acknowledgement row (`csv/325-09-03901-Udsugning-Alarm-Bit0_Fejl.csv`, operator label replaced by a placeholder):

```
20-08-2026 08:00:04;ACKNOWLEDGED;VISTA_SERVER#6A7D7507;325-09-03901-Udsugning-Alarm-Bit0_Fejl;325-09-03901-Udsugning-Alarm-Bit0_Fejl;ATV21 Fejl;3;<INITIALS> (<operator name> (ISS));1;0;0;6A7D7507;13-08-2026 09:40:55;6A7D7507;13-08-2026 09:40:55;1;ACTIVE + ACKNOWLEDGED
```

## What is *not* in the CSV audit

- Rows where nothing changed (the bot writes only on signature change), so `count` is only sampled at transitions.
- Alarms present when `csv_state.json` was first created got a `FIRST_SEEN` row at that moment (2026-04-20/21), not at their real creation; use `date1_human` for the real start.
- `(6,2)` system events (never observed anyway).
- Whether a MainManager incident exists for the alarm — that lives in `alarms_state.json` (and, since v2.1.0, in the `incidents` list of each shipped batch). Joining the two is by `vista_id`.
- Anything from before 2026-04-20 (first CSV rows) — the audit started with the current code version, three days after the bot's first run.

## Observed totals (data as of 2026-09-28)

| Metric | Value |
|---|---:|
| Files | 431 |
| Data rows | 20,584 (~5.3 MB of data; 6.3 MB on disk) |
| Largest file | `…XENTA-4A271_16_MA100.csv` (1,286 rows — one controller's `$EE_Mess` file-transfer failures) |
| Events | NORMAL 9,455 · ACTIVE 8,644 · FIRST_SEEN 1,258 · RESOLVED 1,078 · ACKNOWLEDGED 148 · UNACKNOWLEDGED 1 |
| `status_label` | NORMAL 9,860 · ACTIVE 9,476 · RESOLVED 1,078 · ACTIVE + ACKNOWLEDGED 170 · NORMAL + ACKNOWLEDGED 0 |
| Alarm instances (FIRST_SEEN) by priority | pri 3: 456 · pri 2: 384 · pri 9: 336 · pri 1: 82 |
| Distinct `alarm_object` / `alarm_text` | 290 / 336 over all rows (245 distinct texts at `FIRST_SEEN`; texts change during an alarm's life, e.g. `ATV21 Fejl` → `ATV21 OK`) |
| Most frequent texts (FIRST_SEEN) | `Høj Temperatur` (pri 2, 171) · `Høj rumtemperatur` (pri 3, 132) · `Lavt tryk` (pri 3, 114) · `Lav Temperatur` (pri 2, 69) · `Lav rumtemperatur` (48) · `Temperatur ved legionellabekæmpelse ikke opnået` (32) · `Drifttimer overskredet` (17) |
| Most recurring objects | `VISTA_SERVER-$EE_Mess` (309 instances), then room/zone temperature points such as `320-01-07930-0101_AL` (14×) and `300-01-04904-0104_AH` (13×) |

The 8.6k ACTIVE transitions for 1.26k alarm instances are the quantitative basis for the "same alarms keep flapping" analysis the owner asked for (the recurring-alarms page in digibuild, [ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)); the `count` column carries the same information per instance.

## Loading the files (migration hint)

Written before the importer existed; the digibuild `cts-alarms` importer has its own implementation. Kept as a guide for ad-hoc analysis of the archived files. All files share one header, so they concatenate trivially; the file name adds nothing that the `directory` column does not already contain.

```python
import glob, pandas as pd

frames = []
for path in glob.glob("csv/*.csv"):
    df = pd.read_csv(path, sep=";", encoding="utf-8", dtype=str, keep_default_na=False)
    frames.append(df)
events = pd.concat(frames, ignore_index=True)

# observation time: server local (Europe/Copenhagen) -> UTC
# ambiguous="infer" needs monotonic timestamps, and the frames were concatenated in glob order,
# so sort first (or use ambiguous="NaT" and fix the two ambiguous hours per year by hand)
events["observed_local"] = pd.to_datetime(events["datetime"], format="%d-%m-%Y %H:%M:%S")
events = events.sort_values("observed_local", kind="stable").reset_index(drop=True)
events["observed_at"] = (events["observed_local"]
                           .dt.tz_localize("Europe/Copenhagen", ambiguous="infer")
                           .dt.tz_convert("UTC"))
# Vista timestamps: prefer the hex epoch (already UTC) over the *_human columns.
# Do NOT use Series.replace("", None): in pandas < 3.0 that means method="pad" and would
# forward-fill the empty hex on synthetic rows with the previous row's value.
for col in ("date1", "date2"):
    events[f"{col}_utc"] = pd.to_datetime(events[f"{col}_hex"].map(lambda h: int(h, 16) if h else None),
                                          unit="s", utc=True)
events["synthetic"] = events["user"].eq("")
events["priority"] = pd.to_numeric(events["priority"])
```

Equivalent relational shape (one row per CSV row; a second table of alarm *instances* can be derived by grouping on `vista_id`):

```sql
CREATE TABLE alarm_event (
  id            BIGSERIAL PRIMARY KEY,
  observed_at   TIMESTAMPTZ NOT NULL,   -- from "datetime", converted to UTC
  event         TEXT        NOT NULL,   -- FIRST_SEEN | ACTIVE | NORMAL | ACKNOWLEDGED | UNACKNOWLEDGED | USER_CHANGED | STATE1_x_TO_y | STATE_CHANGED | RESOLVED
  vista_id      TEXT        NOT NULL,   -- VISTA_SERVER#xxxxxxxx
  alarm_object  TEXT        NOT NULL,
  directory     TEXT        NOT NULL,
  alarm_text    TEXT        NOT NULL,
  priority      SMALLINT    NOT NULL,
  "user"        TEXT,                   -- NULL on synthetic rows
  ack_flag      SMALLINT,
  state1        SMALLINT,
  state2        SMALLINT,
  date1_at      TIMESTAMPTZ,            -- from date1_hex
  date2_at      TIMESTAMPTZ,            -- from date2_hex
  count         INTEGER,
  status_label  TEXT        NOT NULL,
  synthetic     BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX ON alarm_event (vista_id, observed_at);
CREATE INDEX ON alarm_event (alarm_object, observed_at);
```

Things the importer had to decide (now on the digibuild side): DST handling for the two ambiguous local hours per year; whether operator names (`user`) are stored as-is or pseudonymised. (The raw `.alr` has trailing-space variants of some `alarm_text`s, but `parse_alarm_file()` strips them before any CSV row is written, so the CSV needs no de-duplication for that: 336 distinct texts before and after stripping.)
