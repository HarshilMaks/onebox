# Development guide

How to run, test and change OneBox on your own machine. The commands below were run once on 2026-11-07 and corrected; the worker was not started (it needs real Pub/Sub settings). For why things are built this way, start at [.lock/README.md](../.lock/README.md).

## Prerequisites

- **Python 3.12** (the Makefile looks for `python3.12` on your PATH)
- **PostgreSQL 16+** (with `pg_isready` support and TLS in staging and production)
- **Redis 7+** (`redis://` for development, authenticated `rediss://` for staging and production)
- **Docker & Docker Compose** (for containerized deployments)
- **Google Cloud Platform Project** with active APIs:
  - Gmail API (`gmail.modify`, `mail.google.com`; the broad `mail.google.com` scope is planned for removal, see [TASKS.md](../.lock/TASKS.md) 0.6)
  - Google Calendar API (`calendar`)
  - Google Tasks API (`tasks`)
  - Cloud Pub/Sub API
  - Vertex AI API / Google GenAI API

Secret files and every setting are in [CONFIGURATION.md](CONFIGURATION.md).

## Local setup, step by step

### 1. Install

```bash
git clone https://github.com/HarshilMaks/onebox.git
cd onebox
make install                  # needs python3.12 on PATH, or: make install PYTHON=/path/to/python3.12
source .venv/bin/activate
```

`make install` runs `pip install --require-hashes -r requirements-dev.txt`, so every dependency is hash-checked.

### 2. Start PostgreSQL and Redis

Compose's own `postgres` and `redis` services publish no host port, so for local development start two containers of your own:

```bash
docker run -d --name onebox-dev-pg -p 127.0.0.1:5432:5432 \
  -e POSTGRES_DB=onebox -e POSTGRES_USER=onebox -e POSTGRES_PASSWORD=<throwaway-password> \
  postgres:16.6-alpine
until docker exec onebox-dev-pg pg_isready -U onebox -d onebox; do sleep 1; done
docker run -d --name onebox-dev-redis -p 127.0.0.1:6379:6379 redis:7.4.2-alpine
```

`scripts/redis_setup.sh` starts Redis through Compose on Compose's private network only, so the API running on your host cannot reach it. Remove your containers later with `docker rm -f onebox-dev-pg onebox-dev-redis`.

### 3. Configure the environment

```bash
cp .env.example .env
```

`.env.example` already sets working local values for the JWT issuer/audience, OAuth redirect URIs, Google location and model, and CORS. Every setting without a default must stay present, and unknown names fail startup. Before the first run, change these:
- `DATABASE_URL`: replace `REPLACE_WITH_PASSWORD` with the password you gave the PostgreSQL container in step 2.
- `SECRET_KEY`: replace the placeholder (which is rejected at startup) with at least 32 bytes, for example `openssl rand -hex 32`.
- `GOOGLE_PROJECT_ID`: your Google Cloud project ID.
- `GOOGLE_OAUTH_CLIENT_SECRETS`: place your OAuth Web Client JSON at `onebox_oauth.json` in the repo root (it is gitignored), or point this at another path.
- `OAUTH_TOKEN_KEYRING_PATH` and `OAUTH_TOKEN_ACTIVE_KEY_ID`: uncomment both. Without them, Google connect and token loading fail closed. Create a local keyring outside version control (all `*.json` files are gitignored):

```bash
python3 -c 'import base64,json,os; print(json.dumps({"local-key-1": base64.urlsafe_b64encode(os.urandom(32)).decode()}))' > onebox-oauth-token-keyring.json
chmod 600 onebox-oauth-token-keyring.json
# then in .env:
# OAUTH_TOKEN_KEYRING_PATH=onebox-oauth-token-keyring.json
# OAUTH_TOKEN_ACTIVE_KEY_ID=local-key-1
```

### 4. Migrate the database

```bash
.venv/bin/alembic upgrade head
```

### 5. Run

```bash
make run-dev       # API with hot reload on port 8000; binds all interfaces, so use it locally only
make run-worker    # automation worker; needs the Pub/Sub settings in CONFIGURATION.md
```

Interactive API docs are at `http://localhost:8000/docs`. `GET /livez` returns 200 once the process is up. `GET /readyz` returns 200 when PostgreSQL is migrated and Redis answers, and 503 `redis_unavailable` when Redis is missing.

## Testing and release gates

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

