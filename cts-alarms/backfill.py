"""
Backfill: send the CSV audit events of a past time window to digibuild.

Every event the bot logs goes to its CSV audit trail (one file per Vista
directory in the csv folder, written by run_csv_logging() in main.py) and,
while the shipper is on, to digibuild in that run's batch. Events of runs that
were never shipped (for example before the shipper was switched on) exist only
in the CSV files. This script reads them back and posts them to digibuild's
ingest route as backfill batches.

    python backfill.py --from "2026-09-28 17:30:00" --to "2026-09-30 13:42:10"
    python backfill.py --from "2026-09-28 17:30:00" --to "2026-09-30 13:42:10" --send

Both bounds are inclusive, in the CTS server's local time, without an offset.
--config works as for main.py: relative paths in it are relative to its folder.

Without --send it only reports: counts, the batches it would send, and a
cross-check against the "CSV audit: logged N events" lines of the daily logs.
It writes no file and makes no network call.

With --send it posts those batches, oldest first: at most 500 events each, and
the rows of one bot run (one CSV timestamp) are never split. Config, secret,
URL, HMAC signature and the POST itself are the live shipper's (shipper.py),
and every attempt is signed afresh. Network errors and 5xx are retried 3
times; anything else stops the script. It writes no file either: no outbox, no
names.json, no state.

Each event is rebuilt the way the live path builds it: main.event_record(),
then shipper.event_row(). The one thing the CSV cannot give back: a ";" in an
alarm text was written as "," (csv_row()), so it is sent as ",".

The body is IngestBatch schema_version 1 with "source": "cts-alarm-backfill",
"run": null, "snapshot": null and "incidents": []. digibuild drops events it
already has (same vista_id, ts and event), so running this twice is safe. A
server that does not take backfill batches yet answers HTTP 422; the script
then stops at once.

main.py does not import this file; the scheduled run is not affected.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

if __name__ == "__main__":
    # Run as a script, a report writes no file at all: not even the
    # __pycache__ that importing main and shipper would leave next to them.
    sys.dont_write_bytecode = True

import main as bot  # noqa: E402  importing main.py runs nothing; only pure helpers are used
import shipper      # noqa: E402  the live shipper's config, secret, signing and POST

SOURCE = "cts-alarm-backfill"
SCHEMA_VERSION = 1              # IngestBatch v1, the version whose events the live shipper sends
MAX_BATCH_EVENTS = 500
LOG_MATCH_S = 120               # a run logs "CSV audit: logged N" within this after its CSV time
RETRIES = 3                     # network errors and 5xx: this many retries after the first attempt
RETRY_BACKOFF_S = (2, 5, 10)
MIN_TIMEOUT_S = 60.0            # a 500-event batch may take the server longer than one run's batch

CLI_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
CSV_TIME_FORMAT = "%d-%m-%Y %H:%M:%S"       # run_csv_logging(): the run's time, no offset
LOG_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"       # setup_logging(): datefmt

CSV_HEADER = bot.CSV_HEADER.rstrip("\n")
C = {name: i for i, name in enumerate(CSV_HEADER.split(";"))}
CSV_FIELDS = len(C)                                     # 17
SYNTHETIC_EMPTY = range(C["user"], C["count"] + 1)      # empty on a synthetic NORMAL/RESOLVED row
EVENT_NAME = re.compile(r"(FIRST_SEEN|NORMAL|ACTIVE|ACKNOWLEDGED|UNACKNOWLEDGED|USER_CHANGED|"
                        r"STATE_CHANGED|STATE1_-?\d+_TO_-?\d+)\Z")   # classify_csv_event()
LOG_LINE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[[A-Z]+\] (.*)")
LOGGED = re.compile(r"CSV audit: logged (\d+) events\b")
LOG_FAILED = "CSV audit logging failed"

FAILED_422 = ("FAILED: the digibuild server does not accept backfill batches yet (HTTP 422). "
              "Nothing was changed. Run this again once the server is updated.")
HINTS = {401: " (check vps.ingest_secret and that this server's clock is within 300 s)",
         404: " (the ingest route is not deployed)",
         429: " (too many requests: wait a few minutes and run this again)"}

Out = Callable[[str], None]
_sleep = time.sleep             # tests replace this


def say(line: str = "") -> None:
    print(line, flush=True)


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ---------------------------------------------------------------------------
# Config and time
# ---------------------------------------------------------------------------

def load_bot_config(path: str) -> dict:
    """config.json exactly as main.main() prepares it: relative entries in
    "paths" are relative to the folder of config.json. Only reads the file."""
    cfg = bot.load_config(path)
    cfg["_config_path"] = str(Path(path).resolve())
    cfg["_config_dir"] = str(Path(path).resolve().parent)
    cfg["paths"] = {k: (os.path.normpath(bot.resolve_path(cfg, v))
                        if isinstance(v, str) and v and not k.startswith("_") else v)
                    for k, v in (cfg.get("paths") or {}).items()}
    return cfg


def local_time(text: str, fmt: str) -> datetime:
    """Server-local wall time without offset -> aware datetime, with the offset
    the server's clock had (astimezone(); no zoneinfo: tzdata may be missing)."""
    return datetime.strptime(text, fmt).astimezone()


