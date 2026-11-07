# OneBox — Task registry

**Status:** living · 2026-11-06 · the single place where every planned unit of work is tracked.
[ROADMAP.md](ROADMAP.md) says *what and why* by phase and gives the totals and cut lines. This registry says *exactly which tasks*: their status, dependencies, where they land, how each is proven done, and how it is split into commits.

## How to use it

- **Status:** `todo` · `doing` · `done` · `blocked`. Update it in the commit that finishes the task.
- **Size and estimate:** S 0.25–0.5 day · M 0.75–1.5 days · L 1.5–3 days · XL more than 3 days (counted as 5–8 in the totals), so it is **split into smaller tasks before it starts**. The phase totals in [ROADMAP.md](ROADMAP.md) are these ranges added up [ESTIMATE].
- **Lands:** `main` (minor) or a branch (major), per [ENGINEERING_PRACTICES.md](ENGINEERING_PRACTICES.md) §3. Branch names are plain, never `claude/`.
- **Done when:** a test or a command, never an assertion. `I-n` is an invariant in [TEST_STRATEGY.md](TEST_STRATEGY.md) §4; `T-n` is a threat in [THREAT_MODEL.md](THREAT_MODEL.md) §5.
- **Commits:** the intended commits in order. Each is one logical step that builds and passes the suite on its own; tests travel with the code they test (no red commits on `main`). Rules and a worked example: [AGENT_GUIDE.md](AGENT_GUIDE.md) §6.
- **One task at a time.** Do not start a task whose dependencies are not `done`. Within a phase, work in the order listed under "Order".

## Phase 0 — Foundations (milestone MS0)

Order: 0.1, 0.10, 0.2, 0.3, 0.12, 0.11, 0.4, 0.13, 0.5, 0.6, 0.7, 0.8, 0.9. Reason: 0.10 gives branches a CI run before the first branch task (0.4); 0.12 shares the test fakes before more tests are written.

| ID | Task | Needs | Lands · size | Status |
|---|---|---|---|---|
| 0.1 | Baseline: confirm `main` is clean and in sync; run the full suite | — | main · S | todo |
| 0.2 | Keep the worker loop alive and fed when retention cleanup fails | 0.1 | main · S | todo |
| 0.3 | Cap triage retries | 0.1 | main · S | todo |
| 0.4 | Promote stuck `processing` actions; accept a confirmed late outcome | 0.1, 0.10 | `fix/stuck-action-sweep` · L | todo |
| 0.5 | Field-level validation errors and stable error codes | 0.1 | `refactor/error-envelope` · M | todo |
| 0.6 | Remove ledger bypasses and the broad Gmail scope | 0.1 | `fix/remove-ledger-bypasses` · L | todo |
| 0.7 | Cleanups: README claims, subject filter, JSON null, dead code | 0.1 | main · M | todo |
| 0.8 | Drop the legacy plaintext credential column | 0.1 | `fix/drop-plaintext-credentials` · M | todo |
| 0.9 | CI: dependency audit and coverage | 0.10 | `chore/ci-audit-and-coverage` · M | todo |
| 0.10 | CI on feature branches | 0.1 | main · S | todo |
| 0.11 | Tests for the Pub/Sub push endpoint | 0.1 | main · S | todo |
| 0.12 | Share the test fakes | 0.1 | main · M | todo |
| 0.13 | Reconcile quiet period; look in spam and trash | 0.4 | `fix/reconcile-quiet-period` · M | todo |

