# ADR-0012: Data lifecycle

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

The design review found that retention, deletion and recovery do not match the claims. The triage and notification-job tables carry no user or connection id, so "delete my data" would miss summaries, and a reassigned address would inherit them. The audit table cascades with its action, so the trail dies with the action at the 30-day retention, which contradicts "append-only". Token associated data includes the mutable account email, so a rename would make a token undecryptable, and the codec accepts only one format version. Restoring an old database snapshot could resurrect already-executed approvals as `pending`. Retention (14 days for triage work) is shorter than weekly Testing-mode token expiry, so a resync can re-propose mail the user already handled. A crash between triage and proposal creation can produce different model output on retry.

## Decision

1. **Every per-user table carries a `connection_id`** (with a foreign key), so disconnect and delete remove summaries, jobs and cached data together.
2. **Audit rows keep only ids, hashes, states and timestamps** (no message content), do not cascade with their action, and are retained for a documented period. The application role has insert and select rights on them only.
3. **Token associated data is the connection id plus a format version `v2`.** Decryption accepts `v1` and `v2` during migration; the keyring rotates by key id.
4. **Message tombstones:** keep the provider message id and handled time for 90 days so retention and resync never re-propose handled mail; resync considers only mail newer than the last processed date.
5. **Restore procedure:** after any restore, expire all `pending` actions and move `processing` ones to `reconciliation_required` before serving traffic.
6. **Persist the reader's typed output** and derive proposals from it inside the transaction that marks triage `succeeded`, so a retry is deterministic.
7. **Deletion matrix:** one table in the architecture doc lists, per data store (tokens, summaries, jobs, proposals, audit, caches, tombstones), what "delete my data" removes.

## Options considered

- **A. Leave as is:** the delete-everything and append-only claims stay false.
- **B. This lifecycle (chosen).**
- **C. Encrypt all stored content:** adds key handling for little gain at this scale; revisit with a key-management service.

## Trade-offs

Solves: honest deletion, a real audit trail, no re-proposals, safe restore. Worsens: a data migration and more tests. Revisit when: scale grows beyond the demo, or content encryption is required.

## Consequences

Invariant 21; threats T20 and T21; tasks 2.6, 3.3 and 3.6 in [TASKS.md](../TASKS.md). Until task 2.6 ships, the delete-everything claim is not allowed ([THREAT_MODEL.md](../THREAT_MODEL.md) §8).

## Action items

1. [ ] Expand and contract migration from `onebox_tokens` to `connections`, with a backfill test (task 2.3).
2. [ ] Content-free audit rows and no cascade (task 2.6).
3. [ ] `v2` associated data with dual decrypt (task 2.6).
4. [ ] Tombstones and the bounded resync (task 3.6).
