# ADR-0020: Ship each run to digibuild `cts-alarms` over HTTPS POST + HMAC

- **Status:** Accepted
- **Date:** 2026-09-28 (transport chosen by the owner: "A — HTTPS POST + HMAC") · 2026-09-29 (implemented in `shipper.py`)
- **Deciders:** Georgi (owner)
- **Refines:** [ADR-0016](0016-cts-server-vs-vps-responsibility-split.md) (option A: the bot stays on the CTS server and ships to the VPS)

## Context

ADR-0016 decided *that* the bot ships every run to the VPS; the first implementation (`server/`, 2026-09-29)
used a bearer API key read from `vps_api_key.txt` and posted to `/api/v1/ingest` of a standalone FastAPI app.
The same day the VPS side was placed in digibuild as sub-project `cts-alarms`
([ADR-0022](0022-cts-side-only-repo-web-app-in-digibuild.md)), whose machine-to-machine routes all use one
contract (digibuild ADR-0022, `apps/worker/lib/hmac.js`). The owner compared the options on security and chose
HTTPS POST + HMAC to `api.digibuild.dk`. A bearer key is a replayable credential sent on every request; an HMAC
signature proves possession of the secret without sending it, binds the body, and expires.

## Decision

`shipper.py` posts one IngestBatch per run to

`POST https://api.digibuild.dk/internal/cts-alarms/v1/ingest`

signed with digibuild's machine-hop contract:

| Item | Value |
|---|---|
| Headers | `X-Timestamp` (unix seconds), `X-Nonce` (uuid4), `X-Signature` |
| Signature | lower-case hex HMAC-SHA256, key = the secret as UTF-8, message = `f"{timestamp}.{nonce}."` + **raw body bytes** (`signed_headers()`, `shipper.py:143`) |
| Receiver rules | reject `|now − timestamp| > 300 s`; each nonce single-use for 600 s; timing-safe compare; `401` before the body is parsed |
| Per request | fresh timestamp + nonce for every POST, resends included |
| Secret | env `CTS_ALARMS_INGEST_SECRET`, else `vps.ingest_secret` in the secrets file (`paths.secrets_file`) — [ADR-0021](0021-secrets-in-secrets-json.md); on the VPS the same value in the worker's env file |
| Obsolete | `vps.api_key_file` / `vps_api_key.txt`: presence of the key is rejected as a bad config |

Outbox semantics (`ship()`, `_post()`):

- **missing secret** → the batch is queued without a request (`not attempted: …`) — nothing is lost while it is
  being set up;
- **401** → retried, logged with the hint "check vps.ingest_secret and that this server's clock is within 300 s";
- **404** → retried: the route is not deployed (yet), the batch is fine;
- 403, 429, 5xx, network errors → retried; other 4xx → dead-lettered (malformed batch);
- bounded per run: `max_resend_per_run + 1` requests, `time_budget_seconds`.

`--dry-run` builds the batch, logs `[DRY] Would ship …`, whether the secret is present, and one anonymous
`GET https://api.digibuild.dk/api/cts-alarms/healthz` (`probe()`) — which answers "can the CTS server reach
api.digibuild.dk" before anything is sent. It sends nothing and touches no outbox.

A golden-vector test (`tests/test_shipper.py::test_signature_matches_digibuild_golden_vector`) pins a signature
produced by digibuild's own `sign()` with non-ASCII body and secret, so the two sides cannot drift apart silently.

## Consequences

### Positive
- No reusable credential on the wire; a captured request cannot be replayed after 600 s or re-used with another
  body.
- One contract across digibuild's inbound routes; the receiver reuses digibuild's verifier semantics.
- The receiving route can be restricted further by source IP at the reverse proxy (the building's address).

### Negative
- **Clock dependency:** the CTS server must stay within 300 s of real time ([arc42 §11 R-19](../arc42/11-risks-and-technical-debt.md#r-19-cts-clock-skew-breaks-the-hmac-ingest), [TODO T-107](../TODO.md)).
- A second secret on the BMS host (next to the MainManager pair, same file, same ACL).
- A 404 from a missing route and a 404 from a typo in `base_url` look the same; the dry-run probe line tells them
  apart.
- The outbox is unbounded ([R-20](../arc42/11-risks-and-technical-debt.md#r-20-unbounded-outbox-growth), [TODO T-106](../TODO.md)).

## Alternatives considered

| Option | Why not |
|---|---|
| Bearer API key (`server/`'s first design) | Replayable, sent on every request; not digibuild's contract. |
| Pull from the VPS (VPS reads the CTS server) | Needs inbound access into the building network. |
| File sync (SFTP/rsync of `csv/`) | Needs an SSH key on the BMS host and inbound SSH on the VPS; no per-run semantics. |
| mTLS | Certificate lifecycle on a Windows Server 2016 BMS host with user-profile Python; more moving parts than the risk warrants. |

## Evidence

- `shipper.py` (`INGEST_PATH` `:59`, `HEALTHZ_PATH` `:60`, `SECRET_ENV` `:61`, `read_ingest_secret()` `:126`,
  `signed_headers()` `:143`, `_post()` `:315`, `probe()` `:344`, `ship()` `:358`, `ship_run()` `:447`)
- `tests/test_shipper.py`, `tests/test_main_events.py`
- [reference/shipper.md](../reference/shipper.md); decision D-E in the [design record](../design/2026-09-28-vps-branch-design.md)
