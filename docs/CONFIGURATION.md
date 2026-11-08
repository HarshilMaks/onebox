# Configuration reference

How OneBox is configured today. Settings are read from the process environment and from a `.env` file in the repository root. Unknown names fail startup, so a variable that only Docker Compose needs, such as `POSTGRES_PASSWORD`, must be exported in the shell and never written to `.env`. Start from `.env.example`; the local setup steps are in the [development guide](DEVELOPMENT.md#local-setup-step-by-step).

The application uses typed Pydantic settings (`server/config.py`) that fail fast on unknown or malformed configuration keys. Relative paths resolve from the repository root.

## Core & Role Configuration

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `ENVIRONMENT` | No | `development` | Environment mode: `development`, `test`, `staging`, or `production`. |
| `SERVICE_ROLE` | No | `api` | Process role: `api`, `automation_worker`, or `combined`. |
| `AUTOMATION_ENABLED` | No | `false` | Enables durable Pub/Sub worker and background triage processing. |
| `AUTOMATION_OWNER_ID` | If worker | `None` | UUID of the single OneBox user whose mailbox is monitored by automation. |
| `PENDING_ACTION_OPERATOR_IDS` | No | `""` | Comma-separated list of authenticated user UUIDs authorized to run action reconciliation. |

## Persistence & Transport

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | Yes | - | Async PostgreSQL URI (`postgresql+asyncpg://user:pass@host:5432/db`). Requires TLS in staging and production. |
| `REDIS_URL` | Yes | - | Redis connection URL (`redis://` or `rediss://`). Requires password and TLS in staging and production. |
| `DATABASE_POOL_SIZE` | No | `5` | Core connection pool size for SQLAlchemy. |
| `DATABASE_MAX_OVERFLOW` | No | `5` | Maximum overflow connections for SQLAlchemy. |
| `REDIS_TRUSTED_LOCAL_NETWORK` | No | `false` | Allows unencrypted Redis in staging or production only inside a controlled private VPC. |
| `PROVIDER_RETRY_MAX_ATTEMPTS` | No | `3` | Maximum retry attempts for safe reads and idempotent writes. |
| `PROVIDER_RETRY_DEADLINE_SECONDS` | No | `10.0` | Wall-clock deadline bounding the entire retry sequence including wait intervals. Ambiguous writes are exempt. |
| `PROVIDER_RETRY_INITIAL_SECONDS` | No | `0.25` | Initial exponential backoff base for provider retries. |
| `PROVIDER_RETRY_MAX_SECONDS` | No | `2.0` | Maximum per-attempt backoff cap before jitter. |
| `PROVIDER_TIMEOUT_SECONDS` | No | `20.0` | Deadline for each Google, Gemini, and Redis operation. |
| `PROVIDER_MAX_CONCURRENCY` | No | `8` | Worker threads per provider adapter (Google, Gemini). |
| `REDIS_MAX_CONCURRENCY` | No | `16` | Maximum concurrent Redis operations per process. |
| `MAX_REQUEST_BODY_BYTES` | No | `1048576` | HTTP request body limit enforced before JSON parsing. |
| `LLM_STREAM_TIMEOUT_SECONDS` | No | `90.0` | Overall deadline for one streamed Gemini response. |
| `LLM_STREAM_IDLE_TIMEOUT_SECONDS` | No | `20.0` | Maximum wait between streamed chunks. |
| `CORS_ALLOWED_ORIGINS` | No | `""` | Comma-separated exact browser origins (no paths). Empty allows no cross-origin requests. HTTPS required in staging and production. |

## Authentication & Token Security

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `SECRET_KEY` | Yes | - | Secret key used for HS256 JWT signature verification (minimum 32 bytes). |
| `JWT_ISSUER` | Yes | - | Expected JWT issuer claim (`iss`). |
| `JWT_AUDIENCE` | Yes | - | Expected JWT audience claim (`aud`). |
| `OAUTH_TOKEN_KEYRING_PATH` | If OAuth | `None` | Filepath to the read-only JSON keyring used for AES-256-GCM token encryption. |
| `OAUTH_TOKEN_ACTIVE_KEY_ID` | If OAuth | `None` | Active key identifier in the keyring for encrypting new/refreshed tokens. |
| `GOOGLE_OAUTH_CLIENT_SECRETS`| Yes | - | Path to Google OAuth 2.0 Web Client secrets JSON file (`.env.example` uses `onebox_oauth.json`). |
| `OAUTH_REDIRECT_URI` | Yes | - | Backend OAuth callback redirect URI (HTTPS required in staging and production). |
| `FRONTEND_OAUTH_CALLBACK_URI`| Yes | - | Frontend URL to redirect the user after OAuth completion. |

## Google Cloud & Automation Worker

| Setting | Required | Default | Description |
| --- | --- | --- | --- |
| `GOOGLE_PROJECT_ID` | Yes | - | Google Cloud Platform project identifier. |
| `GOOGLE_LOCATION` | Yes | - | Google Cloud region for Vertex AI endpoints (`.env.example` uses `us-central1`). |
| `GOOGLE_MODEL` | Yes | - | Explicit Gemini model name used by agent implementations. |
| `PUBSUB_TOPIC` | If worker | `None` | Full GCP Pub/Sub topic path (`projects/{p}/topics/{t}`). |
| `PUBSUB_SUBSCRIPTION` | If worker | `None` | Full GCP Pub/Sub subscription path (`projects/{p}/subscriptions/{s}`). |
| `PUBSUB_PUSH_AUDIENCE` | If worker | `None` | Expected audience in the Google-signed OIDC push authorization token. |
| `PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL` | If worker | `None` | Authorized service account email delivering Pub/Sub push requests. |
| `GMAIL_RESYNC_MAX_MESSAGES` | No | `100` | Maximum messages recovered during history resync before entering `manual_required`. |
| `GMAIL_HISTORY_MAX_MESSAGES` | No | `100` | Maximum normal-history messages processed by one notification job before bounded recovery. |
| `GMAIL_HISTORY_MAX_PAGES` | No | `10` | Maximum normal-history pages read by one notification job before bounded recovery. |
| `GMAIL_NOTIFICATION_LEASE_SECONDS` | No | `120` | Lease length for a claimed notification job, message triage, resync, or watch renewal. |
| `GMAIL_NOTIFICATION_MAX_ATTEMPTS` | No | `5` | Job attempts before the mailbox switches to bounded resync. |
| `GMAIL_RETRY_BACKOFF_INITIAL_SECONDS` / `GMAIL_RETRY_BACKOFF_MAX_SECONDS` | No | `5` / `300` | Exponential backoff range for job, triage, resync, and watch retries. |
| `GMAIL_WATCH_RENEWAL_SECONDS` | No | `86400` | Renew the Gmail watch when it expires within this window. |
| `GMAIL_WORKER_LIVENESS_SECONDS` | No | `60` | Heartbeat age after which health and readiness report the worker as stale. |
| `PUBSUB_MAX_ENVELOPE_BYTES` | No | `65536` | Maximum Pub/Sub push body size. |
| `PENDING_ACTION_RETENTION_DAYS` | No | `30` | Days to keep terminal pending actions. |
| `GMAIL_JOB_RETENTION_DAYS` / `GMAIL_TRIAGE_RETENTION_DAYS` | No | `14` / `14` | Days to keep terminal notification jobs and triage records. |

## Required secret files

Mount these files read-only via secret management; never commit them to version control or bake them into container images:

| File | Purpose | Default Host Path |
| --- | --- | --- |
| `onebox_oauth.json` | Google OAuth 2.0 Web Client configuration (Client ID & Secret). | `GOOGLE_OAUTH_CLIENT_SECRETS_HOST_PATH` |
| `executive-agent.json` | Google Cloud service account credentials with Pub/Sub and Vertex AI roles. | `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH` |
| `onebox-oauth-token-keyring.json` | AES-256-GCM JSON keyring mapping key IDs to base64url 32-byte encryption keys. | `OAUTH_TOKEN_KEYRING_HOST_PATH` |

## Agent profile (`user_config.yaml`)

Edit `user_config.yaml` to configure the local deployment's agent profile, priority contacts, and scheduling/response preferences. The `timezone` field (IANA format, e.g. `America/New_York`) sets the default timezone for calendar tool operations. The current inbound triage eligibility filter is server-controlled; `triage_no`, `triage_notify`, and `triage_email` are profile-template guidance and are not runtime triage rules.
