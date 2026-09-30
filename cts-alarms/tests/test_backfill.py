"""Tests for backfill.py: CSV rows back to the live path's events, the time
window, malformed rows, duplicates, batching, the log cross-check, a report
that touches nothing, and sending (HMAC per attempt, 422, retries, no outbox,
no names.json)."""
import ast
import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from socket import socket as Socket   # the scope guard reads <module>.socket as a unit name

import pytest
import requests

import backfill
import main
import shipper

ROOT = Path(__file__).resolve().parent.parent
SECRET = "hmac-test-SECRET-0123456789abcdef"
LOG = logging.getLogger("test-backfill")
FROM, TO = "2026-09-29 10:00:00", "2026-09-29 12:00:00"
FAILED_422 = ("FAILED: the digibuild server does not accept backfill batches yet (HTTP 422). "
              "Nothing was changed. Run this again once the server is updated.")
TEXT, USER = "Høj Temperatur", "OPR1 (Operator One FM)"     # must never be printed


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def copenhagen(monkeypatch):
    """Local time of the CTS server (+02:00 in September) where time.tzset exists."""
    if not hasattr(time, "tzset"):
        yield
        return
    monkeypatch.setenv("TZ", "Europe/Copenhagen")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The bot's folder as on the CTS server: the committed config.json (relative
    paths), a secrets.json holding the ingest secret, and a logs folder."""
    monkeypatch.delenv(shipper.SECRET_ENV, raising=False)
    folder = tmp_path / "app"
    folder.mkdir()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    cfg["vps"]["base_url"] = "https://api.example.test"
    (folder / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    (folder / "secrets.json").write_text(json.dumps({"vps": {"ingest_secret": SECRET}}),
                                         encoding="utf-8")
    (folder / "logs").mkdir()
    return folder


class Clock(datetime):
    """Stands in for main.datetime: now() is the local time of a bot run."""
    at = datetime(2026, 9, 29, 10, 0, 3)

    @classmethod
    def now(cls, tz=None):
        return cls.at if tz is None else cls.at.astimezone(tz)


@pytest.fixture
def bot(app, monkeypatch):
    """Runs the live path, main.run_csv_logging(), at a chosen local time: it
    writes the app's csv/ and csv_state.json and returns the events it built."""
    monkeypatch.setattr(main, "datetime", Clock)

    def run(alarms, at):
        Clock.at = datetime.strptime(at, "%Y-%m-%d %H:%M:%S")
        return main.run_csv_logging(alarms, str(app / "csv"), str(app / "csv_state.json"), LOG)
    return run


def alarm(n, state1=0, ack=0, user="No user", text=TEXT, directory=None, priority=2):
    return main.Alarm(
        vista_id=f"VISTA_SERVER#{n:08X}", alarm_object=f"325-10-{n:05d}-Udsugning",
        date1_epoch=0x6A3BE340 + n, state1=state1, state2=0, date2_epoch=0x6A3BE59B + n,
        priority=priority, user=user, ack_flag=ack, alarm_text=text, count=n,
        directory=directory or f"VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-{n:05d}_AL")


def iso(local):
    """The ts the live shipper sends for a run at this local time."""
    return datetime.strptime(local, "%Y-%m-%d %H:%M:%S").astimezone().isoformat(timespec="seconds")


def scan(app, t_from=FROM, t_to=TO):
    return backfill.scan_csv(str(app / "csv"), backfill.cli_time(t_from), backfill.cli_time(t_to))


def run_backfill(app, *extra, t_from=FROM, t_to=TO):
    out = []
    rc = backfill.main(["--from", t_from, "--to", t_to, "--config", str(app / "config.json"),
                        *extra], out=out.append)
    return rc, out


def the_csv(app):
    (path,) = (app / "csv").glob("*.csv")
    return path


