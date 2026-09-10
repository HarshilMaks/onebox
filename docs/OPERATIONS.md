# Backend operations runbook

This runbook covers the supported OneBox backend roles: `api` and
`automation_worker` in production, and `combined` only for local development.
Automation supports one configured mailbox (`AUTOMATION_OWNER_ID`) at a time.
Do not enable it until the owner has completed the Google OAuth, Gmail watch,
Pub/Sub push-authentication, database, Redis, DNS/TLS, and secret-manager setup.

## API contract

`docs/openapi.json` is generated from the live FastAPI application. Regenerate
it after changing any route or public Pydantic schema:

```bash
.venv/bin/python scripts/generate_openapi.py
.venv/bin/python scripts/generate_openapi.py --check
```

`POST /generate-stream/` returns `text/event-stream`, not one JSON response.
Each non-comment `data:` frame contains JSON matching `AgentStreamEvent`; the
terminal event is exactly `done` or `error`. All request-validation errors use
the safe `{"error": "validation_error", "detail": ...}` envelope.

## Configuration contract

`.env.example` is the complete commented setting reference. Use placeholder
values or secret-manager mounts only; never place token JSON, OAuth client
files, service-account files, keyrings, or real endpoints in Git.

| Scope | Settings required before the role can perform its work |
| --- | --- |
| All roles | `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE`, `GOOGLE_OAUTH_CLIENT_SECRETS`, `OAUTH_REDIRECT_URI`, `FRONTEND_OAUTH_CALLBACK_URI`, `GOOGLE_PROJECT_ID`, `GOOGLE_LOCATION`, `GOOGLE_MODEL` |
| OAuth credential operations | `OAUTH_TOKEN_KEYRING_PATH` and `OAUTH_TOKEN_ACTIVE_KEY_ID`; credential work fails closed when the pair is absent. Legacy dual-read migration data can remain, so do not claim every historical token row is encrypted until the plaintext-column contraction migration is complete. |
| Automation worker / local combined role | `AUTOMATION_ENABLED=true`, `AUTOMATION_OWNER_ID`, `PUBSUB_TOPIC`, `PUBSUB_SUBSCRIPTION`, `PUBSUB_PUSH_AUDIENCE`, and `PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL` |
| Pending-action reconciliation endpoint | `PENDING_ACTION_OPERATOR_IDS` containing authorized authenticated user UUIDs; an empty list disables the endpoint. |
| Production transport | PostgreSQL TLS in `DATABASE_URL`; authenticated `rediss://` unless `REDIS_TRUSTED_LOCAL_NETWORK=true` is a controlled private-network exception. |
| Compose only | `POSTGRES_PASSWORD`, `GOOGLE_OAUTH_CLIENT_SECRETS_HOST_PATH`, `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH`, `OAUTH_TOKEN_KEYRING_HOST_PATH`, and `OAUTH_TOKEN_ACTIVE_KEY_ID` for the read-only credential/keyring mounts. |

`ENVIRONMENT`, `SERVICE_ROLE`, `AUTOMATION_ENABLED`, provider timeout/retry
bounds, pool sizing, retention, worker lease/recovery bounds, and CORS origin
allowlist all have validated defaults or documented optional overrides in
`.env.example`. Unknown setting names fail startup.

## Human actions versus agent actions

Direct authenticated mail endpoints such as `/mail/send`, message deletion, and
mailbox mutations are human API actions and dispatch their provider operation
from that endpoint. They are not available as agent tools.

Interactive agent email sends/replies, calendar-event creation, and task
creation create owner-bound pending actions. They are dispatched only after
`POST /actions/{action_id}/approve`; a successful external result is
`status: "succeeded"`, never `completed`. Draft creation and marking a message
read are immediate interactive-only agent mutations. Automated inbound triage
has no tools and does not mark promotional or no-reply messages as read, create
events/tasks, draft replies, or send mail.

## Deploy, migrate, and verify

1. Build the immutable image and deploy one one-shot migration role before API
   or worker replicas. Do not run migrations in every API process.
2. Run `alembic upgrade head` with the restricted migration role, then run
   `alembic check` from the release source.
3. Start API replicas with `SERVICE_ROLE=api`. Start the dedicated worker with
   `SERVICE_ROLE=automation_worker` only when automation is fully configured.
   `combined` is local-development convenience mode, not a production role.
4. Check `/livez` for process liveness and `/readyz` for database schema and
   role-required Redis readiness. `/readyz` deliberately makes no Google call.
5. With the configured operator JWT, check `/mail/agent/health` and
   `/mail/agent/status`. Healthy automation requires a valid persisted watch,
   fresh worker heartbeat, idle recovery state, and no terminal queue failure.

The Compose topology runs PostgreSQL, Redis, `migrate`, `app`, and `worker`.
Credential and keyring files are host-mounted read-only and are not copied into
the image.

## Watch renewal, recovery, and reconciliation

The worker renews the Gmail watch under a singleton database lease. Operators
can request a bounded renewal through `POST /mail/renew-watch` after checking
`/mail/agent/status`; never call Gmail `users.stop` during a rolling restart.

If bounded history recovery reaches `GMAIL_RESYNC_MAX_MESSAGES`, the persisted
state becomes `manual_required` and the cursor is intentionally not advanced.
Investigate the queue/status data, establish a safe mailbox baseline under the
owner's approved procedure, and only then resume automation. This prevents
silent message loss.

An ambiguous provider write becomes `reconciliation_required`; it is never
blindly retried. An authorized operator calls
`POST /actions/{action_id}/reconcile`. The service searches deterministic
provider markers, records evidence, and changes state only when the outcome is
known. Keep reconciliation-required records until the operator resolves them.

## Rollback

Before migration, take an owner-managed database backup and record the image
and Alembic revision. On application rollback, stop new rollout attempts,
deploy the previous tested image only if it is compatible with the current
expand-phase schema, and keep the durable worker from racing multiple versions.
Do not run `alembic downgrade` against shared data without an owner-approved,
revision-specific rollback plan and a restore test. Preserve pending actions,
notification jobs, watches, and reconciliation evidence for operator review.

## Release gate

From a clean checkout, install the hashed lock, regenerate/check the OpenAPI
artifact, run Ruff, compilation, tests, migration upgrade/check, Compose
rendering, image smoke checks, and local secret/image scanning. CI runs the
same non-cloud code, contract, migration, image, and committed-history checks.
Owner-supplied non-production credentials are still required for the separate
manual staging smoke gate described in the remediation plan.
