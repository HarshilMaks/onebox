# ADR-0006: Store encrypted refresh tokens; revoke at the provider on disconnect

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Background mail triage needs refresh tokens while the user is away, so "nothing is stored" cannot be literally true. Third-party token custody has failed publicly: Composio's May 2026 breach exposed stored credentials, Salesloft Drift (August 2025) was OAuth token theft, and an n8n file-read flaw exposed the encryption secret kept in config.

## Decision

Keep refresh tokens in the database encrypted with AES-256-GCM, with associated data bound to the user (and, later, the connection) and a keyring with key IDs for rotation. Target: keep the key outside the database and the app host's config (a key-management service or a separate secret store). Decrypt per call, never log. Disconnect revokes at the provider and deletes the stored token. Remove the legacy plaintext `token_json` column after backfill.

## Options considered

- **A. Session-only tokens:** no background triage.
- **B. Third-party custody** (Composio-style): their breach becomes ours.
- **C. Plaintext:** rejected.
- **D. Encrypted at rest, key outside the DB (chosen).**

## Trade-offs

Solves: background work without plaintext secrets. Worsens: tokens remain a target. Revisit when: a KMS is available.

## Consequences

Provider behavior to design around: Microsoft does not revoke old refresh tokens on rotation, a confidential-client token can stay alive after a password change, and session revocation is unsupported for personal accounts. Google Testing-mode tokens expire in seven days. Allowed claim: "OneBox never sees your password, keeps no chat memory, stores encrypted tokens, proposals, summaries and audit events, and one button deletes them."

## Action items

1. [ ] Backfill and drop the legacy plaintext column.
2. [ ] Canary test: tokens never appear in logs or prompts.
3. [ ] Real revoke on disconnect (ROADMAP 2.4).
