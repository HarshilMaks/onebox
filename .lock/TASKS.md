# OneBox — Task registry

**Status:** living · 2026-11-06 · the single place where every planned unit of work is tracked.
[ROADMAP.md](ROADMAP.md) says *what and why* by phase. This registry says *exactly which tasks*: their status, dependencies, where they land, how they are proven done, and how each is split into commits.

## How to use it

- **Status:** `todo` · `doing` · `done` · `blocked`. Update it in the commit that finishes the task.
- **Size:** S up to half a day · M up to a day and a half · L up to three days. Anything bigger is split before it starts.
- **Lands:** `main` (minor) or a branch (major), per [ENGINEERING_PRACTICES.md](ENGINEERING_PRACTICES.md) §3. Branch names are plain, never `claude/`.
- **Done when:** a test or a command, never an assertion. `I-n` is an invariant in [TEST_STRATEGY.md](TEST_STRATEGY.md) §4; `T-n` is a threat in [THREAT_MODEL.md](THREAT_MODEL.md) §5.
- **Commits:** the intended commits in order. Each is one logical step that builds and passes the suite on its own; tests travel with the code they test. Rules and a worked example: [AGENT_GUIDE.md](AGENT_GUIDE.md) §6.
- **One task at a time.** Do not start a task whose dependencies are not `done`.

## Phase 0 — Foundations (milestone M0)

| ID | Task | Needs | Lands · size | Status |
|---|---|---|---|---|
| 0.1 | Baseline: confirm `main` is clean and in sync; run the full suite | — | main · S | todo |
| 0.2 | Keep the worker loop alive when retention cleanup fails | 0.1 | main · S | todo |
| 0.3 | Cap triage retries | 0.1 | main · S | todo |
| 0.4 | Promote stuck `processing` actions automatically | 0.1 | `fix/stuck-action-sweep` · M | todo |
| 0.5 | Field-level validation errors and stable error codes | 0.1 | `refactor/error-envelope` · M | todo |
| 0.6 | Remove ledger bypasses and the broad Gmail scope | 0.1 | `fix/remove-ledger-bypasses` · L | todo |
| 0.7 | Cleanups: README claims, subject filter, JSON null, dead code | 0.1 | main · S each | todo |
| 0.8 | Drop the legacy plaintext credential column | 0.1 | `fix/drop-plaintext-credentials` · M | todo |
| 0.9 | CI: dependency audit and coverage; decide on `python-jose` | 0.1 | `chore/ci-audit-and-coverage` · M | todo |

- **0.1** Run the suite against a disposable PostgreSQL and Redis and confirm the PostgreSQL-backed tests actually ran (they skip silently when it is unreachable). *Done when:* all tests pass with those tests executed. *Commits:* none; note a changed test count in [HANDOFF.md](HANDOFF.md).
- **0.2** *Done when:* a test where cleanup raises shows the heartbeat, job claim and processing continue. *Commits:* 1) `fix: keep the notification worker running when retention cleanup fails`.
- **0.3** *Done when:* a message that always fails ends as `dead_letter` after N attempts, with N in settings. *Commits:* 1) `fix: dead-letter triage work after repeated failures`.
- **0.4** *Done when:* a crash-point test promotes the stuck row and writes an audit event (I-14). *Commits:* 1) `feat: promote expired-lease actions in the retention job` (service and tests) 2) `feat: log and count promoted actions so an alert can fire` 3) `docs: update the failure-mode table`.
- **0.5** *Done when:* an invalid `status` returns the field name and a stable code; the OpenAPI check passes; the client error type matches. *Commits:* 1) `refactor: return field-level details in validation errors` 2) `refactor: add stable error codes to the error envelope` 3) `chore: regenerate OpenAPI and fix the client error type`.
- **0.6** *Done when:* no external write exists outside the ledger (I-1) and `gmail.modify` is confirmed sufficient without the broad scope (T-15). *Commits:* 1) `fix: remove permanent delete and the broad Gmail scope` 2) `fix: route or remove REST send, trash and restore` 3) `refactor: share error handling across mail routes` 4) `test: assert that no write path bypasses the ledger`.
- **0.7** Four small items, each its own commit on `main`: `docs: align README claims with what the code guarantees` · `fix: stop skipping mail by subject keywords` · `fix: store missing credentials as SQL NULL` (use a branch if it needs a migration) · `refactor: remove unused helpers`. *Done when:* a mail titled "Congrats on your promotion" is triaged, and an `IS NULL` check finds credential-less rows.
- **0.8** *Done when:* the backfill command is run on a database copy, no plaintext rows remain, and a reversible migration drops the column (T-5). *Commits:* 1) `feat: add a check that no plaintext credentials remain` 2) `chore: drop the legacy token_json column`.
- **0.9** *Done when:* CI reports coverage and runs a dependency scan, and the `python-jose` decision is recorded in an ADR note. *Commits:* 1) `chore: report coverage in CI` 2) `chore: scan dependencies for known vulnerabilities` 3) `fix: replace or bump python-jose` if the scan requires it.

