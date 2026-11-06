# OneBox — Product Requirements Document

**Status:** DRAFT v1 · 2026-11-06 · **Scope:** requirements only (the *how* is in [ARCHITECTURE.md](ARCHITECTURE.md); the *order* is in [ROADMAP.md](ROADMAP.md)).
**Anti-fluff rule:** anything not backed by evidence is written as `TBD` or `Assumption`, never as fact.

## 1. Problem

People run their work and life across several tools: mail, calendar, tasks. Keeping them in sync is busywork. Assistants that act on those tools can do the wrong thing, such as sending, deleting or booking without the person noticing or saying "done" when nothing happened, and attackers can steer them with content they control (an email, an invite, a ticket, a PDF). The cost of leaving this unsolved is either permanent busywork or an assistant nobody trusts with write access.

## 2. Evidence

Details and confidence tags are in [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md).

- **Tool sprawl is real, but the numbers are soft.** A vendor survey series reports about 60% of the workday on coordination; the figure is survey-based and its wording varies between editions. [secondary]
- **What users complain about in AI inbox tools** (71 low-star Trustpilot reviews, small and self-selected): actions nobody approved and false "done" claims; most other complaints were billing. [anecdote]
- **Attackers steer assistants through content.** Documented incidents include EchoLeak (M365 Copilot), the Superhuman AI exfiltration, the Atlassian MCP proof of concept and Notion agent exfiltration. Platform-security incidents also occurred: the Asana MCP tenant-isolation bug and the Composio token theft. [primary/discloser]
- **Competitors' approval models are weak or undocumented.** Zapier Agents' approval is an instruction in the prompt; no platform checked documents approval expiry; no page read documents a persistent cross-app approval queue or per-action audit. [vendor-doc; absence in documentation is an inference]
- **Assumption — needs validation via beta users:** people prefer a stored review queue to per-call chat prompts.

## 3. Users

- **Primary:** an individual knowledge worker or freelancer with Gmail (and later Outlook) who wants help with triage and scheduling but will not give an AI unsupervised write access.
- **Secondary audience:** engineers and hiring managers who evaluate the project as a product. The project is built to be shown, not launched.
- **Not for:** teams and enterprise administration, regulated data, anyone who wants autonomous action, public launch.

## 4. Hypothesis

We believe **one approve-first assistant with one queue and one audit trail across connected apps** will let a busy person delegate triage and scheduling without losing control.
We will know we are right when the success metrics in §6 hold on real test accounts and a first-time viewer can complete the review-and-approve flow unaided.

## 5. Product in one screen

A person signs in with Google, adds apps with **"+"**, and sets each app to **Off / Read / Propose**. New mail produces a short **digest** and **suggestions** (reply, event, task). Each suggestion shows the exact recipient or time, a link to the source mail, and a conflict warning. The person edits, approves or rejects. Approving runs it at most once. An **activity timeline** shows everything. **Disconnect** revokes access and deletes the stored data.

**Design principles:** the AI never acts · one queue · least privilege · honest status · nothing happens twice · plain text only.

## 6. Success metrics

| # | Metric | Target | How measured |
|---|---|---|---|
| M1 | External writes that bypass the approval ledger | 0 | automated test over every connector write path |
| M2 | Duplicate external effects after an injected lost response, per action type | 0 | fault-injection tests with fake providers; real-account spot check TBD |
| M3 | Unknown outcomes with no visible state | 0 | crash-point tests and the stuck-action sweep test |
| M4 | Tokens or mail bodies found in logs or prompts | 0 | canary-string test |
| M5 | Cross-provider flow (Outlook mail → Google Calendar event) | pass | scripted run on test accounts |
| M6 | Time from new mail to visible suggestions | TBD — measure in a demo run | log timestamps |
| M7 | A first-time viewer completes review-and-approve unaided | TBD — needs 3–5 observers | observation, qualitative |
| M8 | CI green; test count and coverage reported | green; coverage TBD | CI |

## 7. Scope

### Functional requirements

| ID | A user can… | Phase |
|---|---|---|
| FR-1 | Sign in with Google (OpenID Connect); OneBox never sees a password | 1 |
| FR-2 | Add an app with "+", each with its own consent: Google Gmail, Calendar, Tasks first; Outlook later | 2, 5 |
| FR-3 | Set each connected app to Off, Read, or Propose | 2 |
| FR-4 | See a digest of new mail summaries, most important first | 3 |
| FR-5 | Get suggestions (reply, event, task) from new mail as proposals, capped per day | 3 |
| FR-6 | Review a proposal with exact payload, source link, short reason and conflict warning; edit it; approve or reject | 3, 4 |
| FR-7 | Have an approved action run at most once, and have an unclear outcome flagged *needs checking* and resolvable | 0, 3 |
| FR-8 | Ask the assistant on demand to propose actions across connected apps | built |
| FR-9 | See one activity timeline across apps | 4 |
| FR-10 | Disconnect an app (revoke at the provider and delete stored tokens) and delete their data | 2 |
| FR-11 | Try the product in demo mode without signing in | 6 |
| FR-12 | Be told "done" only when the provider confirmed | 3 |