- **0.1** Run the suite against a disposable PostgreSQL and confirm the PostgreSQL-backed tests actually ran (they skip silently when it is unreachable: 202 pass and 44 skip without a database, 246 pass with one). Redis is not needed. *Done when:* all tests pass with those tests executed. *Commits:* none; note a changed count in [HANDOFF.md](HANDOFF.md).
- **0.2** Today cleanup runs before the heartbeat, watch renewal and job claim, so a persistent failure starves all three while the loop survives and `/readyz` goes stale. *Done when:* a test where cleanup always raises shows the heartbeat, watch renewal and job claim still run (I-15). *Commits:* 1) `fix: keep the notification worker running when retention cleanup fails`.
- **0.3** Use the existing `attempt_count`. *Done when:* a message that always fails ends as `dead_letter` after N attempts, with N in settings. *Commits:* 1) `fix: dead-letter triage work after repeated failures`.
- **0.4** *Done when:* a crash-point test promotes a row stuck past its lease and writes an audit event (I-14), and a confirmed provider result is accepted after the lease expired when the attempt token still matches (I-20). *Commits:* 1) `feat: promote expired-lease actions in the retention job` (service and tests) 2) `fix: accept a confirmed outcome that arrives after the lease expired` 3) `feat: log and count promoted actions so an alert can fire` 4) `docs: update the failure-mode table`.
- **0.5** *Done when:* an invalid `status` returns the field name and a stable code; the OpenAPI check passes; the client error type matches. *Commits:* 1) `refactor: return field-level details in validation errors` 2) `refactor: add stable error codes to the error envelope` 3) `chore: regenerate OpenAPI and fix the client error type`.
- **0.6** Covers every REST mail write: permanent delete, send, trash and restore. *Done when:* no external write exists outside the ledger (I-1) and `gmail.modify` is confirmed sufficient without the broad scope (T-15). *Commits:* 1) `fix: remove permanent delete and the broad Gmail scope` 2) `fix: route or remove REST send, trash and restore` 3) `refactor: share error handling across mail routes` 4) `test: assert that no write path bypasses the ledger`.
- **0.7** Four small items, each its own commit on `main`: `docs: align README claims with what the code guarantees` · `fix: stop skipping mail by subject keywords` · `fix: store missing credentials as SQL NULL` (use a branch if it needs a migration) · `refactor: remove unused helpers`. *Done when:* a mail titled "Congrats on your promotion" is triaged, and an `IS NULL` check finds credential-less rows.
- **0.8** *Done when:* the backfill command is run on a database copy, no plaintext rows remain, and a reversible migration drops the column (T-5). *Commits:* 1) `feat: add a check that no plaintext credentials remain` 2) `chore: drop the legacy token_json column`.
- **0.9** *Done when:* CI reports coverage and runs a dependency scan. The `python-jose` question is answered in an ADR note: it is removed with the external-JWT path in 1.2, unless the scan demands an earlier bump. *Commits:* 1) `chore: report coverage in CI` 2) `chore: scan dependencies for known vulnerabilities` 3) `docs: record the python-jose decision`.
- **0.10** CI currently runs only on pull requests and on `main` or `backend/**` pushes, so a plain branch gets no check until a PR exists. *Done when:* a push to a throwaway `fix/…` branch starts the backend workflow. *Commits:* 1) `chore: run CI on every branch push`.
- **0.11** Covers the Pub/Sub push route and its auth dependency. *Done when:* a wrong principal returns 403, a missing bearer token returns 401, and a valid push enqueues one job (T-7). If a test exposes a bug, the fix goes in the same commit. *Commits:* 1) `test: cover the push endpoint's auth and happy path`.
- **0.12** Move the duplicated provider, Redis and clock fakes into one shared test module. *Done when:* the suite passes with the same test count and no duplicated fake remains. *Commits:* 1) `refactor: share provider and clock fakes across tests` 2) `refactor: share the Redis fake across tests`.
- **0.13** Gmail search lags and can hide a message in spam or trash, yet today a lookup that finds nothing returns `failed` at once. *Done when:* a lookup that finds nothing within 10 minutes of the attempt leaves the action in `reconciliation_required` (I-18), and a sent message that landed in spam is found. *Commits:* 1) `fix: do not declare failed inside the reconcile quiet period` 2) `fix: include spam and trash in the Gmail reconcile lookup` 3) `docs: remove the reconcile gap from the architecture`.

## Phase 1 — Identity · branch `feat/identity` (MS1, part 1)

Order: 1.1, 1.2, 1.3. Needs: Phase 0 done.