def tree(path):
    """Every file and folder below `path`, with each file's content hash and mtime."""
    return {str(p.relative_to(path)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                       p.stat().st_mtime_ns) if p.is_file() else "dir"
            for p in sorted(path.rglob("*"))}


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no network call allowed")
    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(Socket, "connect", boom)


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body, ensure_ascii=False) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


def verify(call, secret=SECRET):
    """Server-side HMAC check over the raw bytes received, as digibuild does it."""
    h = call["headers"]
    msg = f"{h['X-Timestamp']}.{h['X-Nonce']}.".encode("utf-8") + call["raw"]
    want = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, h["X-Signature"])


class FakeIngest:
    """digibuild's ingest route as the backfill contract describes it: checks
    the signature, drops events it already has (vista_id, ts, event) and
    answers with the counts, plus `names`, which the backfill must ignore.
    Each `script` entry is the status code (or exception) of the next call;
    an error answer echoes the secret and alarm data, which must never be printed."""

    def __init__(self, *script, rebuilt=True, answer=None):
        self.script, self.calls, self.stored = list(script), [], set()
        self.rebuilt, self.answer = rebuilt, answer

    def __call__(self, url, data=None, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "raw": data, "headers": headers, "timeout": timeout})
        item = self.script.pop(0) if self.script else 200
        if isinstance(item, Exception):
            raise item
        if item != 200:
            return FakeResponse(item, {"detail": f"{SECRET} {TEXT} {USER}"})
        assert verify(self.calls[-1])
        if self.answer is not None:
            return FakeResponse(200, self.answer)
        keys = [(e["vista_id"], e["ts"], e["event"])
                for e in json.loads(data.decode("utf-8"))["events"]]
        new = [k for k in keys if k not in self.stored]
        self.stored.update(new)
        answer = {"run_id": "backfill", "run_created": False, "events_received": len(keys),
                  "events_inserted": len(new), "events_duplicate": len(keys) - len(new),
                  "instances_touched": len({k[0] for k in keys}), "incidents_upserted": 0,
                  "names": {"version": "n1", "points": [{"directory": "X", "name": "Rum 1"}]}}
        if self.rebuilt:
            answer["instances_rebuilt"] = len({k[0] for k in new})
        return FakeResponse(200, answer)


@pytest.fixture
def ingest(monkeypatch):
    """Install a FakeIngest as requests.post; retries do not wait."""
    waits = []
    monkeypatch.setattr(backfill, "_sleep", waits.append)

    def install(*script, **kw):
        server = FakeIngest(*script, **kw)
        server.waits = waits
        monkeypatch.setattr(shipper.requests, "post", server)
        return server
    return install


# ---------------------------------------------------------------------------
# CSV rows -> the live path's events
# ---------------------------------------------------------------------------

def test_round_trip_gives_exactly_the_live_events(app, bot):
    """Rows written by run_csv_logging() come back as the events the live
    shipper builds from the same runs: event_record() + event_row()."""
    a1, a3 = alarm(1), alarm(3, state1=1)
    a2 = alarm(2, ack=1, user=USER, text="ATV61 Fejl på blæser")
    live = bot([a1, a2, a3], "2026-09-29 10:00:03")                  # 3 x FIRST_SEEN
    a1b = alarm(1, state1=1, ack=1, user=USER)
    live += bot([a1b, a3], "2026-09-29 10:05:02")    # a1 NORMAL + ACKNOWLEDGED; a2 gone while ACTIVE
    live += bot([a1b], "2026-09-29 10:10:02")        # a3 gone while NORMAL: RESOLVED only
    assert [e["event"] for e in live].count("RESOLVED") == 2

    got = [r.event for r in scan(app).rows]
    want = [shipper.event_row(e) for e in live]
    as_json = lambda e: json.dumps(e, sort_keys=True, ensure_ascii=False)  # noqa: E731
    assert sorted(map(as_json, got)) == sorted(map(as_json, want))
    assert all(list(e) == [*shipper.ALARM_FIELDS, "ts", "event"] for e in got)

    by = {(e["vista_id"], e["event"]): e for e in got}
    real = by[(a2.vista_id, "FIRST_SEEN")]
    assert (real["user"], real["ack_flag"], real["state1"], real["count"]) == (USER, 1, 0, 2)
    assert real["date1_epoch"] == 0x6A3BE342 and real["status_label"] == "ACTIVE + ACKNOWLEDGED"
    assert real["alarm_text"] == "ATV61 Fejl på blæser" and real["ts"] == iso("2026-09-29 10:00:03")
    for event in ("NORMAL", "RESOLVED"):                  # synthetic rows: None like the live path
        synthetic = by[(a2.vista_id, event)]
        assert synthetic["status_label"] == event and synthetic["priority"] == 2
        for f in ("user", "ack_flag", "state1", "state2", "date1_epoch", "date2_epoch", "count"):
            assert synthetic[f] is None, f