def cli_time(text: str) -> datetime:
    for fmt in CLI_TIME_FORMATS:
        try:
            return local_time(text.strip(), fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f'expected "YYYY-MM-DD HH:MM:SS" (server local time, no offset), got "{text}"')


def show(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def origin(url: str) -> str:
    """scheme://host[:port] only: never a path, query or user part of a URL."""
    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")


# ---------------------------------------------------------------------------
# CSV rows -> events
# ---------------------------------------------------------------------------

class Malformed(ValueError):
    """A CSV row that the live path cannot have written this way."""


def _number(f: list, col: str, base: int = 10) -> int:
    try:
        return int(f[C[col]], base)
    except ValueError:
        raise Malformed(f"{col} is not a number") from None


def event_from_row(f: list, ts: datetime) -> dict:
    """The 17 fields of one CSV row -> the event exactly as the live shipper
    sends it: main.event_record() as run_csv_logging() calls it, then
    shipper.event_row(). Raises Malformed."""
    event = f[C["event"]]
    if not f[C["vista_id"]]:
        raise Malformed("vista_id is empty")
    if f[C["ack_flag"]] == "":
        # Synthetic NORMAL/RESOLVED row, written when an alarm left Vista's list.
        # The live path builds it from the csv_state entry: user..count are None.
        if event not in (bot.ALARM_NORMAL, bot.ALARM_RESOLVED):
            raise Malformed("ack_flag is empty but the event is not NORMAL or RESOLVED")
        if any(f[i] for i in SYNTHETIC_EMPTY):
            raise Malformed("ack_flag is empty but other state columns are set")
        pri = f[C["priority"]]
        entry = {"vista_id": f[C["vista_id"]], "alarm_object": f[C["alarm_object"]],
                 "directory": f[C["directory"]], "initial_alarm_text": f[C["alarm_text"]],
                 "priority": None if pri in ("", "None") else _number(f, "priority")}
        rec = bot.event_record(ts, event, entry=entry)
    else:
        if not EVENT_NAME.match(event):
            raise Malformed("unknown event name")
        alarm = bot.Alarm(
            vista_id=f[C["vista_id"]], alarm_object=f[C["alarm_object"]],
            date1_epoch=_number(f, "date1_hex", 16), state1=_number(f, "state1"),
            state2=_number(f, "state2"), date2_epoch=_number(f, "date2_hex", 16),
            priority=_number(f, "priority"), user=f[C["user"]],
            ack_flag=_number(f, "ack_flag"), alarm_text=f[C["alarm_text"]],
            count=_number(f, "count"), directory=f[C["directory"]])
        rec = bot.event_record(ts, event, alarm=alarm)
    if rec["status_label"] != f[C["status_label"]]:
        raise Malformed("status_label does not match the row's state columns")
    return shipper.event_row(rec)


@dataclass
class Row:
    ts: datetime            # the run's time (aware, local offset)
    event: dict             # as posted: shipper.event_row()


@dataclass
class Scan:
    files: int = 0
    data_rows: int = 0
    in_window: int = 0                  # well-formed rows in the window, duplicates included
    dup_in_file: int = 0
    dup_across_files: int = 0
    malformed: list = field(default_factory=list)       # (where, reason) in the window: fatal
    malformed_outside: int = 0
    rows: list = field(default_factory=list)            # Row, file order, without duplicates
    run_rows: Counter = field(default_factory=Counter)  # ts -> rows in the window (one run each)


def csv_files(folder: str) -> list:
    """The per-directory CSV files, by name. "~$..." files are Excel's lock files."""
    return sorted((p for p in Path(folder).glob("*.csv")
                   if p.is_file() and not p.name.startswith("~$")), key=lambda p: p.name)


def read_lines(path: Path):
    """(line number, text) for each line; rows end in "\\n" (or "\\r\\n")."""
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for n, raw in enumerate(data.split(b"\n"), 1):
        yield n, raw.decode("utf-8", errors="replace").rstrip("\r")


def scan_csv(folder: str, t_from: datetime, t_to: datetime) -> Scan:
    """Read every CSV file; keep the rows whose time is in [t_from, t_to].

    A row in the window must have 17 well-formed fields, else it is listed as
    malformed (fatal). A row whose time cannot be read counts as in the window
    unless its neighbours in the file (append-only, so in time order) place it
    outside. Rows outside the window are only counted. A repeated (vista_id,
    ts, event) is dropped: the server would drop it anyway."""
    scan = Scan()
    seen: dict = {}                     # (vista_id, ts, event) -> file of its first row
    for path in csv_files(folder):
        scan.files += 1
        rows = []                       # (line, fields, ts or None)
        for n, text in read_lines(path):
            if not text.strip() or text == CSV_HEADER:
                continue
            fields = text.split(";")
            try:
                ts: Optional[datetime] = local_time(fields[0], CSV_TIME_FORMAT)
            except ValueError:
                ts = None
            rows.append((n, fields, ts))
        scan.data_rows += len(rows)

        before, last = [], None         # the nearest readable time before / after each row
        for _, _, ts in rows:
            before.append(last)
            last = ts or last
        after, last = [None] * len(rows), None
        for i in range(len(rows) - 1, -1, -1):
            after[i] = last
            last = rows[i][2] or last

        for i, (n, fields, ts) in enumerate(rows):
            where = f"{path.name}:{n}"
            if ts is None:
                if (before[i] and before[i] > t_to) or (after[i] and after[i] < t_from):
                    scan.malformed_outside += 1
                else:
                    scan.malformed.append((where, "no readable date and time"))
                continue
            if not t_from <= ts <= t_to:
                if len(fields) != CSV_FIELDS:
                    scan.malformed_outside += 1
                continue
            if len(fields) != CSV_FIELDS:
                scan.malformed.append((where, f"{len(fields)} fields, expected {CSV_FIELDS}"))
                continue
            try:
                ev = event_from_row(fields, ts)
            except Malformed as e:
                scan.malformed.append((where, str(e)))
                continue
            scan.in_window += 1
            scan.run_rows[ts] += 1
            key = (ev["vista_id"], ev["ts"], ev["event"])
            if key in seen:
                if seen[key] == path.name:
                    scan.dup_in_file += 1
                else:
                    scan.dup_across_files += 1
                continue
            seen[key] = path.name
            scan.rows.append(Row(ts, ev))
    return scan


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

def plan_batches(rows: list, cap: int = MAX_BATCH_EVENTS) -> list:
    """Rows in time order, cut into batches of at most `cap` events.

    The sort is stable, so the file order is kept within one ts: a vanished
    alarm's synthetic NORMAL stays before its RESOLVED. The rows of one run
    (one ts) are never split; a single run bigger than `cap` goes alone in its
    own batch, as the live shipper would have sent it in one."""
    runs: list = []
    for r in sorted(rows, key=lambda row: row.ts):
        if runs and runs[-1][0].ts == r.ts:
            runs[-1].append(r)
        else:
            runs.append([r])
    batches: list = []
    for run in runs:
        if batches and len(batches[-1]) + len(run) <= cap:
            batches[-1].extend(run)
        else:
            batches.append(list(run))
    return batches


def build_body(events: list) -> dict:
    """IngestBatch v1 as a backfill: no run, no snapshot, no incidents."""
    return {"schema_version": SCHEMA_VERSION, "source": SOURCE, "run": None,
            "events": events, "snapshot": None, "incidents": []}


# ---------------------------------------------------------------------------
# Cross-check against the daily logs
# ---------------------------------------------------------------------------

@dataclass
class LogCheck:
    files: int = 0
    runs: int = 0
    match: int = 0
    differ: list = field(default_factory=list)      # (ts, N logged, rows in the CSV)
    missing: list = field(default_factory=list)     # ts of runs without a "logged N" line
    orphans: list = field(default_factory=list)     # (time, N) of "logged N" lines without CSV rows
    failed: list = field(default_factory=list)      # times of "CSV audit logging failed" lines


def read_logs(folder: str, t_from: datetime, t_to: datetime) -> tuple:
    """(files read, [(time, N) of each "CSV audit: logged N events"], [time of
    each "CSV audit logging failed" in the window]). A run writes to the file
    of the day it started, so the day before and after the window are read too."""
    logged, failed, files = [], [], 0
    day, last = (t_from - timedelta(days=1)).date(), (t_to + timedelta(days=1)).date()
    while day <= last:
        path = Path(folder) / f"{day:%Y-%m-%d}.log"
        day += timedelta(days=1)
        if not path.is_file():
            continue
        files += 1
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "CSV audit" not in line:
                    continue
                m = LOG_LINE.match(line)
                if not m:
                    continue
                try:
                    t, msg = local_time(m.group(1), LOG_TIME_FORMAT), m.group(2)
                except ValueError:                      # a garbled time
                    continue
                n = LOGGED.match(msg)
                if n:
                    logged.append((t, int(n.group(1))))
                elif msg.startswith(LOG_FAILED) and t_from <= t <= t_to:
                    failed.append(t)
    return files, sorted(logged), sorted(failed)


def cross_check(run_rows: Counter, logged: list, t_from: datetime, t_to: datetime) -> LogCheck:
    """Match each run (a ts with rows) to the first unused "logged N" line at or
    after its time and within LOG_MATCH_S, and compare N with its row count.
    A "logged N" line from LOG_MATCH_S after t_from up to t_to that no run
    claims means rows the log counted are missing from the CSV files."""
    chk = LogCheck(runs=len(run_rows))
    used = [False] * len(logged)
    j = 0
    for ts in sorted(run_rows):
        while j < len(logged) and logged[j][0] < ts:
            j += 1
        if j < len(logged) and logged[j][0] <= ts + timedelta(seconds=LOG_MATCH_S):
            used[j] = True
            n = logged[j][1]
            j += 1
            if n == run_rows[ts]:
                chk.match += 1
            else:
                chk.differ.append((ts, n, run_rows[ts]))
        else:
            chk.missing.append(ts)
    lo = t_from + timedelta(seconds=LOG_MATCH_S)
    chk.orphans = [(t, n) for (t, n), u in zip(logged, used) if not u and lo <= t <= t_to]
    return chk


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def http_status(detail: str) -> Optional[int]:
    """The status code in shipper._post()'s detail ("HTTP 503: ..."); None
    for a network error ("ConnectionError: ...")."""
    m = re.match(r"HTTP (\d{3})\b", detail)
    return int(m.group(1)) if m else None


def post_batch(shcfg, secret: str, payload: str, label: str, out: Out) -> tuple:
    """POST one batch with the live shipper's _post(), which signs every call
    afresh (new X-Timestamp and X-Nonce). Retries network errors and 5xx.
    Returns (answer, None), or (None, (kind, reason)) with kind "422",
    "refused" (not stored) or "answer" (accepted, but not the expected answer)."""
    for attempt in range(1, RETRIES + 2):
        answers: list = []
        status, detail = shipper._post(shcfg, secret, payload, answers)
        code = http_status(detail)
        if status == shipper.OK:
            if answers:
                return answers[-1], None
            return None, ("answer", f"{label}: HTTP {code}, but not the expected JSON answer")
        if code == 422:
            return None, ("422", "")
        if code is not None and code < 500:
            return None, ("refused", f"{label} was refused: HTTP {code}{HINTS.get(code, '')}")
        what = f"HTTP {code}" if code else detail.split(":", 1)[0]  # never the message: it may hold the URL
        if attempt > RETRIES:
            return None, ("refused", f"{label} could not be sent: {what}, {attempt} attempts")
        wait = RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S)) - 1]
        out(f"  {label}: {what}, trying again in {wait} s")
        _sleep(wait)
    raise AssertionError("unreachable")


