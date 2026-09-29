"""Tests for the additive main.py changes: run_csv_logging() return value,
version banner, and an end-to-end --dry-run with/without a "vps" section."""
import json
from pathlib import Path
import logging
from datetime import datetime

import pytest

import main
import shipper

# 36 tab-separated fields per row (see docs/reference/alr-file-format.md).
# Field 13 (alarm_text) and 10 (user) carry Danish characters; written ISO-8859-1.
ALR_ROWS = [
    # vista_id, object, date1, state1, state2, date2, pri, user, ack, text, count, directory
    ("VISTA_SERVER#67969A95", "345-51-Zonemaster_7_SAL-32090_0721ForcLuk_A", "67969A77",
     0, 0, "67969A77", 1, "No user", 0, "Konvektor ventiler forceret lukket af bruger", 1,
     "VISTA_SERVER-LOYTEC_PORT-RHQ-34551_02_ET7_XENTA-ZM_OMR5_ET6_7-APP.32090_0721ForcLuk_A"),
    ("VISTA_SERVER#683F053E", "325-10-04806-Udsugning-Alarm-Bit0_Fejl", "683F053E",
     0, 0, "683F053E", 2, "OPR1 (Operator One FM)", 1, "ATV61 Fejl på blæser", 1,
     "325-10-04806-Udsugning-Alarm-Bit0_Fejl"),
    ("VISTA_SERVER#683F053F", "325-10-04806-Udsugning-Alarm-Bit3_MotorFejl", "683F053E",
     1, 0, "683F0600", 3, "No user", 0, "Motor fejl på kølekreds", 2,
     "325-10-04806-Udsugning-Alarm-Bit3_MotorFejl"),
]


def alr_line(vid, obj, d1, s1, s2, d2, pri, user, ack, text, count, directory):
    f = [""] * main.EXPECTED_FIELDS
    f[0] = "1"
    f[main.F_VISTA_ID] = vid
    f[main.F_ALARM_OBJECT] = obj
    f[main.F_DATE1] = d1
    f[main.F_STATE1] = str(s1)
    f[main.F_STATE2] = str(s2)
    f[main.F_DATE2] = d2
    f[main.F_PRIORITY] = str(pri)
    f[main.F_USER] = user
    f[main.F_ACK_FLAG] = str(ack)
    f[main.F_ALARM_TEXT] = text
    f[main.F_COUNT] = str(count)
    f[main.F_DIRECTORY] = directory
    return "\t".join(f) + "\n"


def write_alr(path, rows):
    with open(path, "w", encoding="iso-8859-1", newline="") as fh:
        for row in rows:
            fh.write(alr_line(*row))


def alarms_from_rows(rows):
    return [main.Alarm(vista_id=r[0], alarm_object=r[1], date1_epoch=int(r[2], 16),
                       state1=r[3], state2=r[4], date2_epoch=int(r[5], 16),
                       priority=r[6], user=r[7], ack_flag=r[8], alarm_text=r[9],
                       count=r[10], directory=r[11]) for r in rows]


@pytest.fixture
def bot_cfg(tmp_path):
    """config.json equivalent pointing everything at tmp_path (no "vps")."""
    alr = tmp_path / "this.alr"
    write_alr(alr, ALR_ROWS)
    cfg = {
        "thresholds": {"max_priority_number": 2},
        "paths": {
            "vista_alarm_file": str(alr),
            "objects_csv": str(tmp_path / "objects.csv"),
            "exceptions_csv": str(tmp_path / "exceptions.csv"),
            "working_folder": str(tmp_path / "work"),
            "state_file": str(tmp_path / "work" / "alarms_state.json"),
            "csv_folder": str(tmp_path / "csv"),
            "csv_state_file": str(tmp_path / "work" / "csv_state.json"),
            "log_folder": str(tmp_path / "logs"),
        },
        "mainmanager": {
            "base_url": "https://mm.example.test", "username": "u", "password": "p",
            "default_main_id": 14228,
            "incident_defaults": {"CheckwordID": 8, "CheckwordItemID": 277, "GradeID": 10},
        },
        "log_encoding": "iso-8859-1",
        "prune_resolved_after_days": 30,
    }
    return cfg


