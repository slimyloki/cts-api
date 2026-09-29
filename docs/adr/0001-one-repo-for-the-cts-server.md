# ADR-0001: One repository for everything that runs on the CTS server

- **Status:** Accepted
- **Date:** 2026-09-29
- **Decided by:** the owner ("cts-api repo is the main folder in which we will put everything that needs to
  be on cts-server")

## Context

Several applications run on the CTS server (Windows Server 2016, TAC Vista 5.1.9). They lived in separate
repositories, and some of those repositories mixed CTS-server code with code for the VPS:
- The alarm bot was in `slimyloki/cts-alarms`.
- The indoor-climate bot and the Vista OPC .NET API were in `slimyloki/vista-opc`.

This caused three problems:
- **Deploying was hard.** The owner cannot paste into the CTS server. Each deploy meant finding the right
  repository, the right folder and the right command by hand.
- **Old histories leak.** `slimyloki/cts-alarms` is public, and its history holds a (since rotated) MainManager
  password and operator names from committed logs.
- **Nothing kept the server's code apart.** With no single place for it, VPS code and server code drifted
  into the same repositories.

## Decision

1. `slimyloki/cts-api` is the one repository for everything that has to be on the CTS server. It has one
   top-level folder per application. On the server it lives in `C:\cts-api`.
2. It holds **only** what must be on the CTS server:
   - Code, config and the server-side service or task definitions.
   - Install and update scripts.
   - Each application's docs and tests.
   VPS and Vercel code stays in its own repositories (digibuild and others).
3. Applications are imported **without their Git history**. Each import is one commit that names the source
   repository and commit, so old credentials and personal data are not published again.
4. Every action on the CTS server ships as a script pair in this repo, and nothing is pasted:
   - A `.ps1` with a `.md` explaining it, plus a `.cmd` that asks for administrator rights and runs it.
   - One-off, server-wide actions go in `runbooks/`.
   - Install and update scripts go in the application's folder.
5. The repo is public, so the server can download it without a login. That makes rule 2 of the README
   binding: no secrets, no IP addresses, no personal data. `tests/test_repo_scope.py` enforces what a test
   can check.

## Consequences

- The deploy path is the same for every application: get `C:\cts-api` (clone, or ZIP), then run the
  application's `install.cmd`, and later `update.cmd`.
- The source repositories become read-only history once their CTS-server part has moved:
  - `slimyloki/cts-alarms` can be archived, or made private, without affecting any deploy.
  - `slimyloki/vista-opc` keeps whatever is not CTS-server code.
- Three agents on the development box keep this true: `cts-api-keeper` owns migrations and the "one home"
  rule, `cts-server-runbook-writer` turns every server action into a script pair, and `cts-api-scope-guard`
  audits scope and secrets before each push.
- The running applications' own folders on the server, such as the alarm bot's `C:\priorityalarmsapi`, are
  unchanged by this ADR. Moving them is a separate runbook.
