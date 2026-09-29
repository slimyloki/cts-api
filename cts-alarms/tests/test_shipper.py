"""Tests for shipper.py — batch shape, no-op config, HMAC signing, outbox,
resend, dead-letter."""
import hashlib
import hmac
import json
import logging
import sqlite3
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

import main
import shipper

SECRET = "hmac-test-SECRET-0123456789abcdef"
LOGGER = logging.getLogger("test-shipper")
LOGGER.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def mk_alarm(vid="VISTA_SERVER#000001", state1=0, ack=0, user="No user",
             priority=2, text="Fejl på ventilator", state2=0):
    return main.Alarm(
        vista_id=vid, alarm_object="325-10-04806-Udsugning",
        date1_epoch=0x683F053E, state1=state1, state2=state2,
        date2_epoch=0x683F053E, priority=priority, user=user, ack_flag=ack,
        alarm_text=text, count=1,
        directory="VISTA_SERVER-LOYTEC_PORT-RHQ-345_02_ET9_XENTA-0207_01-Udsugning",
    )


def sample_events(now):
    a = mk_alarm()
    return [
        main.event_record(now, "FIRST_SEEN", alarm=a),
        main.event_record(now, "RESOLVED", entry={
            "vista_id": "VISTA_SERVER#000002", "alarm_object": "obj",
            "directory": "dir", "initial_alarm_text": "gone", "priority": 3,
        }),
    ]


def sample_state():
    return {"meta": {"bootstrapped": True, "last_run_iso": "2026-09-28T17:55:12"},
            "alarms": {
                "VISTA_SERVER#000001": {
                    "incident_id": 36693, "main_id": 14228,
                    "main_id_fallback_used": True, "status": "ACTIVE",
                    "first_seen_iso": "2026-09-01T10:00:00",
                    "last_update_iso": "2026-09-02T10:00:00",
                },
                "VISTA_SERVER#000002": {
                    "incident_id": None, "main_id": 9756,
                    "main_id_fallback_used": True, "status": "RESOLVED",
                    "first_seen_iso": "2026-04-17T10:57:58",
                    "last_update_iso": "2026-04-17T10:57:58",
                    "resolved_iso": "2026-04-18T08:00:00",
                },
            }}


def sample_run_meta(now, **over):
    meta = {"run_id": now.isoformat(timespec="seconds"), "started_at": now,
            "finished_at": now, "bot_version": main.__version__,
            "parsed_rows": 3, "kept": 2, "csv_events": 2, "created": 0,
            "updated": 1, "resolved": 0, "unchanged": 1,
            "errors": ["UpdateIncident FAILED for X: 404"], "exit_code": 0}
    meta.update(over)
    return meta


def sample_batch():
    now = datetime.now().astimezone()
    alarms = [mk_alarm(), mk_alarm("VISTA_SERVER#SYS", state1=6, state2=2)]
    return shipper.build_batch(sample_run_meta(now), sample_events(now),
                               alarms, sample_state())


