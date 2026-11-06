# OneBox — Frontend and design system

**Status:** plan · 2026-11-06 · **Starts after the backend loop exists** (task 4.0 in [TASKS.md](TASKS.md)). Until then this document only fixes what the screens must do, so the API is shaped for them.

## 1. Principles

1. **Plain text only.** Anything from a model, an email, an invite or a ticket is rendered as text. No markdown, no HTML, no images, no links that load or open by themselves.
2. **One queue.** Everything that needs a decision is a card in one list.
3. **Honest status.** A card shows exactly what the server says. Never "done" before the provider confirmed.
4. **Show what will happen.** Real recipient, real time, real text, and the source mail.
5. **Calm and keyboard-first.** No animation that carries meaning, no toasts that hide errors, no "approve all".
6. **No chat UI.** The assistant surfaces through cards, not a conversation.

## 2. Screens (four)

| Screen | Purpose | Main content | Primary actions | API |
|---|---|---|---|---|
| **Suggestions** | Decide what OneBox proposes | List of `ActionCard`, newest first; filter by status | Approve · Edit · Reject · Open source mail | `GET /actions`, `POST /actions/{id}/approve`, `/reject` (built); edit-before-approve (planned) |
| **Digest** | See what needs attention | Short summaries of new mail, most important first | Open source mail; jump to its suggestions | `GET /mail/triage` (built); ranking (planned) |
| **Activity** | See everything that happened | One timeline across apps: proposed, approved, done, needs checking | Resolve *needs checking* | Activity endpoint (planned, task 4.1); audit events exist |
| **Connect and settings** | Control access | One `ConnectionCard` per app with the Off / Read / Propose switch; the "+" button; time zone and working hours; Disconnect; Delete my data | Add, switch, disconnect, delete | Connections and settings endpoints (planned, phases 1–2) |

Every screen has **loading, empty and error** states. Demo mode shows a permanent "Demo data" banner.

## 3. Components

| Component | Purpose | Notes |
|---|---|---|
| `ActionCard` | One proposal | Variants reply, event, task. Shows the exact payload, source link, short reason, conflict notice, status badge, history. Buttons: Approve, Edit, Reject. |
| `StatusBadge` | One of the seven states | Icon plus text, never colour alone (see §4) |
| `UntrustedText` | Renders any outside text | Plain text; truncates with "show more"; marks invisible and direction-override characters; never auto-links |
| `SourceLink` | Opens the original mail in its own app | Shown as text with an explicit open action; `rel="noopener noreferrer"` |
| `ConflictNotice` | Calendar conflict warning | Names the conflicting event and the user's local time |
| `DigestItem` | One mail summary | Sender, short summary, link to suggestions |
| `ConnectionCard` | One connected app | Account label, status, three-way switch, Disconnect |
| `ActivityTimeline` | Chronological list | Group by day; each entry links to its card |
| `ErrorNotice`, `EmptyState` | Feedback | Uses the server's stable error code and field names |

The seven `StatusBadge` states and their wording: `pending` → "Waiting for you"; `processing` → "Running"; `succeeded` → "Done"; `failed` → "Failed"; `rejected` → "Rejected"; `expired` → "Expired"; `reconciliation_required` → "Needs checking".

## 4. Tokens [PROPOSED — verify contrast with a checker before use]

Semantic tokens with light and dark values, defined once as CSS variables.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--surface` | #FFFFFF | #14161A | Page |
| `--surface-raised` | #F6F7F9 | #1C1F25 | Cards |
| `--text` | #14161A | #E8EAED | Body |
| `--text-muted` | #4A5160 | #A5ABB8 | Secondary |
| `--border` | #D5D9E0 | #2E333C | Dividers |
| `--accent` | #2F5BEA | #7A9BFF | Primary action |
| `--success` / `--warning` / `--danger` / `--info` | #1E7A46 / #8A5A00 / #B3261E / #1F6C99 | #5FD08A / #F0B84A / #FF8A80 / #6EC1F0 | Status |

- **Type:** system font stack; scale 12 / 14 / 16 / 20 / 24 / 32; line height 1.5 for body.
- **Spacing:** 4-pixel base (4, 8, 12, 16, 24, 32, 48). **Radius:** 6 and 12. **Elevation:** flat; one border, no heavy shadows.
- **Motion:** 150 ms fades only, and none when the user prefers reduced motion.
- **Contrast:** text at least 4.5:1, interface elements at least 3:1, in both themes.

## 5. Security rules in the interface

1. Render all outside text through `UntrustedText`. Add fixtures: a markdown image, a link, zero-width characters, a right-to-left override, and very long text.
2. Show the **verified sender**. If a `Reply-To` or `Cc` differs, show that difference on the card.
3. Approve, reject and edit are POST requests carrying the CSRF token; never GET.
4. Wait for the server's answer before changing a card's status. No optimistic "Done".
5. No "approve all", no "always allow" in version one.
6. After approving, show the final state the server returns and a link to its history.

## 6. Data and state

- A typed client from the OpenAPI document; the drift test already guards `frontend-client-types.ts`.
- Server state only; no client-side copy of tokens or mail bodies. The pending list refreshes by polling (about every 30 seconds) because the server has no push channel to the client.
- Session cookie is HTTP-only and not readable by scripts.

## 7. Accessibility (WCAG 2.2 AA)

- Every action reachable by keyboard in reading order; visible focus.
- A card is an `article`; its buttons have names like "Approve reply to {recipient}".
- Status changes are announced through a polite live region; status is never colour alone.
- Targets are at least 24 by 24 pixels; text resizes to 200%; reduced motion respected.
- Checks: an automated accessibility scan on each screen, one keyboard-only walk-through, and a screen-reader pass on the suggestions screen.

## 8. Stack and structure [OPEN until task 4.0]

Default: Vite, React and TypeScript; accessible headless primitives plus the CSS variables above; Vitest and Testing Library, with an accessibility scan in tests. Keep one folder per screen and one shared components folder. No state-management library until a screen needs it.

## 9. Build order

1. **4.0 walking skeleton:** tokens, `ActionCard`, `StatusBadge`, `UntrustedText`, the Suggestions screen against the real API. This proves the contract early and is what a reviewer sees first.
2. **4.1** API additions the other screens need.
3. **4.2** Digest, Activity, Connect and settings.
4. **4.3** Needs-checking and conflict states; accessibility pass.

## 10. Definition of done for any screen

Loading, empty and error states exist; the untrusted-text fixtures render as plain text; the keyboard walk-through passes; the accessibility scan is clean; a screenshot was reviewed in light and dark.
