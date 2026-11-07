# ADR-0013: Approval tiers — approval cost matches reversibility

**Status:** Accepted · **Date:** 2026-11-07 · **Deciders:** project owner

## Context

[ADR-0001](ADR-0001-approve-first.md) says a person approves every send, reply, event and task, while drafts and mark-as-read run at once. That is one rule applied by habit, not by risk. If every card costs the same click, a person who sees thirty suggestions a day will approve without reading, and the control turns into theater (threat T22). The PRD's open question about drafts is the same question. Approval effort belongs where a mistake cannot be undone or other people see it.

## Decision

Every action schema declares **one tier**. The connector declares it in code. The model cannot choose or change it, and an action with no declared tier is treated as **C**.

| Tier | Meaning | Examples | Rule |
|---|---|---|---|
| **A** Undoable and private | Nothing leaves your account; easy to undo | create a draft, mark as read | Runs at once when the connection is on Propose; recorded in the audit trail and shown in the timeline as "Done automatically"; no card |
| **B** Reversible, visible only to you | Creates something in your own account that you can delete | a task, an event with no attendees | A proposal with individual approval. A batch approve is added **only if** the user test (task 4.5) shows approval fatigue |
| **C** Leaves your account or is visible to others | Cannot be recalled | send, reply, an event with attendees | A proposal with individual approval of the exact payload; never batched, never automatic |
| **D** Not offered | Too hard to undo or too broad | permanent delete, bulk actions, sharing or permission changes, forwarding outside the account | No tool exists |

Rules:

1. An event with attendees is **C** even though attendees are limited to the verified sender ([ADR-0007](ADR-0007-zero-tool-reader.md)), because an invitation reaches another person.
2. **Batch approve, if built:** at most 10 cards per request [PROPOSED]; each item carries its own payload hash and passes every check in [ADR-0010](ADR-0010-approval-and-execution-protocol.md); a batch never contains tier C; one item failing does not fail the others. It is a list of selected cards, not an "approve all" button.
3. Tier A actions need the **Propose** switch. Read blocks them.
4. Moving an action to a lower tier needs a pull request and a note in this ADR's history.
5. This answers the PRD question: drafts and mark-as-read **stay immediate** (tier A) and do not become proposals.

## Options considered

- **A. One tier, as today:** simple; invites rubber-stamping.
- **B. Tiers with batching only for reversible actions (chosen).**
- **C. Auto-run reversible actions** (the direction Inbox Zero took): least friction, but a wrong rule or an injected instruction acts at once, and "the AI never executes" stops being true for tier B.
- **D. Let the model grade its own risk:** the model reads untrusted text, so its grade is untrusted. Rejected.

## Trade-offs

Solves: effort is spent where a mistake costs something, and there is a clear answer to "is everything approved?". Worsens: a second concept to explain, and the headline claim needs exact wording: the assistant cannot send, reply, invite or create a task on its own; it can only write a draft or mark mail read, and both are logged. Revisit when: the user test shows people skip the details even on tier C (then add a step-up such as re-typing the recipient, or smaller batches); the owner approves a written, tested exception to auto-run one tier B action (the exception clause in ADR-0001); or an action does not fit the four tiers.

## Consequences

`action_schemas()` in the connector contract gains a `tier` field. The `ActionCard` shows a one-line effect notice drawn from the tier. FRONTEND's "no approve all" rule stays in force across tiers. Invariant 22 in [TEST_STRATEGY.md](../TEST_STRATEGY.md); threat T22 in [THREAT_MODEL.md](../THREAT_MODEL.md); tasks 2.2, 4.1, 4.4 and 4.5 in [TASKS.md](../TASKS.md).

## Action items

1. [ ] Add `tier` to action schemas and default an undeclared action to C (task 2.2).
2. [ ] Show tier A actions in the activity timeline (task 4.1).
3. [ ] Observe 3–5 people, then decide on batch approve (tasks 4.5, then 4.4).
