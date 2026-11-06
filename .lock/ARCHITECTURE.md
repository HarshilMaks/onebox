# OneBox — Architecture

**Status:** living · 2026-11-06 · baseline commit `55b6dbd`. Diagrams use Mermaid (GitHub renders them).
**Legend:** **[BUILT]** verified in code · **[PLANNED]** decided, not built · **[ESTIMATE]** judgment or arithmetic.

## 1. Context and goals

Goal: let **one AI assistant** work across a person's tools while **never acting on its own**. Every write is a stored, immutable proposal in **one queue** with **one audit trail**; approval is enforced in code; an unclear provider outcome is reconciled, never retried blindly.

Non-goals: autonomy, RAG, public launch, a general workflow engine. See [PRD.md](PRD.md) §7.

## 2. System context

```mermaid
flowchart LR
  user([User]) -->|OIDC sign-in, review, approve| web[Web client - PLANNED]
  web -->|HTTPS JSON| api[OneBox API]
  api --> pg[(PostgreSQL)]
  api --> redis[(Redis)]
  worker[Worker] --> pg
  worker --> redis
  pubsub[Google Pub/Sub push] -->|signed POST| api
  api -->|OAuth + REST| google[Google: Gmail, Calendar, Tasks]
  worker -->|REST| google
  api -->|OAuth + REST - PLANNED| ms[Microsoft Graph: Outlook]
  api --> llm[Gemini on Vertex AI]
  worker --> llm
  api -.->|optional, PLANNED| mcp[MCP servers]
```

## 3. Containers and deployment

| Container | Responsibility | Status |
|---|---|---|
| **API** (FastAPI) | Auth, connections, assistant endpoints, approval API, push receiver, health | BUILT (auth and connections PLANNED) |
| **Worker** | Leases notification jobs, runs triage, renews Gmail watch, resyncs, retention cleanup | BUILT |
| **PostgreSQL 16** | System of record: tokens, proposals, audit, jobs, mailbox state | BUILT |
| **Redis 7** | One-time OAuth state, mail cache generation counter | BUILT |
| **Web client** | Digest, suggestions queue, activity, connect/settings | PLANNED |

```mermaid
flowchart TB
  subgraph vm[Single VM - docker compose]
    proxy[Reverse proxy - TLS - PLANNED] --> app
    app[app: API] --> postgres[(postgres)]
    app --> redis[(redis)]
    worker[worker] --> postgres
    worker --> redis
    migrate[migrate: alembic upgrade] --> postgres
  end
  internet((Internet)) --> proxy
  pubsub[Pub/Sub push] --> proxy
```

The compose services as built are `postgres`, `redis`, `migrate`, `app` and `worker`. The proxy and TLS are planned for the demo deploy.

## 4. API surface [BUILT]

34 paths in `docs/openapi.json`. Grouped:

| Group | Endpoints | Notes |
|---|---|---|
| Actions (the ledger) | `GET /actions`, `GET /actions/{id}`, `POST /actions/{id}/approve`, `/reject`, `/reconcile` | Reconcile is operator-only |
| Assistant | `POST /executive/`, `POST /generate-content/`, `POST /generate-stream/` | `/executive/` has tools; proposals only for writes |
| Google OAuth | `GET /agent/oauth/start`, `/agent/oauth/callback`, `POST /agent/verify_and_create_entry`, `GET /agent/status` | Needs an externally issued JWT today |
| Mail REST | `GET /mail/emails`, `/mail/emails/{id}`, `POST …/read`, `/unread`, `/star`, `/trash`, `/restore`, `DELETE /mail/emails/{id}`, `POST /mail/drafts`, `/mail/send`, `GET /mail/search` | `DELETE` and `/mail/send` bypass the ledger (see [THREAT_MODEL.md](THREAT_MODEL.md)) |
| Push and automation | `POST /mail/notifications`, `/mail/renew-watch`, `/mail/check-inbox`, `/mail/agent/check-inbox`, `GET /mail/agent/status`, `/mail/agent/health` | Push is signature-checked |
| Triage | `GET /mail/triage`, `/mail/triage/{id}` | Owner-scoped |
| Health | `GET /livez`, `/readyz`, `/mail/health`, `GET /` | |

## 5. Component map [BUILT]