@pytest.fixture
def vps_cfg(tmp_path, monkeypatch):
    monkeypatch.delenv(shipper.SECRET_ENV, raising=False)
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"mainmanager": {"username": "u", "password": "p"},
                                        "vps": {"ingest_secret": SECRET}}),
                            encoding="utf-8")
    return {
        "paths": {"working_folder": str(tmp_path), "secrets_file": str(secrets_file)},
        "vps": {
            "base_url": "https://api.example.test/",
            "outbox_file": str(tmp_path / "outbox.sqlite"),
            "timeout_seconds": 5,
            "max_resend_per_run": 20,
            "enabled": True,
        },
    }


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakePost:
    """Scripted requests.post replacement. Each entry is an int status code
    or an Exception instance to raise. Records every call."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, data=None, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "raw": data,
                           "body": json.loads(data.decode("utf-8")),
                           "headers": headers, "timeout": timeout})
        item = self.script.pop(0) if self.script else 200
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item, f"resp-{item}")


def outbox_rows(path, table="outbox"):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT run_id, attempts, last_error FROM {table} "
                            "ORDER BY id").fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_no_vps_section_is_noop():
    assert shipper.load_shipper_config({"paths": {}}) is None
    assert shipper.load_shipper_config({"vps": {"enabled": False,
                                                "base_url": "https://x"}}) is None


def test_ship_run_noop_without_vps(monkeypatch, caplog):
    def boom(*a, **k):
        raise AssertionError("requests.post must not be called")
    monkeypatch.setattr(shipper.requests, "post", boom)
    now = datetime.now().astimezone()
    with caplog.at_level(logging.DEBUG):
        shipper.ship_run({"paths": {}}, sample_run_meta(now), [], [], {}, LOGGER)
    assert caplog.records == []


def test_bad_vps_section_warns_but_does_not_raise(vps_cfg, caplog):
    vps_cfg["vps"]["base_url"] = "alarms.example.test"      # no scheme
    now = datetime.now().astimezone()
    with caplog.at_level(logging.WARNING):
        shipper.ship_run(vps_cfg, sample_run_meta(now), [], [], {}, LOGGER)
    assert "bad \"vps\" config" in caplog.text


def test_obsolete_bearer_key_file_is_rejected(vps_cfg):
    vps_cfg["vps"]["api_key_file"] = "C:\\priorityalarmsapi\\vps_api_key.txt"
    with pytest.raises(ValueError, match="obsolete"):
        shipper.load_shipper_config(vps_cfg)


def test_config_defaults(vps_cfg):
    del vps_cfg["vps"]["outbox_file"]
    del vps_cfg["vps"]["timeout_seconds"]
    shcfg = shipper.load_shipper_config(vps_cfg)
    assert shcfg.base_url == "https://api.example.test"
    assert shcfg.ingest_url == "https://api.example.test/internal/cts-alarms/v1/ingest"
    assert shcfg.healthz_url == "https://api.example.test/api/cts-alarms/healthz"
    assert shcfg.outbox_file.endswith("outbox.sqlite")
    assert shcfg.timeout_seconds == 20
    assert shcfg.max_resend_per_run == 20


# ---------------------------------------------------------------------------
# Batch shape
# ---------------------------------------------------------------------------

def test_build_batch_shape():
    batch = sample_batch()
    assert batch["schema_version"] == 1
    assert batch["source"] == "cts-alarm-bot"
    run = batch["run"]
    assert run["run_id"] == run["started_at"]
    assert run["started_at"].endswith(("+00:00", "+01:00", "+02:00")) or \
        run["started_at"][-6] in "+-"
    assert run["bot_version"] == main.__version__
    assert run["errors"] == ["UpdateIncident FAILED for X: 404"]
    assert run["exit_code"] == 0 and run["updated"] == 1

    # events: real row keeps ints, synthetic row has None for unknowns
    first, resolved = batch["events"]
    assert first["event"] == "FIRST_SEEN" and first["state1"] == 0
    assert first["ack_flag"] == 0 and first["count"] == 1
    assert first["status_label"] == "ACTIVE"
    assert first["ts"][-6] in "+-" and "T" in first["ts"]
    assert resolved["event"] == "RESOLVED"
    assert resolved["vista_id"] == "VISTA_SERVER#000002"
    assert resolved["priority"] == 3
    for f in ("user", "ack_flag", "state1", "state2", "date1_epoch",
              "date2_epoch", "count"):
        assert resolved[f] is None, f
    assert resolved["status_label"] == "RESOLVED"

    # snapshot skips (6,2) system events
    assert [r["vista_id"] for r in batch["snapshot"]] == ["VISTA_SERVER#000001"]
    assert batch["snapshot"][0]["status_label"] == "ACTIVE"

    # incidents from alarms_state.json, ISO strings with offset
    incs = {i["vista_id"]: i for i in batch["incidents"]}
    assert incs["VISTA_SERVER#000001"]["incident_id"] == 36693
    assert incs["VISTA_SERVER#000001"]["first_seen"].startswith("2026-09-01T10:00:00+")
    assert incs["VISTA_SERVER#000001"]["resolved_at"] is None
    assert incs["VISTA_SERVER#000002"]["resolved_at"].startswith("2026-04-18T08:00:00+")
    assert incs["VISTA_SERVER#000002"]["status"] == "RESOLVED"

    # JSON serialisable as-is
    json.dumps(batch, ensure_ascii=False)


FIXTURE = Path(__file__).parent / "fixtures" / "ingest-batch-v1.example.json"


@pytest.fixture
def copenhagen_tz(monkeypatch):
    """Naive state timestamps are local time on the CTS server (Europe/Copenhagen)."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset not available on this platform")
    monkeypatch.setenv("TZ", "Europe/Copenhagen")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def contract_batch(monkeypatch):
    monkeypatch.setattr(shipper.platform, "node", lambda: "CTS-SERVER")
    now = datetime(2026, 9, 29, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    alarms = [mk_alarm(), mk_alarm("VISTA_SERVER#SYS", state1=6, state2=2)]
    meta = sample_run_meta(now, bot_version="2.1.0")
    return shipper.build_batch(meta, sample_events(now), alarms, sample_state())


def test_build_batch_matches_contract_fixture(monkeypatch, copenhagen_tz):
    # The digibuild cts-alarms worker validates this same file against its
    # IngestBatch schema. A change here is a contract change: bump
    # schema_version or update both sides together.
    # Regenerate with:  CTS_WRITE_FIXTURE=1 python -m pytest tests/test_shipper.py -k contract
    batch = contract_batch(monkeypatch)
    if os.environ.get("CTS_WRITE_FIXTURE") == "1":
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    assert batch == json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_errors_list_is_bounded():
    now = datetime.now().astimezone()
    meta = sample_run_meta(now, errors=[f"e{i}" for i in range(500)])
    run = shipper.build_batch(meta, [], [], {})["run"]
    assert len(run["errors"]) == shipper.MAX_ERRORS_PER_RUN + 1
    assert run["errors"][-1].endswith("more errors omitted")


# ---------------------------------------------------------------------------
# Shipping: success, outbox, resend, dead letter
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# HMAC (digibuild ADR-0022 machine-hop contract)
# ---------------------------------------------------------------------------

def test_signature_matches_digibuild_golden_vector():
    # Produced 2026-09-29 by digibuild apps/worker/lib/hmac.js sign() with the same
    # inputs (non-ASCII in body and secret on purpose). If this fails, the CTS side
    # and the VPS verifier no longer hash the same bytes.
    body = '{"run":{"run_id":"2026-09-29T10:00:00+02:00"},"text":"Høj Temperatur æøå"}'.encode("utf-8")
    h = shipper.signed_headers("test-secret-ÆØÅ-123", body, now=1790000000,
                               nonce="0b6f1c8e-8f7a-4d3e-9b1a-2c3d4e5f6a7b")
    assert h == {
        "X-Signature": "41e3779721201195e0edeeca1ce08ca4ce501f3285506cf526a47a1c152628b6",
        "X-Timestamp": "1790000000",
        "X-Nonce": "0b6f1c8e-8f7a-4d3e-9b1a-2c3d4e5f6a7b",
    }


def verify(call, secret=SECRET):
    """Server-side check, as the worker does it: over the raw bytes received."""
    h = call["headers"]
    msg = f"{h['X-Timestamp']}.{h['X-Nonce']}.".encode("utf-8") + call["raw"]
    want = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, h["X-Signature"])


