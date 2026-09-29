"""
TAC Vista alarm bot -> MainManager incident pipeline.

Mirrors the Indeklima bot architecture, but reacts to events in the $this.alr
alarm list instead of polling temperature logs.

Run via Windows Task Scheduler (every 5 min).

MainManager credentials come from secrets.json (paths.secrets_file) or the
MM_USERNAME / MM_PASSWORD environment variables — never from config.json.

CLI:
    python main.py                  # normal run
    python main.py --dry-run        # log intended actions; writes nothing, makes
                                    # no API call except a credential check
    python main.py --parse-only     # print parsed alarms; writes nothing
    python main.py --no-bootstrap   # treat all current alarms as NEW (creates
                                    # incidents for everything in the list right now)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

try:
    import shipper          # optional VPS shipper (ADR-0016), next to main.py
except ImportError:
    shipper = None

__version__ = "2.1.0"   # v3 incident API + optional VPS shipper; logged in the banner


# ---------------------------------------------------------------------------
# Config & logging
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH  = r"C:\priorityalarmsapi\config.json"
DEFAULT_SECRETS_PATH = r"C:\priorityalarmsapi\secrets.json"

# Consecutive non-404 API failures on one transition before the bot stops
# retrying it (state advances, tracking continues).
MAX_API_FAILURES = 3


def load_config(path: str) -> dict:
    # utf-8-sig: Notepad on Server 2016 saves "UTF-8" with a BOM
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_secrets(cfg: dict, log: logging.Logger) -> dict:
    """MainManager credentials, in order of precedence:
    1. environment variables MM_USERNAME / MM_PASSWORD
    2. paths.secrets_file — JSON {"mainmanager": {"username": ..., "password": ...}}
    3. legacy mainmanager.username / .password in config.json (deprecated, warns)
    Returns {} when none is configured; the API is then skipped for the run.
    """
    env_u, env_p = os.environ.get("MM_USERNAME"), os.environ.get("MM_PASSWORD")
    if env_u and env_p:
        log.info("Credentials: from environment (MM_USERNAME/MM_PASSWORD)")
        return {"username": env_u, "password": env_p}

    path = cfg.get("paths", {}).get("secrets_file", DEFAULT_SECRETS_PATH)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:   # tolerate a Notepad BOM
            mm = json.load(f).get("mainmanager", {})
        if mm.get("username") and mm.get("password"):
            log.info(f"Credentials: from {path}")
            return {"username": mm["username"], "password": mm["password"]}
        log.warning(f"{path}: mainmanager.username/password missing")

    legacy = cfg.get("mainmanager", {})
    if legacy.get("username") and legacy.get("password"):
        log.warning("Credentials: from config.json — DEPRECATED, move them to "
                    f"{path or DEFAULT_SECRETS_PATH}")
        return {"username": legacy["username"], "password": legacy["password"]}

    log.warning("No MainManager credentials configured")
    return {}


def setup_logging(log_folder: str) -> logging.Logger:
    Path(log_folder).mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = Path(log_folder) / f"{today}.log"

    logger = logging.getLogger("alarmbot")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# Alarm file parsing
# ---------------------------------------------------------------------------

# Field indices in the tab-delimited $this.alr file (36 fields per row).
F_VISTA_ID       = 1
F_ALARM_OBJECT   = 2
F_DATE1          = 3    # unix epoch in hex — first occurrence
F_STATE1         = 4    # 0 = ACTIVE, 1 = NORMAL (returned to normal), 6 = system
F_STATE2         = 5    # 0 normally, 2 for info/system events
F_DATE2          = 6    # last state transition, unix hex
F_PRIORITY       = 7
F_USER           = 10   # "No user" or named operator
F_ACK_FLAG       = 11   # 0 = unacknowledged, 1 = acknowledged
F_ALARM_TEXT     = 13
F_COUNT          = 18   # re-trigger count
F_DIRECTORY      = 22   # full Vista object path — exception matching key

EXPECTED_FIELDS = 36


# ---------------------------------------------------------------------------
# Vista alarm status terminology
# ---------------------------------------------------------------------------
# TAC Vista uses a two-axis model:
#   Axis 1 — Condition:   ACTIVE (state1=0) or NORMAL (state1=1)
#   Axis 2 — Operator:    UNACKNOWLEDGED (ack=0) or ACKNOWLEDGED (ack=1)
#
#   ACTIVE                  — condition present, no one has acknowledged
#   ACTIVE + ACKNOWLEDGED   — condition present, operator has seen it
#   NORMAL                  — condition gone, not yet acknowledged
#   NORMAL + ACKNOWLEDGED   — gone and acknowledged, Vista removes row soon
#   RESOLVED                — row removed from file (our term, not Vista's)

ALARM_ACTIVE              = "ACTIVE"
ALARM_ACTIVE_ACKNOWLEDGED = "ACTIVE + ACKNOWLEDGED"
ALARM_NORMAL              = "NORMAL"
ALARM_NORMAL_ACKNOWLEDGED = "NORMAL + ACKNOWLEDGED"
ALARM_RESOLVED            = "RESOLVED"


def classify_alarm_status(state1: int, ack_flag: int, user: str) -> str:
    is_active = (state1 == 0)
    is_acked  = (ack_flag == 1 and user.strip().lower() != "no user")
    if is_active and is_acked:
        return ALARM_ACTIVE_ACKNOWLEDGED
    if is_active:
        return ALARM_ACTIVE
    if is_acked:
        return ALARM_NORMAL_ACKNOWLEDGED
    return ALARM_NORMAL


class Alarm:
    __slots__ = ("vista_id", "alarm_object", "date1_epoch", "state1",
                 "state2", "date2_epoch", "priority", "user", "ack_flag",
                 "alarm_text", "count", "directory")

    def __init__(self, *, vista_id, alarm_object, date1_epoch, state1,
                 state2, date2_epoch, priority, user, ack_flag, alarm_text,
                 count, directory):
        self.vista_id     = vista_id
        self.alarm_object = alarm_object
        self.date1_epoch  = date1_epoch
        self.state1       = state1
        self.state2       = state2
        self.date2_epoch  = date2_epoch
        self.priority     = priority
        self.user         = user
        self.ack_flag     = ack_flag
        self.alarm_text   = alarm_text
        self.count        = count
        self.directory    = directory

    @property
    def status_label(self) -> str:
        return classify_alarm_status(self.state1, self.ack_flag, self.user)

    @property
    def is_system_event(self) -> bool:
        return self.state1 == 6 and self.state2 == 2

    def state_signature(self) -> tuple:
        return (self.state1, self.state2, self.ack_flag, self.user.strip())


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def snapshot_alarm_file(src: str, dst: str, log: logging.Logger,
                       max_retries: int = 5, retry_delay: float = 0.5):
    last_err = None
    for attempt in range(max_retries):
        try:
            shutil.copy2(src, dst)
            return
        except (PermissionError, OSError) as e:
            last_err = e
            log.warning(f"snapshot attempt {attempt+1}/{max_retries} failed: {e}")
            time.sleep(retry_delay)
    raise last_err


def parse_alarm_file(path: str, encoding: str, log: logging.Logger) -> list[Alarm]:
    alarms: list[Alarm] = []
    with open(path, "r", encoding=encoding, errors="replace") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < EXPECTED_FIELDS:
                log.warning(f"line {lineno}: expected {EXPECTED_FIELDS} fields, "
                            f"got {len(fields)} — skipping")
                continue
            try:
                alarm = Alarm(
                    vista_id     = fields[F_VISTA_ID].strip(),
                    alarm_object = fields[F_ALARM_OBJECT].strip(),
                    date1_epoch  = int(fields[F_DATE1].strip(), 16),
                    state1       = int(fields[F_STATE1].strip()),
                    state2       = int(fields[F_STATE2].strip()),
                    date2_epoch  = int(fields[F_DATE2].strip(), 16),
                    priority     = int(fields[F_PRIORITY].strip()),
                    user         = fields[F_USER].strip(),
                    ack_flag     = int(fields[F_ACK_FLAG].strip()),
                    alarm_text   = fields[F_ALARM_TEXT].strip(),
                    count        = int(fields[F_COUNT].strip()),
                    directory    = fields[F_DIRECTORY].strip(),
                )
            except (ValueError, IndexError) as e:
                log.warning(f"line {lineno}: parse error ({e}) — skipping")
                continue
            alarms.append(alarm)
    return alarms


# ---------------------------------------------------------------------------
# Mapping & exception files
# ---------------------------------------------------------------------------

def load_objects_csv(path: str, log: logging.Logger) -> dict[str, int]:
    """<alarm_object>,<main_id> — no header, comma or semicolon."""
    mapping: dict[str, int] = {}
    if not os.path.exists(path):
        log.warning(f"objects.csv not found at {path} — all alarms use fallback")
        return mapping
    with open(path, "r", encoding="utf-8-sig") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            sep = "," if "," in line else (";" if ";" in line else None)
            if not sep:
                log.warning(f"objects.csv:{lineno}: no delimiter — skipping")
                continue
            parts = [p.strip() for p in line.split(sep, 1)]
            if len(parts) != 2:
                continue
            try:
                mapping[parts[0]] = int(parts[1])
            except ValueError:
                log.warning(f"objects.csv:{lineno}: MainID not integer")
    log.info(f"Loaded {len(mapping)} object mappings from {path}")
    return mapping


def load_exceptions_csv(path: str, log: logging.Logger) -> set[str]:
    """CSV: first column = directory path (exact match), second = reason (optional).
    Lines starting with '#' are comments. Accepts comma or semicolon.
    """
    exceptions: set[str] = set()
    if not os.path.exists(path):
        log.info(f"exceptions.csv not found at {path} — no exceptions")
        return exceptions
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            sep = "," if "," in line else (";" if ";" in line else None)
            directory = line.split(sep, 1)[0].strip() if sep else line.strip()
            if directory:
                exceptions.add(directory)
    log.info(f"Loaded {len(exceptions)} exception entries from {path}")
    return exceptions


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def empty_state() -> dict:
    return {"meta": {"bootstrapped": False, "last_run_iso": None}, "alarms": {}}


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return empty_state()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "meta" not in data:
        data = empty_state() | {"alarms": data}
    if "alarms" not in data:
        data["alarms"] = {}
    return data


def save_state(path: str, state: dict):
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def prune_state(state: dict, days: int, log: logging.Logger):
    if days <= 0:
        return
    cutoff = datetime.now() - timedelta(days=days)
    removed = []
    for vid, entry in list(state["alarms"].items()):
        if entry.get("status") != ALARM_RESOLVED:
            continue
        iso = entry.get("resolved_iso")
        if not iso:
            continue
        try:
            if datetime.fromisoformat(iso) < cutoff:
                removed.append(vid)
                del state["alarms"][vid]
        except ValueError:
            pass
    if removed:
        log.info(f"Pruned {len(removed)} resolved entries older than {days} days")


# ---------------------------------------------------------------------------
# CSV audit logging — one file per alarm directory, ALL priorities
# ---------------------------------------------------------------------------

CSV_HEADER = (
    "datetime;event;vista_id;alarm_object;directory;alarm_text;priority;"
    "user;ack_flag;state1;state2;date1_hex;date1_human;date2_hex;"
    "date2_human;count;status_label\n"
)


def epoch_hex_to_human(epoch: int) -> str:
    """Convert Unix epoch to DD-MM-YYYY HH:MM:SS local time."""
    try:
        return datetime.fromtimestamp(epoch).strftime("%d-%m-%Y %H:%M:%S")
    except (OSError, ValueError):
        return ""


def sanitize_filename(directory: str, max_len: int = 150) -> str:
    """Turn a Vista directory path into a safe filename.
    Vista paths are already mostly safe (hyphens, underscores, dots).
    Just replace any remaining risky chars and truncate.
    """
    safe = directory.replace("/", "_").replace("\\", "_").replace(":", "_")
    safe = safe.replace('"', "").replace("'", "").replace(" ", "_")
    safe = safe.replace("#", "_").replace("$", "_")
    if len(safe) > max_len:
        safe = safe[:max_len]
    return safe


def csv_row(ts: str, event: str, alarm: Alarm) -> str:
    """Build one semicolon-delimited CSV row."""
    cols = [
        ts,
        event,
        alarm.vista_id,
        alarm.alarm_object,
        alarm.directory,
        alarm.alarm_text.replace(";", ","),  # avoid breaking delimiter
        str(alarm.priority),
        alarm.user,
        str(alarm.ack_flag),
        str(alarm.state1),
        str(alarm.state2),
        f"{alarm.date1_epoch:08X}",
        epoch_hex_to_human(alarm.date1_epoch),
        f"{alarm.date2_epoch:08X}",
        epoch_hex_to_human(alarm.date2_epoch),
        str(alarm.count),
        alarm.status_label,
    ]
    return ";".join(cols) + "\n"


def csv_resolved_row(ts: str, entry: dict) -> str:
    """Build a RESOLVED row from state entry (alarm no longer in file)."""
    cols = [
        ts,
        "RESOLVED",
        entry.get("vista_id", ""),
        entry.get("alarm_object", ""),
        entry.get("directory", ""),
        entry.get("initial_alarm_text", "").replace(";", ","),
        str(entry.get("priority", "")),
        "",  # user unknown — row is gone
        "",
        "", "",
        "", "", "", "",
        "",
        ALARM_RESOLVED,
    ]
    return ";".join(cols) + "\n"


def append_csv_event(csv_folder: str, directory: str, row: str):
    """Append a row to the per-directory CSV file. Create with header if new."""
    Path(csv_folder).mkdir(parents=True, exist_ok=True)
    fname = sanitize_filename(directory) + ".csv"
    fpath = Path(csv_folder) / fname
    is_new = not fpath.exists()
    with open(fpath, "a", encoding="utf-8") as f:
        if is_new:
            f.write(CSV_HEADER)
        f.write(row)


def classify_csv_event(prev_sig: Optional[tuple], alarm: Alarm) -> list[str]:
    """Determine which CSV events to log based on state change.

    Returns a list of event names. Multiple possible if several things
    changed between polls (e.g. NORMAL + ACKNOWLEDGED in one step).
    """
    if prev_sig is None:
        return ["FIRST_SEEN"]

    p_s1, p_s2, p_ack, p_user = prev_sig
    events: list[str] = []

    # Condition axis
    if p_s1 == 0 and alarm.state1 == 1:
        events.append("NORMAL")
    elif p_s1 == 1 and alarm.state1 == 0:
        events.append("ACTIVE")
    elif p_s1 != alarm.state1:
        events.append(f"STATE1_{p_s1}_TO_{alarm.state1}")

    # Acknowledgment axis
    if p_ack == 0 and alarm.ack_flag == 1:
        events.append("ACKNOWLEDGED")
    elif p_ack == 1 and alarm.ack_flag == 0:
        events.append("UNACKNOWLEDGED")

    # User change (without ack change — e.g. re-ack by different person)
    if not events and p_user != alarm.user.strip():
        events.append("USER_CHANGED")

    # Catch-all if signature changed but nothing matched
    if not events:
        events.append("STATE_CHANGED")

    return events


def load_csv_state(path: str) -> dict:
    """Separate state file for CSV tracking — covers ALL alarms, all priorities."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_csv_state(path: str, csv_state: dict):
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(csv_state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def event_record(now: datetime, event: str, alarm: Optional[Alarm] = None,
                 entry: Optional[dict] = None) -> dict:
    """Event as written to CSV, as a dict for the shipper (`ts` is an aware
    datetime; Alarm fields keep their Python types). For synthetic rows
    (built from a csv_state entry) the unknown fields are None."""
    if alarm is not None:
        rec = {f: getattr(alarm, f) for f in Alarm.__slots__}
        rec["status_label"] = alarm.status_label
    else:
        entry = entry or {}
        rec = {f: None for f in Alarm.__slots__}
        rec.update(vista_id=entry.get("vista_id", ""),
                   alarm_object=entry.get("alarm_object", ""),
                   directory=entry.get("directory", ""),
                   alarm_text=entry.get("initial_alarm_text", ""),
                   priority=entry.get("priority"),
                   status_label=event)
    rec.update(ts=now, event=event)
    return rec


def run_csv_logging(alarms: list[Alarm], csv_folder: str,
                    csv_state_path: str, log: logging.Logger,
                    write: bool = True) -> list[dict]:
    """Log state changes for ALL alarms to per-directory CSV files.

    This runs independently from the incident API logic. No priority
    filter, no exception filter — every alarm gets tracked.
    Returns the events written (one dict per CSV row, see event_record()).
    With write=False (--dry-run / --parse-only) the events are computed
    against csv_state.json but nothing is appended or saved.
    """
    csv_state = load_csv_state(csv_state_path)
    now = datetime.now().astimezone()
    ts = now.strftime("%d-%m-%Y %H:%M:%S")
    current_ids = set()
    csv_events = 0
    written: list[dict] = []

    for a in alarms:
        # Skip system events (6,2) — these are info messages, not real alarms
        if a.is_system_event:
            continue

        current_ids.add(a.vista_id)
        prev_entry = csv_state.get(a.vista_id)
        prev_sig = tuple(prev_entry["sig"]) if prev_entry else None

        new_sig = a.state_signature()

        if prev_sig is not None and new_sig == prev_sig:
            continue  # no change

        events = classify_csv_event(prev_sig, a)

        for event in events:
            row = csv_row(ts, event, a)
            if write:
                append_csv_event(csv_folder, a.directory, row)
            csv_events += 1
            written.append(event_record(now, event, alarm=a))

        csv_state[a.vista_id] = {
            "sig": list(new_sig),
            "directory": a.directory,
            "alarm_object": a.alarm_object,
            "initial_alarm_text": a.alarm_text,
            "priority": a.priority,
            "vista_id": a.vista_id,
        }

    # Detect resolved (in csv_state but gone from file)
    for vid in list(csv_state.keys()):
        if vid in current_ids:
            continue
        entry = csv_state[vid]
        if entry.get("resolved"):
            continue

        prev_sig = tuple(entry.get("sig", []))
        prev_s1 = prev_sig[0] if len(prev_sig) > 0 else None

        directory = entry.get("directory", "")
        if directory:
            # If last seen ACTIVE, log the missed NORMAL transition too
            if prev_s1 == 0:
                # Build a minimal resolved row noting the missed NORMAL
                normal_row = (
                    f"{ts};NORMAL;{entry.get('vista_id','')};{entry.get('alarm_object','')}"
                    f";{directory};{entry.get('initial_alarm_text','').replace(';',',')}"
                    f";{entry.get('priority','')};;;;;;;;;;"
                    f"{ALARM_NORMAL}\n"
                )
                if write:
                    append_csv_event(csv_folder, directory, normal_row)
                csv_events += 1
                written.append(event_record(now, "NORMAL", entry=entry))

            resolved_row = csv_resolved_row(ts, entry)
            if write:
                append_csv_event(csv_folder, directory, resolved_row)
            csv_events += 1
            written.append(event_record(now, ALARM_RESOLVED, entry=entry))

        entry["resolved"] = True

    # Drop resolved entries right away (keeps the state file bounded; the
    # CSV files are the permanent record)
    for vid in list(csv_state.keys()):
        if csv_state[vid].get("resolved"):
            del csv_state[vid]

    if write:
        save_csv_state(csv_state_path, csv_state)

    if csv_events > 0:
        verb = "logged" if write else "[DRY] would log"
        log.info(f"CSV audit: {verb} {csv_events} events across alarm directories")
    return written


# ---------------------------------------------------------------------------
# MainManager API client
# ---------------------------------------------------------------------------

class MMNotFound(Exception):
    """A MainManager endpoint answered HTTP 404 — either this incident is gone
    or the endpoint itself is (the /restapi/Incident/* routes died 2026-09-19)."""


class MMClient:
    """MainManager REST client on the v3 incident API.

    /restapi/Incident/{CreateIncident,GetIncident,UpdateIncident} answered 404
    from 2026-09-19 while the tickets still existed; the tenant's other clients
    use /api/v3/incidents. Only the token endpoint is unchanged.
    See docs/adr/0019-mainmanager-v3-incident-api.md.
    """

    TOKEN_MARGIN_S = 300

    # The v3 write model as the tenant echoed it on 2026-09-28 (create of
    # incident 37378), minus ID and Remarks which every call sets itself.
    # Echoed on every PUT so an update cannot blank them. Keys outside this
    # model (Description, DateReported, CheckwordItemID, …) are dropped
    # silently by the tenant.
    ECHO_FIELDS = (
        "Name", "MainID", "LocationID", "BuildingZoneID", "IncidentTypeID",
        "StatusID", "GradeID", "IncidentPriorityID", "DateInspected",
        "ReportedByID", "ReportedByOrganisationID",
        "Contact", "ContactEmail", "ContactNumber",
        "ConditionGradeID", "ConsequenceGradeID", "EstimatedCost",
        "PriorityRemarks", "XID", "Inactive",
    )

    def __init__(self, cfg_mm: dict, creds: dict, log: logging.Logger,
                 token_cache: Optional[str] = None, session=None):
        self.base_url = cfg_mm["base_url"].rstrip("/")
        self.creds = creds or {}
        self.defaults = cfg_mm["incident_defaults"]
        self.log = log
        self.token_cache = token_cache
        self.http = session or requests.Session()
        self._token: Optional[str] = None
        self._token_exp = 0.0
        for k in ("LocationID", "ReportedByID", "ReportedByOrganisationID"):
            if self.defaults.get(k) is None:
                log.warning(f"incident_defaults.{k} not set — MainManager will derive it")

    # -- auth ---------------------------------------------------------------

    def _get_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - self.TOKEN_MARGIN_S:
            return self._token
        if self.token_cache and os.path.exists(self.token_cache):
            try:
                with open(self.token_cache, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if float(cached["exp"]) - self.TOKEN_MARGIN_S > now:
                    self._token, self._token_exp = cached["access_token"], float(cached["exp"])
                    self.log.info("API AUTH: using cached token")
                    return self._token
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if not self.creds.get("username") or not self.creds.get("password"):
            raise RuntimeError("No MainManager credentials configured "
                               "(secrets.json or MM_USERNAME/MM_PASSWORD)")
        url = f"{self.base_url}/restapi/token"
        self.log.info(f"API AUTH: requesting token from {url}")
        r = self.http.post(url, data={
            "username": self.creds["username"],
            "password": self.creds["password"],
            "grant_type": "password",
        }, timeout=30)
        self.log.info(f"API AUTH: HTTP {r.status_code}")
        r.raise_for_status()
        js = r.json()
        self._token = js["access_token"]
        self._token_exp = now + int(js.get("expires_in", 7200))
        if self.token_cache:
            try:
                with open(self.token_cache, "w", encoding="utf-8") as f:
                    json.dump({"access_token": self._token, "exp": self._token_exp}, f)
            except OSError as e:
                self.log.warning(f"API AUTH: token cache not writable: {e}")
        self.log.info("API AUTH: token obtained OK")
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, f"{self.base_url}{path}",
                              headers=self._headers(), timeout=60, **kw)
        if r.status_code == 404:
            raise MMNotFound(f"{method} {path} -> HTTP 404")
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _first_item(js) -> Optional[dict]:
        items = js.get("items") if isinstance(js, dict) else None
        return items[0] if items else None

    def _echo(self, item: dict) -> dict:
        return {k: item[k] for k in self.ECHO_FIELDS if item.get(k) is not None}

    def _put(self, item: dict) -> dict:
        js = self._request("PUT", "/api/v3/incidents", json={"items": [item]})
        res = self._first_item(js) or {}
        if res.get("success") is False:
            raise RuntimeError(f"PUT incident failed: {res.get('errorMessage') or js}")
        return res

    def check_auth(self) -> None:
        """Obtain a token and nothing else — the --dry-run credential check."""
        self._get_token()

    # -- incidents ------------------------------------------------------------

    def probe(self) -> bool:
        """True when the v3 incident endpoint answers at all — tells an
        API-wide failure from one missing ticket. Any error counts as 'down'."""
        try:
            self._request("GET", "/api/v3/incidents",
                          params={"pageNumber": 1, "pageSize": 1})
            return True
        except Exception as e:
            self.log.debug(f"API PROBE failed: {e}")
            return False

    def get_incident(self, incident_id: int) -> dict:
        js = self._request("GET", f"/api/v3/incidents/{int(incident_id)}")
        item = self._first_item(js)
        if item is None:
            raise MMNotFound(f"incident {incident_id}: no item in response")
        return item

    def create_incident(self, main_id: int, name: str, remarks: str) -> int:
        d = self.defaults
        type_id = int(d.get("IncidentTypeID", d.get("CheckwordItemID")))
        # Remarks is the write field: the tenant stores it as the full
        # Description and keeps a ~245-char copy in Remarks. DateReported is
        # set by the tenant to the creation time (not writable).
        item = {
            "MainID":          int(main_id),
            "Name":            name,
            "Remarks":         remarks,
            "IncidentTypeID":  type_id,
            "CheckwordItemID": type_id,   # pre-2026-09 spelling; dropped if unknown
            "GradeID":         int(d["GradeID"]),
            "StatusID":        int(d["StatusID"]),
        }
        for k in ("LocationID", "ReportedByID", "ReportedByOrganisationID"):
            if d.get(k) is not None:
                item[k] = int(d[k])

        self.log.info(f"API CREATE: POST {self.base_url}/api/v3/incidents")
        self.log.info(f"API CREATE: MainID={main_id}, Name={name!r}")
        js = self._request("POST", "/api/v3/incidents", json={"items": [item]})
        res = self._first_item(js) or {}
        self.log.info(f"API CREATE: response={res}")
        if not res.get("success") or not res.get("id"):
            raise RuntimeError(f"CreateIncident failed: {res.get('errorMessage') or js}")
        incident_id = int(res["id"])

        # The StatusID sent on create does not stick on this tenant: enforce it
        # with a PUT and verify with a GET. The incident exists from here on —
        # a failure below is logged, never retried as a second create.
        try:
            back = self.get_incident(incident_id)
            if int(back.get("StatusID") or 0) != int(d["StatusID"]):
                self._put({**self._echo(back), "ID": incident_id,
                           "StatusID": int(d["StatusID"])})
                back = self.get_incident(incident_id)
                if int(back.get("StatusID") or 0) != int(d["StatusID"]):
                    self.log.warning(f"API CREATE: incident {incident_id} StatusID reads "
                                     f"{back.get('StatusID')}, wanted {d['StatusID']}")
        except Exception as e:
            self.log.warning(f"API CREATE: status check for {incident_id} failed: {e}")
        return incident_id

    @staticmethod
    def _text(item: dict) -> str:
        """The incident's full text. `Description` is the full field; `Remarks`
        is a copy the tenant truncates to ~245 characters — never read it back
        as the source, or every update loses the older lines (2026-09-28)."""
        return item.get("Description") or item.get("Remarks") or ""

    def prepend_description_line(self, incident_id: int, new_line: str):
        inc = self.get_incident(incident_id)
        current = self._text(inc)
        combined = new_line + ("\n" + current if current else "")
        self.log.info(f"API UPDATE: PUT {self.base_url}/api/v3/incidents ID={incident_id}")
        self._put({**self._echo(inc), "ID": int(incident_id), "Remarks": combined})
        back = self.get_incident(incident_id)
        if self._text(back) != combined:
            raise RuntimeError(f"UpdateIncident {incident_id}: description did not "
                               "read back as written")
        self.log.info(f"API UPDATE: verified incident {incident_id}")


# ---------------------------------------------------------------------------
# Transition description lines
# ---------------------------------------------------------------------------

def dk_now_str() -> str:
    return datetime.now().strftime("%d-%m-%Y %H:%M")


def describe_transitions(prev_sig: tuple, alarm: Alarm) -> list[str]:
    """Build description lines using Vista terminology."""
    p_s1, p_s2, p_ack, p_user = prev_sig
    lines: list[str] = []
    ts = dk_now_str()

    # Condition axis: ACTIVE <-> NORMAL
    if p_s1 == 0 and alarm.state1 == 1:
        lines.append(
            f'{ts} Alarm bot - alarm returned to NORMAL ("{alarm.alarm_text}")'
        )
    elif p_s1 == 1 and alarm.state1 == 0:
        lines.append(
            f'{ts} Alarm bot - alarm ACTIVE again ("{alarm.alarm_text}")'
        )

    # Operator axis: UNACKNOWLEDGED -> ACKNOWLEDGED
    if p_ack == 0 and alarm.ack_flag == 1 and alarm.user.strip().lower() != "no user":
        lines.append(f"{ts} Alarm bot - ACKNOWLEDGED by {alarm.user}")
    elif (p_user != alarm.user and alarm.ack_flag == 1
          and alarm.user.strip().lower() != "no user"):
        lines.append(f"{ts} Alarm bot - re-acknowledged by {alarm.user}")

    # Catch-all
    if not lines:
        lines.append(
            f"{ts} Alarm bot - status changed to {alarm.status_label} "
            f"(state1 {p_s1}->{alarm.state1}, ack {p_ack}->{alarm.ack_flag})"
        )

    return lines


def initial_description(alarm: Alarm) -> str:
    ts = dk_now_str()
    text = alarm.alarm_text or "(no text)"
    return (
        f'{ts} Alarm bot - NEW alarm, status: {alarm.status_label}, '
        f'priority {alarm.priority}: "{text}"\n'
        f"Object: {alarm.alarm_object}\n"
        f"Directory: {alarm.directory}"
    )


def incident_name(alarm: Alarm, max_len: int = 100) -> str:
    text = alarm.alarm_text or "alarm"
    name = f"CTS Alarm - {alarm.alarm_object} - {text}"
    return name if len(name) <= max_len else name[: max_len - 3] + "..."


# ---------------------------------------------------------------------------
# Per-alarm status table (logged every run)
# ---------------------------------------------------------------------------

def log_alarm_status_table(kept: list[Alarm], state: dict,
                           log: logging.Logger):
    """Log every alarm's current Vista status — one line per alarm.
    Runs every cycle so the log is a complete audit trail.
    """
    log.info("--- Alarm status table ---")
    log.info(f"  {'Vista ID':<30} {'Pri':>3} {'Status':<24} "
             f"{'User':<28} {'Alarm Text':<45} {'Tracking'}")

    for a in kept:
        prev = state["alarms"].get(a.vista_id)
        if prev is None:
            tracking = "NEW — will create incident"
        elif prev.get("incident_id") is not None:
            tracking = f"incident #{prev['incident_id']}"
            if prev.get("incident_missing"):
                tracking += " (missing in MainManager)"
        else:
            tracking = "bootstrapped — no incident yet"

        log.info(
            f"  {a.vista_id:<30} {a.priority:>3} {a.status_label:<24} "
            f"{a.user:<28} {a.alarm_text[:45]:<45} {tracking}"
        )

    # Summary counts
    counts: dict[str, int] = {}
    for a in kept:
        counts[a.status_label] = counts.get(a.status_label, 0) + 1
    summary = ", ".join(f"{s}: {n}" for s, n in sorted(counts.items()))
    log.info(f"--- Total: {len(kept)} alarms — {summary} ---")


# ---------------------------------------------------------------------------
# Main pipeline helpers
# ---------------------------------------------------------------------------

def make_state_entry(alarm: Alarm, main_id: int, incident_id: Optional[int],
                     status: str, main_id_fallback_used: bool) -> dict:
    now_iso = datetime.now().isoformat(timespec="seconds")
    return {
        "incident_id":            incident_id,
        "main_id":                main_id,
        "main_id_fallback_used":  main_id_fallback_used,
        "alarm_object":           alarm.alarm_object,
        "directory":              alarm.directory,
        "priority":               alarm.priority,
        "initial_alarm_text":     alarm.alarm_text,
        "first_seen_iso":         now_iso,
        "last_state_sig":         list(alarm.state_signature()),
        "last_update_iso":        now_iso,
        "status":                 status,
    }


def resolve_main_id(alarm: Alarm, mapping: dict[str, int],
                    default: int, log: logging.Logger) -> tuple[int, bool]:
    if alarm.alarm_object in mapping:
        return mapping[alarm.alarm_object], False
    log.warning(f"No objects.csv mapping for '{alarm.alarm_object}' — "
                f"using fallback MainID {default}")
    return default, True


def create_incident_for_alarm(alarm: Alarm, main_id: int, fallback: bool,
                              mm: "MMClient", state: dict, log: logging.Logger,
                              dry_run: bool,
                              extra_desc_lines: Optional[list[str]] = None,
                              errors: Optional[list[str]] = None
                              ) -> Optional[int]:
    """Create a MainManager incident. Returns incident_id or None."""
    name = incident_name(alarm)
    desc = initial_description(alarm)
    if extra_desc_lines:
        desc = "\n".join(extra_desc_lines) + "\n" + desc

    if dry_run:
        log.info(f"[DRY] Would create incident: main_id={main_id} "
                 f"name={name!r}")
        return None

    try:
        incident_id = mm.create_incident(main_id, name, desc)
    except MMNotFound:
        raise                       # the caller decides what a 404 means
    except Exception as e:
        log.error(f"CreateIncident FAILED for {alarm.vista_id}: {e}")
        if errors is not None:
            errors.append(f"CreateIncident FAILED for {alarm.vista_id}: {e}")
        return None

    log.info(f"CREATED incident {incident_id} for {alarm.vista_id} "
             f"({alarm.alarm_object}) main_id={main_id}"
             f"{' [fallback]' if fallback else ''}")
    return incident_id


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(cfg: dict, args: argparse.Namespace, log: logging.Logger) -> int:
    paths      = cfg["paths"]
    thresholds = cfg["thresholds"]
    mm_cfg     = cfg["mainmanager"]
    creds      = load_secrets(cfg, log)

    # ---- Load inputs ---------------------------------------------------
    mapping    = load_objects_csv(paths["objects_csv"], log)
    exceptions = load_exceptions_csv(paths["exceptions_csv"], log)
    state      = load_state(paths["state_file"])

    # --dry-run and --parse-only never persist anything: no state, no CSV,
    # no outbox (the shipper only logs "[DRY] Would ship" in a dry run).
    read_only = bool(args.dry_run or args.parse_only)

    def _save() -> None:
        if not read_only:
            save_state(paths["state_file"], state)

    # ---- Run bookkeeping for the optional VPS shipper ------------------
    started = datetime.now().astimezone()
    alarms: list[Alarm] = []
    events: list[dict] = []          # CSV events written this run
    errors: list[str] = []           # messages also logged via log.error

    def _ship(exit_code: int, **counts) -> int:
        """Hand the run to shipper.ship_run(); a shipper bug never breaks the bot."""
        if shipper is None:
            return exit_code
        run_meta = {
            "run_id":      started.isoformat(timespec="seconds"),
            "started_at":  started,
            "finished_at": datetime.now().astimezone(),
            "bot_version": __version__,
            "parsed_rows": len(alarms),
            "csv_events":  len(events),
            "errors":      errors,
            "exit_code":   exit_code,
            "dry_run":     args.dry_run,
            **counts,
        }
        try:
            shipper.ship_run(cfg, run_meta, events, alarms, state, log)
        except Exception as e:
            log.error(f"VPS shipper failed (non-fatal): {e}")
        return exit_code

    # ---- Snapshot + parse ---------------------------------------------
    working = Path(paths["working_folder"])
    working.mkdir(parents=True, exist_ok=True)
    snapshot_path = str(working / "alarm_snapshot.alr")
    try:
        snapshot_alarm_file(paths["vista_alarm_file"], snapshot_path, log)
    except Exception as e:
        log.error(f"Could not snapshot alarm file: {e}")
        errors.append(f"Could not snapshot alarm file: {e}")
        return _ship(2)

    alarms = parse_alarm_file(
        snapshot_path, cfg.get("log_encoding", "iso-8859-1"), log
    )
    log.info(f"Parsed {len(alarms)} total alarm rows")

    # ---- CSV audit logging (ALL alarms, all priorities) ---------------
    csv_folder = paths.get("csv_folder", "")
    csv_state_path = paths.get("csv_state_file", "")
    if csv_folder and csv_state_path:
        try:
            # read-only runs compute the events (so "[DRY] Would ship" is
            # accurate) but write no CSV row and no csv_state.json
            events = run_csv_logging(alarms, csv_folder, csv_state_path, log,
                                     write=not read_only)
        except Exception as e:
            log.error(f"CSV audit logging failed (non-fatal): {e}")
            errors.append(f"CSV audit logging failed (non-fatal): {e}")

    # ---- Filter --------------------------------------------------------
    max_pri = int(thresholds["max_priority_number"])
    kept: list[Alarm] = []
    for a in alarms:
        if a.priority > max_pri:
            continue
        if a.directory in exceptions:
            log.debug(f"skip (exception): {a.directory}")
            continue
        kept.append(a)
    log.info(f"{len(kept)} alarms after priority<={max_pri} + exception filter "
             f"(removed {len(alarms) - len(kept)})")

    # ---- Log every alarm's current status -----------------------------
    log_alarm_status_table(kept, state, log)

    if args.parse_only:
        return 0

    current_ids = {a.vista_id for a in kept}

    # ---- Bootstrap on first run ---------------------------------------
    if not state["meta"].get("bootstrapped", False) and not args.no_bootstrap:
        log.info(
            f"BOOTSTRAP: first run — recording {len(kept)} existing alarms. "
            "No incidents created now. Incidents will be created when an "
            "alarm's status changes or a new alarm appears."
        )
        for a in kept:
            mid, fb = resolve_main_id(a, mapping, mm_cfg["default_main_id"], log)
            entry = make_state_entry(a, mid, incident_id=None,
                                     status=a.status_label,
                                     main_id_fallback_used=fb)
            state["alarms"][a.vista_id] = entry
        state["meta"]["bootstrapped"] = True
        state["meta"]["last_run_iso"] = datetime.now().isoformat(timespec="seconds")
        _save()
        log.info("BOOTSTRAP complete." if not read_only
                 else "[DRY] BOOTSTRAP not saved (dry-run).")
        return _ship(0, kept=len(kept))

    # ---- MainManager client (lazy — created on first API call) --------
    mm: Optional[MMClient] = None
    api_down: Optional[str] = None   # set once the API is known unusable this run

    def _mm() -> MMClient:
        nonlocal mm
        if mm is None:
            mm = MMClient(mm_cfg, creds, log,
                          token_cache=str(working / "mm_token.json"))
        return mm

    if not creds and not args.dry_run:
        api_down = "no MainManager credentials configured"
        log.error(f"MainManager API unusable this run: {api_down} — "
                  "alarms are tracked, nothing is sent")
        errors.append(f"MainManager API unusable: {api_down}")

    if args.dry_run:
        # The one API call a dry run makes: prove the credentials work.
        if not creds:
            log.error("[DRY] no MainManager credentials configured")
        else:
            try:
                _mm().check_auth()
                log.info("[DRY] MainManager credentials OK")
            except Exception as e:
                log.error(f"[DRY] MainManager credential check FAILED: {e}")

    def _on_not_found(vid: str, entry: Optional[dict], what: str) -> str:
        """Classify a 404. 'api': the whole v3 incident endpoint is gone —
        stop calling it this run and leave state untouched, so the next run
        retries. 'ticket': only this incident is missing — mark the entry and
        stop sending updates for it (tracking continues)."""
        nonlocal api_down
        if entry is not None and _mm().probe():
            entry["incident_missing"] = True
            entry["incident_missing_iso"] = datetime.now().isoformat(timespec="seconds")
            log.warning(f"{vid}: incident #{entry.get('incident_id')} not found in "
                        f"MainManager during {what} — marked incident_missing, "
                        "no further updates for it")
            return "ticket"
        api_down = f"HTTP 404 from the v3 incident API during {what}"
        log.error(f"MainManager API unusable this run: {api_down} — API change "
                  "or lost permission? State left untouched; will retry next run")
        errors.append(f"MainManager API unusable: {api_down}")
        return "api"

    def _give_up(entry: dict, vid: str, what: str, err: Exception) -> bool:
        """Count consecutive non-404 failures on one entry. True after
        MAX_API_FAILURES: abandon this transition (state advances)."""
        n = entry.get("update_failures", 0) + 1
        entry["update_failures"] = n
        if n < MAX_API_FAILURES:
            msg = f"{what} FAILED for {vid} (attempt {n}/{MAX_API_FAILURES}): {err}"
            log.error(msg)
            errors.append(msg)
            return False
        msg = (f"{what} FAILED for {vid} {n} times — giving up on this "
               f"transition, tracking continues: {err}")
        log.error(msg)
        errors.append(msg)
        entry["update_failures"] = 0
        return True

    # ---- Process current alarms ---------------------------------------
    created = updated = skipped = deferred = 0

    for a in kept:
        prev = state["alarms"].get(a.vista_id)

        # -- Brand-new alarm (never seen before) -------------------------
        if prev is None:
            if api_down and not args.dry_run:
                deferred += 1           # not recorded: next run sees it as new
                continue
            mid, fb = resolve_main_id(a, mapping, mm_cfg["default_main_id"], log)
            try:
                iid = create_incident_for_alarm(
                    a, mid, fb, _mm(), state, log, args.dry_run, errors=errors
                )
            except MMNotFound:
                _on_not_found(a.vista_id, None, "CreateIncident")
                deferred += 1
                continue
            entry = make_state_entry(a, mid, iid, a.status_label, fb)
            state["alarms"][a.vista_id] = entry
            _save()
            if iid is not None:
                created += 1
            continue

        # -- Already resolved (shouldn't reappear) -----------------------
        if prev.get("status") == ALARM_RESOLVED:
            log.warning(f"{a.vista_id} reappeared after RESOLVED — ignoring")
            continue

        # -- Check for state transition ----------------------------------
        new_sig  = a.state_signature()
        prev_sig = tuple(prev.get("last_state_sig", []))

        if new_sig == prev_sig:
            skipped += 1
            continue

        # Signature changed — something happened
        transition_lines = describe_transitions(prev_sig, a)
        incident_id = prev.get("incident_id")

        if prev.get("incident_missing"):
            log.info(f"{a.vista_id}: {transition_lines} (incident #{incident_id} "
                     "missing in MainManager — not sent)")
        elif api_down and not args.dry_run:
            deferred += 1               # state untouched: retried next run
            continue
        elif incident_id is None:
            # *** THE FIX: bootstrapped alarm changed status — CREATE
            # an incident now, with the transition info included. ***
            log.info(
                f"{a.vista_id} bootstrapped alarm changed: "
                f"{prev.get('status')} -> {a.status_label} — "
                "creating incident"
            )
            mid = prev.get("main_id", mm_cfg["default_main_id"])
            fb  = prev.get("main_id_fallback_used", True)
            try:
                incident_id = create_incident_for_alarm(
                    a, mid, fb, _mm(), state, log, args.dry_run,
                    extra_desc_lines=transition_lines, errors=errors,
                )
            except MMNotFound:
                _on_not_found(a.vista_id, None, "CreateIncident")
                deferred += 1
                continue
            prev["incident_id"] = incident_id
            if incident_id is not None:
                created += 1
        else:
            # Existing incident — prepend transition lines
            if args.dry_run:
                log.info(f"[DRY] Would update incident {incident_id}: "
                         f"{transition_lines}")
            else:
                try:
                    for line in transition_lines:
                        _mm().prepend_description_line(incident_id, line)
                except MMNotFound:
                    if _on_not_found(a.vista_id, prev, "UpdateIncident") == "api":
                        deferred += 1
                        continue
                except Exception as e:
                    if not _give_up(prev, a.vista_id, "UpdateIncident", e):
                        _save()
                        continue
                else:
                    prev["update_failures"] = 0
                    updated += 1
                    log.info(f"UPDATED incident {incident_id} for "
                             f"{a.vista_id}: {transition_lines}")

        prev["last_state_sig"]  = list(new_sig)
        prev["last_update_iso"] = datetime.now().isoformat(timespec="seconds")
        prev["status"]          = a.status_label
        _save()

    # ---- Detect RESOLVED (in state but gone from file) ----------------
    resolved_now = 0
    for vid in set(state["alarms"].keys()) - current_ids:
        entry = state["alarms"][vid]
        if entry.get("status") == ALARM_RESOLVED:
            continue

        incident_id = entry.get("incident_id")
        ts = dk_now_str()

        # Vista only removes a row once it is both NORMAL and acknowledged
        # (kvitteret). Build the description lines depending on what we
        # last saw — if we last saw ACTIVE, we missed the NORMAL transition
        # too, so log both.
        prev_sig = tuple(entry.get("last_state_sig", []))
        prev_s1 = prev_sig[0] if len(prev_sig) > 0 else None

        resolve_lines: list[str] = []
        if prev_s1 == 0:
            # Last seen ACTIVE — went NORMAL + kvitteret between polls
            resolve_lines.append(
                f"{ts} Alarm bot - alarm returned to NORMAL "
                f"(missed between polls)"
            )
        resolve_lines.append(
            f"{ts} Alarm bot - RESOLVED — alarm was acknowledged "
            f"(kvitteret) and removed from Vista alarm list"
        )

        if args.dry_run:
            log.info(f"[DRY] Would mark {vid} RESOLVED; "
                     f"incident {incident_id}")
            entry["status"] = ALARM_RESOLVED
            continue

        if incident_id is not None and not entry.get("incident_missing"):
            if api_down:
                deferred += 1           # state untouched: retried next run
                continue
            try:
                # Each line is prepended, so the last one (RESOLVED) ends on top
                for rline in resolve_lines:
                    _mm().prepend_description_line(incident_id, rline)
            except MMNotFound:
                if _on_not_found(vid, entry, "resolution update") == "api":
                    deferred += 1
                    continue
            except Exception as e:
                if not _give_up(entry, vid, "Resolution update", e):
                    _save()
                    continue
            else:
                entry["update_failures"] = 0
                log.info(f"RESOLVED incident {incident_id} for {vid} "
                         f"({entry.get('alarm_object')})")
        elif incident_id is not None:
            log.info(f"RESOLVED (incident #{incident_id} missing in MainManager) "
                     f"{vid} ({entry.get('alarm_object')})")
        else:
            log.info(f"RESOLVED (no incident) {vid} "
                     f"({entry.get('alarm_object')})")

        entry["status"]       = ALARM_RESOLVED
        entry["resolved_iso"] = datetime.now().isoformat(timespec="seconds")
        _save()
        resolved_now += 1

    # ---- Prune + save --------------------------------------------------
    prune_state(state, int(cfg.get("prune_resolved_after_days", 30)), log)
    state["meta"]["last_run_iso"] = datetime.now().isoformat(timespec="seconds")
    _save()
    if read_only:
        log.info("[DRY] nothing written (state, CSV and MainManager untouched)")

    if api_down:
        log.error(f"MainManager API was unusable this run ({api_down}); "
                  f"{deferred} action(s) deferred to the next run")
    log.info(f"Run complete: created={created}, updated={updated}, "
             f"resolved={resolved_now}, unchanged={skipped}, deferred={deferred}")
    return _ship(0, kept=len(kept), created=created, updated=updated,
                 resolved=resolved_now, unchanged=skipped)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="TAC Vista -> MainManager alarm bot")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--parse-only", action="store_true")
    ap.add_argument("--no-bootstrap", action="store_true")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"Config load failed ({args.config}): {e}", file=sys.stderr)
        return 2

    log = setup_logging(cfg["paths"]["log_folder"])
    log.info("=" * 70)
    log.info(f"Alarm bot started (v{__version__}, dry_run={args.dry_run}, "
             f"parse_only={args.parse_only}, "
             f"no_bootstrap={args.no_bootstrap})")
    try:
        return run(cfg, args, log)
    except Exception:
        log.exception("Unhandled exception")
        return 1


if __name__ == "__main__":
    sys.exit(main())