def test_ts_is_iso_8601_with_the_local_offset(app, bot):
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset to set the server's zone")
    bot([alarm(1)], "2026-09-29 10:00:03")
    assert [r.event["ts"] for r in scan(app).rows] == ["2026-09-29T10:00:03+02:00"]


def test_semicolon_in_an_alarm_text_comes_back_as_a_comma(app, bot):
    live = bot([alarm(1, text="Fejl; tjek pumpe")], "2026-09-29 10:00:03")
    (got,) = [r.event for r in scan(app).rows]
    want = shipper.event_row(live[0])
    assert got["alarm_text"] == "Fejl, tjek pumpe"             # csv_row() wrote "," for ";"
    assert {**got, "alarm_text": want["alarm_text"]} == want


def test_window_bounds_are_inclusive(app, bot):
    for i, at in enumerate(("2026-09-29 09:59:59", FROM, TO, "2026-09-29 12:00:01")):
        bot([alarm(1, state1=i % 2)], at)                   # one change per run
    rows = scan(app).rows
    assert [(r.event["ts"], r.event["event"]) for r in rows] == [
        (iso(FROM), "NORMAL"), (iso(TO), "ACTIVE")]


def test_windows_line_endings_bom_and_excel_lock_files(app, bot):
    bot([alarm(1), alarm(2, state1=1)], "2026-09-29 10:00:03")
    want = [r.event for r in scan(app).rows]
    for path in (app / "csv").glob("*.csv"):               # as written in text mode on Windows
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    (app / "csv" / "~$locked.csv").write_bytes(b"\x0bOPR1 not a csv file")
    s = scan(app)
    assert [r.event for r in s.rows] == want
    assert s.files == 2 and not s.malformed


# ---------------------------------------------------------------------------
# Malformed rows and duplicates
# ---------------------------------------------------------------------------

def test_malformed_row_in_the_window_fails_with_file_and_line(app, bot, no_network):
    bot([alarm(1)], "2026-09-29 10:00:03")
    path = the_csv(app)
    with open(path, "a", encoding="utf-8") as f:
        f.write("29-09-2026 10:05:02;ACTIVE;VISTA_SERVER#00000001;too;few\n")      # line 3
    for extra in ((), ("--send",)):
        rc, out = run_backfill(app, *extra)
        assert rc == 1
        assert f"  MALFORMED {path.name}:3: 5 fields, expected 17" in out
        assert out[-1] == "FAILED: 1 malformed row in the window (listed above). Nothing was sent."


