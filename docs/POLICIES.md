# Runtime policies

What the code enforces **today**: input limits, the mail cache, which tools an agent may use, provider retries, account identity and data retention. Known gaps and planned changes are in [THREAT_MODEL.md](../.lock/THREAT_MODEL.md#7-known-gaps-in-the-code-today) and [TASKS.md](../.lock/TASKS.md).

## Ingress & Payload Bounds
To limit resource exhaustion, the API enforces input ceilings before dispatching work to external providers:
- **Generic HTTP Request Bodies:** Capped at `MAX_REQUEST_BODY_BYTES` (1 MiB default) before JSON parsing.
- **Agent Prompts:** Capped at 8,000 characters.
- **Gmail Search Queries:** Capped at 512 characters.
- **Mail Pagination Limits:** Capped at 100 messages per page.
- **Pub/Sub Push Envelopes:** Capped at `PUBSUB_MAX_ENVELOPE_BYTES` (64 KB default).
- **OAuth Callback State & Code:** Capped at 512 and 4,096 characters respectively.
- **Inbound Mail Bodies:** Limited to 256 KB of decoded text. Headers are limited to 200 entries and 4,096 characters each; MIME structure traversal is bounded to 512 parts and 32 nesting levels. An oversized or malformed body or header set is rejected before triage: the message is dead-lettered (`message_payload_invalid`) and never reaches the model. Exceeding the part limit yields an empty body, and parts nested deeper than 32 levels are skipped.

## Generational Mail Cache Policy
Mail detail bodies are cached for **5 minutes**; folder views and search results are cached for **60 seconds**.
- **Atomic Generation Invalidation:** Each direct `/mail` mutation endpoint (read/unread, trash/restore, delete, star, send) atomically advances the user's cache generation integer. Draft saves and agent-initiated writes do not currently invalidate the cache, so those changes appear once the cached entries expire.
- **No Wildcard Scans:** Prevents blocking Redis with dangerous `KEYS *` operations.
- **Fail-Open Behavior:** A Redis outage or serialization failure is treated as a cache miss and does not block mail fetching.

## Agent Execution & Tool Allowlist
Tool access is governed by an immutable per-run server allowlist:
- **Automated Inbound Triage:** Exposes **zero tools**. Inbound automation only records structured triage metadata and never alters provider state.
- **Interactive Agents:** May only access tools explicitly granted by route configuration and user service connections.
- **Staged External Effects:** Sends, replies, event creations, and task insertions stage pending actions; they never perform live external writes directly.
- **Immediate Interactive Tools:** Only `create_draft` and `mark_as_read` execute immediately during interactive sessions.

## Provider Retry, Identity & Retention Policy
External Google API failures are classified into granular categories:
- **Safe Retries:** Reads and idempotent writes execute with bounded exponential backoff and full jitter, honoring a provider `Retry-After` floor. Quota errors (HTTP 429 and HTTP 403 with quota reasons) are included in the retryable category. Each retry sequence is bounded by both `PROVIDER_RETRY_MAX_ATTEMPTS` and `PROVIDER_RETRY_DEADLINE_SECONDS`; whichever limit is reached first stops the attempt.
- **Single Dispatch:** Non-idempotent writes are dispatched at most once and never blind-retried. Ambiguous results transition to `reconciliation_required`.
- **Authoritative Identity:** Persisted case-normalized Google email addresses define account ownership. OneBox fails closed if credentials point to a renamed or mismatched account.
- **Watch Renewal Backoff:** Failed Gmail watch renewals are retried with exponential backoff using a per-mailbox `watch_renewal_next_attempt_at` cursor. Sustained failures surface in `/mail/agent/health` and the operations runbook.
- **Data Retention:** Terminal pending actions are retained for **30 days**; terminal notification jobs and triage logs are retained for **14 days**. Proposals nobody approves are marked `expired` by the worker's hourly cleanup and then removed under the same 30-day rule. Expired rows are purged periodically by the worker.
