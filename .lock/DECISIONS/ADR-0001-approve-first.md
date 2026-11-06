# ADR-0001: Approve-first — the AI proposes, a human approves, the ledger executes

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Assistants that act directly have shipped unapproved actions and false "done" claims (user reviews, small sample). Approval enforced only in a prompt is advice, not a control: Zapier Agents' approval is a sentence in the agent's instructions, and no platform checked documents approval expiry. Inbox Zero removed its approval states ("rules are now always automated"). Claude prompts before writes by default but offers "Always allow".

## Decision

The AI never executes an external write. Every send, reply, event and task becomes an immutable proposal. Only the ledger executes, after a person approves. Approval is single-use, bound to the payload hash, owned by the user, and expires (15 minutes for chat-created proposals, 48 hours for email-bridge suggestions; see [ADR-0010](ADR-0010-approval-and-execution-protocol.md)). Drafts and mark-as-read run immediately today [OPEN: whether they also become proposals].

## Options considered

- **A. Autonomous with rules** (Inbox Zero's current direction): fastest for users; a wrong rule or an injected instruction acts at once.
- **B. Prompt-level confirmation:** cheap; bypassable by prompt tricks or "always allow".
- **C. Approval enforced in code (chosen):** strongest guarantee; costs a review step.

## Trade-offs

Solves: prompt tricks cannot cause a write. Worsens: review fatigue. Mitigation: a per-user daily cap on suggestions and honest status. Revisit when: the owner approves a written, tested exception for one specific reversible action. Sends never become autonomous.

## Consequences

Easier: a clear, testable claim ("the AI cannot send"). Harder: needs a review surface; every connector must route writes through the ledger. To revisit: the immediate-action exceptions.

## Action items

1. [ ] Route or remove REST send, delete and trash (TASKS 0.6).
2. [ ] Test invariant 1: no external write without an approved action.