def write_cfg(tmp_path, cfg):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no HTTP allowed in these tests")
    monkeypatch.setattr(main.requests, "post", boom)
    monkeypatch.setattr(main.requests, "get", boom)
    monkeypatch.setattr(shipper.requests, "post", boom)


@pytest.fixture(autouse=True)
def close_bot_log_handlers():
    yield
    logging.getLogger("alarmbot").handlers.clear()


# ---------------------------------------------------------------------------
# run_csv_logging() return value
# ---------------------------------------------------------------------------

def test_run_csv_logging_returns_written_events(tmp_path):
    log = logging.getLogger("t")
    csv_folder = str(tmp_path / "csv")
    csv_state = str(tmp_path / "csv_state.json")
    alarms = alarms_from_rows(ALR_ROWS)
    sys_event = main.Alarm(vista_id="VISTA_SERVER#SYS", alarm_object="$EE_Mess",
                           date1_epoch=1, state1=6, state2=2, date2_epoch=1,
                           priority=9, user="No user", ack_flag=0,
                           alarm_text="system", count=1, directory="VISTA_SERVER")

    # Run 1: everything FIRST_SEEN, system event skipped
    ev = main.run_csv_logging(alarms + [sys_event], csv_folder, csv_state, log)
    assert [e["event"] for e in ev] == ["FIRST_SEEN"] * 3
    assert [e["vista_id"] for e in ev] == [r[0] for r in ALR_ROWS]
    e1 = ev[1]
    assert isinstance(e1["ts"], datetime) and e1["ts"].tzinfo is not None
    assert e1["state1"] == 0 and e1["ack_flag"] == 1 and e1["count"] == 1
    assert e1["date1_epoch"] == 0x683F053E and e1["priority"] == 2
    assert e1["user"] == "OPR1 (Operator One FM)"
    assert e1["alarm_text"] == "ATV61 Fejl på blæser"
    assert e1["status_label"] == "ACTIVE + ACKNOWLEDGED"
    assert set(e1) == set(main.Alarm.__slots__) | {"ts", "event", "status_label"}

    # Run 2: no change -> nothing written, empty list
    assert main.run_csv_logging(alarms, csv_folder, csv_state, log) == []

    # Run 3: alarm 0 goes NORMAL+ack, alarm 1 (was ACTIVE) disappears -> NORMAL+RESOLVED,
    # alarm 2 (was NORMAL) disappears -> RESOLVED only
    a0 = alarms[0]
    a0.state1, a0.ack_flag, a0.user = 1, 1, "GPST (Georgi ISS)"
    ev = main.run_csv_logging([a0], csv_folder, csv_state, log)
    by_vid = {}
    for e in ev:
        by_vid.setdefault(e["vista_id"], []).append(e)
    assert [e["event"] for e in by_vid[ALR_ROWS[0][0]]] == ["NORMAL", "ACKNOWLEDGED"]
    assert [e["event"] for e in by_vid[ALR_ROWS[1][0]]] == ["NORMAL", "RESOLVED"]
    assert [e["event"] for e in by_vid[ALR_ROWS[2][0]]] == ["RESOLVED"]
    synthetic = by_vid[ALR_ROWS[1][0]][0]
    assert synthetic["status_label"] == "NORMAL"
    assert synthetic["priority"] == 2 and synthetic["directory"] == ALR_ROWS[1][11]
    assert synthetic["alarm_text"] == "ATV61 Fejl på blæser"
    for f in ("user", "ack_flag", "state1", "state2", "date1_epoch", "date2_epoch", "count"):
        assert synthetic[f] is None, f
    assert by_vid[ALR_ROWS[2][0]][0]["status_label"] == "RESOLVED"

    # CSV side effect unchanged: rows in files == events returned (3 + 0 + 5), plus headers
    total_rows = 0
    for p in (tmp_path / "csv").glob("*.csv"):
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines[0] == main.CSV_HEADER.rstrip("\n")
        total_rows += len(lines) - 1
    assert total_rows == 3 + 5
    # csv_state pruned resolved entries
    assert set(json.loads((tmp_path / "csv_state.json").read_text())) == {ALR_ROWS[0][0]}


