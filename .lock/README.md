# `.lock` — OneBox locked plans and decisions

**Status:** living docs · **Snapshot:** 2026-11-06 · **Code baseline:** commit `55b6dbd` (same code as `origin/main` at `4599ff1`)

This folder is the source of truth for **what OneBox is, what it will become, and why**. It is committed with the code so a reader can follow the reasoning next to the implementation.

## What OneBox is

OneBox is a backend (and, in the plan, a small web client) that lets **one AI assistant work across a person's management tools** (mail, calendar, tasks) **without ever acting on its own**. The AI only *proposes*. Every write becomes a stored proposal in **one approval queue** with **one audit trail**. A person approves it, and it runs at most once. If the outcome of a write is unclear, OneBox does not guess: it marks the action *needs checking* and looks at the provider.

Today (built): Google Gmail, Calendar and Tasks; background mail summaries; the approval ledger. Planned: sign-in with Google (OpenID Connect), a "+" connector model with per-app permission switches, suggestions from new mail, a second provider (Microsoft Outlook), and a small web client.

## Reading order

| Group | Doc | It answers |
|---|---|---|
| Product | [PRD.md](PRD.md) | What are we building, for whom, and how do we know it works? |
| Product | [FRONTEND.md](FRONTEND.md) | What do the screens do, and what is the design system? |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) | How is it built (as-built vs planned), with diagrams |
| Architecture | [DECISIONS/](DECISIONS/) | Why each major choice was made (ADR-0001 to ADR-0013) |
| Architecture | [THREAT_MODEL.md](THREAT_MODEL.md) | What can go wrong, and what stops it |
| Architecture | [CONNECTORS.md](CONNECTORS.md) | Which apps, in what order, and how a new app is added |
| Plan | [ROADMAP.md](ROADMAP.md) | Phases, milestones, estimates, cut lines |
| Plan | [TASKS.md](TASKS.md) | Every task: status, dependencies, done-when, commit plan |
| Engineering | [TEST_STRATEGY.md](TEST_STRATEGY.md) | How we test: TDD workflow, invariants, fakes |
| Engineering | [ENGINEERING_PRACTICES.md](ENGINEERING_PRACTICES.md) | Commands, CI, branching, commits, definition of done |
| Engineering | [AGENT_GUIDE.md](AGENT_GUIDE.md) | How AI agents and people work here, step by step |
| Reference | [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) | The evidence behind each decision, with confidence tags |
| Reference | [HANDOFF.md](HANDOFF.md) | Where we are now, and the prompt to resume work |

## Status legend

- **[BUILT]** verified in the code at the baseline commit.
- **[PLANNED]** decided, not built.
- **[DOCUMENT]** accepted and described, not built.
- **[OPEN]** not decided yet; listed in the PRD's open questions.
- **[ESTIMATE]** a judgment or arithmetic, not a measurement.
- **[UNVERIFIED]** the source was secondary or could not be read.

## Rules for this folder

1. **No secrets and no personal data.** Use placeholders such as `user@example.test`. Never paste tokens, keys, real mailboxes or local machine paths.
2. **Repo-relative paths only.**
3. **A locked decision changes only through a new ADR** that supersedes the old one.
4. **Claims about the outside world cite a source** in [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) with a confidence tag, or are marked [UNVERIFIED].
5. **Docs change in the same change (commit or branch) as the behavior they describe.**
6. **Honest claims only.** Never write "exactly once", "zero retention", "cannot be tricked" or "agentic" without a tool loop. See [THREAT_MODEL.md](THREAT_MODEL.md) for the allowed wording.

## Glossary

| Term | Meaning |
|---|---|
| **Proposal** (pending action) | An immutable stored request for an external write, awaiting approval. Table `pending_actions`. |
| **command_key** | Idempotency key. The same key with the same payload returns the same proposal. |
| **Claim / lease / attempt token** | Approving locks the row, marks it `processing`, and issues a time-limited lease so two workers cannot run the same action. |
| **Reconciliation** | A provider lookup that decides whether an unclear write actually landed. State `reconciliation_required`, shown to users as *needs checking*. |
| **Triage** | A background step that reads a new mail (as untrusted data, with no tools) and saves a short summary. |
| **Connector** [PLANNED] | An adapter for one app (Google, Microsoft, …) behind a common contract. |
| **Connection** [PLANNED] | One user's authorization for one connector, with its own token and permission switch. |
| **Switch** [PLANNED] | Per-connection permission: **Off**, **Read**, or **Propose**. |
| **Tier** [PLANNED] | An action's approval class, A to D, set by its connector. A is undoable and private and runs at once; B stays in your account; C leaves it and always needs individual approval; D is not offered. |
| **Demo mode** [PLANNED] | A no-sign-in path on synthetic data and canned model output. |
| **Testing mode** | Google's unverified-app status: up to 100 allowlisted users and 7-day token expiry. |