## Phase 1 — Identity · branch `feat/identity` (M1, part 1)

| ID | Task | Needs | Size | Status |
|---|---|---|---|---|
| 1.1 | OpenID Connect sign-in with PKCE, state, nonce | 0.x | L | todo |
| 1.2 | Opaque sessions, CSRF and Origin checks | 1.1 | M | todo |
| 1.3 | Per-user settings (time zone, working hours) | 1.1 | S | todo |
| 1.4 | Demo login | 1.2 | S | todo |

- **1.1** *Done when:* a wrong audience, tampered nonce or expired ID token is rejected (T-9). *Commits:* 1) `feat: add users keyed by OIDC subject` 2) `feat: add the OIDC authorization-code flow with PKCE, state and nonce` 3) `feat: validate ID tokens` 4) `docs: update architecture for sign-in`.
- **1.2** *Done when:* an expired or forged session returns 401 and a cross-origin POST is rejected (T-4, T-8). *Commits:* 1) `feat: add opaque server-side sessions` 2) `feat: enforce CSRF and Origin checks on state-changing routes` 3) `refactor: replace the external-JWT dependency with sessions`.
- **1.3** *Done when:* settings are isolated per user (cross-user test). *Commits:* 1) `feat: add per-user settings`.
- **1.4** *Done when:* a demo user cannot reach a real connection. *Commits:* 1) `feat: add demo login`.

## Phase 2 — Connector contract · branch `feat/connector-contract` (M1, part 2)

| ID | Task | Needs | Size | Status |
|---|---|---|---|---|
| 2.1 | Spikes: Gmail marker survival; scope tiering; Tasks scope tier | 1.2 | M | todo |
| 2.2 | Connector contract and `GoogleConnector` | 2.1 | L | todo |
| 2.3 | Connections table, Off/Read/Propose, "+" consent flow | 2.2 | L | todo |
| 2.4 | Disconnect: revoke at the provider, delete tokens | 2.3 | M | todo |
| 2.5 | Automation per connection | 2.3 | L | todo |

- **2.1** Needs a throwaway Google test account (never committed). *Done when:* results are written into [PRD.md](PRD.md) §9 and ADR-0005. *Commits:* 1) `docs: record spike results for Gmail markers and scope tiering`.
- **2.2** *Done when:* the shared contract tests pass for `GoogleConnector` and the old suite is green. *Commits:* 1) `feat: define the connector contract and shared contract tests` 2) `refactor: move Google handlers behind GoogleConnector`.
- **2.3** *Done when:* Off blocks all token use and Read blocks proposals (policy tests). *Commits:* 1) `feat: add the connections table` 2) `feat: enforce Off, Read and Propose per connection` 3) `feat: add the "+" consent flow with incremental scopes`.
- **2.4** *Done when:* the provider revoke is called, the row is gone and later calls fail closed (I-13). *Commits:* 1) `feat: revoke at the provider and delete tokens on disconnect` 2) `test: calls fail closed after disconnect`.
- **2.5** *Done when:* two mailboxes are processed independently. *Commits:* 1) `refactor: key mailbox state by connection` 2) `feat: run watch, cursor and resync per connection` 3) `test: two mailboxes are processed independently`.

## Phase 3 — Email to proposals · branch `feat/email-to-proposals` (M2, part 1)

| ID | Task | Needs | Size | Status |
|---|---|---|---|---|
| 3.1 | Typed triage output from the zero-tool reader | 2.5 | M | todo |
| 3.2 | Deterministic validator | 3.1 | L | todo |
| 3.3 | Proposals with source link and reason | 3.2 | M | todo |
| 3.4 | Honest status in assistant replies | 3.3 | S | todo |
| 3.5 | Fault-injection tests for lost responses | 3.3 | M | todo |

