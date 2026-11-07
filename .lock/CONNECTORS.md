# OneBox — Connectors: the vision, the app catalog and the add-an-app playbook

**Status:** living · 2026-11-07 · the contract itself is in [ARCHITECTURE.md](ARCHITECTURE.md) §6; the decisions are [ADR-0003](DECISIONS/ADR-0003-native-connectors-mcp-generic.md), [ADR-0008](DECISIONS/ADR-0008-scope-boundaries.md) and [ADR-0013](DECISIONS/ADR-0013-approval-tiers.md). This file answers *which apps, in what order, and how a new one is added*.

## 1. The vision

OneBox is a **workspace of connected apps with one assistant, one approval queue and one audit trail**. A person presses "+" to connect an app, and sets it to Off, Read or Propose. Each app is one **connector** behind one contract; each person's authorization is one **connection**.

The assistant works across apps because every proposal has the same shape: a type, the exact payload, a source, a reason and an approval tier. That is what lets one email become a calendar event in another provider's calendar (PRD metric M5).

The platform is the architecture. Breadth is gated: the freeze rule ([ADR-0008](DECISIONS/ADR-0008-scope-boundaries.md)) lets an app in only as an owner-approved milestone.

## 2. The catalog

"Wave" is the build order. It is not the approval tier (A–D) of an action.

| Wave | App | Route | Status |
|---|---|---|---|
| **1** | Gmail, Google Calendar, Google Tasks | Native (Google) | Actions [BUILT]; connection model and switches [PLANNED] (phases 1–2) |
| **2, stretch** | Outlook mail and calendar (Microsoft Graph) | Native (Microsoft) | [PLANNED] Phase 5, about 13–22 days [ESTIMATE] |
| **3, candidates** | Up to three, chosen by the owner [OPEN] | MCP, or native if the app can answer "did my write land?" | Not started |

Moving a Wave 3 app ahead of Outlook changes the out-of-scope list in ADR-0008 and needs a new ADR.

### Wave 3 candidates

Only facts already in [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) are stated. Tiers are a [PROPOSED] starting point.

| App | What we know | Likely reads | Likely proposals (tier) | Before starting |
|---|---|---|---|---|
| **Jira** | An official hosted MCP server exists (verify with the vendor). Incident: hostile ticket reaching a support engineer's AI (2025-06) | Issues assigned to the user | Create an issue or comment (C: the team sees it) | Treat ticket text as untrusted; confirm the server's tenant isolation |
| **Notion** | An official hosted MCP server exists (verify). Incident: hidden PDF prompt exfiltrated data through a search query (2025-09) | Pages the user opens | Append to a private page (B); create a shared page (C) | Never let the reader see page content and an outbound tool at once |
| **Asana** | Incident: an MCP tenant-isolation bug showed some users other organisations' data (2025-06) | Tasks assigned to the user | Create a task in a private project (B), in a shared one (C) | Cross-user isolation test first |
| **Linear, Slack, HubSpot** | Not researched | — | A Slack message is C | Run the playbook's spike (step 2) before any claim |
| **Zoho** | Out of scope: no official hosted MCP server found | — | — | — |

## 3. The playbook: adding an app, step by step

Each step says what to do, why, and what proves it.

| # | Do | Why | Proof |
|---|---|---|---|
| 1 | **Write a one-page brief:** the app, the user story, the reads, each proposal with its tier. The owner approves it as a milestone | Stops scope creep ([ADR-0008](DECISIONS/ADR-0008-scope-boundaries.md)) | The brief is committed; the owner said yes |
| 2 | **Spike on a throwaway account:** sign-in type and the smallest scope, token life and refresh, rate limits, whether a client-set id survives, how to find a write after an unclear outcome, webhooks or polling | Every app answers "did my write land?" differently | A row in [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) §10, with no tokens or addresses |
| 3 | **Choose the route** ([ADR-0003](DECISIONS/ADR-0003-native-connectors-mcp-generic.md)): native if the provider can reconcile or the app is core; otherwise MCP. For MCP: pin the server and version, keep OneBox's own allowlist, treat tool annotations as untrusted hints, never forward tokens, leave unclear outcomes as *needs checking* | Native gives real reconciliation; MCP gives speed | One sentence in the brief |
| 4 | **Answer the threat checklist** in §4 | Each app adds a new attack surface | Answers are in the pull request |
| 5 | **Implement the contract:** `capabilities`, `read`, `action_schemas` (each with a `tier`), `execute` (confirmed success, confirmed rejection, or unknown), `reconcile`, `watch` or polling, `revoke`. Writes only through the ledger | One shape for every app | The shared contract tests pass |
| 6 | **Fault-inject every action type:** the write lands but the response is lost | Duplicate effects are the failure that matters most | One provider write, state *needs checking*, no retry (I-4) |
| 7 | **Wire the switches:** a new connection starts Off; Off means the token is not used; Read blocks proposals | Least privilege | Policy tests (I-13, [ADR-0005](DECISIONS/ADR-0005-permission-switches.md)) |
| 8 | **Data lifecycle:** every stored row carries the `connection_id`; add the app to the deletion matrix in [ARCHITECTURE.md](ARCHITECTURE.md) §7; disconnect revokes at the provider | Real deletion ([ADR-0012](DECISIONS/ADR-0012-data-lifecycle.md)) | Delete test passes (I-21) |
| 9 | **Update the docs in the same change:** the catalog row, a threat row, the task entry; an ADR only for a decision that is hard to reverse | Docs match code | The docs check in the test suite passes |
| 10 | **Add a fake provider** so demo mode can show the app without real accounts | [ADR-0009](DECISIONS/ADR-0009-demo-strategy.md) | Demo runs offline |

**Budget [ESTIMATE]:** an MCP app about 5–8 days (the earlier estimate, not re-derived); a native provider about 13–22 days (the Phase 5 sum). A third native connector is the revisit trigger for ADR-0003.

## 4. Threat checklist for each new app

Answer each in writing before step 5.

1. **Whose words are in this app?** List every field other people can write (ticket text, page content, comments, invites). All of it is untrusted: shown as plain text, and read by a model with no tools ([ADR-0007](DECISIONS/ADR-0007-zero-tool-reader.md)).
2. **What can a proposal do that others will see?** Those actions are tier C.
3. **What is the smallest scope?** Can reading and writing use separate tokens?
4. **Does any fetch follow a URL taken from content?** If so it needs the SSRF-safe client (T12).
5. **Could a bug show one organisation's data to another?** Add a cross-user test (T13; the Asana incident).
6. **Where does the token live?** Encrypted, never forwarded to a third-party server ([ADR-0006](DECISIONS/ADR-0006-token-storage.md)).
7. **What does the provider keep?** Say so; never claim "zero retention".
8. **Can an unclear write be found later?** If not, the action ends as *needs checking*, and the docs say so.
9. **What happens at the rate limit or the spend cap?** The work becomes a visible *deferred* state; it is never dropped.
10. **Does disconnect really revoke?** If the provider cannot revoke (some personal accounts), say so.