| ID | Task | Needs | Lands · size | Status |
|---|---|---|---|---|
| 1.1 | OpenID Connect sign-in with PKCE, state, nonce | Phase 0 | `feat/identity` · L | todo |
| 1.2 | Opaque sessions, CSRF and Origin checks; remove the external-JWT path | 1.1 | `feat/identity` · L | todo |
| 1.3 | Per-user settings (time zone, working hours) | 1.1 | `feat/identity` · S | todo |

- **1.1** Sign-in is separate from per-app consent ([ADR-0004](DECISIONS/ADR-0004-oidc-vs-app-consent.md), [ADR-0011](DECISIONS/ADR-0011-identity-and-oauth-hardening.md)). *Done when:* a wrong audience, tampered nonce or expired ID token is rejected (T-9). *Commits:* 1) `feat: add users keyed by OIDC subject` 2) `feat: add the OIDC authorization-code flow with PKCE, state and nonce` 3) `feat: validate ID tokens` 4) `docs: update architecture for sign-in`.
- **1.2** The same task deletes the HS256 token path and `python-jose`. *Done when:* an expired or forged session returns 401, a cross-origin POST is rejected (T-4, T-8), and `python-jose` is gone from the dependencies. *Commits:* 1) `feat: add opaque server-side sessions` 2) `feat: enforce CSRF and Origin checks on state-changing routes` 3) `refactor: replace the external-JWT dependency with sessions`.
- **1.3** *Done when:* settings are isolated per user (cross-user test). *Commits:* 1) `feat: add per-user settings`.

Demo login is no longer here: it moves to 6.1 as a separate stack, so it cannot share a database with real sign-in ([ADR-0009](DECISIONS/ADR-0009-demo-strategy.md)).

## Phase 2 — Connector contract and data lifecycle · branch `feat/connector-contract` (MS1, part 2)

Order: 2.1, 2.2, 2.3, 2.6, 2.4, 2.5. Reason: connection-scoped data (2.6) must exist before disconnect (2.4) and per-connection automation (2.5) can use it.

| ID | Task | Needs | Lands · size | Status |
|---|---|---|---|---|
| 2.1 | Spikes: Gmail marker survival; scope tiering; Tasks scope tier | 1.2 | main (docs) · M | todo |
| 2.2 | Connector contract and `GoogleConnector` | 2.1 | `feat/connector-contract` · L | todo |
| 2.3 | Connections table, Off/Read/Propose, bound "+" consent flow | 2.2, 0.8 | `feat/connector-contract` · L | todo |
| 2.6 | Data lifecycle: connection-scoped data, content-free audit, key context v2, delete-my-data, restore note | 2.3 | `feat/connector-contract` · L | todo |
| 2.4 | Disconnect: revoke at the provider, delete the connection and its data | 2.6 | `feat/connector-contract` · M | todo |
| 2.5 | Automation per connection, with fairness | 2.6 | `feat/connector-contract` · L | todo |