| Area | Where | Responsibility |
|---|---|---|
| App, lifespan, error handling, health | `server/main.py` | Wiring, request limits, readiness |
| Routes | `server/routes/` | `agent_router.py` (assistant and actions), `agent_oauth.py`, `google_mail.py`, `push_router.py`, `triage.py` |
| Ledger | `server/services/pending_actions.py` | Create, claim, execute, finalize, reconcile, expire, list |
| Provider writes and lookups | `server/services/action_handlers.py` | Gmail, Calendar and Tasks writes; deterministic IDs and markers; reconciliation lookups |
| Automation | `server/services/notification_jobs.py`, `server/workers/` | Job queue with leases, triage, watch renewal, resync |
| Tool policy | `server/agent_policy.py` | Per-run tool allowlists and mutation budget |
| Credentials | `server/services/credentials.py`, `server/security/` | Scopes; encrypted token codec with a keyring |
| Inbound mail | `server/mail/` | Bounded MIME parsing; skip filter |
| Integrations | `server/integrations/` | Google client, LLM adapter, Redis adapter |
| Agent runtime | `agents.py`, `clients/prompt.py`, `tools/` | Prompts, tool callables that create proposals |
| Schema | `alembic/versions/` | Migrations |
| Typed client | `frontend-client-types.ts` | API types for a client |

## 6. Connector contract [PLANNED]

Each app is a **connector** behind one interface. A user's authorization for it is a **connection** with its own token and **switch**.

```mermaid
classDiagram
  class Connector {
    <<interface>>
    +capabilities()
    +read(connection, query)
    +action_schemas()
    +execute(connection, action)
    +reconcile(connection, action)
    +watch(connection)
    +revoke(connection)
  }
  Connector <|.. GoogleConnector
  Connector <|.. MicrosoftConnector
  Connector <|.. McpConnector
```

Rules:

1. **Read** methods run only if the switch is Read or Propose. **Writes** exist only as `action_schemas()`: typed payloads that become proposals. No connector method writes outside `execute()`, and `execute()` is called only by the ledger after approval.
2. `execute()` must end in one of three outcomes: **confirmed success**, **confirmed rejection**, or **unknown** (raise `AmbiguousActionOutcome`). Unknown is never retried.
3. `reconcile()` is a read-only lookup that decides whether an unknown write landed. Google has strong lookups (Gmail Message-ID marker search, deterministic Calendar event ID, Tasks marker). A generic MCP connector has none, so its unknown outcomes stay *needs checking*.
4. **Switch semantics:** Off = no token use; Read = read scopes only; Propose = read plus proposals. Enforced twice: by the tool allowlist and by the scopes requested (scope tiering; see ADR-0005, and the spike on `include_granted_scopes`).
5. **MCP connector:** write tools are always wrapped as proposals, tool annotations such as `readOnlyHint` are untrusted hints, and OneBox keeps its own allowlist. See ADR-0003.

## 7. Data model

### As built [BUILT]

```mermaid
erDiagram
  PENDING_ACTIONS ||--o{ PENDING_ACTION_AUDIT_EVENTS : "action_id FK, cascade"
  ONEBOX_TOKENS ||..o{ PENDING_ACTIONS : "user_id by value"
  GMAIL_MAILBOX_STATES ||..o{ GMAIL_NOTIFICATION_JOBS : "mailbox_email by value"
  GMAIL_MAILBOX_STATES ||..o{ GMAIL_TRIAGE_WORK : "mailbox_email by value"
  PENDING_ACTIONS {
    uuid id PK
    uuid user_id
    string action_type
    json payload
    string payload_hash
    string command_key "unique with user_id"
    string status "7 states"
    string attempt_token
    datetime lease_expires_at
    datetime expires_at
  }
  PENDING_ACTION_AUDIT_EVENTS {
    uuid id PK
    uuid action_id FK
    string event_type
    string old_status
    string new_status
    json evidence
  }
  ONEBOX_TOKENS {
    uuid user_id PK
    text encrypted_token_payload
    string credential_key_id
    string google_email_normalized
    string connection_status
  }
  GMAIL_NOTIFICATION_JOBS {
    uuid id PK
    string pubsub_message_id "unique"
    string mailbox_email
    bigint history_id
    string state
  }
  GMAIL_MAILBOX_STATES {
    string mailbox_email PK
    uuid user_id "unique"
    bigint history_cursor
    datetime watch_expires_at
    string resync_state
  }
  GMAIL_TRIAGE_WORK {
    uuid id PK
    string mailbox_email
    string message_id "unique with mailbox"
    string state
    text triage_summary
  }
```

Only the audit table has a real foreign key; the rest link by matching values. A legacy plaintext `token_json` column still exists for a dual-read migration; see [THREAT_MODEL.md](THREAT_MODEL.md).

### Target additions [PLANNED]

