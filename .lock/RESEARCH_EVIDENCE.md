# OneBox research evidence

**Snapshot:** internal research notes dated 2026-11-03 to 2026-11-06 (not committed). Web sources are cited by URL. GitHub projects owned by individuals are named without links, to keep personal handles out of this file.

## 1. Purpose and confidence tags

This file traces each locked OneBox decision to claims, sources and a confidence level. OneBox is a resume-grade project built like a product; it will not be launched publicly. Pages were mostly read through a summarising fetch tool; re-read before quoting.

| Tag | Meaning |
|---|---|
| primary | Researcher read the original page, record, standard, official platform rules or source code |
| vendor-doc | Publisher's documentation or marketing describing its own product's behaviour; unchecked |
| secondary | Third-party description or search snippet |
| anecdote | One person, thread or small self-selected sample |
| own-count | Researcher's computation on a convenience sample |
| unverified | Unreadable, or sources conflict |

"Inference" marks the note authors' reasoning, not evidence. The Confidence column rates the evidence, not the decision.

## 2. Decisions and the evidence behind each

| Decision | Key evidence (claim, source, tag) | Confidence | What would change it |
|---|---|---|---|
| **Approval enforced in code, not prompt** | A "confirm first" instruction was reportedly lost to context compaction when an agent deleted an inbox (2026-02-23); cause not established ([OECD.AI](https://oecd.ai/en/incidents/2026-02-23-d55b)) [secondary]; the compaction account is the user's own [anecdote].<br>Zapier Agents' approval is an instruction sentence ([Zapier](https://help.zapier.com/hc/en-us/articles/41776074420493)) [vendor-doc].<br>Meta: agents with untrusted input, private data and external actions need supervision such as approval; Meta calls its own rule "not sufficient" ([Meta](https://ai.meta.com/blog/practical-ai-agent-security/)) [primary]. OWASP LLM06: enforce authorisation outside the model ([OWASP](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)) [primary].<br>No vendor page says how approval is enforced [primary, absence]. | Medium-high; the difference from vendors rests on absent documentation | A vendor documenting code-enforced, expiring approval; evidence that approvers rubber-stamp |
| **Own Postgres ledger, not a durable-execution engine** | Temporal: activities may run more than once and must be idempotent ([Temporal](https://docs.temporal.io/activity-definition)) [primary]. OpenAI Agents SDK: approval does not guarantee exactly-once; record unknown outcomes ([OpenAI](https://openai.github.io/openai-agents-python/human_in_the_loop/)) [primary].<br>Nango replaced Temporal with a roughly 300-line Postgres queue ([Nango](https://nango.dev/blog/migrating-from-temporal-to-a-postgres-based-task-orchestrator)) [primary; its own choice].<br>Gmail documents no send idempotency ([Gmail](https://developers.google.com/workspace/gmail/api/guides/handle-errors)) [primary]. How reviewers see hand-built ledgers is unevidenced [unverified]. | Medium; the argument is semantic (an engine's retry is what OneBox must refuse) | Multi-step flows with long timers (then evaluate DBOS); evidence reviewers read it as reinvention |
| **Google first, Outlook second** | Google code is built and demoable in Testing mode (section 5). Microsoft refresh tokens rotate but the old one stays valid ([Microsoft](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)) [vendor-doc], and personal accounts lack the revoke-sessions API (section 5). Both vendors bundle mail AI ([Google](https://blog.google/products-and-platforms/products/gmail/gmail-gemini), [Microsoft](https://techcommunity.microsoft.com/blog/outlook/copilot-in-outlook-new-agentic-experiences-for-email-and-calendar/4514601)) [vendor-doc]. No source compares demand; sequencing is inference. | Low-medium | A spike showing Outlook reconciliation is unreliable; demand data |
| **Native connectors for Google and Microsoft; MCP only as the generic path** | MCP leaves approval to implementers, treats tool annotations as untrusted, forbids token passthrough ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization), [tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)) [primary].<br>Zero's MCP server sends and deletes mail without approval ([Zero](https://github.com/Mail-0/Zero)) [primary]. Anthropic's help and docs pages disagree on whether its Google connector can write ([help](https://support.claude.com/en/articles/10166901-use-google-workspace-connectors)) [vendor-doc]. | Medium | A Tasks or Outlook MCP server with documented per-tool scopes |
| **OpenID Connect sign-in separate from per-app OAuth consent** | An ID token is addressed to the relying party and proves sign-in ([OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html)) [primary]. Apps asking Google only for name, email and profile skip the Testing-mode warning and 7-day expiry ([Google](https://support.google.com/cloud/answer/15549945)) [primary]. Sessions are not authentication ([MCP](https://modelcontextprotocol.io/specification/2026-07-28/basic/security_best_practices)) [primary]. That an ID token must never act as an access token is inference [unverified]. | High for validation rules; medium for the rationale | A provider forcing combined consent |
| **Per-app Off/Read/Propose switches enforced by token scopes** | A triage project requesting only `gmail.readonly` cannot write, enforced by OAuth [primary]. OWASP LLM06 cites read-only scopes for email assistants [primary]; Microsoft prefers delegated, least-privilege permissions ([Microsoft](https://learn.microsoft.com/en-us/graph/permissions-overview)) [vendor-doc].<br>Caveat: Google's `include_granted_scopes` merges grants, so a weaker Read token may need separate consent ([Google](https://developers.google.com/identity/protocols/oauth2/web-server)) [vendor-doc; untested]. Every Gmail read scope is restricted ([scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)) [primary]. | Medium | A spike showing merged grants; the Tasks scope tier |
| **Honest minimal-retention privacy claim, not "zero retention"** | Vertex does not train on customer data without permission but may log prompts for abuse detection, caches up to 24 hours, and zero retention needs specific steps ([Vertex](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/data-governance)) [vendor-doc]. The free Developer API allows product-improvement use and human review ([terms](https://aistudio.google.com/docs/terms)) [vendor-doc]. Inference: plaintext summaries are OWASP LLM02 exposure; store little and set retention. | Medium-high | Terms changes; a verified zero-retention setup |
| **Plain-text rendering of model output; no outbound channel for the model** | Shared exfiltration primitive: model output rendered as markdown or HTML that triggers an automatic request (EchoLeak, Superhuman AI, GeminiJack; section 4) [primary/secondary]. A Notion agent leaked data through its web-search tool ([CodeIntegrity](https://www.codeintegrity.ai/blog/notion)) [primary].<br>OWASP ASI09: plain-language summaries, not model rationales; separate preview from effect ([OWASP](https://genai.owasp.org/download/52117/?tmstv=1765059207)) [primary]. A tool-less model can still emit attacker-controlled text ([paper](https://arxiv.org/pdf/2506.08837)) and injection is "not fully solved" ([CaMeL](https://arxiv.org/abs/2503.18813)) [primary], so claims stay structural. | High for the pattern; medium for completeness | A feature rendering images or attachments; a bypass of plain-text rendering |
| **Demo: Google Testing mode plus no-sign-in demo mode on synthetic data** | Testing mode: 100 test users, 7-day expiry, warning screen ([Google](https://support.google.com/cloud/answer/15549945)) [primary]. Nothing found bars using a demo account on one's own app; Google itself asks for a recorded walkthrough ([Google](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)) [primary]. Replay on synthetic data uses no Google user data; Google states no position [unverified]. | Medium | A throwaway-project test of expiry; Google clarifying personal-use exemptions |
| **Out of scope: public launch and verification, RAG, chat UI, auto-send, extra connectors** | Launch: restricted Gmail scopes need a yearly third-party assessment; the $500 to $4,500 fee range comes from an adjacent Google Health page ([Google](https://developers.google.com/health/app-verification)) [primary, adjacent].<br>RAG: 20.1% of 358 sampled postings [own-count]. Chat UI: no source argues it helps. Auto-send: Zero's ungated MCP and voice paths [primary]. Extra connectors: Nango published ten 2026 advisories (SSRF, forged webhooks) [primary; URL not recorded]. | Medium | A real multi-user service (verification); measured low-risk reversible actions (never send) |
| **Out of scope: evals as a deliverable (owner decision)** | Literal "evals" appear in 18.7% and a broad match in 43.0% of 358 postings [own-count]; the 56% claim is unreproducible (section 6). The notes recommended a triage eval and a 50-email injection set; the owner chose standard tests only. N=50 caps claims: 0 of 50 gives a 95% Wilson interval of [0%, 7.1%] [own-count]. | Medium; inputs conflict | Targeting labs or startups, where the literal word is commoner |

## 3. Competitor landscape

**Chat assistants with connectors (including inbox assistants).** Claude asks approval per action by default (per-tool Always allow, Needs approval, Blocked) but research mode skips per-call approval [vendor-doc]. The Gemini app edits calendars and adds tasks with no approval prompt described ([Google](https://support.google.com/gemini/answer/14959807)) [vendor-doc]. Copilot in Outlook confirms only above five emails or for rule changes ([release notes](https://learn.microsoft.com/en-us/microsoft-365/copilot/release-notes)) [vendor-doc]. ChatGPT pages returned 403 [unverified]. Fyxer drafts are not auto-sent ([Fyxer](https://support.fyxer.com/article/how-fyxer-drafts-your-emails-and-where-to-find-them)) [vendor-doc]; Notion shut its mail client on 2026-09-22, citing agents ([9to5Mac](https://9to5mac.com/2026/06/25/notion-shutting-down-its-ai-powered-email-client-including-mac-and-ios-apps/)) [vendor-doc]. *Mistakes:* about 42 of 71 low-star reviews (Fyxer, Lindy, Motion, Superhuman) concern billing; others cite unapproved actions such as seats added or mail hidden ([Fyxer reviews](https://www.trustpilot.com/review/fyxer.com)) [anecdote]. No vendor documents undo, audit or a persistent cross-app queue [vendor-doc, absence].

**Agent and automation platforms.** Zapier's approval is an instruction and its MCP documents no per-call approval ([Zapier](https://help.zapier.com/hc/en-us/articles/41776074420493)) [vendor-doc]. Merge Agent Handler documents no approval step ([Merge](https://docs.merge.dev/merge-agent-handler/how-it-works)) [vendor-doc]. Composio holds provider credentials by default ([docs](https://docs.composio.dev/docs/security/token-custody.md)) [vendor-doc], then was breached (section 4). Nango uses AES-256-GCM with KMS-wrapped keys but audits only control-plane changes ([Nango](https://nango.dev/docs/guides/platform/security.md)) [vendor-doc]. Lindy's low reviews cite billing and credit burn ([Trustpilot](https://www.trustpilot.com/review/lindy.ai)). Pattern (inference): approval as advice to the model, central token stores, coarse permissions, thin audit.

**Open-source projects (read as code, not run) [primary].** Inbox Zero (about 12.9k stars) deprecated its approval states; rules auto-apply. Zero (about 10.9k stars) added approval on 2026-10-24 in one commit: one mutable table, a timeout becomes `failed`, MCP and voice paths ungated. LangChain's [executive assistant](https://github.com/langchain-ai/executive-ai-assistant) was archived 2026-07-27 with no tests; a user report on its tutorial repo says approved calls re-execute on resume. OpenClaw (about 394k stars) has 722 advisories, several on approvals outliving their context. No sampled repo combined an approval gate, send-level idempotency, safety evals and a threat model (about ten repos).

## 4. Documented incidents

| Date | What happened | Root cause | Fix status | Source | Confidence |
|---|---|---|---|---|---|
| 2025-06 | EchoLeak (CVE-2025-32711): crafted email leaks M365 Copilot data, zero-click. **CVSS 9.3 scored by Microsoft; 7.5 by NVD** | Chained bypasses ending in an auto-fetched image | Fixed server-side | [NVD](https://nvd.nist.gov/vuln/detail/CVE-2025-32711); [study](https://arxiv.org/abs/2509.10540) | primary (scores); secondary (mechanism) |
| 2025-06-19 | Atlassian MCP proof of concept: hostile ticket reaches a support engineer's AI | Injection plus insider privileges | "Still being investigated" | [Cato](https://www.catonetworks.com/blog/cato-ctrl-poc-attack-targeting-atlassians-mcp/); [Atlassian](https://www.atlassian.com/blog/company-news/mcp-risk-awareness) | primary note; details unverified |
| 2025-06 | Asana MCP exposure: some users saw other organisations' data; about 1,000 customers notified | Reported tenant-isolation flaw | Fixed | [Register](https://theregister.com/2025/06/18/asana_mcp_server_bug/) | secondary |
| 2025-07-10 | Gemini summary phishing: hidden HTML adds a fake alert | Hidden text obeyed | Partly mitigated, unconfirmed | [0DIN](https://0din.ai/blog/phishing-for-gemini) | primary |
| 2025-08 | Salesloft Drift: stolen OAuth tokens exported Salesforce data and read mail in a few Workspace accounts | Theft method not stated | Tokens revoked 2025-08-20 | [Google](https://cloud.google.com/blog/topics/threat-intelligence/data-theft-salesforce-instances-via-salesloft-drift) | primary |
| 2025-09-19 | Notion agent exfiltration: hidden PDF prompt, data sent in a search query | Private data, untrusted content, outbound tool | Notion response unverified | [CodeIntegrity](https://www.codeintegrity.ai/blog/notion) | primary (discloser) |
| 2025-12-08 | GeminiJack: Gemini Enterprise leaked data via shared docs, invites, emails | Unfiltered HTML image tag | Google fixed | [Infosecurity](https://www.infosecurity-magazine.com/news/google-fixes-gemini-enterprise-flaw) | secondary |
| 2026-01-12 | Superhuman AI exfiltration (PromptArmor): hidden email text sent other emails out via a markdown image to a pre-filled Google Form | Shared context, auto-rendered output, allowlisted docs.google.com | Patched 2025-12-09 to 2026-01-11 | [PromptArmor](https://promptarmor.com/resources/superhuman-ai-exfiltrates-emails) | primary (discloser) |
| 2026-01 | Gemini calendar-invite injection: dormant prompt wrote private meeting summaries into a new event | Calendar text as instructions; write tool | Google says addressed | [Hacker News](https://thehackernews.com/2026/01/google-gemini-prompt-injection-flaw.html) | secondary |
| 2026-01-07 | n8n CVE-2026-21858 (CVSS 10.0 as reported by the discloser): unauthenticated file read leads to admin takeover | Content-Type confusion in a form webhook | Fixed in 1.121.0 | [Cyera](https://www.cyera.com/research-labs/ni8mare-unauthenticated-remote-code-execution-in-n8n-cve-2026-21858) | primary (discloser) |
| 2026-02-09 | Claude Desktop Extensions: one calendar event can compromise a system running unsandboxed extensions; discloser claims CVSS 10 | Local extensions bridge low-risk input to a high-risk executor | Discloser says Anthropic declined to fix; status unknown | [Akamai/LayerX](https://www.akamai.com/blog/security-research/2026/feb/claude-desktop-extensions-rce) | primary (claims); Anthropic reply unverified |
| 2026-02-23 | OpenClaw inbox deletion despite "confirm first" | Not established | n/a | [OECD.AI](https://oecd.ai/en/incidents/2026-02-23-d55b) | secondary; anecdote |
| 2026-05-21 | Composio breach: 5,241 API keys potentially exposed; 5,001 GitHub connections | Employee OAuth token led to internal systems and a key cache | Remediated per 2026-09-16 update | [Composio](https://composio.dev/blog/composio-may-2026-security-incident) | primary (vendor) |

## 5. Platform constraints

**Google (checked 2026-11-03).**
- Gmail read, modify, compose and metadata scopes are restricted; `gmail.send` is sensitive ([scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)) [primary]. Calendar is sensitive per Google's example. Tasks is not on the restricted list ([list](https://support.google.com/cloud/answer/13464325)) [primary]; its tier is unstated, and one third-party doc says sensitive [secondary].
- Restricted data on a server needs verification plus a security assessment every 12 months ([Google](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)) [primary]. Sensitive review is officially up to 10 days; forums say 5 to 7 weeks [anecdote].
- Testing mode: 100 test users, 7-day authorisation expiry. Refresh tokens also die after six months unused, on password change with Gmail scopes, or beyond 100 per account per client ([Google](https://developers.google.com/identity/protocols/oauth2)) [primary].
- Gmail quota: 6,000 units per minute per user; `messages.send` and `watch` cost 100, `history.list` 2 ([quota](https://developers.google.com/workspace/gmail/api/reference/quota)) [primary]. Daily send limits and Calendar and Tasks quotas appear only in §9.1 and are unverified.
- Pub/Sub watch: renew at least every 7 days; one notification per second per user; a `history.list` 404 means full resync ([push](https://developers.google.com/workspace/gmail/api/guides/push), [sync](https://developers.google.com/workspace/gmail/api/guides/sync)) [primary].

**Microsoft Graph.** Access tokens last 60 to 90 minutes; refresh tokens default to 90 days; a user's password change leaves a confidential client's token alive ([Entra](https://learn.microsoft.com/en-us/entra/identity-platform/configurable-token-lifetimes), [refresh tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)) [vendor-doc]. `revokeSignInSessions` revokes every app's tokens and does not support personal accounts ([Graph](https://learn.microsoft.com/en-us/graph/api/user-revokesigninsessions?view=graph-rest-1.0)) [vendor-doc]. Throttling limits appear only in §9.1 and are unverified.

**Gemini on Vertex (page updated 2026-11-02).**
- Retired 2026-10-20: `gemini-2.5-flash`, `-flash-lite`, `-pro`. `gemini-3.5-flash-lite` retires no earlier than 2027-07-21; `gemini-3.8-flash` no earlier than 2027-09-02; `gemini-3.6-flash` on 2026-11-19 ([lifecycle](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions)) [primary]. The 2026-12-01 date is Developer API only ([deprecations](https://ai.google.dev/gemini-api/docs/deprecations)) [primary]. Serving after retirement was not tested [unverified].
- Developer API list prices per million input/output tokens: 3.5 Flash-Lite $0.30/$2.50; 3.8 Flash $0.75/$3.75 until 2026-12-31, then $1.50/$7.50 ([pricing](https://aistudio.google.com/docs/pricing)) [primary]. Vertex parity rests on trackers [secondary]. About $1.35 per 1,000 classifications [own-count].

## 6. Hiring-market context (for the resume goal only)

This informs how to present the project; it is not evidence for any product decision, and both counts (this one and the larger one in §9.2, which supersedes it for sample size) are convenience samples.

**Method (own-count, 2026-11-03).** Public job-board APIs (Greenhouse, Ashby, Lever) for 40 companies ([example](https://boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true)). Titles needed AI, ML, LLM, agent or generative plus engineer or developer; research, manager, solutions and similar roles were excluded; duplicates removed per company. n = 358; regex-based, not hand-audited; biased toward AI-native firms.

**Shares.** Literal "eval(s)" 18.7%; broad evaluation match 43.0%; observability 34.4%; tool use or MCP 22.1%; RAG 20.1%; guardrails or injection 17.3%; human-in-the-loop 12.0%; junior titles 3.4%. Literal/broad "evals" by employer: big tech outside labs 9.9%/29.2% (n=171); labs 14.3%/52.7% (n=91); startups 38.5%/58.3% (n=96), where one company wrote "evals" in 18 of 21 postings. A looser run (n=709) reached 62.9%, so the viral 56% ([dev.to](https://dev.to/ali_82f3409ed625e8ba8515b/ai-engineer-roadmap-2026-what-390-job-postings-actually-require-3jdo), no method) reproduces only if "evaluate" counts [secondary, single source].

**Employer statements.** Databricks asks for human-in-the-loop approvals, audit trails and automated evals ([posting](https://databricks.com/company/careers/open-positions/job?gh_jid=8829033002)); Anthropic asks for engineers who write evals ([posting](https://job-boards.greenhouse.io/anthropic/jobs/5445760008)) [primary]. PostHog's handbook counts side projects and says unexplained polish does not matter ([PostHog](https://posthog.com/handbook/people/hiring-process/engineering-hiring)) [primary]. One named hiring-blog author prefers one polished project with an honest README ([post](https://www.louisbouchard.ai/what-i-look-for-when-hiring-ai-engineers/)) [primary, single author].

**Limits.** No named big-tech hiring manager statement was found; GitHub dwell-time figures are vendor claims; nothing measures a portfolio project's effect on interviews.

## 7. Known conflicts and unverified items

| Topic | Sources disagree | Used here |
|---|---|---|
| EchoLeak severity | 9.3 (Microsoft) vs 7.5 (NVD) | Quote both |
| Gemini 2.5 retirement | 2026-10-20 (Vertex) vs 2026-12-01 (Developer API) | Both right; Vertex applies |
| Approval acceptance | 93% ([Anthropic, March](https://www.anthropic.com/engineering/claude-code-auto-mode)) vs 97% ([Anthropic, August](https://claude.com/blog/auto-mode-default-in-claude-code)); no researcher read both | "93 to 97%, self-reported, coding prompts" |
| Zero's pending actions | Early read: close match to OneBox. Pinned-commit read: thin, never-retry is a comment | Deep read; decays as Zero changes |
| Claude connector writes | Help says writes with approval; docs say read-only | Unresolved |
| Google verification | One page says sensitive apps must verify; another exempts personal use and testing | Unreconciled |
| Evals; Outlook | Notes recommend evals, owner excludes them; a research report defers Outlook, the product sequences it second | Owner decision; consistent |

**Unverified.** Tasks scope tier; whether Gmail preserves a client-set Message-ID (needed for reconciliation); whether unverified production avoids the 7-day expiry; Atlassian and Asana attack details; LayerX fix status and Anthropic's reply; Copilot unattended sending; sender-constrained provider tokens.

## 8. Evidence limits

- Compiled from internal notes only; nothing was re-checked and no code was run. Many pages are undated.
- The raw notes for two research rounds (platform research and final decision) were lost before this document was compiled. Their findings appear only in §9, reconstructed from the working session's written summaries, **without source URLs** and therefore marked [UNVERIFIED] until re-fetched.
- Not researched: ChatGPT first-party docs (unreadable), Reddit and app-store reviews, failure prevalence, email-to-event extraction accuracy, Outlook reconciliation reliability (spike pending).
- Absence of documentation is not proof of absence. Samples are small (about ten repositories, 71 low-star reviews) and dated snapshots; repository comparisons (2026-10-26), model dates, prices and postings decay.

## 9. Addendum — findings whose raw notes were lost (reconstructed 2026-11-06)

**Status of this section:** every item is **[UNVERIFIED]**. The sources were official documentation, public job-board data, repositories and papers, but the links were not kept. Re-fetch each before relying on it (see HANDOFF, "doc debt"). `ARCHITECTURE.md` §12 uses the limits in §9.1.

### 9.1 Provider limits and behavior (feasibility research, official documentation)

- **Gmail:** per-user quota 6,000 units per minute; `messages.send` costs 100 units. Consumer accounts: sending "more than 500 emails in a day" triggers a block lasting 1–24 hours; whether API sends count toward that cap was not stated.
- **Calendar:** 600 requests per minute per user. **Tasks:** 50,000 requests per day (courtesy limit).
- **Google OAuth Testing status:** 100 test users; authorizations expire seven days after consent. Not documented: whether the owner must be a test user, and how to re-authorize.
- **Google API terms:** the API Terms of Service (modified 2021-11-09) and the API Services User Data Policy (2024-02-15) say nothing about automated or load testing; the nearest clauses forbid circumventing limits and disrupting the APIs.
- **Python Google client:** `build(..., http=wrapper)` accepts a custom HTTP object (mutually exclusive with `credentials`, so wrap `AuthorizedHttp`). `execute(num_retries=0)` does not retry; with `num_retries>0` it re-sends POSTs such as `messages.send` on timeouts, connection errors, 429 and 5xx. The underlying httplib2 can silently re-send a POST on a stale connection, so judge duplicates by account state, not by wrapper call counts.
- **Idempotency hooks:** Calendar accepts a client-supplied event ID (collisions are not guaranteed to be detected); Graph events have `transactionId`. None found for Gmail send, Tasks insert, or Graph `sendMail`.
- **Microsoft Graph:** `sendMail` returns 202 with no body; Outlook limit 10,000 requests per 10 minutes per mailbox, 4 concurrent; personal accounts can register apps; no publisher-verification requirement found for personal-account use of one's own app; personal outlook.com sending limits not found.

### 9.2 Hiring-market count from primary job boards (fetched 2026-11-05)

- **Method:** public Greenhouse, Ashby and Lever JSON; 136 usable boards; 16,501 postings, of which 7,768 had engineering-type titles; the AI and agent subset is 1,151 postings from 108 companies. Convenience sample (no Google, Microsoft, Amazon or Meta); a keyword hit is not always a hard requirement.
- **Shares of the subset:** evals 28.1% (47.5% in roles with "agent" in the title); observability 18.9%; RAG 16.0%; human-in-the-loop with audit trail 11.9%; prompt injection, jailbreak or red-team 4.3%; idempotency, exactly-once or durable execution 3.1%; broad "distributed systems / retries" wording about 40%.
- **Closest postings to this design:** Supabase "AI Platform Engineer" (human review gate, audit log, evaluation layer), Databricks (human-in-the-loop approvals, audit trails, evals, prompt injection), Sierra and Okta (handling unclear results from external systems). Several employers say they value shipped work and public write-ups; one interview guide was unreadable (HTTP 403).
- This larger count supersedes the 358-posting sample in §6 for sample size; both are convenience samples.

### 9.3 Prior art (repositories, papers, dataset cards; GitHub and arXiv keyword search only)

- **AgentDojo:** MIT, last commit 2026-06-02; the Workspace suite is in memory and graded by state predicates; it has no approval, proposal-queue or fault concept. **Inspect AI:** MIT; custom tools, sandboxes, scorers and approval hooks. **LLMail-Inject:** MIT; a single send-email goal.
- **arXiv 2609.29095:** six simulated services, 12 fault modes, 25,930 episodes; the duplicate rate fell from 28% to 4% with keys on every write; code "available upon publication". **afterstate:** MIT, v0.1.0, targets a developer's own HTTP and SQL service. **agent-ledger** and **AgentLedger:** Apache-2.0 runtimes with idempotency and approval; no real-provider tests and no reconciliation.
- **Not found:** any harness testing write safety against real Gmail, Calendar, Tasks or Graph; any proposal-level injection benchmark for an approve-first queue.

### 9.4 Competition, platform risk, privacy and demand (platform research)

- **Crowded at both layers.** Multi-account AI mail: Superhuman, Fyxer, Spark, Missive, Lindy and others. Connector infrastructure: Nylas, Aurinko, Unipile, Unified.to, Merge, Composio, Nango, Zapier MCP, Pipedream, at roughly $1–2 per account per month (indicative).
- **Platform risk.** Google restricted-scope apps need an annual third-party security assessment; cost evidence conflicts (about $540 vendor-stated versus $15k–75k from a 2019–2020 anecdote). Microsoft publisher verification is free but needs a verified partner account, and apps registered with personal accounts cannot be verified. Exchange Web Services blocked by default from 2026-10-01 and off for good from 2027-04-01 (secondary mirror).
- **Consolidation.** Clockwise shut down on 2026-03-27 after Salesforce hired its team; Reclaim was bought by Dropbox; Grammarly bought Superhuman (2025); Notion Mail shut down on 2026-09-22; Gmail made Gemini features free in January 2026.
- **Privacy.** Every product checked says it does not train on user data; Fyxer and Superhuman say their model providers retain nothing (secondary). Provider-side retention: OpenAI keeps abuse logs up to 30 days by default; Azure's abuse-monitoring store can include human review unless an opt-out is approved; Vertex may log prompts for abuse monitoring and its in-memory cache lasts up to 24 hours; Anthropic zero retention is per organization and sales-gated. The honest claim is minimal retention plus a verifiable log, not "zero retention".
- **Demand.** No survey on multi-provider demand was found. One opt-in survey (n=1,000, August 2026): 25% would let AI manage their calendar and 46% cite privacy as a main worry.
- **Tool sprawl.** Asana's long-running Anatomy of Work index reports roughly 60% of the workday spent on coordination; the figure is survey-based and its wording varies between editions [secondary; found by search, page not re-read].

## 10. Spike results

One row per spike (ROADMAP task 2.1 and others), added when the spike is run. Never include tokens, addresses or mail content.

| Date | Question | Result | Commit |
|---|---|---|---|
| — | Does Gmail preserve a client-set Message-ID on send? | not run | — |
| — | Does `include_granted_scopes` defeat read and propose scope tiering? | not run | — |
| — | Which tier is the Google Tasks scope? | not run | — |
| — | Is a Sent Items lookup reliable for Outlook reconciliation? | not run | — |

## 11. User observations

One row per round of sessions (task 4.5; plan in [PRD.md](PRD.md) §4). Participants are P1–P5; no names, no mail content.

| Date | Participants | Finished tasks 1–3 unaided | Preferred queue or both | Decision |
|---|---|---|---|---|
| — | — | not run | not run | — |