@pytest.mark.parametrize("row, why", [
    ("29-09-2026 10:05:02;ACTIVE;VISTA_SERVER#1;o;d;t;x;No user;0;0;0;6A3BE340;;6A3BE340;;1;ACTIVE",
     "priority is not a number"),
    ("29-09-2026 10:05:02;BOGUS;VISTA_SERVER#1;o;d;t;2;No user;0;0;0;6A3BE340;;6A3BE340;;1;ACTIVE",
     "unknown event name"),
    ("29-09-2026 10:05:02;ACTIVE;VISTA_SERVER#1;o;d;t;2;No user;0;0;0;6A3BE340;;6A3BE340;;1;NORMAL",
     "status_label does not match the row's state columns"),
    ("29-09-2026 10:05:02;ACTIVE;VISTA_SERVER#1;o;d;t;2;;;;;;;;;;ACTIVE",
     "ack_flag is empty but the event is not NORMAL or RESOLVED"),
    ("29-09-2026 10:05:02;RESOLVED;VISTA_SERVER#1;o;d;t;2;x;;;;;;;;;RESOLVED",
     "ack_flag is empty but other state columns are set"),
])
def test_rows_the_live_path_cannot_have_written_are_malformed(app, bot, row, why):
    bot([alarm(1)], "2026-09-29 10:00:03")
    with open(the_csv(app), "a", encoding="utf-8") as f:
        f.write(row + "\n")
    assert scan(app).malformed == [(f"{the_csv(app).name}:3", why)]


def test_malformed_rows_outside_the_window_are_only_counted(app, bot):
    bot([alarm(1)], "2026-09-29 10:00:03")
    bot([alarm(1, state1=1)], "2026-09-29 13:00:00")
    with open(the_csv(app), "a", encoding="utf-8") as f:
        f.write("29-09-2026 13:05:00;ACTIVE;short\n")       # after the window
        f.write("a line without a readable time\n")         # after rows that are after the window
    s = scan(app)
    assert (s.malformed, s.malformed_outside, len(s.rows)) == ([], 2, 1)
    rc, out = run_backfill(app)
    assert rc == 0 and "  malformed rows:      0 in the window, 2 outside it (ignored)" in out


def test_a_row_without_a_readable_time_between_rows_of_the_window_is_fatal(app, bot):
    bot([alarm(1)], "2026-09-29 10:00:03")
    with open(the_csv(app), "a", encoding="utf-8") as f:
        f.write("29-09-2026 10:0\n")                        # a torn write
    bot([alarm(1, state1=1)], "2026-09-29 10:05:02")
    assert scan(app).malformed == [(f"{the_csv(app).name}:3", "no readable date and time")]


def test_in_file_duplicates_are_dropped_and_counted(app, bot):
    bot([alarm(1), alarm(2, directory="VISTA_SERVER-OTHER_AL")], "2026-09-29 10:00:03")
    path = sorted((app / "csv").glob("*.csv"))[0]
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines + lines[1:]), encoding="utf-8")         # the row again
    s = scan(app)
    assert (s.in_window, s.dup_in_file, s.dup_across_files, len(s.rows)) == (3, 1, 0, 2)
    assert s.run_rows[backfill.cli_time("2026-09-29 10:00:03")] == 3     # the log counts rows
    rc, out = run_backfill(app)
    assert rc == 0
    assert "  rows in the window:  3" in out and "  events to send:      2" in out
    assert "  duplicates dropped:  1 (same vista_id, ts and event again in one file)" in out


# ---------------------------------------------------------------------------
# Batches and body
# ---------------------------------------------------------------------------

def test_batches_respect_the_cap_and_never_split_a_run():
    t0 = backfill.cli_time(FROM)
    sizes = [300, 250, 499, 1, 600, 10, 500]
    rows = [backfill.Row(t0 + timedelta(minutes=5 * i), {"run": i, "n": n})
            for i, size in enumerate(sizes) for n in range(size)]
    batches = backfill.plan_batches(rows[::-1])     # any input order: batches go oldest first
    assert [len(b) for b in batches] == [300, 250, 500, 600, 10, 500]
    for b in batches:
        assert len(b) <= backfill.MAX_BATCH_EVENTS or len({r.ts for r in b}) == 1
    run_batches = {}
    for k, b in enumerate(batches):
        for r in b:
            run_batches.setdefault(r.event["run"], set()).add(k)
    assert all(len(ks) == 1 for ks in run_batches.values())           # no run split
    flat = [r.ts for b in batches for r in b]
    assert flat == sorted(flat) and len(flat) == sum(sizes)


