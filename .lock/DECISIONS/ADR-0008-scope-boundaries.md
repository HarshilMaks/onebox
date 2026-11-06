# ADR-0008: Scope boundaries and the freeze rule

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

The category is crowded: Gmail and Outlook bundle basic AI help; chat assistants offer connectors; automation platforms connect thousands of apps; the category consolidates by acquisition. A solo project cannot win on features, and each added feature adds code, tests and attack surface.

## Decision

**In:** Google first, then Outlook; one approval queue; per-app switches; email-to-proposals; a small web client; demo mode. **Out:** RAG or vector search; a chat UI; auto-send or autonomy; public launch and Google verification; more connectors before Google and Outlook both work; notifications, voice, inbox search, meeting notes, scheduling links, follow-up reminders, writing in the user's voice; Zoho; an eval harness as a deliverable; "zero retention" claims.

**Freeze rule:** nothing enters unless it fixes a documented failure in the email-to-action loop and has a test, or it is an owner-approved milestone. Outlook (phase 5) is such a milestone and a stretch goal: it proves the connector contract is real, but it fixes no documented failure. Everything else goes to the parking list in [ROADMAP.md](../ROADMAP.md).

## Options considered

- **A. Broad feature set:** matches competitors; no depth; unbounded effort.
- **B. Narrow loop with strong guarantees (chosen).**

## Trade-offs

Solves: finishable scope and a defensible story. Worsens: less to show on breadth. Revisit when: Phase 6 is done and a measured user need exists.

## Consequences

Be able to explain every choice; reviewers weigh a defended decision above polish.

## Action items

1. [ ] Keep the PRD's out-of-scope table and the roadmap's parking list in sync.
