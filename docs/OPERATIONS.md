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
   role-required Redis readiness. In the worker and combined roles `/readyz`
   also requires a valid watch, a fresh heartbeat, idle recovery state, and no
   dead-lettered jobs. `/readyz` deliberately makes no Google call.
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
silent message loss. While the state is `manual_required`, every new
notification job is dead-lettered with `resync_manual_required`, and worker
readiness reports `automation_recovery_required`. There is currently no API
endpoint or command that leaves `manual_required`; resuming requires a
reviewed, direct change to the mailbox's `gmail_mailbox_states` row.

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

## When something is wrong

Start from the symptom. "Today" describes what the code does now; "Planned" names the task in `.lock/TASKS.md` that changes it.

### An action is stuck in `processing` or shows "needs checking" (`reconciliation_required`)

1. Read it: `GET /actions/{action_id}` shows the status and its audit events.
2. Today: an authorized operator (a user id in `PENDING_ACTION_OPERATOR_IDS`) calls `POST /actions/{action_id}/reconcile`. It searches the provider for the deterministic marker, records what it found, and changes the state only when the outcome is known.
3. A `processing` row whose 2-minute lease has expired moves to `reconciliation_required` only when something touches it. No automatic sweep exists yet (planned: task 0.4).
4. Before trusting `failed` on a send, look in Sent, Spam and Trash by hand. Today a lookup that finds nothing can say `failed` at once even if the message lands a few minutes later (planned: task 0.13, a 10-minute quiet period).
5. Never approve again, resend by hand, edit the stored payload or delete the row. The payload hash check refuses an edited row, and a second send is a duplicate.

### New mail is not being processed, or `/readyz` is red

1. Check `/livez` and `/readyz`, then with the operator token `/mail/agent/health` and `/mail/agent/status` (valid watch, fresh worker heartbeat, idle recovery, no dead-lettered jobs).
2. Expired watch: `POST /mail/renew-watch` after checking the status. Never call Gmail `users.stop` during a rolling restart. See "Watch renewal, recovery, and reconciliation" above for `manual_required`.
3. Dead-lettered jobs: read the job's error code. `credentials_unavailable` means the next section.
4. Known cause: a retention cleanup that fails every time runs before the heartbeat, the watch renewal and the job claim, so the worker looks alive but claims no work. Look for repeated cleanup errors in the worker log (planned: task 0.2).

### Google says the token expired or was revoked

1. What happens: a failed refresh marks the stored connection `reconnect_required` and calls raise a reconnect error. The worker records `watch_credentials_unavailable` for renewals and `credentials_unavailable` for jobs. `GET /agent/status` shows `not_connected`; it does not yet say that a reconnect is what is needed (planned: task 2.3).
2. Expected in the demo: Google's Testing status expires authorizations seven days after consent (checked against Google's own page on 2026-11-07).
3. Fix: the user opens `GET /agent/oauth/start` and consents again, then `GET /agent/status` should show `connected`. Afterwards check `/mail/agent/status` for dead-lettered jobs or `manual_required`.

### After restoring a database backup

A restored database can hold approvals that were already carried out. Serving traffic from it could run them again.

1. Stop the API and the worker before the restore, and keep them stopped afterwards.
2. Today there is no restore command. With the owner reviewing the rows, in one transaction: set every `pending` action to `expired` and every `processing` action to `reconciliation_required`. Record the image and the Alembic revision.
3. Start the API, then reconcile each `reconciliation_required` action as in the first section.
4. Planned: a command that does steps 2 and 3 safely (task 2.6, [ADR-0012](../.lock/DECISIONS/ADR-0012-data-lifecycle.md)).

Alerts that say which of these happened are planned in task 6.3; extend this section when they exist.

## Release gate

From a clean checkout, install the hashed lock, regenerate/check the OpenAPI
artifact, run Ruff, compilation, tests, migration upgrade/check, Compose
rendering, image smoke checks, and local secret/image scanning. CI runs the
same non-cloud code, contract, migration, image, and committed-history checks.
A staging smoke test with owner-supplied non-production Google credentials is
still a separate manual step; it is not automated in this repository.
