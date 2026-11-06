# ADR-0006: Store encrypted refresh tokens; revoke at the provider on disconnect

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Background mail triage needs refresh tokens while the user is away, so "nothing is stored" cannot be literally true. Third-party token custody has failed publicly: Composio's May 2026 breach exposed stored credentials, Salesloft Drift (August 2025) was OAuth token theft, and an n8n file-read flaw exposed the encryption secret kept in config.

## Decision

Keep refresh tokens in the database encrypted with AES-256-GCM, with associated data bound to the connection and a format version (`v2`), not to the mutable account email, and a keyring with key IDs for rotation; `v1` stays decryptable during migration ([ADR-0012](ADR-0012-data-lifecycle.md)). Today the keyring is a read-only file mounted from the host, already outside the database. Target: a secret manager. Decrypt per call, never log. Disconnect revokes at the provider and deletes the stored token. Remove the legacy plaintext `token_json` column after backfill.

## Options considered

- **A. Session-only tokens:** no background triage.
- **B. Third-party custody** (Composio-style): their breach becomes ours.
- **C. Plaintext:** rejected.
- **D. Encrypted at rest, key outside the DB (chosen).**

## Trade-offs

Solves: background work without plaintext secrets. Worsens: tokens remain a target. Revisit when: a KMS is available.

## Consequences

Provider behavior to design around: Microsoft does not revoke old refresh tokens on rotation, a confidential-client token can stay alive after a password change, and session revocation is unsupported for personal accounts. Google Testing-mode tokens expire in seven days. Allowed claim: "OneBox never sees your password and keeps no chat memory. Tokens are encrypted; proposals, summaries and audit events are stored as plain text with a retention limit." The delete-everything claim waits for ROADMAP 2.6.

## Action items

1. [ ] Backfill and drop the legacy plaintext column.
2. [ ] Canary test: tokens never appear in logs or prompts.
3. [ ] Real revoke on disconnect (ROADMAP 2.4).