def test_file_order_is_kept_within_one_ts_normal_before_resolved(app, bot):
    """One directory, one run: alarm 9 goes NORMAL + ACKNOWLEDGED, alarm 1
    vanishes while ACTIVE (synthetic NORMAL, then RESOLVED). Neither sorting by
    event nor by vista_id would keep this order; the stable sort by ts does."""
    one_dir = "VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-SAME_AL"
    a9, a1 = alarm(9, directory=one_dir), alarm(1, directory=one_dir)
    bot([a9, a1], "2026-09-29 10:00:03")
    live = bot([alarm(9, state1=1, ack=1, user=USER, directory=one_dir)], "2026-09-29 10:05:02")
    order = [(e["vista_id"], e["event"]) for e in live]
    assert order == [(a9.vista_id, "NORMAL"), (a9.vista_id, "ACKNOWLEDGED"),
                     (a1.vista_id, "NORMAL"), (a1.vista_id, "RESOLVED")]
    (batch,) = backfill.plan_batches(scan(app).rows)
    assert [(r.event["vista_id"], r.event["event"]) for r in batch][2:] == order


def test_body_is_a_backfill_batch():
    body = backfill.build_body([{"vista_id": "V"}])
    assert body == {"schema_version": 1, "source": "cts-alarm-backfill", "run": None,
                    "events": [{"vista_id": "V"}], "snapshot": None, "incidents": []}
    assert list(body) == ["schema_version", "source", "run", "events", "snapshot", "incidents"]


# ---------------------------------------------------------------------------
# Log cross-check
# ---------------------------------------------------------------------------

def test_log_cross_check_counts_matches_and_mismatches(app, bot):
    bot([alarm(1), alarm(2)], "2026-09-29 10:00:03")                        # 2 rows: log 2
    bot([alarm(1, state1=1), alarm(2)], "2026-09-29 10:05:02")              # 1 row: log 3
    bot([alarm(1, state1=1), alarm(2, state1=1)], "2026-09-29 10:10:02")    # 1 row: no line
    bot([alarm(1), alarm(2, state1=1)], "2026-09-29 23:59:59")              # 1 row: logged after midnight
    (app / "logs" / "2026-09-29.log").write_text("\n".join([
        "2026-09-29 10:00:04 [INFO] CSV audit: logged 2 events across alarm directories",
        f"2026-09-29 10:00:04 [INFO] NEW ALARM: {TEXT} ({USER})",
        "2026-09-29 10:05:03 [INFO] CSV audit: logged 3 events across alarm directories",
        "2026-09-29 10:12:00 [ERROR] CSV audit logging failed (non-fatal): disk full",
        "2026-09-29 10:30:00 [INFO] CSV audit: logged 4 events across alarm directories",
        "2026-09-29 11:00:00 [INFO] CSV audit: [DRY] would log 7 events across alarm directories",
        "2026-09-30 00:00:01 [INFO] CSV audit: logged 1 events across alarm directories",
    ]) + "\n", encoding="utf-8")
    t_from, t_to = backfill.cli_time(FROM), backfill.cli_time("2026-09-30 00:30:00")
    s = backfill.scan_csv(str(app / "csv"), t_from, t_to)
    files, logged, failed = backfill.read_logs(str(app / "logs"), t_from, t_to)
    chk = backfill.cross_check(s.run_rows, logged, t_from, t_to)
    assert files == 1 and len(logged) == 4
    assert (chk.runs, chk.match) == (4, 2)
    assert chk.differ == [(backfill.cli_time("2026-09-29 10:05:02"), 3, 1)]
    assert chk.missing == [backfill.cli_time("2026-09-29 10:10:02")]
    assert chk.orphans == [(backfill.cli_time("2026-09-29 10:30:00"), 4)]
    assert failed == [backfill.cli_time("2026-09-29 10:12:00")]

    rc, out = run_backfill(app, t_to="2026-09-30 00:30:00")
    assert rc == 0
    assert '  4 runs: 2 match their "CSV audit: logged N" line, 1 do not, 1 have no such line' in out
    assert "  first that do not:   2026-09-29 10:05:02 (log 3, CSV 1)" in out
    assert "  first without line:  2026-09-29 10:10:02" in out
    assert ('  WARNING: 1 log line "CSV audit: logged N" in the window without CSV rows (4 events), '
            "first at 2026-09-29 10:30:00") in out
    assert '  WARNING: "CSV audit logging failed" at 2026-09-29 10:12:00' in out
    assert not any(TEXT in line or USER in line for line in out)