```mermaid
erDiagram
  USERS ||--o{ SESSIONS : has
  USERS ||--o{ CONNECTIONS : owns
  USERS ||--|| USER_SETTINGS : has
  CONNECTIONS ||--o{ PENDING_ACTIONS : "connection_id"
  CONNECTIONS {
    uuid id PK
    uuid user_id
    string connector "google or microsoft or mcp"
    string account_ref
    string permission "off or read or propose"
    string status
    text encrypted_token_payload
  }
  USERS {
    uuid id PK
    string oidc_sub "unique"
  }
  SESSIONS {
    string id_hash PK
    datetime expires_at
  }
  USER_SETTINGS {
    uuid user_id PK
    string timezone
    string working_hours
  }
  PENDING_ACTIONS {
    uuid id PK
    string source_ref "message id and link"
    string reason
  }
```

`onebox_tokens` evolves into `connections` (many per user). `pending_actions` gains `connection_id`, `source_ref` and `reason`. The exact schema is fixed in the Phase 1 plan.

## 8. Key flows

### 8.1 Sign-in and adding an app [PLANNED]

```mermaid
sequenceDiagram
  participant U as User
  participant W as Web client
  participant A as API
  participant G as Google
  U->>W: Sign in with Google
  W->>A: start OIDC (state, nonce, PKCE)
  A->>G: authorize (openid email profile)
  G-->>A: code
  A->>G: token exchange; validate ID token (iss, aud, exp, nonce)
  A-->>W: opaque server-side session; user keyed by sub
  U->>W: click + and choose Google Calendar
  W->>A: start consent for calendar scope only
  A->>G: authorize (incremental authorization)
  G-->>A: code and refresh token
  A->>A: encrypt token (AES-GCM, per-user context); store connection with switch
```

### 8.2 New mail to proposals [triage BUILT; bridge PLANNED]

```mermaid
sequenceDiagram
  participant G as Gmail
  participant P as Pub/Sub
  participant A as API push endpoint
  participant D as Postgres
  participant W as Worker
  participant L as Gemini, no tools
  G->>P: new message
  P->>A: signed push (message id, history id)
  A->>D: insert notification job, dedupe by Pub/Sub message id
  A-->>P: 2xx only after the durable insert
  W->>D: lease job; read mailbox cursor
  W->>G: history.list, messages.get
  W->>D: claim triage work (unique mailbox and message)
  W->>L: email as untrusted data; typed output [PLANNED]
  L-->>W: summary and suggestions
  W->>W: validate: recipient is the verified sender only, local-day free/busy, daily cap [PLANNED]
  W->>D: save summary; create proposals with source link [PLANNED]
```

### 8.3 Approve and execute [BUILT]

```mermaid
sequenceDiagram
  participant U as User
  participant A as API
  participant D as Postgres
  participant C as Connector
  participant X as Provider
  U->>A: POST /actions/{id}/approve
  A->>D: SELECT FOR UPDATE; check owner, status, expiry, payload hash
  A->>D: status processing, attempt token, lease; COMMIT before any external call
  A->>C: execute(action)
  C->>X: write with deterministic ID or marker
  alt confirmed
    A->>D: succeeded and audit event
  else confirmed rejection
    A->>D: failed and audit event
  else unknown outcome
    A->>D: reconciliation_required and audit event
  end
  A-->>U: action state
```

### 8.4 Unknown outcome and reconcile [BUILT]

An operator calls `POST /actions/{id}/reconcile`. A read-only lookup (Gmail marker search, Calendar event get, Tasks marker scan) moves the action to `succeeded`, `failed`, or leaves it `reconciliation_required` with fresh evidence. An expired-lease `processing` action is promoted to `reconciliation_required` on the next approve attempt or reconcile call. **Gap:** nothing promotes it automatically yet (ROADMAP step 0.4).

### 8.5 Disconnect [PLANNED]

Revoke the token at the provider, delete the connection and its tokens, keep or delete audit rows according to the user's choice, and confirm in the timeline.

## 9. State machines

### Pending action [BUILT]

```mermaid
stateDiagram-v2
  [*] --> pending
  pending --> processing: approve (claim)
  pending --> rejected: reject
  pending --> expired: past expires_at
  pending --> reconciliation_required: payload hash mismatch
  processing --> succeeded: provider confirmed
  processing --> failed: provider rejected
  processing --> reconciliation_required: unknown outcome or lease expired
  reconciliation_required --> succeeded: reconcile finds it
  reconciliation_required --> failed: reconcile finds nothing
  reconciliation_required --> reconciliation_required: still unknown
```

### Background work [BUILT, retry edges simplified]

`gmail_notification_jobs`: `pending → processing → succeeded`, or `dead_letter` after repeated failure. `gmail_triage_work`: `processing → succeeded | noop | dead_letter`. Both use leases and `next_attempt_at` for backoff. Known gap: triage retries are not capped (ROADMAP step 0.3).

