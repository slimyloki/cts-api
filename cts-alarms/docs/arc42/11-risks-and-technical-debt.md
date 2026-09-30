# 11. Risks and Technical Debt

Ranked register of known risks and debt, each with evidence. Ranking is by *impact × likelihood* as judged on 2026-09-28; the **Status** column and the dated notes in 11.2 record what `main.py` v2.0.0 … v2.1.0 and the 2026-09-29 decisions changed (the detail text below each note is the original 2026-09-28 analysis of v1.x, kept as the record; its line numbers are v1.x). The owner may re-rank. Mitigation ideas are proposals, not decisions — the concrete work items are in [docs/TODO.md](../TODO.md).

Scale: Impact **H** / **M** / **L** · Likelihood **certain** (already happening) / **high** / **medium** / **low**.

Related: [08 – Crosscutting Concepts](08-crosscutting-concepts.md) · [10 – Quality Requirements](10-quality-requirements.md) · [ADR index](09-architecture-decisions.md)

## 11.1 Register

| Rank | ID | Item | Impact | Likelihood | Type | Status 2026-09-29 |
|---|---|---|---|---|---|---|
| 1 | [R-18](#r-18-mainmanager-integration-not-working-since-2026-09-19) | MainManager integration not working since 2026-09-19 (all incident endpoints 404) | H | certain | Outage | **Fixed in code** (v2.0.0, v3 API, live-tested); open in production until v2.0.1 is deployed (T-103) |
| 2 | [R-22](#r-22-indeklima-bot-on-the-same-account-still-on-the-removed-api) | Indeklima bot (same account) still on the removed API | H | certain | Outage | **Open** (T-105) |
| 3 | [R-03](#r-03-secrets-committed-to-a-public-repository) | Secrets committed to a public repository | H | certain | Security | **Mitigated**: password rotated 2026-09-29; secrets in `secrets.json` (ADR-0021); history purge is T-102 |
| 4 | [R-01](#r-01-404-retry-forever-loop-and-token-request-every-run) | 404 retry-forever loop; token requested every run | M | certain | Bug | **Fixed in code** (v2.0.0: 404 classification, 3-strike limit, token cache) |
| 5 | [R-04](#r-04-personal-data-in-committed-files) | Personal data of operators in committed files | H | certain | Privacy | **Partly**: runtime data leaves the repo tip; the code moved to cts-api without history (ADR-0023); the old repo's history is still public (T-102, T-007) |
| 6 | [R-02](#r-02-objectscsv-is-empty--every-incident-lands-on-the-fallback-mainid) | `objects.csv` empty → all incidents on fallback MainID | M | certain | Data quality | Open (T-020) |
| 7 | [R-13](#r-13-no-alerting-when-the-bot-itself-fails) | No alerting when the bot itself fails | H | medium | Operations | **Designed**: digibuild `late` flag + watchdog; the shipper is live since 2026-09-30 (T-115), the alert path is T-065 |
| 8 | [R-05](#r-05-no-log-rotation-and-no-csvstate-retention-strategy) | No log rotation; no CSV/state retention strategy | M | certain | Operations | Open (T-023, T-006) |
| 9 | [R-20](#r-20-unbounded-outbox-growth) | Unbounded shipper outbox growth | M | medium | Operations | **New**, open (T-106) |
| 10 | [R-12](#r-12-single-point-of-failure-on-the-cts-server) | Single point of failure on the CTS server | H | low | Availability | Unchanged by design (the reader must stay there); history now also off-site |
| 11 | [R-19](#r-19-cts-clock-skew-breaks-the-hmac-ingest) | CTS clock skew breaks the HMAC ingest | M | low | Operations | **New**, open (T-107) |
| 12 | [R-06](#r-06-single-file-script-with-no-automated-tests) | Single-file script with no automated tests | M | high | Maintainability | **Partly**: 59 tests, no CI, `run()` still long (T-024) |
| 13 | [R-10](#r-10-deployed-code-changed-over-time-without-version-history) | Deployed code changed over time without version history | M | certain | Maintainability | **Mitigated**: `__version__` in the banner; deploys are committed versions |
| 14 | [R-07](#r-07-build-reference-drift) | Build reference drift | M | certain | Documentation | Mitigated by these docs; mark the old file historical (T-069) |
| 15 | [R-21](#r-21-cached-token-hides-a-wrong-password-in-a-dry-run) | Cached token hides a wrong password in a dry run | L | medium | Operations | **New**, open (T-108) |
| 16 | [R-14](#r-14-token-per-run-rather-than-cached) | Token per run rather than cached | L | certain | Efficiency | **Fixed in code** (`mm_token.json`) |
| 17 | [R-15](#r-15-vista-id-reuse-and-format-assumptions) | Vista ID reuse / format assumptions | M | low | Correctness | Open; digibuild model uses a surrogate key |
| 18 | [R-11](#r-11-32-bit-python-runtime) | 32-bit Python runtime | L | low | Platform | Open (T-090); no longer blocks anything |
| 19 | [R-08](#r-08-legacy-status-values-in-alarms_statejson) | Legacy `"ACKNOWLEDGED"` status values in `alarms_state.json` | L | certain | Data debt | Harmless; mapped by the importer |
| 20 | [R-09](#r-09-stale-7-days-comment-in-run_csv_logging) | Stale "7 days" comment in `run_csv_logging()` | L | certain | Code debt | **Fixed** (comment corrected) |
| 21 | [R-16](#r-16-mass-resolution-on-an-empty-alarm-file) | Mass-resolution on an empty alarm file | H | low | Correctness | Open |
| 22 | [R-17](#r-17---dry-run-and---parse-only-are-not-side-effect-free) | `--dry-run` / `--parse-only` are not side-effect free | L | medium | Testability | **Fixed in code** (v2.0.1 read-only) |

---

## 11.2 Detail

### R-01: 404 retry-forever loop and token request every run

> **Status 2026-09-29 — fixed in code (v2.0.0).** A 404 raises `MMNotFound`; `_on_not_found()` (`main.py:1128`) probes the v3 list: ticket missing → `incident_missing`, never sent again; API down → one ERROR, no further calls, state untouched, `deferred=N`. Non-404 failures give up after 3 runs (`_give_up()`, `:1147`). The token is cached in `mm_token.json`. Tests: `tests/test_main.py::RunTests`. Effective on the server with T-103. [ADR-0019](../adr/0019-mainmanager-v3-incident-api.md)


**What.** When `GetIncident` returns HTTP 404 for any reason (observed: for every incident id since 2026-09-19, see R-18; a single deleted/merged incident would behave the same way), `prepend_description_line()` (`main.py:613-617`) raises, the caller logs an ERROR and `continue`s **without** updating `last_state_sig` / `status` (`main.py:918-923` for transitions, `main.py:969-975` for resolutions). The same diff is therefore recomputed and retried on every run, forever, until the entry is pruned — and resolution entries are never pruned because they never reach `RESOLVED`.

**Evidence.**
- 6,559 `FAILED … 404` lines across `logs/`; 531 on 2026-09-28 alone (`grep -c FAILED logs/2026-09-28.log`).
- `Resolution update FAILED for VISTA_SERVER#6A5D0D16 … GetIncident?IncidentID=34570` logged 2,150 times; `UpdateIncident FAILED … IncidentID=36674` 947 times, `36696` 835, `36676` 494, `36701` 421, `36688` 389, `36683` 254, `36804` 225, `36703` 208, `36702` 178, `36693` 113; 30 distinct ids in total, the remaining 18 retried 1–40 times each.
- Side effect: because `MMClient` is created lazily and the token is fetched on first API call (`main.py:544-558`, `853-857`), every run that hits a stuck entry requests a token. `API AUTH: requesting token` appears 8,258 times over 45,610 runs; 40,053 runs ended with `created=0, updated=0, resolved=0`, and the run at 2026-09-28 17:55 shows an auth call followed only by three 404s.

**Impact (M).** Log noise hides real errors; 1–4 pointless `GetIncident` calls per run (11 distinct stuck incidents on 2026-09-28, each retried in the runs where its diff is recomputed) plus one token request every 5 minutes against a third-party system; affected alarms never reach `RESOLVED` in state, so `T2`-style questions get a wrong answer; run time grows with each stuck entry (each costs up to a 60 s timeout if the API is slow).

**Mitigation ideas.** Treat `404` (and other 4xx except 401/429) as permanent: mark the entry `incident_id = null` + `status = "INCIDENT_GONE"` (or similar) and stop retrying; log once. Optionally cap retries per entry with a counter in the state file. Add a test for this branch. **But** first distinguish an endpoint-wide failure (R-18: every id 404, `CreateIncident` 404 too) from a single missing incident — marking 30 entries as gone while the API is merely broken would detach them for good. See docs/TODO.md T-018 (blocked on T-085).

### R-02: `objects.csv` is empty → every incident lands on the fallback MainID

**What.** `resolve_main_id()` (`main.py:735-741`) falls back to `mainmanager.default_main_id` when `alarm_object` is not in `objects.csv`. The file contains only comments.

**Evidence.** `Loaded 0 object mappings from C:\priorityalarmsapi\objects.csv` on every run since the current code version first ran on 2026-04-17 (the two earlier runs that day, under the old code, logged `Loaded 3 object mappings` — mappings existed once and were removed; 2026-04-18 and 04-19 have no log at all). In `alarms_state.json`, 119 of 122 entries have `main_id = 14228` and 3 have `9756` (the earlier default); every one has `main_id_fallback_used = true`. A `WARNING No objects.csv mapping for '…' — using fallback MainID` precedes every create.

**Impact (M).** All incidents are filed against one MainManager object regardless of building, so FM staff cannot route or report by location inside MainManager; the WARNING is emitted once per created incident (457 times since April, typically 0–3 per day) and carries no information while `objects.csv` is empty.

**Mitigation ideas.** Either populate `objects.csv` (or the future friendly-name registry, [ADR 0017](../adr/0017-friendly-alarm-naming-registry.md), with a MainID column) from a MainManager export, or decide explicitly that one MainID is intended and downgrade the warning. Undecided. See docs/TODO.md.

### R-03: Secrets committed to a public repository

> **Status 2026-09-29 — mitigated.** Credentials are read from `secrets.json`/environment since v2.0.0 ([ADR-0021](../adr/0021-secrets-in-secrets-json.md)); the committed `config.json` has none and `tests/test_repo_hygiene.py` guards it; the owner **rotated the password on 2026-09-29**, so the value in the history is dead. The old value is in 8 commits (7 on GitHub). Since 2026-09-29 the code lives in `slimyloki/cts-api`, started without that history ([ADR-0023](../adr/0023-moved-into-cts-api.md)), so the old commits are isolated in `slimyloki/cts-alarms`. Remaining: archive or make private that old repo (T-102), the Indeklima bot still needs the new value (R-22).


**What.** `config.json` holds the MainManager service-account credentials (`mainmanager.username` / `mainmanager.password`) in plain text ([ADR 0013](../adr/0013-secrets-in-config-json.md)). The file is in the initial commit of `github.com/slimyloki/cts-alarms`.

**Evidence.** `config.json` in the repo root; `git log` shows one commit containing it; `MMClient.__init__` reads both keys (`main.py:538-539`) and sends them in a password grant (`main.py:549-553`).

**Impact (H).** Anyone with the repo can create, read and rewrite incidents in Ramboll FM MainManager as the bot. Rewriting history does not undo exposure once cloned or indexed.

**Mitigation ideas.** (1) Rotate the credentials in MainManager. (2) Move them out of `config.json` into an environment variable, Windows Credential Manager/DPAPI, or a file outside the repo referenced by path; keep a `config.example.json`. (3) Add `config.json` to `.gitignore`. (4) Consider making the repo private until the runtime data is removed ([ADR 0014](../adr/0014-commit-runtime-data-for-migration.md)). The owner has stated rotation will happen later — see docs/TODO.md.

### R-04: Personal data in committed files

> **Status 2026-09-29 — partly mitigated.** From 2026-09-29 the repo tip carries no runtime data (`.gitignore`, [ADR-0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)); the data is archived privately on the VPS. The public history of the old repo `slimyloki/cts-alarms` still contains it (T-102). Since 2026-09-29 the code lives in `slimyloki/cts-api`, which was started **without** that history ([ADR-0023](../adr/0023-moved-into-cts-api.md)). The old repo is now history only and can be archived or made private with no deploy impact. Every shipped batch carries operator names to digibuild, whose pages are proposed to show login tokens only (T-007).


**What.** Field 10 of `$this.alr` is the acknowledging operator's display name, e.g. the owner's own `"GPST (Georgi ISS)"`; other operators' labels have the same shape. The bot copies it into every store.

**Evidence.** `alarm_snapshot.alr` (field 10); `alarms_state.json -> last_state_sig[3]` (`main.py:729`); `csv_state.json -> sig[3]`; column `user` in every `csv/*.csv` (`main.py:347`); every status-table line in `logs/` (`main.py:703`); MainManager description lines "ACKNOWLEDGED by <name>" (`main.py:646`). All except the last are committed to the public repo.

**Impact (H).** Names of identifiable employees, with timestamps of their actions, are public; likely a GDPR issue for the employer/operator organisations.

**Mitigation ideas.** Remove runtime data from the public repo (or make the repo private) once the migration review is done; in the new system store operator *initials/IDs* rather than full display names, or keep a separate lookup table with access control; define a retention period. See docs/TODO.md.

### R-05: No log rotation and no CSV/state retention strategy

**What.** `setup_logging()` opens `logs/<date>.log` in append mode and never deletes anything (`main.py:44-64`). `csv/` files are append-only forever (`main.py:381-390`). `alarms_state.json` prunes only `RESOLVED` entries (`main.py:284-302`); entries that never resolve (bootstrapped, legacy statuses, stuck 404s) stay indefinitely.

**Evidence.** 163 daily logs covering 165 calendar days (2026-04-18 and 04-19 missing), 356 MiB; recent days 3.8–4.1 MB each (`ls -l logs/2026-09-2*.log`), driven by the ~70-line status table logged 288 times a day. `csv/`: 431 files, 20,584 data rows, ~5.3 MB of data (6.3 MB on disk). `csv_state.json`: 180 live entries, resolved ones deleted immediately (`main.py:521-523`).

**Impact (M).** ~1.4 GB/year of logs on the CTS server disk with no owner; the audit trail is unbounded and unqueryable; the repo itself is bloated by data.

**Mitigation ideas.** `RotatingFileHandler`/`TimedRotatingFileHandler` or a scheduled purge; log the status table at DEBUG or only on change; move history to SQL ([ADR 0015](../adr/0015-sql-storage-for-alarm-history.md)) with an explicit retention policy. See docs/TODO.md.

### R-06: Single-file script with no automated tests

> **Status 2026-09-29 — partly mitigated.** `tests/` has 59 passing tests (MainManager client with a fake v3 tenant, 404 paths, secrets, read-only dry run, shipper incl. an HMAC golden vector, repo hygiene). Still: no CI, `run()` is ~360 lines, pure text/parse functions uncovered (T-024).


**What.** `main.py` is 1,028 lines; `run()` (`main.py:776-994`) interleaves file I/O, filtering, API calls and state mutation. There is no `tests/` directory, no CI, no linting.

**Evidence.** Repo tree; `Alarm_bot_build_reference.md` § 12 "CLI flags" presents manual `--dry-run` as the way to verify behaviour (and wrongly claims it does not update the state file — see R-17).

**Impact (M).** Every change (e.g. fixing R-01, changing status labels, migrating to SQL) is verified only by running against production. Regressions like the label rename that produced R-08 pass unnoticed.

**Mitigation ideas.** Split parsing / classification / diff / API into modules; unit-test `parse_alarm_file`, `classify_alarm_status`, `classify_csv_event`, `describe_transitions`, and the 404 branch with the committed `alarm_snapshot.alr` as fixture; add a CI workflow. See docs/TODO.md.

### R-07: Build reference drift

**What.** `Alarm_bot_build_reference.md` was written for an earlier version and no longer matches `main.py` / `config.json` / the scheduler task. Where they disagree, **the code wins**.

| Topic | Build reference | Current reality | Evidence |
|---|---|---|---|
| Cadence | every 3 minutes (ref line 47, 382); `main.py` docstring line 7 also says "default every 3 min" | every 5 minutes | `TACVista_Alarm_Bot.xml` `<Interval>PT5M</Interval>`; 288 runs/day in logs |
| Exceptions file | `exceptions.txt`, config key `exceptions_file` (ref 33, 129, 157) | `exceptions.csv`, key `exceptions_csv` | `config.json`; `main.py:235`, `783`. The file's own header comment still says `exceptions.txt` |
| Working folder | `C:\ctsapi\alarms\` (ref 156-160, 360, 413) | `C:\priorityalarmsapi\` | `config.json`, `TACVista_Alarm_Bot.xml`, `main.py:36` |
| Default MainID | `9756` (ref 126, 166, 351, 418) | `14228` | `config.json`; 119/122 state entries on 14228 |
| Priority threshold | `3` (ref 152, 181, 445); task description and `config.json` `_comment` also say "1-3" | `2` | `config.json` `max_priority_number: 2`; log line `alarms after priority<=2` |
| Status labels | `OPEN`, `CLEARED`, `ACKNOWLEDGED`, `RESOLVED`, `BOOTSTRAPPED` (ref 220-221) | `ACTIVE`, `ACTIVE + ACKNOWLEDGED`, `NORMAL`, `NORMAL + ACKNOWLEDGED`, `RESOLVED` | `main.py:101-105` |
| Description wording | "condition cleared (…)", "acknowledged by …" (ref 249-264) | "alarm returned to NORMAL (…)", "ACKNOWLEDGED by …", "alarm ACTIVE again (…)" | `main.py:636-649` |
| CSV audit | not mentioned ("absence of CSV uploads", ref 284) | per-directory CSV audit log exists | `main.py:446-528` |

**Impact (M).** A new maintainer following the build reference would deploy to the wrong folder with the wrong threshold. **Mitigation.** This arc42/ADR set supersedes the build reference for behaviour; keep the old file for history or mark it superseded at the top. See docs/TODO.md.

### R-08: Legacy status values in `alarms_state.json`

**What.** Two entries carry `status = "ACKNOWLEDGED"`, a label that no current code path emits (`main.py:101-117` produces only the five labels in § 8.1). Example: `VISTA_SERVER#684AB94B` (`incident_id: null`, `main_id: 9756`, `first_seen_iso: 2026-04-17`). They date from the earlier code version referenced by the build reference (R-07).

**Evidence.** Status count from `alarms_state.json`: NORMAL 63, RESOLVED 50, ACTIVE + ACKNOWLEDGED 5, **ACKNOWLEDGED 2**, ACTIVE 2.

**Impact (L).** Harmless at runtime (flow is driven by the signature, not the label — `main.py:887`), and the stale label is never shown in the status table (which prints the live label computed from the snapshot row, `main.py:701-702`); it would surface only in the `bootstrapped alarm changed: ACKNOWLEDGED -> …` log line (`main.py:898-901`) and in any export of `alarms_state.json`. `prune_state()` will never remove them, and any SQL migration must map the legacy value. **Mitigation.** One-off state migration; add a label validation on load. See docs/TODO.md.

### R-09: Stale "7 days" comment in `run_csv_logging()`

> **Status 2026-09-28 — fixed.** The comment now reads "Drop resolved entries right away" (`main.py:600`).


**What.** `main.py:519-520` says "Prune resolved entries older than 7 days from csv_state" but the loop at `main.py:521-523` deletes **every** entry flagged `resolved` in the same run, with no age check.

**Impact (L).** Misleading to maintainers; combined with the absence of tests it invites a "fix" that changes behaviour. **Mitigation.** Correct the comment (or implement the 7-day window if that was intended — undecided). See docs/TODO.md.

### R-10: Deployed code changed over time without version history

> **Status 2026-09-29 — mitigated.** `__version__` (`main.py:40`) is logged in every banner and shipped as `run.bot_version`; each deployed `main.py` is a committed version (v2.0.1 = commit `4becae7`).


**What.** The repo has a single commit ("Initial commit"), but the logs prove at least two earlier versions ran in production.

**Evidence.** `logs/2026-04-17.log`: `alarm bot run started`, `Loaded 1 exception directory entries from …\exceptions.txt`, `74 alarms after priority<=3 + exception filter`, `Loaded 3 object mappings`. Current wording: `Alarm bot started`, `Loaded 1 exception entries from …\exceptions.csv`, `priority<=2`, `Loaded 0 object mappings`. A burst of incident creation on 2026-04-20 12:43 used fallback MainID 9756; later entries use 14228. Task `StartBoundary` is 2026-04-21, i.e. the task itself was re-registered. There are no log files for 2026-04-18 and 04-19, so the bot was not running on those two days.

**Impact (M).** It is impossible to correlate a given log line, state entry or MainManager incident with the code that produced it, or to explain why `objects.csv` went from 3 mappings to 0.

**Mitigation ideas.** Commit every deployed change; log a version string (git hash or `__version__`) in the run banner; consider a deploy script that copies from a tagged commit. See docs/TODO.md T-087.

### R-11: 32-bit Python runtime

**What.** The task runs `C:\Users\GPST\AppData\Local\Programs\Python\Python313-32\python.exe` (`TACVista_Alarm_Bot.xml`).

**Impact (L).** No functional problem for a script of this size; but 32-bit builds are the less common download, some wheels (drivers for SQL, e.g. `pyodbc`, or `psycopg`) may lack 32-bit binaries, and the user-profile install path ties the task to the `GPST` account (task fails if that profile is removed). **Mitigation.** Move to a 64-bit system-wide install when the SQL migration adds native dependencies. See docs/TODO.md T-090.

### R-12: Single point of failure on the CTS server

> **Status 2026-09-29.** Decided to keep the bot on the CTS server (ADR-0016 option A); the history is now also kept off-site by digibuild once shipping is live, and digibuild's watchdog notices when runs stop (R-13).


**What.** Everything — polling, state, CSV history, logs, MainManager integration — runs on one Windows Server 2016 host in the `GPST` user profile, reading Vista's live database folder. There is no second instance, no backup of `alarms_state.json` beyond the ad-hoc repo commit, and the host is a production building-management server that the bot's owner may not fully control.

**Evidence.** `config.json` paths all under `C:\priorityalarmsapi`; task `Principal UserId = GPST`, `LogonType = Password` (a password change breaks the task).

**Impact (H) / Likelihood (low).** If the server, the user profile, or the Vista installation path changes, alarm→incident creation stops silently (see R-13). Because Vista only writes `$this.alr` locally, the *reader* must stay on the CTS server; only the rest can move — the subject of [ADR 0016](../adr/0016-cts-server-vs-vps-responsibility-split.md) (undecided).

**Mitigation ideas.** Minimal agent on CTS (snapshot + ship rows), everything else on the VPS; back up state; run the task as a service account; heartbeat monitoring. See docs/TODO.md.

### R-13: No alerting when the bot itself fails

> **Status 2026-09-30 — designed; the shipper is live (T-115).** Every run is shipped with counters, `errors[]` and `exit_code`; the digibuild worker is designed to report `late` after 15 minutes without a run and digibuild's Vercel watchdog to alert on it — not yet verified end to end (T-065). An API outage now shows as one ERROR per run plus `deferred=N`, not thousands of lines.


**What.** Exit codes 1/2 (`main.py:794`, `1013`, `1024`) are not consumed by anything; Task Scheduler is configured with `RestartOnFailure` (3× at 1-minute intervals), but whether it treats the bot's exit codes 1/2 as a failure has not been verified on the CTS server; errors inside a run that still exits 0 (e.g. every API call failing) are visible only by reading the day's log on the server.

**Evidence.** No monitoring configuration in the repo; `RestartOnFailure Count=3 Interval=PT1M` is the only reaction mechanism; the 404 loop (R-01) has logged 200–1,300 errors a day since it started on 2026-09-19 (first `FAILED … 404` line in `logs/2026-09-19.log`) without anything reacting to it.

**Impact (H) / Likelihood (medium).** A stopped bot means priority-1/2 building alarms (fire dampers, sprinkler faults, "Brand fra ABA") produce no MainManager incident and nobody notices until an operator compares systems manually.

**Mitigation ideas.** Heartbeat: write `meta.last_run_iso` (already exists, `main.py:989`) to a location the VPS can see and alert when stale > 15 min; summarise ERROR counts per run and notify on threshold; watchdog on the task's last-run result. See docs/TODO.md.

### R-14: Token per run rather than cached

> **Status 2026-09-28 — fixed in code (v2.0.0).** Token cached in `mm_token.json` with its expiry (300 s margin); `MMClient` is lazy, so idle runs make no call. Side effect: R-21.


**What.** `MMClient._token` lives only in process memory (`main.py:542`); every run that makes any API call does a password-grant `POST /restapi/token` first (`main.py:544-558`).

**Evidence.** 8,258 `API AUTH: requesting token` lines; combined with R-01 this is now every run.

**Impact (L).** One extra HTTPS round-trip per run (~2 s observed) and unnecessary exposure of the credentials on the wire every 5 minutes; MainManager may rate-limit. **Mitigation.** Persist the token with its expiry (the `/restapi/token` response's `expires_in`, not currently read) in a file with restricted permissions; re-auth on 401. Fixing R-01 removes most of the calls anyway. See docs/TODO.md.

### R-15: Vista ID reuse and format assumptions

**What.** The bot assumes `vista_id` is unique for the lifetime of the state file ([ADR 0003](../adr/0003-vista-id-as-primary-key.md)). The value looks like a 32-bit hex counter close to (but not equal to) the epoch of first occurrence (`#67969A95` vs `date1 67969A77`), and consecutive alarms get consecutive IDs (`#6AA31632`, `#6AA31633`, `#6AA31634`). Its generation rule is not documented anywhere in the repo.

**Evidence.** `alarm_snapshot.alr` field 1; `main.py:879-881` guards against a reappearing resolved ID by ignoring it (0 occurrences logged in 165 days); RESOLVED entries are pruned after 30 days, after which a reused ID would be treated as new.

**Impact (M) / Likelihood (low).** If Vista ever reuses an ID (database rebuild, counter wrap, server migration) the bot would either ignore a real alarm (within 30 days) or silently merge two alarms' histories in `csv/`. **Mitigation.** Record the assumption; in the SQL model give alarm instances a surrogate key and store `(vista_id, date1_epoch)` together; alert on the "reappeared after RESOLVED" warning. See docs/TODO.md T-088.

### R-16: Mass-resolution on an empty alarm file

**What.** If `$this.alr` is copied successfully but is empty or truncated (Vista restart, database maintenance), `parse_alarm_file()` returns 0 alarms and the "gone from file" branch (`main.py:935-985`) marks every tracked alarm RESOLVED and posts resolution lines to every open incident. The CSV path does the same (`main.py:489-517`).

**Evidence.** No row-count sanity check exists between `main.py:796` and `main.py:935`. Not observed: the smallest row count ever parsed is 32 (`grep "Parsed N total alarm rows"` across `logs/`), never 0.

**Impact (H) / Likelihood (low).** One bad poll would corrupt state (entries become terminal RESOLVED and are later pruned) and spam MainManager. **Mitigation.** Abort the run (exit 2) if parsed rows drop below a configurable fraction of the previous count, or if the file is empty while state has open alarms. See docs/TODO.md.

### R-17: `--dry-run` and `--parse-only` are not side-effect free

> **Status 2026-09-28 — fixed in code (v2.0.1).** `read_only` (`main.py:996`) turns off every state save, the CSV write and the outbox; a dry run makes one token request as a credential check and ends with `[DRY] nothing written`. Found before the first deploy: v2.0.0's dry run on the live folder would have advanced the state past the backlog stuck since 2026-09-19.


**What.** `--parse-only` still runs the CSV audit and writes `csv/` and `csv_state.json` (`main.py:804-808` executes before the `main.py:826` return). `--dry-run` skips API calls but still inserts new alarms into `alarms_state.json` with `incident_id = null` (`main.py:868-873`) and sets `status = RESOLVED` on missing ones (`main.py:965`), then saves (`main.py:990`). Because the dry-run branch `continue`s before `resolved_iso` is set (`main.py:966` vs `983`), such entries are never pruned by `prune_state()` (`main.py:292-294` skips entries without `resolved_iso`) and stay in `alarms_state.json` indefinitely.

**Impact (L) / Likelihood (medium).** Running either flag against the production folder to "check something" mutates production state; a dry-run-inserted entry later gets an incident only when its signature changes ("THE FIX" path), so a real new alarm can be silently delayed. **Mitigation.** Add a true read-only mode (skip all `save_*` calls) or require `--config` pointing at a scratch folder; document the caveat in the CLI help. See docs/TODO.md T-089.

### R-18: MainManager integration not working since 2026-09-19

> **Status 2026-09-29 — cause found, fixed in code.** EG removed the `/restapi/Incident/*` routes on Sat 2026-09-19 between 00:40 and 02:55 CEST, without notice; the tickets still existed (digibuild's mirror) and the tenant's other clients use `/api/v3/incidents`. `main.py` v2.0.0 moved to the v3 API and was live-tested on 2026-09-28 (update of 34570, test ticket 37378). Production stays down until v2.0.1 is deployed (T-103); the backlog was never consumed and goes out on the first real run. [ADR-0019](../adr/0019-mainmanager-v3-incident-api.md)


**What.** Since 2026-09-19 no call to the MainManager incident endpoints has succeeded. The last successful write is `2026-09-19 00:40:06 [INFO] UPDATED incident 36693`; from 02:55:13 the same day every `GetIncident` returns HTTP 404 (30 distinct incident ids, including 36693 itself), and from 2026-09-23 14:40 `POST /restapi/Incident/CreateIncident` returns 404 on every attempt (8 of 8, six runs up to 2026-09-28 15:55). The token endpoint still answers 200. Priority-1/2 alarms have therefore produced no MainManager incident, update or resolution for ten days, and nobody has been told (R-13).

**Evidence.** `grep -h 'CREATED incident\|UPDATED incident\|RESOLVED incident' logs/2026-09-2*.log` → 0 lines after 09-19 00:40; `Run complete: created=0, updated=0, resolved=0` on every run 09-20 … 09-28; 6,551 `GetIncident` 404s and 8 `CreateIncident FAILED … 404` lines; no successful create since 2026-09-14 13:35 (incident 37058). See [mainmanager-api.md](../reference/mainmanager-api.md#history-the-v1-api-until-2026-09-19).

**Impact (H) / Likelihood (certain).** The bot's only external output is dead. **Cause unknown** from this repo: the all-at-once pattern (every id, the create endpoint too, token unaffected) points to an API/route change, tenant change or lost service-account permission on the MainManager side rather than to deletion of 30 individual tickets. **Mitigation.** Confirm with Ramboll FM / MainManager what changed and restore access; add alerting on "N runs with API errors and 0 successes" (R-13); only then decide the 404 handling in R-01. See docs/TODO.md T-085.

### R-19: CTS clock skew breaks the HMAC ingest

**What.** The digibuild receiver rejects any ingest request whose `X-Timestamp` differs from its own clock by more than 300 s ([ADR-0020](../adr/0020-hmac-ingest-to-digibuild.md)). The shipper signs with the CTS server's clock.

**Evidence.** `shipper.signed_headers()`; digibuild ADR-0022 (a clock-skew window of 300 s). The CTS server's time source has not been checked. **2026-09-30:** seen from digibuild, the clock ran about 72 s behind for about 40 minutes (from about 22:25 to 23:06–23:10 server time), then came back, apparently a resync; inside the 300 s window, so nothing was refused, but every run seemed to arrive about 75 s late (T-107, T-117).

**Impact (M) / Likelihood (low).** A drifting clock turns every POST into `HTTP 401 … (check vps.ingest_secret and that this server's clock is within 300 s)`; batches queue in the outbox (nothing lost, but R-20 grows and digibuild shows the bot as late). Ticketing is unaffected. **Mitigation.** Verify `w32tm /query /status` and the NTP source (T-107).

### R-20: Unbounded outbox growth

**What.** Every undelivered batch is kept in `outbox.sqlite` until accepted; each carries the full incident list and snapshot, not only the run's events.

**Evidence.** `shipper._enqueue()`; no size cap in `shipper.py`. A missing secret, a wrong secret, a clock skew or a receiver outage all queue.

**Impact (M) / Likelihood (medium).** At 288 runs/day, weeks of queueing grow the file by tens of MB per day on the BMS host, and each run's resend is capped at `max_resend_per_run` (20), so a long backlog drains slowly (20 extra batches per 5 minutes). **Mitigation.** Cap by rows/bytes with a WARNING, or collapse queued snapshot/incident payloads and keep only the events (T-106).

### R-21: Cached token hides a wrong password in a dry run

**What.** `--dry-run`'s credential check calls `check_auth()` → `_get_token()`, which returns a token from `mm_token.json` if it is still valid; the password is then not sent at all.

**Evidence.** `MMClient._get_token()` (`main.py:665-680`): `API AUTH: using cached token` path.

**Impact (L) / Likelihood (medium).** After a password rotation, a dry run within two hours of the last successful token says `[DRY] MainManager credentials OK` even if `secrets.json` holds a wrong password; the failure appears only when the cache expires. **Mitigation.** Delete `mm_token.json` before a verifying dry run (documented in the deploy steps); let `check_auth()` bypass the cache (T-108).

### R-22: Indeklima bot on the same account still on the removed API

**What.** The sibling Indeklima bot (room-temperature logs → MainManager incidents, type 281, runs on the CTS server) uses the same MainManager account and — per its (stale) repository copy — the same removed `/restapi/Incident/*` routes. It has created no incident since Fri 2026-09-18. The account's password was rotated on 2026-09-29, so it would fail authentication even with a working client.

**Evidence.** digibuild's MainManager mirror: 7,265 `CTS Log - Rum …` incidents by the same user, the last on 2026-09-18; the bot runs Monday–Friday office hours only, so the silence started with the weekend of the API removal.

**Impact (H) / Likelihood (certain).** Indoor-climate deviations produce no tickets. **Mitigation.** Port its client to `/api/v3/incidents` like ADR-0019 (write `Remarks`, read `Description`), move its password to a secrets file with the new value; needs the live folder from the CTS server (T-105).

## 11.3 Debt summary by initiative (status 2026-09-29)

| Initiative | Risks it addresses | State |
|---|---|---|
| Restore the MainManager integration | R-18, R-01, R-14, R-17 | coded (v2.0.0/v2.0.1); deploy T-103 |
| Rotate/move secrets | R-03 | done (rotated 2026-09-29, `secrets.json`); history: T-102 |
| Port the Indeklima bot | R-22 | T-105 (needs the live folder) |
| Ship to digibuild ([ADR 0016](../adr/0016-cts-server-vs-vps-responsibility-split.md), [ADR 0020](../adr/0020-hmac-ingest-to-digibuild.md)) | R-13, R-12 (history off-site), R-05 (query-ability) — adds R-19, R-20 | live since 2026-09-30 (T-115) |
| SQL storage / web app / friendly names (digibuild, [ADR 0022](../adr/0022-cts-side-only-repo-web-app-in-digibuild.md)) | R-02 (via the catalogue), R-04 (pseudonymised pages), R-15 | being built in digibuild |
| Code hygiene | R-06, R-07, R-16, R-21, R-20 | T-024, T-069, T-027 (snapshot-size guard), T-106, T-108 |