def test_cross_check_shows_the_first_five_that_do_not_match():
    t0 = backfill.cli_time(FROM)
    runs = {t0 + timedelta(minutes=5 * i): 1 for i in range(7)}
    logged = [(ts + timedelta(seconds=1), 2) for ts in runs]
    chk = backfill.cross_check(runs, logged, t0, t0 + timedelta(hours=1))
    assert (chk.match, len(chk.differ), chk.missing, chk.orphans) == (0, 7, [], [])
    out = []
    backfill.report(out.append, {"_config_path": "config.json", "paths": {"csv_folder": "csv"}},
                    t0, t0, backfill.Scan(), chk, [], None)
    (line,) = [x for x in out if x.startswith("  first that do not:")]
    assert line.count("(log 2, CSV 1)") == 5


# ---------------------------------------------------------------------------
# Report: read-only
# ---------------------------------------------------------------------------

def test_report_makes_no_network_call_and_writes_no_file(app, bot, no_network):
    bot([alarm(1), alarm(2, ack=1, user=USER)], "2026-09-29 10:00:03")
    (app / "logs" / "2026-09-29.log").write_text(
        "2026-09-29 10:00:04 [INFO] CSV audit: logged 2 events across alarm directories\n",
        encoding="utf-8")
    before = tree(app.parent)
    rc, out = run_backfill(app)
    assert rc == 0
    assert tree(app.parent) == before
    assert out[-1] == ("OK: report only, nothing was sent. 2 events in 1 batch would be sent; "
                       "add --send to send them.")
    assert "  by type:             FIRST_SEEN 2" in out
    assert "  by day:              2026-09-29 2" in out
    assert "  distinct alarms:     2 vista_ids, 0 of them RESOLVED in the window" in out
    assert "  runs (ts groups):    1" in out
    assert '  1 run: 1 match their "CSV audit: logged N" line, 0 do not, 0 have no such line' in out
    assert "  batch 1: 2 events, 1 run, 2026-09-29 10:00:03 .. 2026-09-29 10:00:03" in out
    assert "  sending:             ready (vps config and ingest secret found)" in out
    text = "\n".join(out)
    assert SECRET not in text and TEXT not in text and USER not in text


def test_script_run_writes_no_bytecode_either(app, bot, tmp_path):
    """As a script (how the owner runs it) a report leaves no file behind, not
    even __pycache__ next to main.py and shipper.py."""
    bot([alarm(1)], "2026-09-29 10:00:03")
    code = tmp_path / "code"
    code.mkdir()
    for name in ("main.py", "shipper.py", "backfill.py"):
        shutil.copy(ROOT / name, code / name)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    before = tree(app)
    r = subprocess.run([sys.executable, str(code / "backfill.py"), "--from", FROM, "--to", TO,
                        "--config", str(app / "config.json")],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-1].startswith("OK: report only, nothing was sent. 1 event")
    assert not (code / "__pycache__").exists()
    assert tree(app) == before


