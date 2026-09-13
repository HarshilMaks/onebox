# OneBox

> Production-grade asynchronous AI agent orchestration platform integrating Google Workspace (Gmail, Calendar, Tasks) with Gemini models, durable Pub/Sub workers, and human-in-the-loop action governance.

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-336791.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7+-DC382D.svg)](https://redis.io/)
[![Contract](https://img.shields.io/badge/OpenAPI-v3.1.0-green.svg)](docs/openapi.json)

[Overview](#overview) • [Features](#features) • [Architecture & Roles](#architecture--service-roles) • [Prerequisites](#prerequisites) • [Quick Start](#quick-start) • [Configuration](#configuration-reference) • [API & Event Streams](#api--event-stream-reference) • [Operational Policies](#operational--security-policies) • [Docker Deployment](#docker-deployment) • [Testing & Verification](#testing--release-gates) • [Project Structure](#project-structure)

---

## Overview

Managing high-volume executive communication, meeting scheduling, and task coordination requires continuous context switching and carries high operational risk if automated blindly.

**OneBox** provides a resilient, secure foundation for AI-assisted executive operations. It pairs Google Gemini models with Google Workspace APIs (Gmail, Google Calendar, Google Tasks) while enforcing strict operational boundaries:

1. **Interactive Agent Planning:** Users engage with executive and streaming agents to inspect schedules, search messages, and compose plans. Any mutation with an external side effect (sending an email, creating a calendar event, adding a task) is staged as an immutable, typed **pending action** requiring explicit user approval.
2. **Durable Inbound Automation:** An asynchronous background worker receives authenticated Google Cloud Pub/Sub push notifications for a configured mailbox, acquiring singleton PostgreSQL leases and recovering message history safely. Inbound automation operates with **zero tool permissions**—it analyzes incoming messages according to user triage rules without autonomously modifying mail or issuing writes.
3. **Enterprise Resilience & Governance:** Built with encrypted OAuth token keyrings, classified provider retry policies, generational Redis caching, and operator reconciliation workflows for uncertain provider writes.

---

## Features

- **Human-in-the-Loop Safeguards:** External mutations (email sends/replies, calendar events, tasks) are staged as immutable pending actions in PostgreSQL; no destructive effect executes without explicit authorization (`POST /actions/{action_id}/approve`).
- **Durable Gmail Pub/Sub Worker:** Scalable background notification daemon with PostgreSQL row-level locks, bounded lease recovery, automatic watch renewal, and safe history resynchronization.
- **Strict Separation of Concerns:** Inbound automated triage is decoupled from interactive agent execution. Automated triage has zero tools, preventing unauthorized automated replies or state changes.
- **Encrypted OAuth Keyring:** User tokens are protected at rest with AES-256-GCM using an active key ID from a mounted, read-only JSON keyring (`OAUTH_TOKEN_KEYRING_PATH`).
- **Classified Provider Retries:** Google API requests are categorized into permanent, authentication, quota, retryable transport/read, and ambiguous write. Ambiguous writes are dispatched once and never blind-retried.
- **Operator Reconciliation Workflow:** Ambiguous external writes enter a `reconciliation_required` state, allowing authorized operators to inspect deterministic provider markers and resolve state safely.
- **Generational Caching:** Redis caching for mail details (5-minute TTL) and folder/search pages (60-second TTL) using atomic per-user generation keys to invalidate stale data without wildcard key scans.
- **Multi-Role Process Topology:** First-class support for separate `api`, `automation_worker`, `migrate`, and local `combined` service roles.
- **Migration-Aware Health Checks:** Dedicated dependency-free `/livez` endpoint alongside `/readyz` that verifies schema migration heads and Redis connectivity without making external Google calls.

---

## Architecture & Service Roles

OneBox isolates API ingress, durable background processing, database migrations, and external provider integrations into distinct, single-responsibility components:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            FastAPI Application                              │
│         (/mail, /executive, /generate-stream, /actions, /agent, /readyz)    │
└──────────────────────┬───────────────────────────────┬──────────────────────┘
                       │                               │
                User JWT & OAuth              Pub/Sub Push Ingress
                       │                               │
        ┌──────────────▼──────────────┐ ┌──────────────▼──────────────┐
        │   PostgreSQL 16 (Async)     │ │   PostgreSQL Job Queue      │
        │ (Tokens, Actions, Audits)   │ │ (Durable Leases & Backoff)  │
        └──────────────┬──────────────┘ └──────────────┬──────────────┘
                       │                               │
                       │                        Worker Singleton Lease
                       │                               │
        ┌──────────────▼──────────────┐ ┌──────────────▼──────────────┐
        │       Interactive Agent     │ │   Durable Worker Daemon     │
        │   (Gemini + Staged Tools)   │ │   (mail_notifications.py)   │
        └──────────────┬──────────────┘ └──────────────┬──────────────┘
                       │                               │
                       │ Staged Pending Actions        │ Read-Only Triage
                       │ (Explicit User Approval)      │ (Zero Tools Allowed)
                       ▼                               ▼
        ┌─────────────────────────────────────────────────────────────┐
        │             Google Workspace APIs & Adapters                │
        │            (Gmail API, Calendar API, Tasks API)             │
        └─────────────────────────────────────────────────────────────┘
```

### Supported Process Roles

| Role | Target Command | Primary Responsibilities |
| --- | --- | --- |
| `api` | `uvicorn server.main:app` | Serves REST/SSE routes, authenticates user JWTs, handles OAuth flows, accepts Pub/Sub webhook pushes, and manages pending action approvals. |
| `automation_worker` | `python -m server.workers` | Claims pending notification jobs from PostgreSQL, executes inbox triage with zero tools, renews Gmail push watches, and runs retention cleanups. |
| `combined` | `uvicorn server.main:app` | **Local development only**. Runs API routes and in-process automation worker tasks concurrently. |
| `migrate` | `alembic upgrade head` | One-shot migration container running schema upgrades prior to API or worker boot. |

---

## Prerequisites

- **Python 3.12+**
- **PostgreSQL 16+** (with `pg_isready` support and TLS in production)
- **Redis 7+** (`redis://` for development, authenticated `rediss://` for production)
- **Docker & Docker Compose** (for containerized deployments)
- **Google Cloud Platform Project** with active APIs:
  - Gmail API (`gmail.modify`, `mail.google.com`)
  - Google Calendar API (`calendar`)
  - Google Tasks API (`tasks`)
  - Cloud Pub/Sub API
  - Vertex AI API / Google GenAI API

### Required Secret Files

Mount these files read-only via secret management; never commit them to version control or bake them into container images:

| File | Purpose | Default Host Path |
| --- | --- | --- |
| `onebox_oauth.json` | Google OAuth 2.0 Web Client configuration (Client ID & Secret). | `GOOGLE_OAUTH_CLIENT_SECRETS_HOST_PATH` |
| `executive-agent.json` | Google Cloud service account credentials with Pub/Sub and Vertex AI roles. | `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH` |
| `onebox-oauth-token-keyring.json` | AES-256-GCM JSON keyring mapping key IDs to base64url 32-byte encryption keys. | `OAUTH_TOKEN_KEYRING_HOST_PATH` |

---

## Quick Start

### 1. Clone Repository & Setup Environment

```bash
git clone https://github.com/HarshilMaks/onebox.git
cd onebox

# Create virtual environment and install verified pinned dependencies
make install
source .venv/bin/activate
```

> [!NOTE]
> `make install` uses `pip install --require-hashes -r requirements-dev.txt` to enforce hash verification across all dependencies.

### 2. Configure Environment

```bash
cp .env.example .env
```

Configure `.env` with your PostgreSQL connection, Redis URL, JWT secrets, and Google credentials. For local development, minimum requirements are:
- `DATABASE_URL=postgresql+asyncpg://onebox:password@localhost:5432/onebox`
- `REDIS_URL=redis://localhost:6379/0`
- `SECRET_KEY=<32-byte-hex-secret>`
- `GOOGLE_PROJECT_ID=<gcp-project-id>`
- `OAUTH_TOKEN_KEYRING_PATH=./onebox-oauth-token-keyring.json`
- `OAUTH_TOKEN_ACTIVE_KEY_ID=<key-id>`

### 3. Start Local Redis

```bash
bash scripts/redis_setup.sh
```

### 4. Run Database Migrations

```bash
.venv/bin/alembic upgrade head
```

### 5. Launch Application Services

To start the API in development mode with hot reload:

```bash
make run-dev
```

To run the background automation worker:

```bash
make run-worker
```

Interactive OpenAPI documentation is accessible at `http://localhost:8000/docs`.

---

## Configuration Reference

The application uses typed Pydantic settings (`server/config.py`) that fail fast on unknown or malformed configuration keys. Relative paths resolve from the repository root.

### Core & Role Configuration

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `ENVIRONMENT` | No | `development` | Environment mode: `development`, `test`, `staging`, or `production`. |
| `SERVICE_ROLE` | No | `api` | Process role: `api`, `automation_worker`, or `combined`. |
| `AUTOMATION_ENABLED` | No | `false` | Enables durable Pub/Sub worker and background triage processing. |
| `AUTOMATION_OWNER_ID` | If worker | `None` | UUID of the single OneBox user whose mailbox is monitored by automation. |
| `PENDING_ACTION_OPERATOR_IDS` | No | `""` | Comma-separated list of authenticated user UUIDs authorized to run action reconciliation. |

### Persistence & Transport

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | Yes | - | Async PostgreSQL URI (`postgresql+asyncpg://user:pass@host:5432/db`). Requires TLS in production. |
| `REDIS_URL` | Yes | - | Redis connection URL (`redis://` or `rediss://`). Requires password and TLS in production. |
| `DATABASE_POOL_SIZE` | No | `5` | Core connection pool size for SQLAlchemy. |
| `DATABASE_MAX_OVERFLOW` | No | `5` | Maximum overflow connections for SQLAlchemy. |
| `REDIS_TRUSTED_LOCAL_NETWORK` | No | `false` | Allows unencrypted Redis in production only if operating inside a private VPC. |

### Authentication & Token Security

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `SECRET_KEY` | Yes | - | Secret key used for HS256 JWT signature verification (minimum 32 bytes). |
| `JWT_ISSUER` | Yes | - | Expected JWT issuer claim (`iss`). |
| `JWT_AUDIENCE` | Yes | - | Expected JWT audience claim (`aud`). |
| `OAUTH_TOKEN_KEYRING_PATH` | If OAuth | `None` | Filepath to the read-only JSON keyring used for AES-256-GCM token encryption. |
| `OAUTH_TOKEN_ACTIVE_KEY_ID` | If OAuth | `None` | Active key identifier in the keyring for encrypting new/refreshed tokens. |
| `GOOGLE_OAUTH_CLIENT_SECRETS`| Yes | `onebox_oauth.json` | Path to Google OAuth 2.0 Web Client secrets JSON file. |
| `OAUTH_REDIRECT_URI` | Yes | - | Backend OAuth callback redirect URI (HTTPS required in production). |
| `FRONTEND_OAUTH_CALLBACK_URI`| Yes | - | Frontend URL to redirect the user after OAuth completion. |

### Google Cloud & Automation Worker

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `GOOGLE_PROJECT_ID` | Yes | - | Google Cloud Platform project identifier. |
| `GOOGLE_LOCATION` | Yes | `us-central1` | Google Cloud region for Vertex AI endpoints. |
| `GOOGLE_MODEL` | Yes | `gemini-2.0-flash-lite` | Gemini model name used by agent implementations. |
| `PUBSUB_TOPIC` | If worker | `None` | Full GCP Pub/Sub topic path (`projects/{p}/topics/{t}`). |
| `PUBSUB_SUBSCRIPTION` | If worker | `None` | Full GCP Pub/Sub subscription path (`projects/{p}/subscriptions/{s}`). |
| `PUBSUB_PUSH_AUDIENCE` | If worker | `None` | Expected audience in the Google-signed OIDC push authorization token. |
| `PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL` | If worker | `None` | Authorized service account email delivering Pub/Sub push requests. |
| `GMAIL_RESYNC_MAX_MESSAGES` | No | `100` | Maximum messages recovered during history resync before entering `manual_required`. |

---

## API & Event Stream Reference

All authenticated endpoints require an `Authorization: Bearer <JWT_TOKEN>` header with matching `JWT_ISSUER` and `JWT_AUDIENCE`. The source of truth for the HTTP API contract is [`docs/openapi.json`](docs/openapi.json).

### 1. Health, Liveness, and Readiness

```http
GET /livez HTTP/1.1
Host: localhost:8000
```
Returns `200 OK` if the process is up.

```http
GET /readyz HTTP/1.1
Host: localhost:8000
```
Returns `200 OK` if PostgreSQL is reachable and migrated to the latest Alembic revision, and Redis is responsive. Makes zero external Google calls.

**Readiness Response (200 OK):**
```json
{
  "status": "ready",
  "database": "connected",
  "migration_head": "e84c0a9b6d21",
  "redis": "connected"
}
```

### 2. Gmail Automation Diagnostics

```http
GET /mail/agent/health HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
```
Reports automation health; returns `200 OK` only if a valid unexpired Gmail watch and fresh worker heartbeat exist.

```http
GET /mail/agent/status HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
```
Provides safe diagnostic telemetry on queue depths, lease states, and history resync status without exposing message content.

### 3. Interactive Executive Agent

Executes multi-turn Gemini reasoning with strict read-only tool inspection:

```http
POST /executive/ HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json

{
  "input": "Schedule a 45-minute sync with sarah@example.com tomorrow at 3pm to review roadmap items"
}
```

**Response (200 OK):**
```json
{
  "result": "I have verified your availability and staged a calendar event for tomorrow at 3:00 PM. Please review and approve pending action 9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d."
}
```

### 4. Agent Event Streaming (SSE)

Streams real-time agent thoughts and structured events via Server-Sent Events (`text/event-stream`):

```http
POST /generate-stream/ HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json

{
  "input": "Summarize my unread emails from this morning"
}
```

**SSE Stream Output:**
```
data: {"event": "status", "content": "Querying inbox..."}

data: {"event": "chunk", "content": "You have 3 unread messages..."}

data: {"event": "done", "content": ""}
```

### 5. Approve Pending Action

Executes a staged action after user verification:

```http
POST /actions/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/approve HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
```

**Response (200 OK):**
```json
{
  "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "action_type": "create_event",
  "status": "succeeded",
  "summary": "Create Calendar Event: Sync to review roadmap items",
  "result": {
    "event_id": "c198a28f7e2a9b"
  }
}
```

### 6. Reconcile Ambiguous Action

Authorized operators can investigate and resolve uncertain provider mutations:

```http
POST /actions/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/reconcile HTTP/1.1
Host: localhost:8000
Authorization: Bearer <JWT_TOKEN>
```

**Response (200 OK):**
```json
{
  "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "status": "succeeded",
  "reconciliation_evidence": {
    "discovered_provider_id": "c198a28f7e2a9b",
    "verified_at": "2026-09-11T19:30:00Z"
  }
}
```

---

## Operational & Security Policies

### Ingress & Payload Bounds
To prevent resource exhaustion and Denial of Service, the API enforces strict input ceilings before dispatching work to external providers:
- **Agent Prompts:** Capped at 8,000 characters.
- **Gmail Search Queries:** Capped at 512 characters.
- **Mail Pagination Limits:** Capped at 100 messages per page.
- **Pub/Sub Push Envelopes:** Capped at `PUBSUB_MAX_ENVELOPE_BYTES` (64 KB default).
- **OAuth Callback State & Code:** Capped at 512 and 4,096 characters respectively.

### Generational Mail Cache Policy
Mail detail bodies are cached for **5 minutes**; folder views and search results are cached for **60 seconds**.
- **Atomic Generation Invalidation:** Each mail state mutation (marking read, moving to trash, starring) atomically advances the user's cache generation integer.
- **No Wildcard Scans:** Prevents blocking Redis with dangerous `KEYS *` operations.
- **Fail-Open Behavior:** A Redis outage or serialization failure is treated as a cache miss and does not block mail fetching.

### Agent Execution & Tool Allowlist
Tool access is governed by an immutable per-run server allowlist:
- **Automated Inbound Triage:** Exposes **zero tools**. Inbound automation only records structured triage metadata and never alters provider state.
- **Interactive Agents:** May only access tools explicitly granted by route configuration and user service connections.
- **Staged External Effects:** Sends, replies, event creations, and task insertions stage pending actions; they never perform live external writes directly.
- **Immediate Interactive Tools:** Only `create_draft` and `mark_as_read` execute immediately during interactive sessions.

### Provider Retry, Identity & Retention Policy
External Google API failures are classified into granular categories:
- **Safe Retries:** Reads and idempotent writes execute with bounded exponential backoff, jitter, and a provider `Retry-After` floor.
- **Single Dispatch:** Non-idempotent writes are executed exactly once. Ambiguous results transition to `reconciliation_required`.
- **Authoritative Identity:** Persisted case-normalized Google email addresses define account ownership. OneBox fails closed if credentials point to a renamed or mismatched account.
- **Data Retention:** Terminal pending actions are retained for **30 days**; terminal notification jobs and triage logs are retained for **14 days**. Expired rows are purged periodically by the worker.

---

## Docker Deployment

OneBox provides a production-grade multi-container topology via `docker-compose.yaml` consisting of `postgres`, `redis`, a one-shot `migrate` service, the FastAPI `app`, and the background `worker`:

```bash
# Verify Compose configuration syntax
make compose-config

# Launch all services in background
make compose-up
```

Credential and keyring files are mounted read-only from the host into `/run/secrets/`:

```bash
export GOOGLE_OAUTH_CLIENT_SECRETS_HOST_PATH="$PWD/onebox_oauth.json"
export GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH="$PWD/executive-agent.json"
export OAUTH_TOKEN_KEYRING_HOST_PATH="$PWD/onebox-oauth-token-keyring.json"
export OAUTH_TOKEN_ACTIVE_KEY_ID="active-key-id"
export POSTGRES_PASSWORD="secure-postgres-password"
export SECRET_KEY="secure-jwt-secret-key"
export GOOGLE_PROJECT_ID="your-gcp-project-id"

make compose-up
```

To tail logs from the application and worker:

```bash
docker compose logs -f app worker
```

To gracefully shut down services:

```bash
make compose-down
```

> [!IMPORTANT]
> Detailed procedures for rolling upgrades, safe rollbacks, Gmail watch renewal, and pending-action reconciliation are documented in the [Backend Operations Runbook](docs/OPERATIONS.md).

---

## Testing & Release Gates

All backend changes must pass the automated release gate before deployment:

```bash
# Execute full suite: Ruff linting, OpenAPI drift check, and Pytest
make check
```

Individual developer workflows:

```bash
# Run Ruff lint checks
make lint

# Run Pytest unit and integration test suite
make test

# Regenerate OpenAPI schema contract
make contract

# Verify OpenAPI schema has not drifted
make contract-check

# Verify container security: non-root execution and zero secret leaks
make image-smoke
```

---

## Project Structure

```
onebox/
├── server/
│   ├── main.py                  # FastAPI application entrypoint & lifespan
│   ├── config.py                # Typed settings, validation, and role definitions
│   ├── database.py              # Async SQLAlchemy engine, sessionmaker, and Base
│   ├── models.py                # Database models (Tokens, Actions, Jobs, Watches)
│   ├── redis_cache.py           # Generational Redis cache client and utilities
│   ├── agent_policy.py          # Immutable per-run agent authorization policy
│   ├── agent_tools.py           # Gemini function declarations & trusted tool bindings
│   ├── mail/
│   │   ├── mime.py              # RFC 822 / MIME parsing & CID inline image decoding
│   │   └── inbound.py           # Inbound notification triage & rule evaluation
│   ├── integrations/
│   │   ├── google.py            # Classified Google API retry & backoff wrapper
│   │   ├── gmail.py             # Gmail resource builders & batch operations
│   │   └── redis.py             # Redis connection pools and concurrency limits
│   ├── routes/
│   │   ├── agent_oauth.py       # Google OAuth 2.0 PKCE / state flows
│   │   ├── agent_router.py      # Executive/streaming agents & pending action lifecycle
│   │   ├── google_mail.py       # User mail fetch, search, star, and delete endpoints
│   │   └── push_router.py       # Authenticated Google Pub/Sub push receiver
│   ├── services/
│   │   ├── pending_actions.py   # Pending action staging, claiming, and reconciliation
│   │   ├── action_handlers.py   # Executable handlers for approved pending actions
│   │   ├── notification_jobs.py # Durable notification queue storage & leases
│   │   ├── mailbox.py           # User-facing mailbox fetch and cache services
│   │   ├── readiness.py         # Migration and persistence health verifiers
│   │   ├── retention.py         # Terminal record pruning routines
│   │   └── setup_google.py      # Authenticated Google Workspace service factories
│   └── workers/
│       ├── __main__.py          # Worker entrypoint (`python -m server.workers`)
│       └── mail_notifications.py# Durable Pub/Sub notification worker & watch renewal
├── tools/
│   ├── llm_tools.py             # Interactive agent tools & pending action generators
│   └── utils.py                 # RFC 2822 message encoders and header utilities
├── clients/
│   ├── base.py                  # Vertex AI / GenAI client initializers
│   └── prompt.py                # Executive and email agent system prompts
├── tests/
│   ├── unit/                    # Fast isolated unit tests
│   └── integration/             # Database and notification queue integration tests
├── docs/
│   ├── openapi.json             # Generated, backend-owned OpenAPI contract
│   └── OPERATIONS.md            # Production runbook, watch renewal, and recovery
├── alembic/                     # Database migrations
├── scripts/
│   ├── generate_openapi.py      # Tool to generate/check OpenAPI schema drift
│   └── redis_setup.sh           # Local developer Redis provisioning script
├── Dockerfile                   # Multi-stage hardened non-root container image
├── docker-compose.yaml          # Multi-container orchestration (App, Worker, DB, Redis)
├── Makefile                     # Common development, testing, and release targets
└── user_config.yaml             # Triage rules, schedule preferences, and user context
```