def test_ship_success_sends_signed_json(vps_cfg, monkeypatch):
    fake = FakePost(200)
    monkeypatch.setattr(shipper.requests, "post", fake)
    shcfg = shipper.load_shipper_config(vps_cfg)
    batch = sample_batch()
    assert shipper.ship(batch, shcfg, LOGGER) is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://api.example.test/internal/cts-alarms/v1/ingest"
    assert "Authorization" not in call["headers"]
    assert verify(call)
    assert not verify(call, secret=SECRET + "x")
    assert abs(int(call["headers"]["X-Timestamp"]) - time.time()) < 5
    assert uuid.UUID(call["headers"]["X-Nonce"]).version == 4
    assert call["headers"]["Content-Type"].startswith("application/json")
    assert call["timeout"] == 5
    assert call["body"]["run"]["run_id"] == batch["run"]["run_id"]
    assert outbox_rows(shcfg.outbox_file) == []


def test_failure_queues_then_resend_on_success(vps_cfg, monkeypatch, caplog):
    shcfg = shipper.load_shipper_config(vps_cfg)

    # Run 1: connection error -> queued
    fake = FakePost(requests.ConnectionError("boom"))
    monkeypatch.setattr(shipper.requests, "post", fake)
    b1 = sample_batch()
    with caplog.at_level(logging.INFO):
        assert shipper.ship(b1, shcfg, LOGGER) is False
    rows = outbox_rows(shcfg.outbox_file)
    assert [r[0] for r in rows] == [b1["run"]["run_id"]]
    assert rows[0][1] == 1 and "ConnectionError" in rows[0][2]
    assert any(r.levelno == logging.WARNING and "queued" in r.getMessage()
               for r in caplog.records)

    # Run 2: 503 -> queued as well, resend of run 1 fails first and stops
    fake = FakePost(503)
    monkeypatch.setattr(shipper.requests, "post", fake)
    b2 = sample_batch()
    b2["run"]["run_id"] = "2026-09-29T00:00:01+02:00"
    assert shipper.ship(b2, shcfg, LOGGER) is False
    assert len(fake.calls) == 1                      # stopped on first failure
    rows = outbox_rows(shcfg.outbox_file)
    assert [r[0] for r in rows] == [b1["run"]["run_id"], b2["run"]["run_id"]]
    assert rows[0][1] == 2                           # attempts incremented

    # Run 3: server back -> both queued batches resent oldest-first, then current
    fake = FakePost(200, 200, 200)
    monkeypatch.setattr(shipper.requests, "post", fake)
    b3 = sample_batch()
    b3["run"]["run_id"] = "2026-09-29T00:00:02+02:00"
    assert shipper.ship(b3, shcfg, LOGGER) is True
    sent = [c["body"]["run"]["run_id"] for c in fake.calls]
    assert sent == [b1["run"]["run_id"], b2["run"]["run_id"], b3["run"]["run_id"]]
    assert outbox_rows(shcfg.outbox_file) == []
    # every request, resends included, is freshly signed with its own nonce
    assert all(verify(c) for c in fake.calls)
    assert len({c["headers"]["X-Nonce"] for c in fake.calls}) == 3