def test_paths_resolve_next_to_config_json_like_main(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                     # the working folder must not matter
    cfg = backfill.load_bot_config(str(app / "config.json"))
    assert cfg["paths"]["csv_folder"] == str(app / "csv")
    assert cfg["paths"]["log_folder"] == str(app / "logs")
    assert shipper.load_shipper_config(cfg).secrets_file == str(app / "secrets.json")


def test_the_scheduled_bot_does_not_import_backfill():
    for name in ("main.py", "shipper.py"):
        mod = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(mod) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module for n in ast.walk(mod) if isinstance(n, ast.ImportFrom)}
        assert "backfill" not in imported, name


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def test_send_signs_every_attempt_afresh_retries_and_writes_nothing(app, bot, ingest):
    bot([alarm(1), alarm(2, ack=1, user=USER)], "2026-09-29 10:00:03")
    bot([alarm(1, state1=1)], "2026-09-29 10:05:02")               # NORMAL; alarm 2 vanishes
    server = ingest(503, requests.ConnectionError("down"), 200)
    before = tree(app)
    rc, out = run_backfill(app, "--send")
    assert rc == 0, out
    assert len(server.calls) == 3 and server.waits == [2, 5]
    assert "  batch 1/1: HTTP 503, trying again in 2 s" in out
    assert "  batch 1/1: ConnectionError, trying again in 5 s" in out
    for call in server.calls:
        assert call["url"] == "https://api.example.test/internal/cts-alarms/v1/ingest"
        assert verify(call) and not verify(call, SECRET + "x")
        assert uuid.UUID(call["headers"]["X-Nonce"]).version == 4
        assert abs(int(call["headers"]["X-Timestamp"]) - time.time()) < 10
        assert call["timeout"] >= backfill.MIN_TIMEOUT_S
    assert len({c["headers"]["X-Nonce"] for c in server.calls}) == 3
    assert len({c["headers"]["X-Signature"] for c in server.calls}) == 3
    body = json.loads(server.calls[-1]["raw"].decode("utf-8"))
    assert (body["schema_version"], body["source"], body["run"], body["snapshot"],
            body["incidents"]) == (1, "cts-alarm-backfill", None, None, [])
    assert [e["event"] for e in body["events"]] == ["FIRST_SEEN", "FIRST_SEEN",
                                                    "NORMAL", "NORMAL", "RESOLVED"]
    assert "  batch 1/1: 5 events, 5 inserted, 0 already there, 2 alarms rebuilt" in out
    assert out[-1] == "OK: backfill sent — 5 events, 5 inserted, 0 already there, 2 alarms rebuilt"
    assert tree(app) == before                       # no outbox, no names.json, nothing else
    assert not (app / "outbox.sqlite").exists() and not (app / "names.json").exists()
    assert SECRET not in "\n".join(out)


def test_running_twice_is_safe_the_second_run_finds_everything(app, bot, ingest):
    bot([alarm(1), alarm(2)], "2026-09-29 10:00:03")
    server = ingest()
    assert run_backfill(app, "--send")[0] == 0
    rc, out = run_backfill(app, "--send")
    assert rc == 0 and len(server.calls) == 2
    assert out[-1] == "OK: backfill sent — 2 events, 0 inserted, 2 already there, 0 alarms rebuilt"


def test_an_older_server_without_instances_rebuilt_counts_zero(app, bot, ingest):
    bot([alarm(1)], "2026-09-29 10:00:03")
    ingest(rebuilt=False)
    rc, out = run_backfill(app, "--send")
    assert rc == 0
    assert out[-1] == "OK: backfill sent — 1 events, 1 inserted, 0 already there, 0 alarms rebuilt"


def test_422_is_a_clean_stop_with_the_exact_message(app, bot, ingest):
    bot([alarm(1)], "2026-09-29 10:00:03")
    server = ingest(422)
    before = tree(app)
    rc, out = run_backfill(app, "--send")
    assert rc == 1
    assert len(server.calls) == 1 and server.waits == []          # never retried
    assert out[-1] == FAILED_422
    assert tree(app) == before
    text = "\n".join(out)
    assert SECRET not in text and TEXT not in text and USER not in text