# Build the image; check it runs as non-root and holds no .env, .git, tests or JSON files
make image-smoke
```

Facts to know before trusting a green run (checked on 2026-11-07):

- `make test` runs `python -m pytest -q` inside the virtual environment, so no `PYTHONPATH` is needed. A bare `pytest` outside it needs `PYTHONPATH=$PWD`, which CI sets.
- Only `DATABASE_URL` must be exported in the shell for the tests, because the integration fixtures read `os.environ["DATABASE_URL"]`. The other settings can come from `.env`. With it unset a run shows 1 failure and 43 errors, not skips.
- With PostgreSQL running: 246 passed. With `DATABASE_URL` set but no database: 202 passed and 44 skipped, so a green run without PostgreSQL does **not** cover the integration tests. They create a temporary database per test, so the configured user needs `CREATEDB`.
- CI's values are in [`.github/workflows/backend.yml`](../.github/workflows/backend.yml). The full test strategy is [.lock/TEST_STRATEGY.md](../.lock/TEST_STRATEGY.md).

## Project structure

```
onebox/
├── agents.py                    # Executive, general, and streaming Gemini agent loops
├── server/
│   ├── main.py                  # FastAPI application entrypoint & lifespan
│   ├── config.py                # Typed settings, validation, and role definitions
│   ├── auth.py                  # Verified JWT principal (user ID, email, frozen claims)
│   ├── oauth_state.py           # One-time OAuth state stored in Redis
│   ├── database.py              # Async SQLAlchemy engine, sessionmaker, and Base
│   ├── models.py                # Database models (Tokens, Actions, Jobs, Watches)
│   ├── schemas.py               # Public request/response models for the OpenAPI contract
│   ├── action_payloads.py       # Strict typed payloads for each pending action type
│   ├── redis_cache.py           # Generational Redis cache client and utilities
│   ├── pagination.py            # Opaque keyset page tokens shared by list endpoints
│   ├── request_limits.py        # Request-body size limit middleware
│   ├── logging_config.py        # Redacting log formatter and correlation IDs
│   ├── agent_policy.py          # Immutable per-run agent authorization policy
│   ├── agent_tools.py           # Gemini function declarations & trusted tool bindings
│   ├── commands/
│   │   └── backfill_oauth_credentials.py # One-off legacy plaintext token encryption
│   ├── security/
│   │   └── oauth_credentials.py # AES-256-GCM OAuth token codec and keyring loader
│   ├── mail/
│   │   ├── mime.py              # Bounded MIME decoding & strict HTML sanitizing
│   │   └── inbound.py           # Inbound message parsing & triage eligibility filter
│   ├── integrations/
│   │   ├── provider.py          # Bounded thread runner for blocking SDK calls
│   │   ├── google.py            # Classified Google API retry & backoff wrapper
│   │   ├── gmail.py             # Gmail request dispatch by safety class & HTTP error mapping
│   │   ├── llm.py               # Gemini (Vertex AI) generate and stream adapter
│   │   └── redis.py             # Redis connection pools and concurrency limits
│   ├── routes/
│   │   ├── agent_oauth.py       # Google OAuth 2.0 authorization-code / state flows
│   │   ├── agent_router.py      # Executive/streaming agents & pending action lifecycle
│   │   ├── google_mail.py       # User mail fetch, search, star, and delete endpoints
│   │   ├── push_router.py       # Authenticated Google Pub/Sub push receiver
│   │   └── triage.py            # Read-only view of the worker's triage results
│   ├── services/
│   │   ├── pending_actions.py   # Pending action staging, claiming, and reconciliation
│   │   ├── action_handlers.py   # Executable handlers for approved pending actions
│   │   ├── notification_jobs.py # Durable notification queue storage & leases
│   │   ├── credentials.py       # OAuth exchange, token storage, refresh, and service building
│   │   ├── mailbox.py           # User-facing mailbox fetch and cache services
│   │   ├── readiness.py         # Migration and persistence health verifiers
│   │   ├── retention.py         # Expires stale proposals and prunes terminal records
│   │   ├── triage_results.py    # Owner-scoped triage result queries
│   │   └── setup_google.py      # Authenticated Google Workspace service factories
│   └── workers/
│       ├── __main__.py          # Worker entrypoint (`python -m server.workers`)
│       └── mail_notifications.py# Durable Pub/Sub notification worker & watch renewal
├── tools/
│   ├── llm_tools.py             # Interactive agent tools & pending action generators
│   ├── idempotency.py           # Canonical payload hashing and command record keys
│   └── utils.py                 # RFC 2822 message encoders and header utilities
├── clients/
│   ├── base.py                  # Base Agent class (injectable Gemini provider)
│   └── prompt.py                # Executive and email agent system prompts
├── tests/
│   ├── unit/                    # Fast isolated unit tests
│   └── integration/             # Database and notification queue integration tests
├── docs/
│   ├── API.md                   # Endpoint and event-stream reference
│   ├── CONFIGURATION.md         # Settings, secret files, agent profile
│   ├── POLICIES.md              # Limits, cache, tool allowlist, retries, retention
│   ├── OPERATIONS.md            # Runbook: Compose, deploy, recovery, when something breaks
│   ├── DEVELOPMENT.md           # This guide
│   └── openapi.json             # Generated, backend-owned OpenAPI contract
├── .lock/                       # Planning docs: PRD, architecture, ADRs, roadmap, tasks
├── alembic/                     # Database migrations
├── scripts/
│   ├── generate_openapi.py      # Tool to generate/check OpenAPI schema drift
│   └── redis_setup.sh           # Local developer Redis provisioning script
├── Dockerfile                   # Multi-stage hardened non-root container image
├── docker-compose.yaml          # Multi-container orchestration (App, Worker, DB, Redis)
├── Makefile                     # Common development, testing, and release targets
└── user_config.yaml             # Agent profile: identity, timezone, and preferences
```