def test_resend_respects_max_and_stops_on_failure(vps_cfg, monkeypatch):
    vps_cfg["vps"]["max_resend_per_run"] = 2
    shcfg = shipper.load_shipper_config(vps_cfg)
    conn = shipper.open_outbox(shcfg.outbox_file)
    for i in range(5):
        shipper._enqueue(conn, f"run-{i}", json.dumps({"run": {"run_id": f"run-{i}"}}), "x")
    conn.close()

    # all good: 2 resends + current = 3 requests, 3 left queued
    fake = FakePost(200, 200, 200)
    monkeypatch.setattr(shipper.requests, "post", fake)
    assert shipper.ship(sample_batch(), shcfg, LOGGER) is True
    assert len(fake.calls) == 3
    assert [r[0] for r in outbox_rows(shcfg.outbox_file)] == ["run-2", "run-3", "run-4"]

    # server down: first resend times out -> stop, current queued WITHOUT a request
    fake = FakePost(requests.Timeout("slow"))
    monkeypatch.setattr(shipper.requests, "post", fake)
    b = sample_batch()
    assert shipper.ship(b, shcfg, LOGGER) is False
    assert len(fake.calls) == 1
    rows = outbox_rows(shcfg.outbox_file)
    assert [r[0] for r in rows] == ["run-2", "run-3", "run-4", b["run"]["run_id"]]
    assert rows[0][1] == 2


def test_dead_letter_on_400(vps_cfg, monkeypatch, caplog):
    shcfg = shipper.load_shipper_config(vps_cfg)
    fake = FakePost(422)
    monkeypatch.setattr(shipper.requests, "post", fake)
    b = sample_batch()
    with caplog.at_level(logging.INFO):
        assert shipper.ship(b, shcfg, LOGGER) is False
    assert outbox_rows(shcfg.outbox_file) == []
    dead = outbox_rows(shcfg.outbox_file, "dead")
    assert [d[0] for d in dead] == [b["run"]["run_id"]]
    assert "HTTP 422" in dead[0][2] and "resp-422" in dead[0][2]
    assert any(r.levelno == logging.ERROR and "malformed" in r.getMessage()
               for r in caplog.records)

    # a queued batch that turns out malformed is buried too, and does not stop the resend loop
    conn = shipper.open_outbox(shcfg.outbox_file)
    shipper._enqueue(conn, "bad-run", json.dumps({"run": {"run_id": "bad-run"}}), "x")
    conn.close()
    fake = FakePost(400, 200)
    monkeypatch.setattr(shipper.requests, "post", fake)
    assert shipper.ship(sample_batch(), shcfg, LOGGER) is True
    assert len(fake.calls) == 2
    assert outbox_rows(shcfg.outbox_file) == []
    assert len(outbox_rows(shcfg.outbox_file, "dead")) == 2


