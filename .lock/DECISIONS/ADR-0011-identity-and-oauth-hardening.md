# ADR-0011: Identity and OAuth hardening

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Today the OAuth callback is unauthenticated, and its state binds only the user id and the expected email. For Google that is enough, because the connecting account must equal the user's own email. With a second Google account or Outlook that check disappears: an attacker can start "+", send the provider link to a victim, and when the victim consents, the victim's token lands on the attacker's user, who can then approve actions as the victim. Separately, the planned sign-in path would keep `python-jose` 3.3.0 and an HS256 external-issuer JWT route, and a demo login in the real stack would mint anonymous sessions.

## Decision

1. **The OAuth state row stores a hash of the session id and a PKCE verifier** (single use, 10 minutes). The callback requires a live session whose user equals the state's user.
2. **Sign-in validates ID tokens with Google's own library.** `python-jose` and the HS256 external-JWT path are deleted when sessions land, not left behind a flag.
3. **Sessions are opaque and server-side:** a `__Host-` cookie that is HttpOnly, Secure and SameSite=Lax, with idle and absolute timeouts, a CSRF token and an Origin check on state-changing routes.
4. **Demo mode is a separate deployment** with no real connections. It is never a login option in the real stack.

## Options considered

- **A. Keep email equality as the only binding:** fine for one Google account, unsafe with more.
- **B. Session-bound state and our own sessions (chosen).**
- **C. A managed auth provider:** faster, but adds a vendor and hides what this project is meant to show.

## Trade-offs

Solves: forwarded-link account linking and bypass-by-config. Worsens: more code than a library flow. Revisit when: a managed auth provider is adopted, or more than one deployment exists.

## Consequences

Invariant 16 and threat T16; tasks 1.1, 1.2, 2.3 and 6.1 in [TASKS.md](../TASKS.md). The sign-in flow in [ARCHITECTURE.md](../ARCHITECTURE.md) §8.1 gains the session binding.

## Action items

1. [ ] ID-token validation tests: wrong audience, tampered nonce, expired (task 1.1).
2. [ ] Session, CSRF and forgery tests; remove `python-jose` and the HS256 path (task 1.2).
3. [ ] A forwarded-link test for the "+" flow (task 2.3).