def test_version_banner(bot_cfg, tmp_path, caplog):
    assert main.__version__ == "2.2.0"
    cfg_path = write_cfg(tmp_path, bot_cfg)
    with caplog.at_level(logging.INFO, logger="alarmbot"):
        rc = main.main(["--config", cfg_path, "--parse-only"])
    assert rc == 0
    banner = [r.getMessage() for r in caplog.records if "Alarm bot started" in r.getMessage()]
    assert banner and banner[0].startswith(f"Alarm bot started (v{main.__version__}, ")
    # the banner names the config file actually used (in place vs copied install)
    assert f"config={Path(cfg_path).resolve()}, " in banner[0]
    assert banner[0].endswith("no_bootstrap=False)")


def test_relative_paths_keep_everything_in_the_config_folder(bot_cfg, tmp_path, monkeypatch):
    """The CTS server runs the bot in place: config.json uses relative paths and
    everything the bot writes lands next to it (C:\\cts-api\\cts-alarms)."""
    app = tmp_path / "app"
    app.mkdir()
    bot_cfg["paths"] = {
        "vista_alarm_file": bot_cfg["paths"]["vista_alarm_file"],   # absolute, like Vista's
        "objects_csv": "objects.csv", "exceptions_csv": "exceptions.csv",
        "working_folder": ".", "state_file": "alarms_state.json",
        "csv_folder": "csv", "csv_state_file": "csv_state.json",
        "log_folder": "logs", "secrets_file": "secrets.json",
    }
    offline(bot_cfg)
    monkeypatch.chdir(tmp_path)                 # the process cwd must not matter
    cfg_path = app / "config.json"
    cfg_path.write_text(json.dumps(bot_cfg), encoding="utf-8")
    assert main.main(["--config", str(cfg_path)]) == 0           # bootstrap run
    assert (app / "alarms_state.json").is_file()
    assert (app / "csv_state.json").is_file()
    assert (app / "csv").is_dir() and (app / "logs").is_dir()
    assert not (tmp_path / "logs").exists() and not (tmp_path / "alarms_state.json").exists()


# ---------------------------------------------------------------------------
# End-to-end --dry-run
# ---------------------------------------------------------------------------

def offline(cfg):
    """No MainManager credentials: a dry run then skips its token check
    instead of calling mm.example.test."""
    cfg["mainmanager"].pop("username", None)
    cfg["mainmanager"].pop("password", None)
    return cfg


def test_dry_run_without_vps_never_mentions_shipping(bot_cfg, tmp_path, caplog):
    cfg_path = write_cfg(tmp_path, offline(bot_cfg))
    with caplog.at_level(logging.DEBUG, logger="alarmbot"):
        assert main.main(["--config", cfg_path]) == 0                # real bootstrap
        before = (tmp_path / "work" / "alarms_state.json").read_bytes()
        assert main.main(["--config", cfg_path, "--dry-run"]) == 0   # dry normal run
    assert "ship" not in caplog.text.lower()
    assert "VPS" not in caplog.text
    assert (tmp_path / "work" / "alarms_state.json").read_bytes() == before  # read-only
    state = json.loads(before)
    assert state["meta"]["bootstrapped"] is True
    assert len(state["alarms"]) == 2          # priority 3 filtered out