@pytest.mark.parametrize("code", [401, 403, 404, 429, 500, 502])
def test_auth_and_server_errors_are_retried(vps_cfg, monkeypatch, code):
    shcfg = shipper.load_shipper_config(vps_cfg)
    monkeypatch.setattr(shipper.requests, "post", FakePost(code))
    b = sample_batch()
    assert shipper.ship(b, shcfg, LOGGER) is False
    rows = outbox_rows(shcfg.outbox_file)
    assert len(rows) == 1 and f"HTTP {code}" in rows[0][2]
    assert outbox_rows(shcfg.outbox_file, "dead") == []


def test_401_hints_at_secret_and_clock(vps_cfg, monkeypatch):
    shcfg = shipper.load_shipper_config(vps_cfg)
    monkeypatch.setattr(shipper.requests, "post", FakePost(401))
    shipper.ship(sample_batch(), shcfg, LOGGER)
    assert "within 300 s" in outbox_rows(shcfg.outbox_file)[0][2]


def test_secret_never_logged(vps_cfg, monkeypatch, caplog):
    shcfg = shipper.load_shipper_config(vps_cfg)
    # Response text and exception message that echo the secret must be scrubbed too
    class Echo(FakePost):
        def __call__(self, url, data=None, headers=None, timeout=None, **kw):
            self.calls.append(headers)
            item = self.script.pop(0) if self.script else 200
            if isinstance(item, Exception):
                raise item
            return FakeResponse(item, f"denied, expected key {SECRET}")
    for post in (Echo(401), Echo(400),
                 Echo(requests.ConnectionError(f"bad token {SECRET}"))):
        caplog.clear()
        monkeypatch.setattr(shipper.requests, "post", post)
        with caplog.at_level(logging.DEBUG):
            shipper.ship(sample_batch(), shcfg, LOGGER)
        assert caplog.records
        for rec in caplog.records:
            assert SECRET not in rec.getMessage()
    for row in outbox_rows(shcfg.outbox_file) + outbox_rows(shcfg.outbox_file, "dead"):
        assert SECRET not in (row[2] or "")


@pytest.mark.parametrize("content", [
    None,                                                   # no secrets file
    {"mainmanager": {"username": "u", "password": "p"}},    # no vps section
    {"vps": {"ingest_secret": "  "}},                       # blank
])
def test_missing_secret_queues_and_sends_nothing(vps_cfg, monkeypatch, caplog, content):
    path = vps_cfg["paths"]["secrets_file"]
    if content is None:
        vps_cfg["paths"]["secrets_file"] = path + ".missing"
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f)
    shcfg = shipper.load_shipper_config(vps_cfg)
    fake = FakePost(200)
    monkeypatch.setattr(shipper.requests, "post", fake)
    b = sample_batch()
    with caplog.at_level(logging.WARNING):
        assert shipper.ship(b, shcfg, LOGGER) is False
    assert fake.calls == []
    rows = outbox_rows(shcfg.outbox_file)
    assert [r[0] for r in rows] == [b["run"]["run_id"]]
    assert rows[0][2].startswith("not attempted:")
    assert "no ingest secret" in caplog.text and "install.cmd -IngestSecret" in caplog.text
    # a WARNING, not an ERROR: status.cmd reports it once, as its own problem
    assert all(r.levelno == logging.WARNING for r in caplog.records if "no ingest secret" in r.getMessage())


def test_relative_secrets_file_is_next_to_config_json(vps_cfg, tmp_path):
    vps_cfg["paths"]["secrets_file"] = "secrets.json"
    vps_cfg["_config_dir"] = str(tmp_path)
    shcfg = shipper.load_shipper_config(vps_cfg)
    assert shcfg.secrets_file == str(tmp_path / "secrets.json")
    assert shipper.read_ingest_secret(shcfg) == SECRET


def test_secret_from_env_wins_and_bom_file_is_read(vps_cfg, monkeypatch):
    shcfg = shipper.load_shipper_config(vps_cfg)
    path = vps_cfg["paths"]["secrets_file"]
    with open(path, "w", encoding="utf-8-sig") as f:        # Notepad "UTF-8"
        json.dump({"vps": {"ingest_secret": "from-file-æ"}}, f, ensure_ascii=False)
    assert shipper.read_ingest_secret(shcfg) == "from-file-æ"
    monkeypatch.setenv(shipper.SECRET_ENV, "from-env")
    assert shipper.read_ingest_secret(shcfg) == "from-env"


class FakeGet:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, url, headers=None, timeout=None, **kw):
        self.calls.append(url)
        if isinstance(self.result, Exception):
            raise self.result
        return FakeResponse(self.result, "ok")