def _count(answer: dict, key: str) -> Optional[int]:
    v = answer.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def send(batches: list, shcfg, secret: str, out: Out) -> int:
    total = sum(len(b) for b in batches)
    out(f"Sending {plural(total, 'event', 'events')} in "
        f"{plural(len(batches), 'batch', 'batches')} to {origin(shcfg.base_url)}")
    sent = inserted = duplicate = rebuilt = 0
    for k, batch in enumerate(batches, 1):
        label = f"batch {k}/{len(batches)}"
        payload = json.dumps(build_body([r.event for r in batch]), ensure_ascii=False)
        answer, failure = post_batch(shcfg, secret, payload, label, out)
        if answer is not None:
            ins, dup = _count(answer, "events_inserted"), _count(answer, "events_duplicate")
            received = answer.get("events_received", len(batch))
            if ins is None or dup is None:
                failure = ("answer", f"{label}: the answer has no events_inserted / events_duplicate")
            elif received != len(batch):
                failure = ("answer", f"{label}: the server received {received} of {len(batch)} events")
            else:
                reb = _count(answer, "instances_rebuilt") or 0      # missing on an older server
                sent, inserted, duplicate, rebuilt = (sent + len(batch), inserted + ins,
                                                      duplicate + dup, rebuilt + reb)
                out(f"  {label}: {len(batch)} events, {ins} inserted, {dup} already there, "
                    f"{reb} alarms rebuilt")
                continue
        kind, reason = failure
        if k > 1:
            out(f"  batches 1-{k - 1} ({sent} events) were accepted before this one")
        if kind == "422":
            out(FAILED_422)
        elif kind == "answer":
            out(f"FAILED: {reason}. It may have been stored; running this again is safe. "
                "Nothing more was sent.")
        else:
            out(f"FAILED: {reason}. " + ("Nothing more was sent; running this again is safe."
                                         if k > 1 else "Nothing was sent."))
        return 1
    out(f"  total: {sent} events, {inserted} inserted, {duplicate} already there, "
        f"{rebuilt} alarms rebuilt")
    out(f"OK: backfill sent — {sent} events, {inserted} inserted, {duplicate} already there, "
        f"{rebuilt} alarms rebuilt")
    return 0


