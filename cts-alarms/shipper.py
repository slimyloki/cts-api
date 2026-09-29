"""
CTS-side shipper: posts one IngestBatch per bot run to the digibuild
`cts-alarms` worker on the VPS (ADR-0016, ADR-0020).

Lives next to main.py on the CTS server. Standard library + requests only.

    POST {base_url}/internal/cts-alarms/v1/ingest
    X-Timestamp: <unix seconds>   X-Nonce: <uuid4>
    X-Signature: hex(HMAC-SHA256(secret, f"{timestamp}.{nonce}.{raw_body}"))

This is digibuild's machine-hop contract (its ADR-0022): the signature covers
the exact UTF-8 bytes sent, the server rejects a timestamp more than 300 s
from its own clock and any nonce it has seen in the last 600 s. A fresh
timestamp and nonce are made for every request, resends included, so the CTS
server's clock must stay within 300 s of real time.

Enabled by an OPTIONAL "vps" section in config.json:

    "vps": {
        "base_url":            "https://api.digibuild.dk",
        "outbox_file":         "outbox.sqlite",
        "timeout_seconds":     20,
        "max_resend_per_run":  20,
        "time_budget_seconds": 120,
        "enabled":             true
    }

The shared secret is NOT in config.json. It is read from the environment
variable CTS_ALARMS_INGEST_SECRET, else from the secrets file main.py uses
(paths.secrets_file; a relative path is next to config.json):

    {"mainmanager": {...}, "vps": {"ingest_secret": "<same value as the VPS env>"}}

No section, or "enabled": false -> every function here is a no-op and the bot
behaves exactly as before. The secret is never logged. Batches that could not
be delivered are queued in a local sqlite outbox and resent oldest-first on
later runs; the server is idempotent (dedup by run_id / (vista_id, ts, event))
so re-sending is always safe.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import ntpath
import os
import platform
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

SCHEMA_VERSION = 1
SOURCE = "cts-alarm-bot"
INGEST_PATH = "/internal/cts-alarms/v1/ingest"
HEALTHZ_PATH = "/api/cts-alarms/healthz"      # anonymous reachability check
SECRET_ENV = "CTS_ALARMS_INGEST_SECRET"
DEFAULT_SECRETS_PATH = "secrets.json"          # relative: next to config.json
USER_AGENT = "cts-alarm-bot-shipper/2"
MAX_ERRORS_PER_RUN = 100      # bound the run.errors list in the payload
MAX_RESPONSE_TEXT = 300       # truncate server text in log / outbox

ALARM_FIELDS = ("vista_id", "alarm_object", "directory", "alarm_text",
                "priority", "user", "ack_flag", "state1", "state2",
                "date1_epoch", "date2_epoch", "count", "status_label")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class ShipperConfig:
    __slots__ = ("base_url", "secrets_file", "outbox_file", "timeout_seconds",
                 "max_resend_per_run", "time_budget_seconds", "names_file")

    def __init__(self, *, base_url: str, secrets_file: str, outbox_file: str,
                 timeout_seconds: float, max_resend_per_run: int,
                 time_budget_seconds: float, names_file: str = ""):
        self.names_file          = names_file
        self.base_url            = base_url
        self.secrets_file        = secrets_file
        self.outbox_file         = outbox_file
        self.timeout_seconds     = timeout_seconds
        self.max_resend_per_run  = max_resend_per_run
        self.time_budget_seconds = time_budget_seconds

    @property
    def ingest_url(self) -> str:
        return f"{self.base_url}{INGEST_PATH}"

    @property
    def healthz_url(self) -> str:
        return f"{self.base_url}{HEALTHZ_PATH}"


def load_shipper_config(cfg: dict) -> Optional[ShipperConfig]:
    """Return the shipper config, or None when the "vps" section is absent
    or disabled. Raises ValueError for a present-but-broken section."""
    vps = cfg.get("vps")
    if not isinstance(vps, dict) or not vps.get("enabled", True):
        return None
    base_url = str(vps.get("base_url", "")).strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("vps.base_url must start with http:// or https://")
    if vps.get("api_key_file"):
        raise ValueError("vps.api_key_file is obsolete (bearer key replaced by "
                         "HMAC) — remove it and put vps.ingest_secret in the "
                         "secrets file")
    paths = cfg.get("paths") or {}
    working = paths.get("working_folder") or "."
    outbox_file = _resolve(cfg, str(vps.get("outbox_file") or
                      os.path.join(working, "outbox.sqlite")))
    return ShipperConfig(
        base_url            = base_url,
        secrets_file        = _resolve(cfg, str(paths.get("secrets_file") or DEFAULT_SECRETS_PATH)),
        outbox_file         = outbox_file,
        timeout_seconds     = float(vps.get("timeout_seconds", 20)),
        max_resend_per_run  = max(0, int(vps.get("max_resend_per_run", 20))),
        time_budget_seconds = float(vps.get("time_budget_seconds", 120)),
        names_file          = _resolve(cfg, str(paths.get("names_file") or "names.json")),
    )


def _resolve(cfg: dict, p: str) -> str:
    """Relative config paths are relative to config.json's folder (as in main.py)."""
    if not p or os.path.isabs(p) or ntpath.isabs(p):
        return p
    return os.path.join(cfg.get("_config_dir") or ".", p)