- **2.1** Needs a throwaway Google test account (never committed). *Done when:* results are written into [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) §10, [PRD.md](PRD.md) §9 is updated and ADR-0005 is amended if scope tiering fails. *Commits:* 1) `docs: record spike results for Gmail markers and scope tiering`.
- **2.2** *Done when:* the shared contract tests pass for `GoogleConnector`, the old suite is green, and every action schema declares an approval tier with an undeclared one treated as C (I-22, [ADR-0013](DECISIONS/ADR-0013-approval-tiers.md)). *Commits:* 1) `feat: define the connector contract and shared contract tests` 2) `feat: declare an approval tier on every action schema` 3) `refactor: move Google handlers behind GoogleConnector`.
- **2.3** Includes the migration from `onebox_tokens`, and a "+" flow whose OAuth state stores a session hash and a PKCE verifier (I-16, T-16). *Done when:* Off blocks all token use, Read blocks proposals (policy tests) and a callback from a different session is rejected. *Commits:* 1) `feat: add the connections table and migrate stored tokens` 2) `feat: enforce Off, Read and Propose per connection` 3) `feat: add the "+" consent flow bound to the session` 4) `feat: request narrower scopes where the provider allows`.
- **2.6** [ADR-0012](DECISIONS/ADR-0012-data-lifecycle.md). Split in two if it passes three days: commits 1–4 (schema), then 5–6 (user-facing deletion). *Done when:* deleting a connection or all of a user's data leaves no summaries, jobs or cached data (I-21), audit rows carry only ids, hashes and states, and the restore procedure expires `pending` and quarantines `processing` actions (T-20, T-21). *Commits:* 1) `feat: scope triage, job and mailbox tables to a connection` (migration, backfill) 2) `feat: stop audit rows cascading and keep them content-free` 3) `feat: bind stored credentials to the connection in the key context` (re-encrypt migration) 4) `feat: expire and quarantine actions after a restore` 5) `feat: add delete-my-data` 6) `docs: describe the restore and deletion procedures`.
- **2.4** *Done when:* the provider revoke is called, the connection and its data are gone and later calls fail closed (I-13). *Commits:* 1) `feat: revoke at the provider and delete the connection on disconnect` 2) `test: calls fail closed after disconnect`.
- **2.5** Today a job holds a 120-second lease for up to 100 messages and push for a disconnected mailbox is not handled. *Done when:* two mailboxes are processed independently, one flooded mailbox cannot starve the other, a job covers at most 10 messages and renews its lease per message, and push for a disconnected mailbox returns 2xx and queues a resync. *Commits:* 1) `feat: run watch, cursor and resync per connection` 2) `feat: schedule jobs fairly across connections` 3) `fix: bound a job to ten messages and renew its lease per message` 4) `test: two mailboxes are processed independently`.

## Phase 3 — Email to proposals · branch `feat/email-to-proposals` (MS2, part 1)

Order: 3.1, 3.2, 3.3, 3.4, 3.6, 3.5, 3.7.

| ID | Task | Needs | Lands · size | Status |
|---|---|---|---|---|
| 3.1 | Typed triage output from a one-message, zero-tool reader | 2.5 | `feat/email-to-proposals` · M | todo |
| 3.2 | Deterministic validator | 3.1 | `feat/email-to-proposals` · L | todo |
| 3.3 | Proposals with source link and reason | 3.2 | `feat/email-to-proposals` · L | todo |
| 3.4 | Honest status in assistant replies | 3.3 | `feat/email-to-proposals` · S | todo |
| 3.6 | Tombstones and bounded resync | 3.3 | `feat/email-to-proposals` · M | todo |
| 3.5 | Fault-injection tests for lost responses | 3.3, 0.12 | `feat/email-to-proposals` · M | todo |
| 3.7 | Golden set: fixture, validator check and first live run | 3.3 | `feat/email-to-proposals` · M | todo |

- **3.1** The reader sees one message and no calendar data ([ADR-0007](DECISIONS/ADR-0007-zero-tool-reader.md)); the output gains an `importance` field for the digest order. *Done when:* schema violations are rejected and a prompt-breakout fixture cannot change the output structure (I-12). *Commits:* 1) `feat: define the triage output schema and parser` 2) `feat: ask the zero-tool reader for typed output, one message at a time`.
- **3.2** *Done when:* I-8, I-9 and I-19 pass; an exhausted daily cap leaves the message in a visible `deferred` state and does not advance the cursor. *Commits:* 1) `feat: resolve reply recipients from the verified sender only` (verified means `dmarc=pass`) 2) `feat: limit event attendees to the verified sender and reject bidi and control characters` 3) `feat: judge calendar conflicts on the user's local day` 4) `feat: cap suggestions per user per day in PostgreSQL, failing closed to deferred` 5) `feat: dedupe suggestions by message and type`.
- **3.3** *Done when:* re-triaging the same mail creates no duplicates, the reader's JSON is stored with the proposal, bridge proposals live 48 hours (chat ones 15 minutes), and conflicts are rechecked when the action is claimed (I-17). *Commits:* 1) `feat: store source reference, reason and reader output on proposals` (migration) 2) `feat: create proposals from validated suggestions with a 48-hour lifetime` 3) `feat: recheck conflicts when an action is claimed` 4) `feat: expose source and reason in the actions API and client types`.
- **3.4** *Done when:* I-10 passes. *Commits:* 1) `fix: never report success before the provider confirms`.
- **3.6** *Done when:* a message is never proposed twice after its work row was cleaned up (tombstones hold provider ids only, 90 days), and a failing job escalates to a bounded resync before `dead_letter`. *Commits:* 1) `feat: keep message tombstones after cleanup` 2) `test: bounded resync and dead letter for repeated job failures`.
- **3.5** *Done when:* zero duplicate effects per action type under injected lost responses (I-4). *Commits:* 1) `test: add a fault-injecting HTTP wrapper for provider calls` 2) `test: assert zero duplicate effects per action type`.
- **3.7** Spec: [TEST_STRATEGY.md](TEST_STRATEGY.md) §8. *Done when:* the 20-case fixture exists, CI shows the validator accepting every expected output and rejecting an unsafe variant of every S-case, and a dated live-run note records the model, the score and the failing ids (PRD metric M9). *Commits:* 1) `test: add the golden email fixture` 2) `test: run the validator over the golden set` 3) `docs: record the first live reader run`.