def sender(cfg: dict) -> tuple:
    """(shipper config, secret, None) loaded exactly like the shipper, or
    (None, None, why not). Reads config and secrets file only; never logs."""
    try:
        shcfg = shipper.load_shipper_config(cfg)
    except ValueError as e:
        return None, None, f'bad "vps" section in config.json: {e}'
    if shcfg is None:
        return None, None, 'config.json has no "vps" section, or it has "enabled": false'
    try:
        secret = shipper.read_ingest_secret(shcfg)
    except (OSError, ValueError) as e:
        return None, None, f"no ingest secret: {e}"
    shcfg.timeout_seconds = max(shcfg.timeout_seconds, MIN_TIMEOUT_S)
    return shcfg, secret, None


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(out: Out, cfg: dict, t_from: datetime, t_to: datetime, scan: Scan,
           chk: Optional[LogCheck], batches: list, not_ready: Optional[str]) -> None:
    """Counts only: never an alarm text, a user name, the secret or a URL query."""
    events = [r.event for r in scan.rows]
    offsets = sorted({t_from.isoformat()[-6:], t_to.isoformat()[-6:]})
    by_type = Counter(e["event"] for e in events)
    by_day = Counter(r.ts.strftime("%Y-%m-%d") for r in scan.rows)
    resolved = {e["vista_id"] for e in events if e["event"] == bot.ALARM_RESOLVED}
    out(f"Backfill {show(t_from)} .. {show(t_to)} (server local time, UTC{'/'.join(offsets)}, "
        "both ends included)")
    out(f"  config:              {cfg['_config_path']}")
    out(f"  CSV files scanned:   {scan.files} in {cfg['paths']['csv_folder']} "
        f"({scan.data_rows} rows)")
    out(f"  rows in the window:  {scan.in_window}")
    out(f"  duplicates dropped:  {scan.dup_in_file} (same vista_id, ts and event again in one file)"
        + (f", {scan.dup_across_files} across files" if scan.dup_across_files else ""))
    out(f"  malformed rows:      {len(scan.malformed)} in the window, "
        f"{scan.malformed_outside} outside it (ignored)")
    out(f"  events to send:      {len(events)}")
    out(f"  distinct alarms:     {len({e['vista_id'] for e in events})} vista_ids, "
        f"{len(resolved)} of them RESOLVED in the window")
    out(f"  runs (ts groups):    {len(scan.run_rows)}")
    out("  by type:             " + (", ".join(f"{k} {v}" for k, v in by_type.most_common()) or "-"))
    out("  by day:              " + (", ".join(f"{k} {by_day[k]}" for k in sorted(by_day)) or "-"))
    for where, why in scan.malformed[:20]:
        out(f"  MALFORMED {where}: {why}")
    if len(scan.malformed) > 20:
        out(f"  MALFORMED ... and {len(scan.malformed) - 20} more")

    if chk is None:
        out("Log cross-check: skipped (config.json has no paths.log_folder)")
    else:
        out(f"Log cross-check ({plural(chk.files, 'daily log file', 'daily log files')}):")
        out(f'  {plural(chk.runs, "run", "runs")}: {chk.match} match their "CSV audit: logged N" '
            f"line, {len(chk.differ)} do not, {len(chk.missing)} have no such line")
        if chk.differ:
            out("  first that do not:   " + ", ".join(
                f"{show(ts)} (log {n}, CSV {rows})" for ts, n, rows in chk.differ[:5]))
        if chk.missing:
            out("  first without line:  " + ", ".join(show(ts) for ts in chk.missing[:5]))
        if chk.orphans:
            out(f"  WARNING: {plural(len(chk.orphans), 'log line', 'log lines')} "
                f'"CSV audit: logged N" in the window without CSV rows '
                f"({sum(n for _, n in chk.orphans)} events), first at "
                + ", ".join(show(t) for t, _ in chk.orphans[:5]))
        for t in chk.failed[:10]:
            out(f'  WARNING: "{LOG_FAILED}" at {show(t)}')
        if len(chk.failed) > 10:
            out(f'  WARNING: ... and {len(chk.failed) - 10} more "{LOG_FAILED}" lines')

    out(f"Batches to send (at most {MAX_BATCH_EVENTS} events; one run is never split):")
    for k, b in enumerate(batches, 1):
        runs = len({r.ts for r in b})
        big = f" (one run over the {MAX_BATCH_EVENTS} cap)" if len(b) > MAX_BATCH_EVENTS else ""
        out(f"  batch {k}: {plural(len(b), 'event', 'events')}, {plural(runs, 'run', 'runs')}, "
            f"{show(b[0].ts)} .. {show(b[-1].ts)}{big}")
    if not batches:
        out("  none")
    out("  sending:             " + (f"NOT READY: {not_ready}" if not_ready
                                     else "ready (vps config and ingest secret found)"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None, out: Out = say) -> int:
    ap = argparse.ArgumentParser(
        description="Send the CSV audit events of a past time window to digibuild. "
                    "Without --send: a read-only report.")
    ap.add_argument("--from", dest="t_from", required=True, type=cli_time,
                    help='first CSV time to include, "YYYY-MM-DD HH:MM:SS", server local time')
    ap.add_argument("--to", dest="t_to", required=True, type=cli_time,
                    help='last CSV time to include, "YYYY-MM-DD HH:MM:SS", server local time')
    ap.add_argument("--send", action="store_true", help="send the batches (default: report only)")
    ap.add_argument("--config", default=bot.DEFAULT_CONFIG_PATH)
    args = ap.parse_args(argv)
    if args.t_from > args.t_to:
        ap.error("--from is after --to")

    try:
        cfg = load_bot_config(args.config)
    except Exception as e:
        out(f"FAILED: could not read the config ({args.config}): {e}")
        return 1
    csv_folder = cfg["paths"].get("csv_folder")
    if not csv_folder or not Path(csv_folder).is_dir():
        out(f"FAILED: no CSV folder (paths.csv_folder in {cfg['_config_path']}: {csv_folder or '-'})")
        return 1

    scan = scan_csv(csv_folder, args.t_from, args.t_to)
    chk = None
    if cfg["paths"].get("log_folder"):
        files, logged, failed = read_logs(cfg["paths"]["log_folder"], args.t_from, args.t_to)
        chk = cross_check(scan.run_rows, logged, args.t_from, args.t_to)
        chk.files, chk.failed = files, failed
    batches = plan_batches(scan.rows)
    shcfg, secret, not_ready = sender(cfg)
    report(out, cfg, args.t_from, args.t_to, scan, chk, batches, not_ready)

    if scan.malformed:
        out(f"FAILED: {plural(len(scan.malformed), 'malformed row', 'malformed rows')} in the "
            "window (listed above). Nothing was sent.")
        return 1
    if not args.send:
        out(f"OK: report only, nothing was sent. {plural(len(scan.rows), 'event', 'events')} in "
            f"{plural(len(batches), 'batch', 'batches')} would be sent; add --send to send them.")
        return 0
    if not batches:
        out("OK: nothing to send, there are no events in the window.")
        return 0
    if not_ready:
        out(f"FAILED: {not_ready}. Nothing was sent.")
        return 1
    return send(batches, shcfg, secret, out)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")    # a console code page must not stop the run
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
