# OneBox — Threat Model

**Status:** living · 2026-11-06 · baseline commit `55b6dbd`. Evidence and sources: [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md). Status tags: **[BUILT]**, **[PLANNED]**, **[DOCUMENT]** (accepted and described, not built).

## 1. Scope and method

OneBox holds a user's OAuth tokens for several apps and lets an AI act through them. This model lists assets, actors and trust boundaries, then threats with the control for each, then breach scenarios. It is deliberately short enough to be kept current.

## 2. Assets

User refresh tokens · mail, calendar and task content (read from providers) · proposals and the audit trail · sessions · encryption keys and signing secrets · model prompts and outputs.

## 3. Actors

| Actor | Capability |
|---|---|
| **Content author** | Anyone who can put text in front of the model: email sender, invite sender, ticket or document author |
| **Stolen-session attacker** | Holds a valid session or JWT for one user |
| **Database reader** | Has a database dump |
| **Server attacker** | Runs code on the app host and can read memory and config |
| **Malicious connector** | A compromised third-party MCP server or provider response |
| **The model** | Can be steered by content; treated as untrusted |

## 4. Non-negotiable design rules

1. **The AI never executes a send, reply, event or task.** It only proposes. [BUILT] The assistant's `create_draft` and `mark_as_read` tools act immediately; whether they become proposals is open (PRD §9).
2. **A model that reads untrusted content has no tools.** [BUILT for triage] This is the Rule of Two applied: it sees untrusted input and private data, but cannot change state.
3. **Plain code validates model output** and creates proposals. [PLANNED]
4. **Approval is enforced in code:** single use, bound to the payload hash, owned by the user, expiring. [BUILT: hash, claim, owner, expiry]
5. **Model output is shown as plain text.** No markdown, no auto-loaded images or links, no URL-fetching or web-search tool for the model. [PLANNED]
6. **Least privilege:** minimal scopes; per-app Off / Read / Propose switch. [PLANNED]
7. **Tokens are encrypted**, with the key outside the database. [BUILT: the keyring is a read-only file mounted from the host; a key-management service is PLANNED]
8. **Disconnect revokes at the provider** and deletes stored tokens. [PLANNED]
9. **No tokens or mail bodies in logs or prompts**, enforced by a canary test. [PLANNED]
10. **Every external write goes through the ledger,** except the assistant's `create_draft` and `mark_as_read` (tier A, [ADR-0013](DECISIONS/ADR-0013-approval-tiers.md)). [agent path BUILT; the REST mail endpoints still bypass it, see §7]

## 5. Threats and controls

