# ADR-0005: Per-app permission switches — Off, Read, Propose

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Competitors' controls are coarse or hard to scope: Zapier cannot scope its MCP separately, and Rovo cannot restrict by group (as documented). Users need a control they can understand at a glance.

## Decision

Each connection has one switch. **Off:** the token is not used. **Read:** read scopes and read tools only. **Propose:** read plus proposals (never direct writes). Enforced by the per-run tool allowlist (`server/agent_policy.py`) and re-checked when an approved action is claimed ([ADR-0010](ADR-0010-approval-and-execution-protocol.md)). Where the provider allows it, write scopes are requested only when Propose is enabled (scope tiering), but a granted scope cannot be downgraded without a re-consent or a revoke, and **Off** leaves a live token unused: only Disconnect removes it.

## Options considered

- **A. Per-tool toggles:** precise, but too many controls.
- **B. A global autonomy dial:** conflicts with ADR-0001.
- **C. Three-way switch per app (chosen).**

## Trade-offs

Solves: simple, enforceable, explainable. Worsens: coarse. Revisit when: per-tool control is requested.

## Consequences

Spike required: Google's `include_granted_scopes` may merge earlier grants, which could defeat scope tiering. If it does, enforce at the policy layer and request write scopes only when Propose is on, accepting that downgrading may need a re-consent or a revoke.

## Action items

1. [ ] Policy tests: Off blocks every token use; Read blocks proposals.
2. [ ] Close the `include_granted_scopes` spike (TASKS 2.1).