Already built and kept: drafts and mark-as-read run immediately; send, reply, event and task become proposals. [OPEN: should drafts and mark-as-read also become proposals when the switch is Propose?]

### Non-functional requirements

| Area | Requirement |
|---|---|
| **Security** | Approval is enforced in code, single-use, bound to the exact payload, with an expiry. Least-privilege scopes. Model output rendered as plain text. Untrusted content never reaches a model that has tools. See [THREAT_MODEL.md](THREAT_MODEL.md). |
| **Reliability** | At-most-once dispatch; unknown outcomes are reconciled, never auto-retried; a failing background step must not stop the worker. |
| **Privacy** | No passwords, no chat memory. OneBox stores encrypted tokens plus proposals, summaries and audit events in plain text with a retention limit (defaults: proposals 30 days, jobs and triage work 14 days), and caches parsed mail in Redis for up to 5 minutes. A delete-my-data action is planned (FR-10). The honest claim is minimal retention, not "zero retention". |
| **Performance and cost** | Budgets in [ARCHITECTURE.md §12](ARCHITECTURE.md#12-capacity-and-cost-estimate). A per-user daily cap on model calls and proposals. |
| **Operability** | Health endpoints, structured logs with a correlation id, alerts on stuck work. |
| **Demo scale** | At most 100 allowlisted users (Google Testing mode); weekly re-consent. |

### Out of scope

| Item | Why |
|---|---|
| Public launch and Google verification | Restricted Gmail scopes need an annual third-party security assessment; cost evidence conflicts. |
| Chat UI, notifications, voice, inbox search, meeting notes, scheduling links, follow-up reminders, "write in my voice" | Other products' features; the loop does not need them. |
| RAG or vector search | Not needed; Gmail search exists. |
| Auto-send or an autonomous mode | Contradicts [ADR-0001](DECISIONS/ADR-0001-approve-first.md). |
| More connectors until Google and Outlook both work | Each adds auth, duplicate protection and a new attack surface. Jira or Notion via MCP is an optional later step. |
| Zoho | Evidence is thin; no official hosted MCP server found. |
| Evals as a deliverable | Owner decision; standard tests only. |
| "Zero retention" or "forgets everything" claims | Not deliverable. |
| Team and enterprise features | Not the target user. |

## 8. Delivery milestones

| # | Milestone | Outcome | Status |
|---|---|---|---|
| M0 | Foundations | Audit bugs fixed; honest README | pending |
| M1 | Identity and connections | Sign-in, connections, switches, disconnect (Google) | pending |
| M2 | The loop | New mail → suggestions → review → one effect | pending |
| M3 | Second provider | Outlook connector and the cross-provider demo | pending |
| M4 | Demo and packaging | Demo mode, deploy, video, docs | pending |

## 9. Open questions

- [ ] Do drafts and mark-as-read become proposals under the Propose switch?
- [ ] Default daily cap for suggestions and for model calls.
- [ ] Does Gmail preserve a client-set Message-ID on send, so reconciliation can find the message? (spike)
- [ ] Does splitting Read and Propose into separate tokens conflict with Google's `include_granted_scopes`? (spike)
- [ ] Tier of the Google Tasks scope (sensitive or non-sensitive).
- [ ] How reliable is a Sent Items lookup for Outlook reconciliation? (spike)
- [ ] Is weekly re-consent acceptable for the demo, or is a seeded replay enough?
- [ ] Where is the demo hosted?

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Crowded category; no moat | High | Medium | Pitch enforcement and honest status, not features; present as engineering depth |
| Stolen session can approve actions | Medium | High | Opaque server-side sessions, short lifetimes, single-use approvals, notice on each write; document step-up approval |
| Outlook reconciliation is weaker than Gmail's | High | Medium | Treat unclear outcomes as *needs checking*; say so in the docs |
| Scope growth | High | High | Freeze rule in [ROADMAP.md](ROADMAP.md); cut lines |
| Google Testing-mode token expiry breaks the demo weekly | High | Low | Clear "reconnect needed" state; demo mode |
| Solo effort roughly 8–12 weeks [ESTIMATE] | Medium | Medium | Ship Google loop first; Outlook is a cut line |

---
*Status: DRAFT. Requirements only; implementation plan in [ROADMAP.md](ROADMAP.md).*