def read_ingest_secret(shcfg: ShipperConfig) -> str:
    """The HMAC secret: env CTS_ALARMS_INGEST_SECRET, else vps.ingest_secret
    in the secrets file. Raises ValueError when neither has it. Never log it."""
    env = (os.environ.get(SECRET_ENV) or "").strip()
    if env:
        return env
    path = shcfg.secrets_file
    if not os.path.exists(path):
        raise ValueError(f"no {SECRET_ENV} and no secrets file at {path}")
    with open(path, "r", encoding="utf-8-sig") as f:       # tolerate a Notepad BOM
        secret = str(((json.load(f) or {}).get("vps") or {})
                     .get("ingest_secret") or "").strip()
    if not secret:
        raise ValueError(f"vps.ingest_secret missing in {path}")
    return secret


def signed_headers(secret: str, body: bytes, *, now: Optional[float] = None,
                   nonce: Optional[str] = None) -> dict:
    """digibuild's X-Signature / X-Timestamp / X-Nonce for one request.
    The canonical string is f"{timestamp}.{nonce}." followed by the raw body
    bytes; the signature is lower-case hex HMAC-SHA256 keyed with the UTF-8
    secret — byte-for-byte what digibuild's lib/hmac.js signs and verifies."""
    timestamp = str(int(time.time() if now is None else now))
    nonce = nonce or str(uuid.uuid4())
    msg = f"{timestamp}.{nonce}.".encode("utf-8") + body
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return {"X-Signature": sig, "X-Timestamp": timestamp, "X-Nonce": nonce}


# ---------------------------------------------------------------------------
# Batch building (IngestBatch schema_version 1, validated by the digibuild
# cts-alarms worker; tests/fixtures/ingest-batch-v1.example.json is the contract)
# ---------------------------------------------------------------------------

