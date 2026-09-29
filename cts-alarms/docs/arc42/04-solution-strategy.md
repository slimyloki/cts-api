# 4. Solution Strategy

Part of the [arc42 documentation](../README.md).
Previous: [3. Context and Scope](03-context-and-scope.md) · Next: [5. Building Block View](05-building-block-view.md) · Decisions in full: [ADR index](../adr/README.md)

## Strategy in one paragraph

A **stateless-per-run, file-driven poller**: every 5 minutes a fresh Python process snapshots Vista's alarm file, parses it into a list of `Alarm` values, and reconciles that list against two small JSON state files keyed by Vista ID. All decisions are made by comparing a four-field **state signature** `(state1, state2, ack_flag, user)` between the previous and current run — never by interpreting alarm text. Two independent consumers hang off the parsed list: an **audit trail** (all alarms → per-directory CSV) and an **incident pipeline** (filtered alarms → MainManager). Side effects to MainManager are additive only (create, prepend text), through the v3 incident API. A third, optional consumer — the **shipper** — hands each run to the digibuild `cts-alarms` worker over HTTPS + HMAC, where the history, reports and web pages live; it can never block ticketing. The bot is one main file plus the optional shipper, one dependency, deployed by copying, scheduled by the OS, secrets outside the repo, and verifiable with a read-only `--dry-run`.

## Key decisions

| Concern | Decision | Why (short) | Trade-off / consequence | ADR |
|---------|----------|-------------|-------------------------|-----|
| Runtime & deployment | One Python script run by Windows Task Scheduler on the CTS server, every 5 min | `$this.alr` is only readable locally; no infrastructure to maintain; same pattern as the Indeklima bot | Polling latency ≤ 5 min; transitions between polls may be missed; state that must survive a run is on disk (state files, token cache, outbox) | [0002](../adr/0002-scheduled-python-script-on-cts-server.md) |
| Identity of an alarm | `vista_id` (`VISTA_SERVER#<hex>`) is the primary key in the state files and CSV rows and the key under which the incident id is stored (the incident itself carries no Vista ID) | Stable for the life of one alarm instance; unique by construction | A re-triggered alarm on the same point after removal is a *new* instance → new incident; reappearance of a RESOLVED id is ignored with a WARNING (`main.py:1192-1194`) | [0003](../adr/0003-vista-id-as-primary-key.md) |
| Change detection | Diff the tuple `(state1, state2, ack_flag, user)` run-over-run | Alarm text changes inconsistently on reset ("OK", "resat", or unchanged); the tuple is Vista's actual state model | Text changes alone are invisible; the `count` field is recorded but not diffed | [0004](../adr/0004-state-signature-diff.md) |
| Reading the source | Copy `$this.alr` to `alarm_snapshot.alr` (5 retries) and parse the copy | Vista rewrites the file in place; avoids partial reads and never holds the live file open | One file copy per run; on persistent lock the run aborts with exit 2 and state is untouched | [0005](../adr/0005-snapshot-then-parse.md) |
| First deployment | First run records all present alarms as tracked, creates no incidents | Prevents opening 60+ tickets for pre-existing alarms | Bootstrapped alarms get an incident only when they change ("THE FIX", `main.py:1214`); `--no-bootstrap` overrides | [0006](../adr/0006-bootstrap-on-first-run.md) |
| What becomes a ticket | Priority ≤ `max_priority_number` (config 2; 1 = most urgent) | Vista priority is the operator-maintained urgency signal; pri 9 is mostly `$EE_Mess` system messages (plus a few `nvoAlarmStatus_A0` controller status points) | Pri 3 alarms (e.g. "Lavt tryk" and most "Høj rumtemperatur" instances — the same text also occurs at pri 1/2) never become tickets — but are still audited to CSV | [0007](../adr/0007-priority-threshold-filter.md) |
| Suppressing known noise | Exact-match list of full Vista **directory** paths in `exceptions.csv` | Short `alarm_object` names collide across controllers; the directory pins one physical point | No wildcards; one entry today | [0008](../adr/0008-directory-based-exceptions.md) |
| Where a ticket lands | `objects.csv` maps `alarm_object` → `MainID`, else `default_main_id` with WARNING | Lets FM route incidents to the right asset without code changes | File is empty today → all incidents on MainID 14228; `main_id_fallback_used` recorded in state | [0009](../adr/0009-objects-csv-mainid-mapping-with-fallback.md) |
| Incident lifecycle | Create once (status enforced); on every transition GET `Description` → PUT `Remarks` with a prepended dated line → GET verify; never change `StatusID` afterwards | Humans own closure; the description becomes a readable timeline (newest on top) | Three API calls per line; the whole text is rewritten each time | [0010](../adr/0010-prepend-description-never-change-status.md), [0019](../adr/0019-mainmanager-v3-incident-api.md) |
| API failure handling | A 404 is classified with a probe: API down → no further calls this run, state untouched, retried next run; ticket missing → mark and stop sending; other failures at most 3 runs per transition | An unannounced API removal (2026-09-19) must cost one ERROR per run, not thousands of retries, and must not lose the backlog | A missing ticket is never re-created; the alarm is still tracked locally | [0019](../adr/0019-mainmanager-v3-incident-api.md) |
| Audit trail | Independent CSV logger for **all** alarms, one `;`-file per directory, event-typed rows | Complete history regardless of ticket threshold; easy to open in Excel; grouped by physical location | 431 files and growing; no cross-file querying; `RESOLVED` rows carry no user | [0011](../adr/0011-per-directory-csv-audit-log.md) |
| State persistence | Two JSON files, written atomically (`.tmp` + `os.replace`) after every change; resolved entries pruned (30 days / immediately for csv_state) | Crash-safe with zero dependencies; human-inspectable | Not queryable; whole file rewritten per change; two files can drift from each other | [0012](../adr/0012-json-state-files-atomic-writes.md) |
| Secrets | `secrets.json` (git-ignored, ACL-restricted) or environment variables; never `config.json` (v1.x did that — superseded) | The repo is public; one file per host holds both secrets | Plain text on the server behind an ACL; the v1 value in history was rotated | [0021](../adr/0021-secrets-in-secrets-json.md) (supersedes 0013) |
| History, reports, web | Ship every run to the digibuild sub-project `cts-alarms` (HTTPS POST + HMAC); SQLite there; pages on Vercel | Owner's standing rule (Vercel pages, VPS background, Clerk, DA/EN); the bot stays simple and keeps ticketing independent of the VPS | Two repos; a wire contract to keep in step (golden-vector test) | [0016](../adr/0016-cts-server-vs-vps-responsibility-split.md), [0020](../adr/0020-hmac-ingest-to-digibuild.md), [0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md) |
| Repo scope | This repo holds only what runs on the CTS server; runtime data stays there | Public repo; no personal data or operations data in git | Historical analysis needs the private archive or digibuild | [0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md) (supersedes 0014, 0018) |

