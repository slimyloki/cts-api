"""Tests for the alarm bot — run with `python -m unittest` from the repo root.

Pure stdlib (no pytest on the box). The MainManager API is a fake session;
nothing here talks to the network or touches C:\\priorityalarmsapi.
"""
import argparse
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as bot  # noqa: E402

BASE = "https://mm.test"
CFG_MM = {
    "base_url": BASE,
    "default_main_id": 14228,
    "incident_defaults": {
        "IncidentTypeID": 277, "LocationID": 6, "ReportedByID": 748,
        "ReportedByOrganisationID": 11, "GradeID": 10, "StatusID": 5,
    },
}
CREDS = {"username": "u", "password": "p"}
TOKEN = ("POST", "/restapi/token")


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = {} if body is None else body
        self.text = json.dumps(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise bot.requests.HTTPError(f"{self.status_code} Client Error")


def ok_token():
    return FakeResponse(200, {"access_token": "T", "expires_in": 7200})


def items(*objs):
    return FakeResponse(200, {"items": list(objs)})


class FakeSession:
    """(METHOD, path) -> response, or a list consumed in order (last repeats).
    Unknown routes answer 404, like the dead /restapi/Incident/* endpoints."""

    def __init__(self, routes):
        self.routes = {k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()}
        self.calls = []

    def _answer(self, key, body=None):
        seq = self.routes.get(key)
        if seq is None:
            return FakeResponse(404)
        if callable(seq):
            return seq(body)
        if isinstance(seq, list):
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return seq

    def post(self, url, data=None, timeout=None, **kw):
        path = url[len(BASE):]
        self.calls.append(("POST", path, data))
        return self._answer(("POST", path), data)

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        path = url[len(BASE):]
        body = json if json is not None else params
        self.calls.append((method, path, body))
        return self._answer((method, path), body)

    def bodies(self, method, path):
        return [b for m, p, b in self.calls if m == method and p == path]


class FakeIncident:
    """A stateful incident: PUT stores the item, GET returns it — so the
    client's read-back verification sees what it wrote."""

    def __init__(self, **fields):
        self.item = fields

    def get(self, _body):
        return items(dict(self.item))

    def put(self, body):
        sent = dict(body["items"][0])
        if "Remarks" in sent:                 # the tenant: Remarks -> full Description,
            sent["Description"] = sent["Remarks"]   # Remarks kept as a ~245-char copy
            sent["Remarks"] = sent["Remarks"][:245]
        self.item.update(sent)
        return items({"success": True, "id": self.item.get("ID")})


def quiet_log(name="alarmbot-test"):
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    log.addHandler(logging.NullHandler())
    return log


# ---------------------------------------------------------------------------
# MMClient
# ---------------------------------------------------------------------------

class MMClientTests(unittest.TestCase):
    def client(self, routes, cache=None):
        session = FakeSession({TOKEN: ok_token(), **routes})
        return bot.MMClient(CFG_MM, CREDS, quiet_log(), token_cache=cache, session=session), session

    def test_create_sends_v3_item_and_enforces_status(self):
        c, s = self.client({
            ("POST", "/api/v3/incidents"): items({"success": True, "id": 999}),
            ("GET", "/api/v3/incidents/999"): [
                items({"ID": 999, "StatusID": 8, "Name": "n", "MainID": 14228, "DateReported": "2026-09-28T10:00:00Z"}),
                items({"ID": 999, "StatusID": 5, "Name": "n", "MainID": 14228}),
            ],
            ("PUT", "/api/v3/incidents"): items({"success": True, "id": 999}),
        })
        self.assertEqual(c.create_incident(14228, "n", "remarks"), 999)

        item = s.bodies("POST", "/api/v3/incidents")[0]["items"][0]
        self.assertEqual(item["IncidentTypeID"], 277)
        self.assertEqual(item["CheckwordItemID"], 277)
        self.assertEqual(item["LocationID"], 6)
        self.assertEqual(item["ReportedByID"], 748)
        self.assertEqual(item["Remarks"], "remarks")
        self.assertEqual(item["StatusID"], 5)
        for dropped in ("IncidentMode", "CheckwordID", "Description", "DateReported"):
            self.assertNotIn(dropped, item)

        put = s.bodies("PUT", "/api/v3/incidents")[0]["items"][0]
        self.assertEqual(put["ID"], 999)
        self.assertEqual(put["StatusID"], 5)
        self.assertEqual(put["Name"], "n")                      # echoed
        self.assertNotIn("DateReported", put)                   # not writable, not echoed

    def test_create_skips_put_when_status_already_right(self):
        c, s = self.client({
            ("POST", "/api/v3/incidents"): items({"success": True, "id": 7}),
            ("GET", "/api/v3/incidents/7"): items({"ID": 7, "StatusID": 5}),
        })
        self.assertEqual(c.create_incident(1, "n", "r"), 7)
        self.assertEqual(s.bodies("PUT", "/api/v3/incidents"), [])

    def test_create_failure_flag_raises(self):
        c, _ = self.client({
            ("POST", "/api/v3/incidents"): items({"success": False, "errorMessage": "missing IncidentTypeID"}),
        })
        with self.assertRaises(RuntimeError):
            c.create_incident(1, "n", "r")

    def test_404_raises_not_found(self):
        c, _ = self.client({})   # every incident route 404s
        with self.assertRaises(bot.MMNotFound):
            c.create_incident(1, "n", "r")
        with self.assertRaises(bot.MMNotFound):
            c.get_incident(5)

    def test_probe(self):
        up, _ = self.client({("GET", "/api/v3/incidents"): items({"ID": 1})})
        self.assertTrue(up.probe())
        down, _ = self.client({})
        self.assertFalse(down.probe())

    def test_prepend_reads_full_description_not_truncated_remarks(self):
        full = "\n".join(f"line {i}" for i in range(40))          # > 245 chars
        c, s = self.client({
            ("GET", "/api/v3/incidents/5"): [
                items({"ID": 5, "Description": full, "Remarks": full[:245],
                       "Name": "n", "MainID": 1, "StatusID": 5}),
                items({"ID": 5, "Description": "new\n" + full, "Remarks": ("new\n" + full)[:245]}),
            ],
            ("PUT", "/api/v3/incidents"): items({"success": True, "id": 5}),
        })
        c.prepend_description_line(5, "new")
        put = s.bodies("PUT", "/api/v3/incidents")[0]["items"][0]
        self.assertEqual(put, {"Name": "n", "MainID": 1, "StatusID": 5, "ID": 5,
                               "Remarks": "new\n" + full})

    def test_prepend_falls_back_to_remarks_and_empty(self):
        c, s = self.client({
            ("GET", "/api/v3/incidents/6"): [
                items({"ID": 6, "Remarks": "d"}),
                items({"ID": 6, "Description": "x\nd"}),
            ],
            ("PUT", "/api/v3/incidents"): items({"success": True}),
        })
        c.prepend_description_line(6, "x")
        self.assertEqual(s.bodies("PUT", "/api/v3/incidents")[0]["items"][0]["Remarks"], "x\nd")

    def test_prepend_readback_mismatch_raises(self):
        c, _ = self.client({
            ("GET", "/api/v3/incidents/5"): items({"ID": 5, "Description": "old"}),   # never changes
            ("PUT", "/api/v3/incidents"): items({"success": True}),
        })
        with self.assertRaises(RuntimeError):
            c.prepend_description_line(5, "new")

    def test_token_is_cached_to_file_and_reused(self):
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "mm_token.json")
            c1, s1 = self.client({("GET", "/api/v3/incidents"): items({})}, cache=cache)
            c1.probe()
            self.assertEqual(len(s1.bodies("POST", "/restapi/token")), 1)
            self.assertTrue(os.path.exists(cache))

            c2, s2 = self.client({("GET", "/api/v3/incidents"): items({})}, cache=cache)
            c2.probe()
            self.assertEqual(s2.bodies("POST", "/restapi/token"), [])   # no new token

    def test_no_credentials_raises_before_any_call(self):
        session = FakeSession({})
        c = bot.MMClient(CFG_MM, {}, quiet_log(), session=session)
        with self.assertRaises(RuntimeError):
            c.get_incident(1)
        self.assertEqual(session.calls, [])


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