## Phase 4 — Web client · branch `feat/web-client` (MS2, part 2)

Design lives in [FRONTEND.md](FRONTEND.md). Build the suggestions queue first: it is the core screen and it proves the API shape early.

Order: 4.0, 4.5, 4.1, 4.2, 4.3, then 4.4 only if 4.5 shows approval fatigue. The observation (4.5) comes right after the walking skeleton so its findings can still change the other screens.

| ID | Task | Needs | Lands · size | Status |
|---|---|---|---|---|
| 4.0 | Walking skeleton: scaffold, tokens, `ActionCard`, suggestions queue | 3.3 | `feat/web-client` · L | todo |
| 4.1 | API: hash-bound approve, edit supersedes, owner reconcile, activity timeline | 3.3 | `feat/web-client` · L | todo |
| 4.2 | Digest, activity, connect and settings screens | 4.0, 4.1 | `feat/web-client` · L | todo |
| 4.3 | Needs-checking and conflict states; accessibility pass | 4.2 | `feat/web-client` · M | todo |
| 4.5 | Observe 3–5 people on the walking skeleton | 4.0, 3.7 | main (notes only) · M | todo |
| 4.4 | Optional: batch approve for tier B proposals (not in the totals) | 4.1, 4.5 | `feat/web-client` · M | todo |

- **4.0** Decide the stack first (S-2 in X.4). *Done when:* a first-time viewer can review and approve a proposal against the real API. *Commits:* 1) `chore: scaffold the web client` 2) `feat: add design tokens and base components` 3) `feat: add the suggestions queue with ActionCard`.
- **4.1** [ADR-0010](DECISIONS/ADR-0010-approval-and-execution-protocol.md). Could start before 4.0 if the API is wanted first. *Done when:* approving with a stale payload hash is rejected, editing atomically rejects the original and creates a new proposal (I-17), an owner can run a read-only reconcile on their own action and mark it `resolved_by_user`, and OpenAPI is regenerated. *Commits:* 1) `feat: bind approval to the payload hash the user saw` 2) `feat: supersede the original proposal when it is edited` 3) `feat: let an owner reconcile their own action with a read-only check` 4) `feat: add the activity timeline endpoint, including tier A actions` 5) `chore: regenerate OpenAPI and client types`.
- **4.2** One commit per screen. *Done when:* each screen has loading, empty and error states.
- **4.3** *Done when:* the accessibility checks in [FRONTEND.md](FRONTEND.md) §7 pass and the untrusted-text fixtures render as plain text.
- **4.5** Plan: [PRD.md](PRD.md) §4 "Validation plan". The only task that tests the product thesis. *Done when:* 3–5 sessions are run to the plan, the results are recorded in [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) §11, and the plan's decision rule is applied and written down (continue, change the approval design, or add an "Ask" box). *Commits:* 1) `docs: record the first user observations`.
- **4.4** Built **only if** 4.5 shows approval fatigue; otherwise it stays `todo` and costs nothing. [ADR-0013](DECISIONS/ADR-0013-approval-tiers.md) rule 2. *Done when:* a request approves up to 10 selected tier B cards, each with its own payload hash; a tier C card cannot be added to a batch; one failing item leaves the others intact (I-22). *Commits:* 1) `feat: approve a batch of selected tier B proposals` 2) `feat: add the batch action to the suggestions screen`.