## 10. Security architecture

```mermaid
flowchart LR
  subgraph untrusted[Untrusted content]
    mail[Email bodies, invites, tickets]
  end
  subgraph reader[No tools]
    triage[Triage and typed extraction]
  end
  subgraph det[Deterministic code]
    validate[Validate and create proposals]
  end
  subgraph human[Human]
    approve[Review and approve]
  end
  subgraph exec[Execution]
    ledger[Ledger claim and execute]
  end
  mail --> triage --> validate --> approve --> ledger --> providers[(Providers)]
```

The model that reads untrusted content has **no tools**. Plain code turns its output into proposals. A person approves. Only the ledger executes. Details, breach scenarios and controls: [THREAT_MODEL.md](THREAT_MODEL.md).

## 11. Reliability and failure modes

| Failure | Behavior |
|---|---|
| Process dies after the provider accepted a write | Row stays `processing`; lease expires; promoted to `reconciliation_required` on next touch; automatic sweep PLANNED |
| Provider timeout or lost response | `AmbiguousActionOutcome`; never retried; reconcile |
| Duplicate Pub/Sub delivery | Unique `pubsub_message_id`; ignored |
| Two workers, same message | Lease plus unique `(mailbox, message)` row |
| Gemini unavailable or slow | Triage work released with backoff; interactive call returns a provider error |
| Redis down | Cache treated as miss; OAuth state flow fails closed |
| Retention cleanup raises | Today: can stop the worker loop (ROADMAP 0.2). Target: isolated and logged |
| Gmail watch expires | Renewed by worker; resync on stale history id |

## 12. Capacity and cost estimate

All figures are [ESTIMATE] unless a source is named; assumptions are stated so they can be checked.

- **Demo scale:** at most 100 allowlisted users (Google Testing-mode cap, official docs).
- **Mail volume:** assume 5–10 active users × 50 mails/day ≈ 250–500 mails/day ≈ well under 1 mail/s even at 10× peak.
- **Model cost:** assume about 4,000 input and 300 output tokens per triage call. At Flash-Lite prices of $0.30 and $2.50 per million tokens (Gemini Developer API page; Vertex parity unverified) that is about **$2 per 1,000 mails**, so roughly **$1 per day** at 500 mails/day. A per-user daily cap bounds the worst case.
- **Gmail quota (official):** 6,000 units per minute per user; `messages.send` costs 100 units; consumer accounts block above about 500 sent emails per day. Not a constraint at demo scale.
- **Calendar:** 600 requests per minute per user. **Tasks:** 50,000 requests per day. **Graph (Outlook):** 10,000 requests per 10 minutes per mailbox, 4 concurrent.
- **Gmail push:** at most 1 notification per second per user; `watch` must be renewed at least every 7 days (daily recommended).
- **Hosting:** a single VM at roughly $6–12 per month, about $21 with managed Postgres (third-party prices).

## 13. Observability

[BUILT] `/livez`, `/readyz`, correlation id binding. [PLANNED] one structured log line per model call (model, tokens, latency, finish reason, estimated cost, correlation id equal to the action id); alerts on health, oldest pending notification job, `reconciliation_required` older than N minutes, and daily spend. No tokens or mail bodies in logs (tested).

## 14. Trade-offs

| Decision | Solves | Worsens | Revisit when | ADR |
|---|---|---|---|---|
| Approval enforced in code | Prompt tricks cannot cause a write | A person must review | Never for sends | [0001](DECISIONS/ADR-0001-approve-first.md) |
| Own Postgres ledger | Full control; small | Must maintain it | Multi-step flows with long timers | [0002](DECISIONS/ADR-0002-own-ledger.md) |
| Native connectors, MCP for the generic path | Real reconciliation where it matters | Slow to add apps | A third connector is requested | [0003](DECISIONS/ADR-0003-native-connectors-mcp-generic.md) |
| OIDC separate from per-app consent | Least privilege; clear UX | More consent steps | Never | [0004](DECISIONS/ADR-0004-oidc-vs-app-consent.md) |
| Off/Read/Propose switches | Simple, enforceable | Coarse | Per-tool control is requested | [0005](DECISIONS/ADR-0005-permission-switches.md) |
| Encrypted refresh tokens | Background triage works | Tokens are a target | A KMS is available | [0006](DECISIONS/ADR-0006-token-storage.md) |
| Reader has no tools | Injection cannot act | Less flexible | Measured need | [0007](DECISIONS/ADR-0007-zero-tool-reader.md) |

## 15. Open design questions

See [PRD.md §9](PRD.md#9-open-questions) and the spikes in [ROADMAP.md](ROADMAP.md).