class SecretsTests(unittest.TestCase):
    def test_environment_wins(self):
        with mock.patch.dict(os.environ, {"MM_USERNAME": "e", "MM_PASSWORD": "ep"}):
            got = bot.load_secrets({"mainmanager": {"username": "c", "password": "cp"}}, quiet_log())
        self.assertEqual(got, {"username": "e", "password": "ep"})

    def test_secrets_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            p = os.path.join(d, "secrets.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"mainmanager": {"username": "f", "password": "fp"}}, f)
            got = bot.load_secrets({"paths": {"secrets_file": p}, "mainmanager": {}}, quiet_log())
        self.assertEqual(got, {"username": "f", "password": "fp"})

    def test_secrets_and_config_with_notepad_bom(self):
        # Notepad on Windows Server 2016 saves "UTF-8" with a byte-order mark
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            p = os.path.join(d, "secrets.json")
            with open(p, "w", encoding="utf-8-sig") as f:
                json.dump({"mainmanager": {"username": "f", "password": "fæp"}}, f, ensure_ascii=False)
            got = bot.load_secrets({"paths": {"secrets_file": p}, "mainmanager": {}}, quiet_log())
            c = os.path.join(d, "config.json")
            with open(c, "w", encoding="utf-8-sig") as f:
                json.dump({"paths": {}}, f)
            self.assertEqual(bot.load_config(c), {"paths": {}})
        self.assertEqual(got, {"username": "f", "password": "fæp"})

    def test_legacy_config_still_works_with_warning(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            cfg = {"paths": {"secrets_file": os.path.join(d, "missing.json")},
                   "mainmanager": {"username": "c", "password": "cp"}}
            log = quiet_log("legacy")
            with self.assertLogs(log, level="WARNING") as cm:
                got = bot.load_secrets(cfg, log)
        self.assertEqual(got, {"username": "c", "password": "cp"})
        self.assertTrue(any("DEPRECATED" in m for m in cm.output))

    def test_nothing_configured(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            cfg = {"paths": {"secrets_file": os.path.join(d, "missing.json")}, "mainmanager": {}}
            self.assertEqual(bot.load_secrets(cfg, quiet_log()), {})


# ---------------------------------------------------------------------------
# run(): 404 classification end to end (fake API, real files in a temp dir)
# ---------------------------------------------------------------------------

def alr_row(vista_id, *, state1=0, ack=0, user="No user", priority=1,
            text="Brand fra ABA", obj="320-01-TEST_A"):
    f = [""] * bot.EXPECTED_FIELDS
    f[bot.F_VISTA_ID] = vista_id
    f[bot.F_ALARM_OBJECT] = obj
    f[bot.F_DATE1] = "6ABA16F9"
    f[bot.F_STATE1] = str(state1)
    f[bot.F_STATE2] = "0"
    f[bot.F_DATE2] = "6ABA16F9"
    f[bot.F_PRIORITY] = str(priority)
    f[bot.F_USER] = user
    f[bot.F_ACK_FLAG] = str(ack)
    f[bot.F_ALARM_TEXT] = text
    f[bot.F_COUNT] = "1"
    f[bot.F_DIRECTORY] = obj
    return "\t".join(f)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.alr = d / "this.alr"
        self.state = d / "alarms_state.json"
        self.cfg = {
            "thresholds": {"max_priority_number": 2},
            "paths": {
                "vista_alarm_file": str(self.alr),
                "objects_csv": str(d / "objects.csv"),
                "exceptions_csv": str(d / "exceptions.csv"),
                "working_folder": str(d / "work"),
                "state_file": str(self.state),
                "csv_folder": str(d / "csv"),
                "csv_state_file": str(d / "csv_state.json"),
                "log_folder": str(d / "logs"),
                "secrets_file": str(d / "secrets.json"),
            },
            "mainmanager": {**CFG_MM, **CREDS},   # legacy creds: keeps the test self-contained
            "log_encoding": "iso-8859-1",
            "prune_resolved_after_days": 30,
        }
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, rows, state_alarms):
        self.alr.write_text("\r\n".join(rows) + "\r\n", encoding="iso-8859-1")
        self.state.write_text(json.dumps(
            {"meta": {"bootstrapped": True, "last_run_iso": None}, "alarms": state_alarms}))

    def run_bot(self, routes, dry_run=False, parse_only=False):
        session = FakeSession({TOKEN: ok_token(), **routes})
        real = bot.MMClient
        log = quiet_log("run")
        args = argparse.Namespace(dry_run=dry_run, parse_only=parse_only, no_bootstrap=False)
        with mock.patch.object(bot, "MMClient",
                               lambda cfg_mm, creds, lg, token_cache=None:
                               real(cfg_mm, creds, lg, session=session)):
            with self.assertLogs(log, level="INFO") as cm:
                rc = bot.run(self.cfg, args, log)
        return rc, json.loads(self.state.read_text())["alarms"], session, cm.output

    def entry(self, vista_id, incident_id, sig):
        return {"incident_id": incident_id, "main_id": 14228, "main_id_fallback_used": True,
                "alarm_object": "320-01-TEST_A", "directory": "320-01-TEST_A", "priority": 1,
                "initial_alarm_text": "Brand fra ABA", "first_seen_iso": "2026-09-28T00:00:00",
                "last_state_sig": sig, "last_update_iso": "2026-09-28T00:00:00", "status": "ACTIVE"}

    def test_endpoint_wide_404_on_create_leaves_alarm_unrecorded(self):
        self.write([alr_row("VISTA_SERVER#1")], {})
        rc, alarms, _, out = self.run_bot({})          # POST /api/v3/incidents -> 404
        self.assertEqual(rc, 0)
        self.assertEqual(alarms, {})                    # next run sees it as new again
        self.assertTrue(any("MainManager API unusable" in m for m in out))
        self.assertTrue(any("deferred=1" in m for m in out))

    def test_single_missing_ticket_is_marked_and_transition_advances(self):
        vid = "VISTA_SERVER#2"
        self.write([alr_row(vid, ack=1, user="GPST (Georgi ISS)")],
                   {vid: self.entry(vid, 123, [0, 0, 0, "No user"])})
        rc, alarms, session, out = self.run_bot({
            ("GET", "/api/v3/incidents"): items({"ID": 1}),   # probe: API is up
            # GET /api/v3/incidents/123 -> 404 (unknown route)
        })
        self.assertEqual(rc, 0)
        e = alarms[vid]
        self.assertTrue(e["incident_missing"])
        self.assertEqual(e["last_state_sig"], [0, 0, 1, "GPST (Georgi ISS)"])
        self.assertEqual(e["status"], "ACTIVE + ACKNOWLEDGED")
        self.assertTrue(any("marked incident_missing" in m for m in out))

    def test_endpoint_wide_404_on_update_leaves_state_untouched(self):
        vid = "VISTA_SERVER#3"
        self.write([alr_row(vid, ack=1, user="GPST (Georgi ISS)")],
                   {vid: self.entry(vid, 123, [0, 0, 0, "No user"])})
        rc, alarms, _, out = self.run_bot({})          # GET incident 404, probe 404
        self.assertEqual(rc, 0)
        e = alarms[vid]
        self.assertNotIn("incident_missing", e)
        self.assertEqual(e["last_state_sig"], [0, 0, 0, "No user"])   # retried next run
        self.assertTrue(any("MainManager API unusable" in m for m in out))

    def test_missing_ticket_is_not_called_again_and_resolves_locally(self):
        vid = "VISTA_SERVER#4"
        e = self.entry(vid, 123, [0, 0, 0, "No user"])
        e["incident_missing"] = True
        self.write([], {vid: e})                        # gone from the file
        rc, alarms, session, out = self.run_bot({})
        self.assertEqual(rc, 0)
        self.assertEqual(alarms[vid]["status"], bot.ALARM_RESOLVED)
        self.assertEqual([c for c in session.calls if c[0] != "POST"], [])   # no incident calls
        self.assertTrue(any("missing in MainManager" in m for m in out))

    def test_non_404_failures_give_up_after_three(self):
        vid = "VISTA_SERVER#5"
        e = self.entry(vid, 123, [0, 0, 0, "No user"])
        e["update_failures"] = bot.MAX_API_FAILURES - 1
        self.write([alr_row(vid, ack=1, user="GPST (Georgi ISS)")], {vid: e})
        rc, alarms, _, out = self.run_bot({
            ("GET", "/api/v3/incidents/123"): FakeResponse(500),
        })
        self.assertEqual(rc, 0)
        self.assertEqual(alarms[vid]["last_state_sig"], [0, 0, 1, "GPST (Georgi ISS)"])
        self.assertEqual(alarms[vid]["update_failures"], 0)
        self.assertTrue(any("giving up" in m for m in out))

    def test_dry_run_writes_nothing_and_only_checks_credentials(self):
        new, changed, gone = "VISTA_SERVER#8", "VISTA_SERVER#9", "VISTA_SERVER#10"
        self.write([alr_row(new), alr_row(changed, ack=1, user="GPST (Georgi ISS)")],
                   {changed: self.entry(changed, 1, [0, 0, 0, "No user"]),
                    gone: self.entry(gone, 2, [0, 0, 0, "No user"])})
        state_before = self.state.read_bytes()
        rc, alarms, session, out = self.run_bot({}, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.state.read_bytes(), state_before)              # state untouched
        self.assertFalse((Path(self.tmp.name) / "csv").exists())            # no CSV audit
        self.assertFalse((Path(self.tmp.name) / "csv_state.json").exists())
        self.assertEqual([c[:2] for c in session.calls], [("POST", "/restapi/token")])  # token only
        self.assertTrue(any("[DRY] MainManager credentials OK" in m for m in out))
        self.assertTrue(any("[DRY] Would create incident" in m for m in out))
        self.assertTrue(any("[DRY] Would update incident 1" in m for m in out))
        self.assertTrue(any("[DRY] Would mark VISTA_SERVER#10 RESOLVED" in m for m in out))
        self.assertTrue(any("nothing written" in m for m in out))

    def test_parse_only_writes_nothing(self):
        vid = "VISTA_SERVER#11"
        self.write([alr_row(vid)], {})
        state_before = self.state.read_bytes()
        rc, alarms, session, out = self.run_bot({}, parse_only=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertFalse((Path(self.tmp.name) / "csv").exists())
        self.assertEqual(session.calls, [])

    def test_happy_path_create_and_resolve(self):
        new, gone = "VISTA_SERVER#6", "VISTA_SERVER#7"
        ticket = FakeIncident(ID=55, Description="old", Remarks="old", Name="n", MainID=14228, StatusID=5)
        # last seen ACTIVE -> two lines are prepended; RESOLVED must end on top
        self.write([alr_row(new)], {gone: self.entry(gone, 55, [0, 0, 0, "No user"])})
        rc, alarms, session, out = self.run_bot({
            ("POST", "/api/v3/incidents"): items({"success": True, "id": 900}),
            ("GET", "/api/v3/incidents/900"): items({"ID": 900, "StatusID": 5}),
            ("GET", "/api/v3/incidents/55"): ticket.get,
            ("PUT", "/api/v3/incidents"): ticket.put,
        })
        self.assertEqual(rc, 0)
        self.assertEqual(alarms[new]["incident_id"], 900)
        self.assertEqual(alarms[gone]["status"], bot.ALARM_RESOLVED)
        lines = ticket.item["Description"].split("\n")
        self.assertEqual(lines[2], "old")
        self.assertTrue(lines[0].endswith(
            "RESOLVED — alarm was acknowledged (kvitteret) and removed from Vista alarm list"))
        self.assertTrue(lines[1].endswith("alarm returned to NORMAL (missed between polls)"))
        self.assertEqual(ticket.item["Name"], "n")             # echoed, not blanked
        self.assertTrue(any("created=1, updated=0, resolved=1" in m for m in out))


if __name__ == "__main__":
    unittest.main()
