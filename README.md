# OneBox

> Intelligent AI agent orchestration platform for automated email triage, calendar scheduling, and task management using Gemini and Google Workspace APIs.

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-336791.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7+-DC382D.svg)](https://redis.io/)

[Overview](#overview) • [Features](#features) • [Architecture](#architecture) • [Prerequisites](#prerequisites) • [Quick Start](#quick-start) • [Configuration](#configuration) • [API Reference](#api-reference) • [Docker](#docker) • [Project Structure](#project-structure)

---

## Overview

Managing high-volume executive communication, meeting coordination, and task tracking requires constant context switching.

**OneBox** is an interactive assistant integrated with Google Workspace accounts (Gmail, Google Calendar, and Google Tasks). Gemini function calling can prepare narrowly authorized interactive actions. Optional Gmail automation durably receives and analyzes Pub/Sub notifications for one configured mailbox; it does not autonomously change mail, draft or send replies, create events/tasks, or synchronize them. Sensitive agent effects use a human-in-the-loop pending-action workflow.

---

## Features

- **Durable Mail Triage:** Receives and analyzes authenticated Pub/Sub notifications for one configured mailbox with PostgreSQL leases, history recovery, and watch renewal. Automated triage does not alter messages or send mail.
- **Calendar Planning:** Interactive agents can inspect calendar availability and stage a calendar-event action for explicit approval; event creation is never automatic.
- **Task Planning:** Interactive agents can stage a Google Tasks creation action for explicit approval. OneBox does not automatically synchronize calendar events and tasks.
- **Human-in-the-Loop Safeguards:** Stages sensitive operations (such as sending live emails or updating calendar events) as immutable pending actions awaiting explicit user approval.
- **Real-Time Pub/Sub Ingestion:** Receives real-time mailbox push notifications via authenticated Google Cloud Pub/Sub webhooks.
- **Multi-Tenant OAuth Management:** Handles Google OAuth 2.0 authorization with automatic token persistence, background token refresh, and JWT-authenticated session management.
- **High-Performance Caching:** Redis caching layer for paginated mailbox listings and message bodies to minimize external Google API latency and quota consumption.

---

## Architecture

OneBox follows a modular, layered architecture separating HTTP routes, agent execution loops, tool adapters, and persistence layers:

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Server                         │
│  (/mail, /executive, /generate-stream, /actions, /agent)    │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
        OAuth & User Auth             Pub/Sub Webhooks
               │                               │
┌──────────────▼──────────────┐ ┌──────────────▼──────────────┐
│  SQLAlchemy & PostgreSQL    │ │     Background Worker       │
│  (Tokens & Pending Actions) │ │ (notification triage worker) │
└─────────────────────────────┘ └──────────────┬──────────────┘
                                               │
                                ┌──────────────▼──────────────┐
                                │      Executive Agent        │
                                │   (Gemini Function Call)    │
                                └──────────────┬──────────────┘
                                               │
                   ┌───────────────────────────┼───────────────────────────┐
                   ▼                           ▼                           ▼
         ┌───────────────────┐       ┌───────────────────┐       ┌───────────────────┐
         │     Gmail API     │       │   Calendar API    │       │     Tasks API     │
         └───────────────────┘       └───────────────────┘       └───────────────────┘
```

- **API Layer (`server/routes/`):** FastAPI routers managing authentication, email operations, agent invocation, and pending action approvals.
- **Agent Orchestrator (`agents.py`):** Multi-turn Gemini agent dynamically binding authorized Google Workspace tool callables via `functools.partial`.
- **Tool Suite (`tools/`):** Isolated integrations for Gmail, Google Calendar, and Google Tasks.
- **Data & State (`server/models.py`, `server/redis_cache.py`):** PostgreSQL stores OAuth connection state, pending-action state, and durable notification jobs; Redis provides best-effort cache/state support. See the runbook for credential-migration caveats.

---

## Prerequisites

- **Python 3.12+**
- **PostgreSQL 14+**
- **Docker & Docker Compose** (for running Redis or containerized application)
- **Google Cloud Platform Project** with the following APIs enabled:
  - Gmail API
  - Google Calendar API
  - Google Tasks API
  - Cloud Pub/Sub API
  - Vertex AI API / Google GenAI API

### Required Google Credentials

The owner supplies these through read-only local or secret-manager mounts; never commit them or add them to a container image:

| File | Description |
|------|-------------|
| `onebox_oauth.json` | Google OAuth 2.0 Web Client credentials (client ID, client secret, redirect URIs) |
| `executive-agent.json` | Google Cloud service account key with Pub/Sub and Vertex AI permissions |

---

## Quick Start

### 1. Clone and Set Up Environment

```bash
git clone https://github.com/HarshilMaks/onebox.git
cd onebox

python3.12 -m venv .venv
source .venv/bin/activate

make install
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your PostgreSQL database connection, JWT secret, and Google Cloud parameters.

### 3. Start Redis

Start Redis using the included helper script:

```bash
bash scripts/redis_setup.sh
```

Or run it via Docker directly:

```bash
docker run -d --name redis -p 6379:6379 redis:latest
```

### 4. Run Database Migrations

Apply database migrations using Alembic:

```bash
alembic upgrade head
```

### 5. Start the Server

```bash
make run
```

The server will start at `http://0.0.0.0:8000`.

> [!TIP]
> Swagger UI documentation is available at `http://localhost:8000/docs` and ReDoc at `http://localhost:8000/redoc`.

---

## Configuration

The typed settings loader rejects unknown names and resolves relative credential
paths from the repository root. `.env.example` is the complete commented
reference; `docs/OPERATIONS.md#configuration-contract` explains production
mounting and operational consequences. Use placeholders and secret-manager
mounts only, never real values in tracked files.

| Role or feature | Required settings |
| --- | --- |
| Every process | `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE`, `GOOGLE_OAUTH_CLIENT_SECRETS`, `OAUTH_REDIRECT_URI`, `FRONTEND_OAUTH_CALLBACK_URI`, `GOOGLE_PROJECT_ID`, `GOOGLE_LOCATION`, `GOOGLE_MODEL` |
| OAuth credential operations | `OAUTH_TOKEN_KEYRING_PATH` and `OAUTH_TOKEN_ACTIVE_KEY_ID`; the application fails closed without both. |
| `automation_worker` / local `combined` | `AUTOMATION_ENABLED=true`, `AUTOMATION_OWNER_ID`, `PUBSUB_TOPIC`, `PUBSUB_SUBSCRIPTION`, `PUBSUB_PUSH_AUDIENCE`, `PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL` |
| Pending-action reconciliation | `PENDING_ACTION_OPERATOR_IDS` with authorized authenticated user UUIDs |
| Production transport | PostgreSQL TLS and authenticated `rediss://`, unless a controlled private network explicitly sets `REDIS_TRUSTED_LOCAL_NETWORK=true` |
| Compose only | `POSTGRES_PASSWORD`, read-only OAuth/service-account/keyring host paths, and `OAUTH_TOKEN_ACTIVE_KEY_ID` |

Production supports `api` and `automation_worker`; `combined` is a local-only
convenience role. Automation is disabled by default and supports one configured
mailbox. See the runbook before enabling it.

---

## API Reference

All protected endpoints require an `Authorization: Bearer <JWT_TOKEN>` header.
The generated, backend-owned contract is [`docs/openapi.json`](docs/openapi.json);
run `scripts/generate_openapi.py --check` to detect drift. Agent streaming is
`text/event-stream`, with one JSON payload per non-comment SSE frame.

### Action boundary

Direct authenticated `/mail` send, delete, draft, and mailbox-mutation endpoints
are human API operations; they are not agent tools. Agent sends/replies,
calendar-event creation, and task creation create pending actions and require
explicit approval. Successful external effects report `succeeded`; ambiguous
writes remain `reconciliation_required` for an authorized operator rather than
being blindly retried.

### 1. Health, Liveness, and Readiness

```http
GET /livez HTTP/1.1
Host: localhost:8000
```

`/livez` reports dependency-free process liveness. `/readyz` returns `503` unless
PostgreSQL is reachable and migrated to the Alembic head, role-required Redis is
reachable, and local combined automation has a valid persisted worker state.

**Liveness response (200 OK):**
```json
{
  "status": "ok"
}
```

### 2. Invoke Executive Agent

Executes conversational planning or triggers tools (email drafting, calendar scheduling, task creation):

```http
POST /executive/ HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json

{
  "input": "Schedule a 45-minute sync with sarah@example.com tomorrow at 3pm to review sprint goals"
}
```

**Response (200 OK):**
```json
{
  "result": "✓ Scheduled 'Sync to review sprint goals' for 15-09-2026 15:00 and added reminder task."
}
```

### 3. Approve Pending Action

Executes a staged action (such as sending an email or updating an event):

```http
POST /actions/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/approve HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
```

**Response (200 OK):**
```json
{
  "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "action_type": "send_email",
  "status": "succeeded",
  "summary": "Send email to sarah@example.com regarding Sprint Goals",
  "result": {
    "message_id": "18f67bc82a1"
  }
}
```

### 4. Fetch Inbox Emails

Retrieves paginated emails with inline CID images automatically converted to browser-safe data URIs:

```http
GET /mail/emails?folder=inbox&limit=10 HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
```

**Response (200 OK):**
```json
{
  "emails": [
    {
      "id": "18f67bc82a1",
      "subject": "Q3 Planning Meeting",
      "sender": "sarah@example.com",
      "to": ["user@example.com"],
      "snippet": "Can we meet tomorrow to discuss roadmap priorities?",
      "is_read": false,
      "is_starred": true,
      "labels": ["INBOX", "UNREAD", "STARRED"]
    }
  ],
  "next_page_token": "0982347102934"
}
```

---

## Docker Deployment

The supplied Compose topology starts a one-shot migration service, the FastAPI
`api` service, and one dedicated durable Gmail `automation_worker` for the
single configured automation mailbox.
The API owns authenticated Pub/Sub ingress; the worker runs
`python -m server.workers`, renews the Gmail watch, and claims PostgreSQL jobs.

Set the normal application settings plus these Compose-only mount paths before
starting it. Credential files and the OAuth token keyring remain on the host and
are mounted read-only; they are not baked into the image:

```bash
export GOOGLE_OAUTH_CLIENT_SECRETS_HOST_PATH="$PWD/onebox_oauth.json"
export GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH="$PWD/executive-agent.json"
export OAUTH_TOKEN_KEYRING_HOST_PATH="$PWD/onebox-oauth-token-keyring.json"
export OAUTH_TOKEN_ACTIVE_KEY_ID='<active-key-id>'
export AUTOMATION_OWNER_ID='<automation-user-uuid>'
export PUBSUB_TOPIC='projects/PROJECT/topics/gmail-notifications'
export PUBSUB_SUBSCRIPTION='projects/PROJECT/subscriptions/gmail-notifications'
export PUBSUB_PUSH_AUDIENCE='https://api.example.com/mail/notifications'
export PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL='push@PROJECT.iam.gserviceaccount.com'
make compose-config
make compose-up
```

`make compose-up` waits for PostgreSQL, applies Alembic migrations, then starts
both the API and worker. The unauthenticated `/livez` route is process liveness only.
The unauthenticated `/readyz` route verifies PostgreSQL schema readiness and
role-required Redis without making Google calls. The authenticated
`/mail/agent/health` route reports Gmail readiness only when a
valid unexpired watch and a fresh worker heartbeat exist. `/mail/agent/status`
contains safe queue and recovery diagnostics.

If bounded history recovery reaches `GMAIL_RESYNC_MAX_MESSAGES`, automation
enters `manual_required` and deliberately does not advance its Gmail cursor. This
prevents silently skipping mail; investigate the safe status endpoint and recover
under an explicit operator procedure before resuming automation.

For migration, readiness, watch renewal, manual recovery, pending-action
reconciliation, and rollback procedures, follow
[`docs/OPERATIONS.md`](docs/OPERATIONS.md). Do not issue Gmail `users.stop` as
part of a normal deployment or rollback.

To view application logs or stop the stack:

```bash
docker compose logs -f app worker
docker compose down
```

---

## Project Structure

```
onebox/
├── server/
│   ├── agent_policy.py      # Immutable per-run agent authorization policy
│   ├── agent_tools.py       # Gemini declarations and trusted tool bindings
│   ├── integrations/        # Google, Gmail, LLM, and Redis adapters
│   ├── mail/                # MIME parsing and inbound notification triage
│   ├── routes/              # HTTP endpoints and Pub/Sub ingress
│   ├── services/            # Mailbox use cases and durable action services
│   ├── workers/             # Durable Gmail notification worker
│   ├── models.py            # SQLAlchemy database models
│   ├── database.py          # Sole metadata base, engine, and session factory
│   ├── redis_cache.py       # Redis caching utilities
│   └── main.py              # FastAPI application entrypoint and lifespan
├── tools/                   # Agent tool implementations
│   ├── llm_tools.py         # Pending-action and interactive Google tool callables
│   └── utils.py             # Shared raw-message and header helpers
├── clients/                 # LLM client abstractions & system prompts
│   ├── base.py              # Vertex AI & GenAI client setup
│   └── prompt.py            # Executive & email agent system instructions
├── alembic/                 # Database migrations
├── scripts/                 # Utility scripts (e.g., redis_setup.sh)
├── Dockerfile               # Multi-stage production container image
├── docker-compose.yaml      # Multi-container orchestration (App + Redis)
├── Makefile                 # Common developer workflow targets
└── user_config.yaml         # User profile, triage rules, & preferences
```

### Ingress limits

The API rejects oversized or malformed request data before provider dispatch: agent
prompts are capped at 8,000 characters; Gmail search queries at 512 characters;
mail page sizes at 100; OAuth callback code/state values at 4,096/512 characters;
and Pub/Sub envelopes at `PUBSUB_MAX_ENVELOPE_BYTES` (65,536 bytes by default).
Recipient lists, subjects, bodies, page tokens, and provider IDs are also bounded.
Enforce request-rate limits at the authenticated deployment gateway/ingress; the
application does not implement a second, divergent in-process rate limiter.


### Mail content cache policy

Mail detail bodies are cached for **5 minutes**; paginated folders and search
pages are cached for **60 seconds**. Cache entries are JSON-only and fail open:
a Redis outage or corrupt value is treated as a cache miss and does not prevent a
Gmail request. Each mail mutation atomically advances that user's cache
generation instead of scanning wildcard keys. Older-generation entries may
remain in Redis only until their normal TTL expires, but cannot be selected by
new requests after a successful generation advance. The cache is not a durable
mail-retention store.


### Agent execution policy

Agent tool access is a server-owned per-run allowlist, not a prompt capability.
Automated inbound Gmail triage exposes **zero tools**. Interactive executive and
streaming runs can use only the tools authorized by their authenticated route and
available connected account services, and accept at most one primary mutation per
request. Agent email sends/replies, calendar events, and task creation always
create typed pending actions for explicit approval; they never perform live
provider writes directly. Draft creation and marking a message read are the two
intentional immediate **interactive-only** mutations and are unavailable to
inbound automation. Direct `/mail/send` and deletion endpoints are separate API
operations and are not part of the agent tool registry.

Agent prompts render current time per invocation using an IANA `ZoneInfo`
timezone from the deployment profile. SSE agent streams are nonblocking,
disconnect-cancellable, deadline/queue-bounded, emit heartbeat comment frames,
and finish with exactly one `done` or `error` event; they do not support replay.


### Provider retry, identity, and retention policy

Google provider failures are classified as permanent, authentication/reconnect,
quota, retryable transport/read, ambiguous write, or internal. Only read
operations and explicitly idempotent mutations use bounded exponential backoff
with jitter and a provider `Retry-After` floor. Email sends, replies, calendar
creation, task creation, deletion, and other ambiguous writes are dispatched
once; an uncertain outcome remains `reconciliation_required` and is never
blind-retried. Gmail star updates accept an explicit desired `starred` state,
not a read-then-toggle operation.

The persisted case-normalized Google email is the authoritative connected
account and sender identity. OneBox deliberately fails closed when a selected
or persisted account email changes; it does not silently reuse credentials for
a renamed/different account. Legacy account rows are normalized during safe
credential loading or OAuth persistence.

Terminal pending-action payloads are retained for **30 days**; terminal Gmail
notification jobs and triage summaries for **14 days**. Reconciliation-required
actions are retained for operator resolution. The durable worker performs the
configured periodic cleanup. Mail-cache retention remains limited to its 5
minute detail and 60 second page TTLs. In production PostgreSQL must require
TLS, and Redis must be authenticated and use `rediss://` unless explicitly
configured as a controlled trusted local network.