def iso_with_offset(value: Any) -> Optional[str]:
    """datetime (aware or naive-local) or ISO string -> ISO 8601 with local
    UTC offset, second precision. None/'' -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.astimezone()      # naive = server local time
    return value.isoformat(timespec="seconds")


def alarm_row(a: Any) -> dict:
    """main.Alarm (or anything with the same attributes) -> AlarmRowIn."""
    return {field: getattr(a, field) for field in ALARM_FIELDS}


def event_row(ev: dict) -> dict:
    """Event dict from main.run_csv_logging() -> AlarmEventIn."""
    row = {field: ev.get(field) for field in ALARM_FIELDS}
    row["ts"]    = iso_with_offset(ev["ts"])
    row["event"] = ev["event"]
    return row


def incident_row(vista_id: str, entry: dict) -> dict:
    """alarms_state.json entry -> IncidentIn."""
    return {
        "vista_id":              vista_id,
        "incident_id":           entry.get("incident_id"),
        "main_id":               entry.get("main_id"),
        "main_id_fallback_used": entry.get("main_id_fallback_used"),
        "status":                entry.get("status"),
        "first_seen":            iso_with_offset(entry.get("first_seen_iso")),
        "last_update":           iso_with_offset(entry.get("last_update_iso")),
        "resolved_at":           iso_with_offset(entry.get("resolved_iso")),
    }


def build_batch(run_meta: dict, events: list, snapshot_alarms: list,
                state: dict) -> dict:
    """Assemble the IngestBatch dict for one bot run.

    run_meta keys: run_id, started_at, finished_at, bot_version, parsed_rows,
    kept, csv_events, created, updated, resolved, unchanged, errors, exit_code
    (missing counters are sent as null).
    """
    errors = [str(e) for e in (run_meta.get("errors") or [])]
    if len(errors) > MAX_ERRORS_PER_RUN:
        dropped = len(errors) - MAX_ERRORS_PER_RUN
        errors = errors[:MAX_ERRORS_PER_RUN] + [f"... {dropped} more errors omitted"]

    run = {
        "run_id":      run_meta["run_id"],
        "started_at":  iso_with_offset(run_meta["started_at"]),
        "finished_at": iso_with_offset(run_meta.get("finished_at")),
        "bot_version": run_meta.get("bot_version"),
        "host":        platform.node() or None,
        "parsed_rows": run_meta.get("parsed_rows"),
        "kept":        run_meta.get("kept"),
        "csv_events":  run_meta.get("csv_events", len(events)),
        "created":     run_meta.get("created"),
        "updated":     run_meta.get("updated"),
        "resolved":    run_meta.get("resolved"),
        "unchanged":   run_meta.get("unchanged"),
        "errors":      errors,
        "exit_code":   run_meta.get("exit_code"),
    }
    snapshot = [alarm_row(a) for a in snapshot_alarms
                if not getattr(a, "is_system_event", False)]
    incidents = [incident_row(vid, entry)
                 for vid, entry in ((state or {}).get("alarms") or {}).items()]
    return {
        "schema_version": SCHEMA_VERSION,
        "source":         SOURCE,
        "run":            run,
        "events":         [event_row(ev) for ev in events],
        "snapshot":       snapshot,
        "incidents":      incidents,
    }


# ---------------------------------------------------------------------------
# Outbox (sqlite, next to the state files)
# ---------------------------------------------------------------------------

OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY,
    created_iso TEXT,
    run_id      TEXT UNIQUE,
    payload     TEXT,
    attempts    INTEGER DEFAULT 0,
    last_error  TEXT
);
CREATE TABLE IF NOT EXISTS dead (
    id          INTEGER PRIMARY KEY,
    created_iso TEXT,
    run_id      TEXT,
    payload     TEXT,
    attempts    INTEGER,
    last_error  TEXT,
    dead_iso    TEXT
);
"""


def open_outbox(path: str) -> sqlite3.Connection:
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(OUTBOX_SCHEMA)
    return conn


def outbox_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    queued = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
    dead   = conn.execute("SELECT COUNT(*) FROM dead").fetchone()[0]
    return queued, dead


def _enqueue(conn: sqlite3.Connection, run_id: str, payload: str, error: str):
    now = iso_with_offset(datetime.now())
    conn.execute(
        "INSERT OR IGNORE INTO outbox(created_iso, run_id, payload, attempts, last_error) "
        "VALUES (?, ?, ?, 1, ?)", (now, run_id, payload, error))
    conn.commit()