def test_422_after_an_accepted_batch_says_so_and_stops(app, bot, ingest):
    one_dir = "VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-SAME_AL"
    bot([alarm(n, directory=one_dir) for n in range(1, 301)], "2026-09-29 10:00:03")
    bot([alarm(n, state1=1, directory=one_dir) for n in range(1, 301)], "2026-09-29 10:05:02")
    server = ingest(200, 422)
    rc, out = run_backfill(app, "--send")
    assert rc == 1 and len(server.calls) == 2
    assert "  batch 1/2: 300 events, 300 inserted, 0 already there, 300 alarms rebuilt" in out
    assert out[-2:] == ["  batches 1-1 (300 events) were accepted before this one", FAILED_422]


def test_5xx_is_retried_three_times_then_fails(app, bot, ingest):
    bot([alarm(1)], "2026-09-29 10:00:03")
    server = ingest(500, 502, 503, 504)
    rc, out = run_backfill(app, "--send")
    assert rc == 1
    assert len(server.calls) == 4 and server.waits == [2, 5, 10]
    assert len({c["headers"]["X-Nonce"] for c in server.calls}) == 4
    assert out[-1] == "FAILED: batch 1/1 could not be sent: HTTP 504, 4 attempts. Nothing was sent."


@pytest.mark.parametrize("code, hint", [(401, "within 300 s"), (403, ""), (404, "not deployed")])
def test_other_4xx_stop_at_once(app, bot, ingest, code, hint):
    bot([alarm(1)], "2026-09-29 10:00:03")
    server = ingest(code)
    rc, out = run_backfill(app, "--send")
    assert rc == 1 and len(server.calls) == 1 and server.waits == []
    assert out[-1].startswith(f"FAILED: batch 1/1 was refused: HTTP {code}")
    assert hint in out[-1] and out[-1].endswith("Nothing was sent.")
    text = "\n".join(out)
    assert SECRET not in text and TEXT not in text and USER not in text


def test_an_unexpected_answer_stops(app, bot, ingest):
    bot([alarm(1)], "2026-09-29 10:00:03")
    ingest(answer={"run_id": "backfill", "events_received": 0, "events_inserted": 0,
                   "events_duplicate": 0})
    rc, out = run_backfill(app, "--send")
    assert rc == 1
    assert out[-1].startswith("FAILED: batch 1/1: the server received 0 of 1 events.")


def test_send_needs_the_vps_section_and_the_secret(app, bot, ingest):
    bot([alarm(1)], "2026-09-29 10:00:03")
    server = ingest()
    (app / "secrets.json").write_text(json.dumps({"mainmanager": {}}), encoding="utf-8")
    rc, out = run_backfill(app, "--send")
    assert rc == 1 and server.calls == []
    assert out[-1].startswith("FAILED: no ingest secret: vps.ingest_secret missing in ")
    assert out[-1].endswith(". Nothing was sent.")
    rc, out = run_backfill(app)                      # the report still works, and says so
    assert rc == 0 and any(x.startswith("  sending:             NOT READY: no ingest secret")
                           for x in out)
    cfg = json.loads((app / "config.json").read_text(encoding="utf-8"))
    cfg["vps"]["enabled"] = False
    (app / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    rc, out = run_backfill(app, "--send")
    assert rc == 1 and server.calls == []
    assert out[-1] == ('FAILED: config.json has no "vps" section, or it has "enabled": false. '
                       "Nothing was sent.")


def test_nothing_in_the_window_sends_nothing(app, bot, ingest):
    bot([alarm(1)], "2026-09-29 09:00:00")
    server = ingest()
    rc, out = run_backfill(app, "--send")
    assert rc == 0 and server.calls == []
    assert out[-1] == "OK: nothing to send, there are no events in the window."


def test_from_after_to_is_a_usage_error(app):
    with pytest.raises(SystemExit) as e:
        run_backfill(app, t_from=TO, t_to=FROM)
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        run_backfill(app, t_from="29-09-2026 10:00:00")
