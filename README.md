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

**OneBox** operates as an autonomous digital assistant directly integrated with Google Workspace accounts (Gmail, Google Calendar, Google Tasks). Powered by Google Gemini models with structured function calling, OneBox listens for incoming mail via real-time Google Cloud Pub/Sub push notifications, triages messages against user preferences, prepares contextual draft replies, and schedules calendar events. For destructive or sensitive actions, OneBox implements a secure human-in-the-loop pending actions workflow.

---

## Features

- **Automated Mail Triaging:** Evaluates incoming emails against customizable rules, automatically marking promotional or no-reply emails as read while escalating actionable threads.
- **Calendar & Meet Scheduling:** Parses natural language time requests, checks existing calendar availability, creates Google Calendar events, and generates Google Meet conference links.
- **Task Synchronization:** Automatically creates and updates follow-up action items in Google Tasks tied to scheduled events and commitments.
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
│  (Tokens & Pending Actions) │ │    (mail.py triage loop)    │
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
- **Data & State (`server/models.py`, `server/redis_cache.py`):** PostgreSQL database storing encrypted OAuth tokens and pending action states, supplemented by Redis for payload caching.

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

Create or place these files in the project root:

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

The application is configured through environment variables in `.env`:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | - | Async PostgreSQL connection string (`postgresql+asyncpg://...`) |
| `SECRET_KEY` | Yes | - | Secret key used to sign and verify JWT tokens (`openssl rand -hex 32`) |
| `ALGORITHM` | No | `HS256` | JWT signing algorithm |
| `GOOGLE_OAUTH_CLIENT_SECRETS` | No | `onebox_oauth.json` | Path to Google OAuth client secrets file |
| `OAUTH_REDIRECT_URI` | Yes | - | Backend OAuth callback endpoint (e.g. `https://api.example.com/agent/oauth/callback`) |
| `FRONTEND_OAUTH_CALLBACK_URI` | Yes | - | Frontend URI redirected to after successful OAuth exchange |
| `PUBSUB_TOPIC` | Yes | - | Full Google Cloud Pub/Sub topic string for mailbox notifications |
| `PUBSUB_SUBSCRIPTION` | Yes | - | Google Cloud Pub/Sub subscription string |
| `GOOGLE_APPLICATION_CREDENTIALS` | No | `executive-agent.json` | Path to Google service account credentials file |
| `PUBSUB_PUSH_AUDIENCE` | No | `""` | Target audience URL configured on the Pub/Sub push subscription |
| `PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL` | No | `""` | Service account email authorized to deliver push notifications |
| `CORS_ALLOWED_ORIGINS` | No | `""` | Comma-separated list of permitted frontend browser origins |

> [!IMPORTANT]
> The Pub/Sub push endpoint `/mail/notifications` validates Google OIDC identity tokens when `PUBSUB_PUSH_AUDIENCE` and `PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL` are configured. Ensure these match your GCP Pub/Sub push subscription settings.

---

## API Reference

All protected endpoints require an `Authorization: Bearer <JWT_TOKEN>` header.

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
  "status": "completed",
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

The supplied Compose topology starts three application roles in order: a one-shot
migration service, the FastAPI API service, and a dedicated durable Gmail worker.
The API owns authenticated Pub/Sub ingress; the worker runs
`python -m server.workers`, renews the Gmail watch, and claims PostgreSQL jobs.

Set the normal application settings plus these Compose-only mount paths before
starting it. The credential files remain on the host and are mounted read-only;
they are not baked into the image:

```bash
export GOOGLE_OAUTH_CLIENT_SECRETS_HOST_PATH="$PWD/onebox_oauth.json"
export GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH="$PWD/executive-agent.json"
export AUTOMATION_OWNER_ID='00000000-0000-0000-0000-000000000000'
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
│   ├── routes/              # API endpoints
│   │   ├── agent_oauth.py   # Google OAuth start & callback flows
│   │   ├── agent_router.py  # Agent execution & pending action approval
│   │   ├── google_mail.py   # Gmail fetch, search, star, trash operations
│   │   └── push_router.py   # Google Pub/Sub push notification receiver
│   ├── services/            # Core business & background services
│   │   ├── mail.py          # Background email worker & triage pipeline
│   │   ├── pending_actions.py # Human-in-the-loop pending action store
│   │   └── setup_google.py  # Google API service builders & JWT auth
│   ├── models.py            # SQLAlchemy database models
│   ├── database.py          # Async database engine & session factory
│   ├── redis_cache.py       # Redis caching utilities
│   └── main.py              # FastAPI application entrypoint & lifespan
├── tools/                   # Agent tool implementations
│   ├── calender/            # Google Calendar tool & invite generators
│   ├── email/               # Gmail send, reply, and draft tools
│   ├── tasks/               # Google Tasks creation & list management
│   └── llm_tools.py         # Standardized tool declarations for Gemini
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