def test_dry_run_bootstrap_writes_nothing(bot_cfg, tmp_path, caplog):
    cfg_path = write_cfg(tmp_path, offline(bot_cfg))
    with caplog.at_level(logging.INFO, logger="alarmbot"):
        assert main.main(["--config", cfg_path, "--dry-run"]) == 0
    assert "[DRY] BOOTSTRAP not saved (dry-run)." in caplog.text
    assert "CSV audit: [DRY] would log 3 events" in caplog.text
    assert not (tmp_path / "work" / "alarms_state.json").exists()
    assert not (tmp_path / "work" / "csv_state.json").exists()
    assert not (tmp_path / "csv").exists()


def with_vps(cfg, tmp_path, monkeypatch):
    """Add a "vps" section plus a secrets file that holds only the ingest
    secret (no MainManager pair, so the bot stays offline); the dry-run
    reachability probe is stubbed."""
    monkeypatch.delenv(shipper.SECRET_ENV, raising=False)
    monkeypatch.setattr(shipper, "probe", lambda shcfg: "HTTP 200")
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"vps": {"ingest_secret": "hmac-not-a-real-secret"}}),
                       encoding="utf-8")
    cfg["paths"]["secrets_file"] = str(secrets)
    cfg["vps"] = {"base_url": "https://api.example.test",
                  "outbox_file": str(tmp_path / "work" / "outbox.sqlite")}
    return offline(cfg)


def test_dry_run_with_vps_logs_would_ship(bot_cfg, tmp_path, caplog, monkeypatch):
    with_vps(bot_cfg, tmp_path, monkeypatch)

    # Run 1 = dry bootstrap: 3 FIRST_SEEN CSV events would be shipped; nothing saved
    cfg_path = write_cfg(tmp_path, bot_cfg)
    with caplog.at_level(logging.INFO, logger="alarmbot"):
        assert main.main(["--config", cfg_path, "--dry-run"]) == 0
    assert "[DRY] Would ship 3 events to https://api.example.test" in caplog.text
    assert "[DRY] VPS ingest secret: present" in caplog.text
    assert "healthz -> HTTP 200" in caplog.text
    assert "[DRY] BOOTSTRAP not saved (dry-run)." in caplog.text

    # Run 2 = real bootstrap without "vps" (no network), so state and csv_state exist
    caplog.clear()
    vps = bot_cfg.pop("vps")
    assert main.main(["--config", write_cfg(tmp_path, bot_cfg)]) == 0
    bot_cfg["vps"] = vps
    cfg_path = write_cfg(tmp_path, bot_cfg)

    # Run 3 = dry: alarm 1 removed from the file -> NORMAL + RESOLVED synthetic events
    caplog.clear()
    write_alr(tmp_path / "this.alr", [ALR_ROWS[0], ALR_ROWS[2]])
    state_before = (tmp_path / "work" / "alarms_state.json").read_bytes()
    with caplog.at_level(logging.INFO, logger="alarmbot"):
        assert main.main(["--config", cfg_path, "--dry-run"]) == 0
    assert "[DRY] Would mark VISTA_SERVER#683F053E RESOLVED" in caplog.text
    assert "[DRY] Would ship 2 events to https://api.example.test" in caplog.text
    assert "hmac-not-a-real-secret" not in caplog.text
    assert not (tmp_path / "work" / "outbox.sqlite").exists()
    assert (tmp_path / "work" / "alarms_state.json").read_bytes() == state_before


def test_snapshot_failure_still_exits_2(bot_cfg, tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    bot_cfg["paths"]["vista_alarm_file"] = str(tmp_path / "does-not-exist.alr")
    with_vps(bot_cfg, tmp_path, monkeypatch)
    cfg_path = write_cfg(tmp_path, bot_cfg)
    with caplog.at_level(logging.INFO, logger="alarmbot"):
        assert main.main(["--config", cfg_path, "--dry-run"]) == 2
    assert "Could not snapshot alarm file" in caplog.text
    assert "[DRY] Would ship 0 events" in caplog.text