## Phase 5 — Outlook connector · branch `feat/outlook-connector` (MS3, stretch)

Start only after Phase 6 is done or the owner decides to trade the demo polish for breadth. Tasks marked XL **must be split into capability tasks of L or smaller before they start**; the commit plan is written at that point.

| ID | Task | Needs | Lands · size | Status |
|---|---|---|---|---|
| 5.0 | Spike: Graph personal account, Sent Items lookup, event `transactionId` | 2.3 | main (docs) · M | todo |
| 5.1 | Graph OAuth for a personal account; read mail | 5.0 | `feat/outlook-connector` · XL | todo |
| 5.2 | Send, reply, create event; reconcile through Sent Items | 5.1 | `feat/outlook-connector` · XL | todo |
| 5.3 | Polling for new items | 5.1 | `feat/outlook-connector` · L | todo |
| 5.4 | Cross-provider demo: Outlook mail to Google event | 5.2, 4.2 | `feat/outlook-connector` · M | todo |

*Done when* (5.4): a scripted run passes (PRD metric M5). Where the Sent Items lookup is weak, the action ends as *needs checking*, and the docs say so.

## Phase 6 — Demo and packaging · branch `feat/demo-and-packaging` (MS4)

| ID | Task | Needs | Lands · size | Status |
|---|---|---|---|---|
| 6.1 | Demo mode: separate stack, demo login, seeded mailbox, canned model output, fake providers | 4.2 | `feat/demo-and-packaging` · L | todo |
| 6.2 | Single-VM deploy with TLS and health checks | 6.1 | `feat/demo-and-packaging` · M | todo |
| 6.3 | Alerts, spend cap, per-call telemetry | 6.2 | `feat/demo-and-packaging` · M | todo |
| 6.4 | README, diagram, video, finalize ADRs | 6.3 | `feat/demo-and-packaging` · M | todo |

*Done when* (6.4): a stranger follows the README to a running demo. 6.1 is done when a demo user cannot reach a real connection because the demo runs against its own database ([ADR-0009](DECISIONS/ADR-0009-demo-strategy.md)), and the seeded mailbox is built from the golden-set cases ([TEST_STRATEGY.md](TEST_STRATEGY.md) §8).

## Cross-cutting (any time, on `main`)

| ID | Task | Status |
|---|---|---|
| X.1 | Re-fetch the sources behind [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) §9 | todo |
| X.2 | Render the Mermaid diagrams on GitHub and fix syntax errors | todo |
| X.3 | Run the commands in [ENGINEERING_PRACTICES.md](ENGINEERING_PRACTICES.md) once and correct them | todo |
| X.4 | Decide the simplification candidates below | todo |

### X.4 Simplification candidates (decide before the phase that needs them)

| ID | Candidate | Decide before | Why it might be worth it | Why it might not |
|---|---|---|---|---|
| S-1 | Keep durable state (OAuth state, PKCE verifier, daily counters) in PostgreSQL so Redis holds only disposable cache | 1.1 | One fewer store to lose state in; tests already need only PostgreSQL | One more table, and a cleanup job for expired state |
| S-2 | Render the four screens on the server (for example with htmx) instead of a React client | 4.0 | Far less code and no second toolchain; fits a plain-text-only rule | Weaker as a "frontend" line on a résumé; less interactive editing |

Considered and rejected: deferring the connector interface until Outlook exists. The interface is what makes "+" real, and writing it against one provider invites a Google-shaped contract; the contract tests are cheap insurance.

## Optional — MCP connector · branch `feat/mcp-connector`

One app with an official MCP server; writes wrapped as proposals. Only after phases 0–6.
