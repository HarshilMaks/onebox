# ADR-0009: Demo strategy — Testing mode plus a no-sign-in demo mode

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Restricted Gmail scopes (`gmail.modify` and broader) require OAuth verification and an annual third-party security assessment for apps that store or pass data through a server; cost evidence conflicts. Google's "Testing" status allows up to 100 allowlisted users, expires refresh tokens after seven days, and shows an unverified-app warning. Reviewers should not have to connect their own Gmail.

## Decision

Do not pursue verification. Use (1) Google Testing mode with a dedicated demo account and an allowlist, (2) a **demo mode** with no sign-in: a seeded mailbox, canned model output and fake providers, and (3) a recorded walkthrough.

## Options considered

- **A. Verify and launch:** annual cost, calendar time, repeated yearly.
- **B. Video only:** cheap, not interactive.
- **C. Testing mode plus demo mode plus video (chosen).**

## Trade-offs

Solves: a safe, repeatable demonstration without third-party consent. Worsens: weekly re-consent for real accounts; demo mode is not live data. Revisit when: a real multi-user service is intended.

## Consequences

Whether replay mode on synthetic data falls outside Google's user-data policy is an inference; no Google page addresses it. The README must say plainly what is real and what is canned.

## Action items

1. [ ] Build demo mode (ROADMAP phase 6) with the same code path as real mode where possible.
2. [ ] Show a clear "reconnect needed" state when a Testing-mode token expires.