def _bury(conn: sqlite3.Connection, run_id: str, payload: str,
          attempts: int, error: str, created_iso: Optional[str] = None,
          outbox_id: Optional[int] = None):
    now = iso_with_offset(datetime.now())
    conn.execute(
        "INSERT INTO dead(created_iso, run_id, payload, attempts, last_error, dead_iso) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (created_iso or now, run_id, payload, attempts, error, now))
    if outbox_id is not None:
        conn.execute("DELETE FROM outbox WHERE id = ?", (outbox_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

OK, RETRY, DEAD = "ok", "retry", "dead"


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _answer(r: Any) -> Optional[dict]:
    """The JSON of a 2xx answer, or None (never raises)."""
    try:
        data = r.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _post(shcfg: ShipperConfig, secret: str, payload: str,
          answers: Optional[list] = None) -> tuple[str, str]:
    """POST one JSON payload, freshly signed. Returns (OK|RETRY|DEAD, detail).
    The JSON of an accepted answer is appended to `answers` when given.
    RETRY = transient or auth problem (network, timeout, 5xx, 401/403/404/429);
    DEAD  = the server rejected the batch as malformed (other 4xx).
    404 is retried: it means the route is not deployed yet, not a bad batch."""
    body = payload.encode("utf-8")
    headers = {
        **signed_headers(secret, body),
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent":   USER_AGENT,
    }
    try:
        r = requests.post(shcfg.ingest_url, data=body,
                          headers=headers, timeout=shcfg.timeout_seconds)
    except requests.RequestException as e:
        return RETRY, _scrub(f"{type(e).__name__}: {e}", secret)

    code = r.status_code
    text = _scrub((r.text or "")[:MAX_RESPONSE_TEXT], secret)
    if 200 <= code < 300:
        if answers is not None:
            data = _answer(r)
            if data is not None:
                answers.append(data)
        return OK, f"HTTP {code}"
    if code == 401:
        return RETRY, (f"HTTP 401: {text} (check vps.ingest_secret and that "
                       f"this server's clock is within 300 s)")
    if code in (403, 404, 429) or code >= 500 or code < 400:
        return RETRY, f"HTTP {code}: {text}"
    return DEAD, f"HTTP {code}: {text}"


def probe(shcfg: ShipperConfig) -> str:
    """One anonymous GET of the worker's healthz, for --dry-run. Never raises."""
    try:
        r = requests.get(shcfg.healthz_url, headers={"User-Agent": USER_AGENT},
                         timeout=min(shcfg.timeout_seconds, 10))
        return f"HTTP {r.status_code}"
    except requests.RequestException as e:
        return f"unreachable ({type(e).__name__})"


# ---------------------------------------------------------------------------
# Alarm names from digibuild (ADR-0025)
# ---------------------------------------------------------------------------

NAME_KEYS = ("name", "building", "floor", "system")


def save_names(path: str, names: Any, log: logging.Logger) -> bool:
    """Write digibuild's answer `names` ({version, points: [{directory, ...}]})
    to names.json, which main.load_names() reads next run. Only when the
    version changed; atomic (tmp + replace). Never raises. True if written."""
    try:
        if not path or not isinstance(names, dict):
            return False
        version = str(names.get("version") or "")
        rows = names.get("points")
        if not version or not isinstance(rows, list):
            return False
        points: dict = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("directory"), str):
                continue
            entry = {k: row[k].strip() for k in NAME_KEYS
                     if isinstance(row.get(k), str) and row[k].strip()}
            if entry:
                points[row["directory"]] = entry
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    if (json.load(f) or {}).get("version") == version:
                        return False
            except Exception:
                pass                    # unreadable: overwrite it
        doc = {"version": version,
               "received": datetime.now().astimezone().isoformat(timespec="seconds"),
               "points": points}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
        log.info(f"VPS: alarm names updated -- {len(points)} named points "
                 f"(version {version}), used from the next run")
        return True
    except Exception as e:
        log.warning(f"VPS: could not save alarm names to {path}: {e}")
        return False


# ---------------------------------------------------------------------------
# Shipping
# ---------------------------------------------------------------------------

def ship(batch: dict, shcfg: ShipperConfig, log: logging.Logger) -> bool:
    """Resend queued batches (oldest first), then post this run's batch.
    Returns True if this run's batch was accepted now, False if it was queued
    or dead-lettered. Never raises on network problems. A missing secret
    queues the batch without a request (nothing is lost while it is being set
    up); only an unusable outbox raises, for the caller to catch.

    Bounded: at most max_resend_per_run + 1 requests, timeout_seconds each,
    and no new request once time_budget_seconds has elapsed.
    """
    run_id  = batch["run"]["run_id"]
    payload = json.dumps(batch, ensure_ascii=False)
    t0 = time.monotonic()

    def over_budget() -> bool:
        return time.monotonic() - t0 > shcfg.time_budget_seconds

    conn = open_outbox(shcfg.outbox_file)
    answers: list = []
    try:
        try:
            secret = read_ingest_secret(shcfg)
        except (OSError, ValueError) as e:
            _enqueue(conn, run_id, payload, f"not attempted: {e}")
            queued, _ = outbox_counts(conn)
            log.warning(f"VPS: no ingest secret ({e}) — run {run_id} queued, "
                        f"{queued} batches waiting (install.cmd -IngestSecret)")
            return False

        # ---- 1. Resend queued batches, oldest first ---------------------
        server_down = False
        if shcfg.max_resend_per_run > 0:
            rows = conn.execute(
                "SELECT id, run_id, payload, attempts, created_iso FROM outbox "
                "ORDER BY id LIMIT ?", (shcfg.max_resend_per_run,)).fetchall()
            for oid, orun_id, opayload, attempts, created_iso in rows:
                if over_budget():
                    log.warning("VPS: time budget exhausted — resend stopped")
                    server_down = True
                    break
                status, detail = _post(shcfg, secret, opayload, answers)
                if status == OK:
                    conn.execute("DELETE FROM outbox WHERE id = ?", (oid,))
                    conn.commit()
                    log.info(f"VPS: resent queued run {orun_id} ({detail})")
                elif status == DEAD:
                    log.error(f"VPS: queued run {orun_id} rejected as malformed "
                              f"— moved to dead table ({detail})")
                    _bury(conn, orun_id, opayload, attempts + 1, detail,
                          created_iso, outbox_id=oid)
                else:
                    conn.execute(
                        "UPDATE outbox SET attempts = attempts + 1, last_error = ? "
                        "WHERE id = ?", (detail, oid))
                    conn.commit()
                    log.warning(f"VPS: resend of queued run {orun_id} failed "
                                f"(attempt {attempts + 1}): {detail} — stopping")
                    server_down = True
                    break

        # ---- 2. Post this run --------------------------------------------
        accepted = False
        if server_down or over_budget():
            _enqueue(conn, run_id, payload, "not attempted: server unreachable")
            log.warning(f"VPS: run {run_id} queued without attempt "
                        f"(server unreachable this run)")
        else:
            status, detail = _post(shcfg, secret, payload, answers)
            if status == OK:
                accepted = True
                log.info(f"VPS: shipped run {run_id} — {len(batch['events'])} events, "
                         f"{len(batch.get('snapshot') or [])} snapshot rows, "
                         f"{len(batch.get('incidents') or [])} incidents ({detail})")
            elif status == DEAD:
                log.error(f"VPS: run {run_id} rejected as malformed — "
                          f"moved to dead table ({detail})")
                _bury(conn, run_id, payload, 1, detail)
            else:
                _enqueue(conn, run_id, payload, detail)
                log.warning(f"VPS: ship of run {run_id} failed: {detail} — queued")

        queued, dead = outbox_counts(conn)
        if queued or dead:
            log.info(f"VPS: outbox has {queued} queued, {dead} dead batches "
                     f"({shcfg.outbox_file})")
        named = [a["names"] for a in answers if "names" in a]
        if named:
            save_names(shcfg.names_file, named[-1], log)
        return accepted
    finally:
        conn.close()


def ship_run(cfg: dict, run_meta: dict, events: list, alarms: list,
             state: dict, log: logging.Logger) -> None:
    """Entry point called by main.run(). No-op without a "vps" section.
    run_meta["dry_run"] = True logs what would be shipped, whether the secret
    is configured and whether the worker answers (one anonymous GET of its
    healthz) — and sends nothing, touches no outbox."""
    try:
        shcfg = load_shipper_config(cfg)
    except ValueError as e:
        log.warning(f"VPS shipper disabled — bad \"vps\" config: {e}")
        return
    if shcfg is None:
        return

    batch = build_batch(run_meta, events, alarms, state)
    if run_meta.get("dry_run"):
        log.info(f"[DRY] Would ship {len(batch['events'])} events to "
                 f"{shcfg.base_url} (run {batch['run']['run_id']}, "
                 f"{len(batch['snapshot'])} snapshot rows, "
                 f"{len(batch['incidents'])} incidents)")
        try:
            read_ingest_secret(shcfg)
            log.info("[DRY] VPS ingest secret: present")
        except (OSError, ValueError) as e:
            log.warning(f"[DRY] VPS ingest secret: MISSING — {e}")
        log.info(f"[DRY] VPS reachability: GET {shcfg.healthz_url} -> {probe(shcfg)}")
        return
    ship(batch, shcfg, log)