## Approaches to the quality goals

| Quality goal ([1.2](01-introduction-and-goals.md#12-quality-goals)) | Strategy |
|---|---|
| Do no harm to the BMS | Read-only, snapshot-then-parse, least-privilege scheduled task, no listening ports. |
| No ticket spam | Bootstrap; one incident per Vista ID; signature diff (no re-create on text change); threshold + exceptions; `IgnoreNew` so overlapping runs cannot double-create. |
| Traceability | CSV audit for all alarms; per-run status table in the daily log; dated lines in every incident; `main_id_fallback_used` and `first_seen_iso` kept in state. |
| Robustness | Per-alarm `try/except`; state saved after each alarm; atomic writes; 404 classification and three-strikes limit; CSV logging and shipping wrapped as non-fatal; outbox for undelivered batches; scheduler restarts. |
| Humans keep control | Bot never acknowledges in Vista and never changes `StatusID` after creation. |
| Operability | `main.py` + optional `shipper.py`, `requests` only, JSON config, secrets file, read-only `--dry-run`, version in every run banner, unit tests. |
| Security | Secrets outside the repo; HMAC instead of a bearer key on the wire; guard test against committing credentials. |

## From proposal to decision (2026-09-29)

The owner's next phase was first recorded as *Proposed* ADRs 0015–0018 and three candidate topologies. All are
decided: storage is SQLite in the digibuild worker (0015), the split is option A (0016), the naming registry lives
in digibuild (0017), and the standalone web app (0018) was superseded by the digibuild sub-project (0022). See
[C4 §05](../c4/05-target-architecture-proposal.md) for the proposal and how it was realised.
