# ADR-0004: Sign-in (OpenID Connect) is separate from per-app OAuth consent

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Today OAuth start requires a JWT issued by a system that does not exist, so no real user can begin. Sign-in (who you are) and access (what OneBox may do in each app) are different questions and different risks.

## Decision

Sign in with OpenID Connect (`openid email profile`). Validate the ID token (signature, issuer, audience, expiry, nonce) once; key the user by the subject (`sub`). Use PKCE, exact redirect URIs and one-time state. The session is opaque and server-side. Each app is authorized separately when the user clicks "+", requesting only that app's scopes (incremental authorization). OneBox never sees a password.

## Options considered

- **A. Externally issued JWT (current):** no issuer exists.
- **B. Google sign-in plus all scopes at once:** one screen, broad access, poor least privilege.
- **C. OIDC sign-in plus per-app consent (chosen).**

## Trade-offs

Solves: least privilege and a clear mental model. Worsens: more consent steps. Revisit when: Outlook ships (optional Microsoft sign-in).

## Consequences

In Google Testing mode, refresh tokens expire after seven days, so "reconnect needed" must be a clean state. Google's own page on incremental authorization could not be read; the pattern is taken from an IETF draft and third-party sources.

## Action items

1. [ ] ID-token validation tests: wrong audience, tampered nonce, expired.
2. [ ] `invalid_grant` handled as "reconnect needed", never a 500.
