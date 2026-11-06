# ADR-0010: Approval and execution protocol

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

The design review found gaps in how an approval becomes a write. The approve request carries only an id, so the hash check compares the stored row with itself and does not prove the user saw what will run. Editing a proposal leaves the original pending, so a stale tab can still approve it. Execution does not recheck the connection or the permission switch. The 15-minute lifetime suits a chat but not a queue of suggestions reviewed later. A confirmed success that finalizes after the 2-minute lease is turned into "needs checking". Reconcile can mark a write `failed` when the provider has only not caught up, which invites a duplicate on the next attempt. And reconcile is operator-only, so a user cannot resolve their own unclear action.

## Decision

1. **Approve carries the payload hash the user saw.** The server compares it with the stored hash under the row lock and refuses on a mismatch.
2. **Claim rechecks authority:** the connection still exists, its switch is Propose, and for a send the sending account is the connection's account.
3. **Editing supersedes.** An edit creates a new proposal with its own `command_key` and atomically rejects the original, recording `superseded_by` in the audit event.
4. **Lifetimes:** chat-created proposals keep 15 minutes. Proposals from the email bridge live 48 hours, and conflicts and availability are rechecked when they are claimed.
5. **Late confirmation is kept.** Finalize accepts a confirmed result when the attempt token matches and the row is still `processing`, even after the lease expired. Lease comparisons use database time, not the application clock.
6. **Reconcile never says `failed` inside a quiet period** of 10 minutes after the attempt started, and Gmail lookups include spam and trash. A draft-then-send flow for Gmail (store the draft id, decide with `drafts.get`) is evaluated in a spike.
7. **Owners can resolve their own action:** a read-only reconcile on their own action; after a configured delay an unresolved action can be closed as `resolved_by_user`, with an audit event. Operators keep override rights.

## Options considered

- **A. Keep today's behavior:** simplest; stale approvals, lost confirmations and false failures remain.
- **B. This protocol (chosen).**
- **C. Provider-side two-phase commit:** Gmail, Calendar, Tasks and Graph do not offer it.

## Trade-offs

Solves: stale approvals, wrong-account sends, lost confirmations, early "failed". Worsens: more fields, states and tests, and a change to the approve request contract. Revisit when: a provider offers idempotency keys for sends.

## Consequences

"At most once" holds only if `failed` is never trusted early. Invariants 17, 18 and 20 in [TEST_STRATEGY.md](../TEST_STRATEGY.md); threats T17 and T18 in [THREAT_MODEL.md](../THREAT_MODEL.md); tasks 0.4, 0.13, 3.3 and 4.1 in [TASKS.md](../TASKS.md).

## Action items

1. [ ] Accept a late confirmation when the attempt token matches (task 0.4).
2. [ ] Add the quiet period and spam and trash lookup to Gmail reconcile (task 0.13).
3. [ ] Add the hash to the approve request, edit supersession and owner reconcile (task 4.1).
4. [ ] Spike draft-then-send (task 2.1).
