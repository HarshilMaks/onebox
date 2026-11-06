# ADR-0002: A Postgres ledger of our own, not a durable-execution engine

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

The ledger already exists: immutable `pending_actions`, an append-only audit table, claim with a row lock, lease and attempt token, idempotency by `(user_id, command_key)`, and a `reconciliation_required` state. Durable-execution engines (Temporal, Restate, DBOS, Inngest) are the usual alternative for retries and recovery.

## Decision

Keep and extend the Postgres ledger. The problem here is **action governance** (approval, expiry, ambiguous outcomes), not workflow orchestration.

## Options considered

- **A. Temporal, Restate, DBOS or Inngest:** strong retries and timers. Their own docs still require idempotent activities that may partly complete more than once, and approval and reconciliation would still be hand-built. One public example (Nango) moved from Temporal to a small Postgres queue, though for different reasons.
- **B. Own Postgres ledger (chosen):** small, fully understood, testable.
- **C. A generic agent-ledger library:** several small open-source libraries exist; none was checked for maintenance or adoption.

## Trade-offs

Solves: full control of the approval and ambiguity semantics with a small footprint. Worsens: we maintain it. Revisit when: a second executing instance is added, or multi-step flows with long timers appear; then evaluate DBOS.

## Consequences

Do **not** claim novelty for "idempotency keys everywhere" (a 2026 paper reports duplicate rates falling from 28% to 4% with keys on every write in simulated services). The README must state why this is not Temporal and what the ledger does not do.

## Action items

1. [ ] Add the five-line "why not Temporal or DBOS" note to the README.
2. [ ] Crash-point tests for every state transition.