def test_ship_run_dry_run_sends_nothing(vps_cfg, monkeypatch, caplog):
    fake, get = FakePost(200), FakeGet(200)
    monkeypatch.setattr(shipper.requests, "post", fake)
    monkeypatch.setattr(shipper.requests, "get", get)
    now = datetime.now().astimezone()
    with caplog.at_level(logging.INFO):
        shipper.ship_run(vps_cfg, sample_run_meta(now, dry_run=True),
                         sample_events(now), [mk_alarm()], sample_state(), LOGGER)
    assert fake.calls == []
    assert get.calls == ["https://api.example.test/api/cts-alarms/healthz"]
    assert "[DRY] Would ship 2 events to https://api.example.test" in caplog.text
    assert "[DRY] VPS ingest secret: present" in caplog.text
    assert "healthz -> HTTP 200" in caplog.text
    assert SECRET not in caplog.text
    import os
    assert not os.path.exists(vps_cfg["vps"]["outbox_file"])   # dry run touches no outbox


def test_ship_run_dry_run_reports_missing_secret_and_unreachable(vps_cfg, monkeypatch, caplog):
    vps_cfg["paths"]["secrets_file"] += ".missing"
    monkeypatch.setattr(shipper.requests, "post", FakePost())
    monkeypatch.setattr(shipper.requests, "get", FakeGet(requests.ConnectionError("x")))
    now = datetime.now().astimezone()
    with caplog.at_level(logging.INFO):
        shipper.ship_run(vps_cfg, sample_run_meta(now, dry_run=True), [], [], {}, LOGGER)
    assert "[DRY] VPS ingest secret: MISSING" in caplog.text
    assert "healthz -> unreachable (ConnectionError)" in caplog.text


# ---------------------------------------------------------------------------
# Alarm names on the ingest answer (ADR-0025)
# ---------------------------------------------------------------------------

class JsonResponse(FakeResponse):
    def __init__(self, status_code, body):
        super().__init__(status_code, json.dumps(body))
        self._body = body

    def json(self):
        return self._body


NAMES = {"version": "abcdef0123456789", "points": [
    {"directory": "VISTA_SERVER-Bygning_A-Rum_AL", "name": " Kølerum 2 ", "building": "Bygning A"},
    {"directory": "BAD"},                                   # nothing set: left out
    {"name": "no directory"},                               # malformed: skipped
]}


def test_accepted_answer_names_are_saved_for_the_bot(vps_cfg, tmp_path, monkeypatch, caplog):
    vps_cfg["paths"]["names_file"] = str(tmp_path / "names.json")
    answer = {"run_id": "x", "names": NAMES}
    monkeypatch.setattr(shipper.requests, "post", lambda *a, **k: JsonResponse(200, answer))
    shcfg = shipper.load_shipper_config(vps_cfg)
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        assert shipper.ship(sample_batch(), shcfg, LOGGER) is True
    saved = json.loads((tmp_path / "names.json").read_text(encoding="utf-8"))
    assert saved["version"] == "abcdef0123456789"
    assert saved["points"] == {"VISTA_SERVER-Bygning_A-Rum_AL": {"name": "Kølerum 2",
                                                                 "building": "Bygning A"}}
    assert "alarm names updated -- 1 named points" in caplog.text
    # ...and the bot reads exactly that.
    assert main.load_names(str(tmp_path / "names.json"), LOGGER) == saved["points"]

    # Same version again: the file is not rewritten.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        shipper.ship(sample_batch(), shcfg, LOGGER)
    assert "alarm names updated" not in caplog.text


def test_names_file_defaults_next_to_the_config(vps_cfg, tmp_path):
    vps_cfg["_config_dir"] = str(tmp_path)
    assert shipper.load_shipper_config(vps_cfg).names_file == str(tmp_path / "names.json")


def test_an_answer_without_names_or_json_changes_nothing(vps_cfg, tmp_path, monkeypatch):
    vps_cfg["paths"]["names_file"] = str(tmp_path / "names.json")
    monkeypatch.setattr(shipper.requests, "post", FakePost(200))     # no .json() at all
    assert shipper.ship(sample_batch(), shipper.load_shipper_config(vps_cfg), LOGGER) is True
    assert not (tmp_path / "names.json").exists()
    assert shipper.save_names(str(tmp_path / "names.json"), {"points": []}, LOGGER) is False
    assert shipper.save_names(str(tmp_path / "names.json"), "junk", LOGGER) is False