| # | Threat (public example) | Control | Status | Residual |
|---|---|---|---|---|
| T1 | **Prompt injection steers proposals** (EchoLeak; Superhuman AI exfiltration) | Zero-tool reader; typed output; deterministic validator; recipient is the verified sender only; show payload and source link; daily cap | reader BUILT; rest PLANNED | An attacker can still influence proposal text a person sees |
| T2 | **Exfiltration through model output** (image or link auto-fetch; Notion agent web search) | Plain text only; no URL fetch tools | PLANNED | Low |
| T3 | **Duplicate or lost writes** (double-send bugs in other projects) | Idempotency key, claim, lease, attempt token, deterministic IDs and markers, reconciliation | BUILT | Gmail marker survival is a spike |
| T4 | **Stolen session approves its own malicious proposals** | Opaque server-side sessions; short lifetime; single-use approvals; notice on each write; step-up approval | sessions PLANNED; step-up DOCUMENT | **High**, accepted for the demo |
| T5 | **Database dump** | AES-256-GCM with per-user context; keyring for rotation | BUILT | Legacy plaintext column (see §7) |
| T6 | **Server compromise reads keys from memory or config** (n8n file-read bug exposed the encryption secret) | The keyring is a host-mounted file, outside the database but readable by a host compromise; planned: a key-management service; minimal scopes; revoke; anomaly alerts | keyring file BUILT; KMS PLANNED; rest DOCUMENT | Medium |
| T7 | **Forged push notifications** (advisories in another project) | Pub/Sub signed bearer token and service-account check | BUILT | Tests exist for 503, an invalid token and malformed claims; add wrong principal, missing bearer and the happy path |
| T8 | **Approval consumed by a link prefetch or cross-site request** | Approvals are POST; CSRF and Origin checks | POST BUILT; CSRF PLANNED | Low |
| T9 | **OAuth flow attacks** | One-time state bound to user and email; fixed redirect URI; PKCE; nonce; ID-token validation | state and fixed redirect BUILT; rest PLANNED | Low |
| T10 | **Token lifecycle gaps** (Google Testing mode expires tokens in 7 days; Microsoft does not revoke old refresh tokens on rotation; session revocation unsupported for personal accounts) | Handle `invalid_grant` as "reconnect needed"; real revoke on disconnect | PLANNED | Medium on Microsoft |
| T11 | **Malicious or compromised MCP server** (tool poisoning, confused deputy) | Allowlist; annotations untrusted; no token forwarding; writes wrapped as proposals | PLANNED, optional | Medium |
| T12 | **SSRF through connector fetches** | SSRF-safe HTTP client | PLANNED | Low |
| T13 | **Cross-user data exposure** (Asana MCP tenant-isolation bug) | Owner-scoped queries (actions, triage); add cross-user tests everywhere | BUILT / PLANNED | Low |
| T14 | **Cost abuse** | Per-user daily caps and rate limits | PLANNED | Low |
| T15 | **Ledger bypass** | Remove or route every REST mail write through the ledger | PLANNED (TASKS 0.6) | Medium until done |
| T16 | **A forwarded OAuth link attaches the victim's account to the attacker's user** | The OAuth state stores a session hash and a PKCE verifier; the callback requires the same live session ([ADR-0011](DECISIONS/ADR-0011-identity-and-oauth-hardening.md)) | PLANNED | Low; today only the email-equality check blocks it, and only for Google |
| T17 | **A stale or edited proposal runs** (an old tab approves unedited text; Off or Read still runs queued proposals; a send leaves the wrong mailbox) | Approve carries the payload hash; claim rechecks connection, switch and sending account; edit supersedes the original ([ADR-0010](DECISIONS/ADR-0010-approval-and-execution-protocol.md)) | PLANNED | Low |
| T18 | **A write lands late and reconcile says `failed`** (search lag, spam or trash, a slow provider) | No `failed` inside a 10-minute quiet period; include spam and trash; evaluate draft-then-send (ADR-0010) | PLANNED | Medium |
| T19 | **Injection through invites and look-alike text** (attendees, bidi characters, look-alike domains, calendar data in the reader prompt) | Attendees and recipients limited to the verified sender; reject bidi and control characters; show punycode; the reader sees one message and no calendar data ([ADR-0007](DECISIONS/ADR-0007-zero-tool-reader.md)) | PLANNED | Medium |
| T20 | **A restored backup resurrects executed approvals** | After a restore, expire `pending` and quarantine `processing` actions before serving ([ADR-0012](DECISIONS/ADR-0012-data-lifecycle.md)) | DOCUMENT | Low |
| T21 | **Deletion leaves data behind** (summaries, jobs, caches, audit) | Connection-scoped data and a deletion matrix (ADR-0012; [ARCHITECTURE.md](ARCHITECTURE.md) §7) | PLANNED | Medium |
| T22 | **Approval fatigue: a person approves without reading** | Approval tiers ([ADR-0013](DECISIONS/ADR-0013-approval-tiers.md)): effort goes to what cannot be undone; tier C is never batched and always shows the exact recipient and time; a batch for tier B only after the user test (task 4.5) | PLANNED | Medium; a human factor that tests cannot close, so it is observed, not asserted |

## 6. Breach scenarios

| Scenario | Attacker gets | What limits it | Build or document |
|---|---|---|---|
| **a) Database dump** | Encrypted tokens, proposals, summaries | AES-GCM with per-user context; no passwords exist; the key is not in the database | Move the keyring to a key-management service; backfill and remove the legacy plaintext column |
| **b) Server compromise** | Live keys and decrypted tokens in memory | Minimal scopes; Off/Read/Propose; revoke; alerts | Build minimal scopes and revoke; document KMS and process isolation |
| **c) Stolen session or JWT** | Can call the API **and approve actions** | Short lifetime, opaque sessions, single-use approvals, notice on each write | Build the first four; **document** step-up approval and passkey-signed approval as future work |
| **d) Leaked signing or session secret** | Forge sessions | Separate secrets; rotation | Build separation; document rotation |
| **e) Malicious third-party MCP server** | Tool results it controls; nothing else if tokens are not forwarded | Allowlist; no token passthrough; proposals only | Build only if the MCP connector is built |

## 7. Known gaps in the code today

