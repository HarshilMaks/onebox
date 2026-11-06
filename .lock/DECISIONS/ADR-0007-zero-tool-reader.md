# ADR-0007: The model that reads untrusted content has no tools

**Status:** Accepted · **Date:** 2026-11-06 · **Deciders:** project owner

## Context

Email-borne prompt injection is documented (EchoLeak; the Superhuman AI exfiltration through an image link). Meta's Agents Rule of Two says a session should combine at most two of: untrusted input, access to private data, and the ability to change state; Meta also says the rule is not sufficient on its own. A published design-patterns paper lists a quarantined, tool-less reader as the Dual-LLM pattern and warns that such a reader can still produce attacker-influenced output.

## Decision

Any model run that sees untrusted content (mail bodies, invites, tickets) has **no tools** and returns **typed fields**. Plain code validates them (reply recipients and event attendees are the verified sender only; dates parse; the slot is free on the user's local day; bidi and control characters are rejected and look-alike domains are shown in punycode; a daily cap applies) and creates proposals. "Verified sender" means the receiving server recorded `dmarc=pass` for the message; otherwise the mail is shown but earns no reply or invite proposal. The reader sees one message and no calendar data. Output is rendered as plain text. The model has no outbound channel (no URL fetch, image load or web search).

## Options considered

- **A. One tool-enabled agent that reads mail:** simplest; exactly the pattern that failed publicly.
- **B. Plan-then-execute:** untrusted content can still shape arguments such as recipients.
- **C. Zero-tool reader plus deterministic proposal creation (chosen).**

## Trade-offs

Solves: injection cannot act. Worsens: less flexible than a free agent. Revisit when: a documented incident occurs, or a tool-enabled run must read untrusted content.

## Consequences

The human approval is the break in the Rule of Two. Attacker-influenced text can still reach the approver, so every proposal shows its exact payload and a link to the source, and a daily cap limits volume. Do not claim "cannot be tricked" or "Rule of Two compliant".

## Action items

1. [ ] Invariants 7, 8 and 12 as tests (triage has zero tools; recipient rule; prompt-breakout markers).
2. [ ] Plain-text rendering test with a markdown image and link fixture.