- **3.1** *Done when:* schema violations are rejected. *Commits:* 1) `feat: define the triage output schema and parser` 2) `feat: ask the zero-tool reader for typed output`.
- **3.2** *Done when:* I-8 and I-9 pass. *Commits:* 1) `feat: resolve reply recipients from the verified sender only` 2) `feat: judge calendar conflicts on the user's local day` 3) `feat: cap suggestions per user per day` 4) `feat: dedupe suggestions by message and type`.
- **3.3** *Done when:* re-triaging the same mail creates no duplicates. *Commits:* 1) `feat: store source reference and reason on proposals` (migration) 2) `feat: create proposals from validated suggestions` 3) `feat: expose source and reason in the actions API and client types`.
- **3.4** *Done when:* I-10 passes. *Commits:* 1) `fix: never report success before the provider confirms`.
- **3.5** *Done when:* zero duplicate effects per action type under injected lost responses (I-4). *Commits:* 1) `test: add a fault-injecting HTTP wrapper for provider calls` 2) `test: assert zero duplicate effects per action type`.

## Phase 4 — Web client · branch `feat/web-client` (M2, part 2)

Design lives in [FRONTEND.md](FRONTEND.md). Build the suggestions queue first: it is the core screen and it proves the API shape early.

| ID | Task | Needs | Size | Status |
|---|---|---|---|---|
| 4.0 | Walking skeleton: scaffold, tokens, `ActionCard`, suggestions queue | 3.3 | L | todo |
| 4.1 | API additions: activity timeline, edit-before-approve | 3.3 | M | todo |
| 4.2 | Digest, activity, connect and settings screens | 4.0, 4.1 | L | todo |
| 4.3 | Needs-checking and conflict states; accessibility pass | 4.2 | M | todo |

- **4.0** *Done when:* a first-time viewer can review and approve a proposal against the real API. *Commits:* 1) `chore: scaffold the web client` 2) `feat: add design tokens and base components` 3) `feat: add the suggestions queue with ActionCard`.
- **4.1** *Done when:* contract tests pass and OpenAPI is regenerated. *Commits:* 1) `feat: add the activity timeline endpoint` 2) `feat: add edit-before-approve as a new proposal` 3) `chore: regenerate OpenAPI and client types`.
- **4.2** One commit per screen. *Done when:* each screen has loading, empty and error states.
- **4.3** *Done when:* the accessibility checks in [FRONTEND.md](FRONTEND.md) §7 pass and the untrusted-text fixtures render as plain text.

## Phase 5 — Outlook connector · branch `feat/outlook-connector` (M3, optional)

| ID | Task | Needs | Size | Status |
|---|---|---|---|---|
| 5.1 | Graph OAuth for a personal account; read mail | 2.3 | L | todo |
| 5.2 | Send, reply, create event; reconcile through Sent Items | 5.1 | L | todo |
| 5.3 | Polling for new items | 5.1 | M | todo |
| 5.4 | Cross-provider demo: Outlook mail to Google event | 5.2, 4.2 | M | todo |

Commits follow the same shape: one per capability (read, send and reply, event, polling), each with its contract tests. *Done when* (5.4): a scripted run passes (PRD metric M5).

## Phase 6 — Demo and packaging · branch `feat/demo-and-packaging` (M4)

| ID | Task | Needs | Size | Status |
|---|---|---|---|---|
| 6.1 | Demo mode: seeded mailbox, canned model output, fake providers | 4.2 | L | todo |
| 6.2 | Single-VM deploy with TLS and health checks | 6.1 | M | todo |
| 6.3 | Alerts, spend cap, per-call telemetry | 6.2 | M | todo |
| 6.4 | README, diagram, video, finalize ADRs | 6.3 | M | todo |

*Done when* (6.4): a stranger follows the README to a running demo.

## Cross-cutting (any time, on `main`)

| ID | Task | Status |
|---|---|---|
| X.1 | Re-fetch the sources behind [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) §9 | todo |
| X.2 | Render the Mermaid diagrams on GitHub and fix syntax errors | todo |
| X.3 | Run the commands in [ENGINEERING_PRACTICES.md](ENGINEERING_PRACTICES.md) once and correct them | todo |

## Optional — MCP connector · branch `feat/mcp-connector`

One app with an official MCP server; writes wrapped as proposals. Only after phases 0–6.
