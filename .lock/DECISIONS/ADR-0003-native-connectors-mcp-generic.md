# ADR-0003: Native connectors for Google and Microsoft; MCP only as the generic path

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Users want more than one app. Writing an adapter per app is slow; wrapping an app's MCP server is fast but cannot check whether a write landed. Unified-API vendors (Nylas, Merge, Composio, Nango) sell the connector layer but take custody of tokens; Composio's May 2026 breach exposed credentials it held by default.

## Decision

Google (Gmail, Calendar, Tasks) and Microsoft (Outlook via Graph) are **native connectors** implementing the full contract, including `reconcile()`. Other apps use an **MCP connector** later: every write tool is wrapped as a proposal, tool annotations (`readOnlyHint`, `destructiveHint`) are untrusted hints, OneBox keeps its own allowlist, tokens are never forwarded, and unknown outcomes stay *needs checking*.

## Options considered

- **A. All native:** best semantics, slowest.
- **B. All MCP:** fastest; no reconciliation; trusts third-party servers.
- **C. Unified-API vendor:** custody of tokens, cost, hides provider behavior.
- **D. Hybrid (chosen).**

## Trade-offs

Solves: real reconciliation where it matters, fast breadth where it does not. Worsens: two code paths. Revisit when: a third native connector is requested.

## Consequences

Official hosted MCP servers exist for Atlassian (Jira) and Notion (aggregator sources; verify with vendor docs); none was found for Outlook (community only) or Zoho. Graph's `sendMail` returns 202 with no body and no documented idempotency, so Outlook reconciliation is weaker than Gmail's and must be stated.

## Action items

1. [ ] Contract tests every connector must pass.
2. [ ] Spike: Sent Items lookup reliability for Outlook.