- **No user login.** JWTs are expected from an external issuer that does not exist; OAuth start needs one.
- **Reply recipient resolution prefers `Reply-To`.** `resolve_reply_target` (in `server/services/pending_actions.py`) currently favors the `Reply-To` header over the verified `From`; TASKS 3.2 and invariant 8 change this, and the first failing test will exercise the real behavior.
- **No disconnect or revoke endpoint** that I have seen; verify.
- **No rate limiting.**
- **Ledger bypasses:** every REST mail write: `DELETE /mail/emails/{id}` (the only reason for the broad `https://mail.google.com/` scope), `POST /mail/send`, `POST /mail/drafts`, read, unread, star, trash and restore. The assistant's `create_draft` and `mark_as_read` also act immediately.
- **Legacy plaintext column** `token_json` still exists for a dual-read migration; a backfill command exists in `server/commands/`. Confirm no plaintext rows remain, then drop the column.
- **What is stored in plain text:** proposal payloads (full message bodies for sends and replies), triage summaries and audit evidence are not encrypted; only tokens are. Redis caches parsed mail for 60 seconds (lists) and 300 seconds (message detail). Retention defaults: proposals 30 days, notification jobs 14 days, triage work 14 days.
- `python-jose==3.3.0` is pinned; later releases fixed advisories (recalled, not scanned). Add dependency scanning, then bump or replace. CI has no dependency scanning or coverage tooling today.
- **A persistently failing retention cleanup starves the worker.** The loop survives (it logs and retries), but cleanup runs before the heartbeat, watch renewal and job claim, so no work is claimed and `/readyz` reports the worker stale. Triage retries are uncapped. The HTTP error handler discards detail strings.
- Expired-lease `processing` actions are promoted only on the next approve or reconcile call, not automatically.

## 8. Allowed and forbidden claims

**Allowed:** "The assistant cannot send, reply, book or create a task on its own; a person approves each one." · "A write is dispatched at most once; unclear outcomes are checked, not retried." · "OneBox never sees your password and keeps no chat memory. Tokens are encrypted; proposals, summaries and audit events are stored as plain text with a retention limit." · "Measured on a fixed set: …" (only with the numbers).

**Not yet allowed:** "one button deletes everything" (until ROADMAP 2.6 ships).

**Forbidden:** "exactly once" · "zero retention" · "nothing is stored" · "cannot be tricked" · "GDPR compliant" · "Rule of Two compliant" (Meta says the rule is not sufficient) · "agentic" without a tool loop.

## 9. Minimum security checklist (12 items, by value for effort)

| # | Item | Status |
|---|---|---|
| 1 | Single-use approvals bound to exact arguments | BUILT (hash, claim, status); add a replay test |
| 2 | Scope tiering per switch | PLANNED (spike on `include_granted_scopes`) |
| 3 | Opaque server-side sessions; separate secrets | PLANNED |
| 4 | Validate the ID token once; key users by `sub` | PLANNED |
| 5 | Exact redirect URI, state, PKCE | state and fixed redirect BUILT; PKCE PLANNED |
| 6 | Token encryption with per-user context; key outside the DB | BUILT (host-mounted keyring file); key-management service PLANNED |
| 7 | Real revoke on disconnect | PLANNED |
| 8 | Audit log, rate limits, notice on each write | audit BUILT; rest PLANNED |
| 9 | MCP allowlist; annotations untrusted; no token forwarding | PLANNED, optional |
| 10 | Untrusted-content rule | BUILT for triage |
| 11 | SSRF-safe client | PLANNED |
| 12 | Receiver for Google's Cross-Account Protection events | DOCUMENT; after the demo |

## 10. Incident-derived requirements

| Public incident | Requirement it produced here |
|---|---|
| Superhuman AI exfiltration; EchoLeak | Plain-text output; no outbound channel for the model; zero-tool reader |
| Atlassian MCP proof of concept; Notion agent exfiltration | Untrusted content never reaches a model with tools |
| Asana MCP exposure | Owner-scoped queries and cross-user tests |
| Composio breach; Salesloft Drift | Token custody stays with OneBox; encryption; real revoke; least privilege |
| n8n file-read bug exposing the encryption secret | Keep the key out of config on the app host |
| Double-send fixes in other projects | Idempotency, claim and replay tests |
| Prefetched approval links consumed (another project's ADR) | Approvals are POST, never GET |

## 11. Residual and accepted risks

A stolen session can approve actions (T4); provider-side abuse logs may exist for model calls; weekly token expiry in Testing mode; Outlook reconciliation is weaker than Gmail's. These are stated in the README, not hidden.

## 12. Review cadence

Re-read this document at the end of every phase, and whenever a connector is added or a write path changes.
