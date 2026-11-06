# OneBox — Handoff

**Status:** living · 2026-11-06. Read this first when resuming. It states where the project is and gives a prompt for the next working session.

## 1. Where we are

- **Code baseline:** commit `55b6dbd` (same code as `origin/main` at `4599ff1`).
- **Verified on 2026-11-05 at that baseline:** lint clean; OpenAPI drift check passes (34 paths); one Alembic head with no schema drift; **246 tests pass** (unit plus integration on real PostgreSQL); the API boots and answers `/livez`, `/readyz`, `/actions` (401 without a token) and `/mail/triage`.
- **Built:** the approval ledger and its states; Gmail, Calendar and Tasks writes with deterministic IDs and markers; reconciliation; Pub/Sub push, notification jobs with leases, triage with zero tools; encrypted credential storage; list endpoints; CI.
- **Not built:** sign-in and sessions, connections and switches, email-to-proposals bridge, web client, Outlook, demo mode, deploy, rate limits, disconnect.
- **Decisions are locked in** [DECISIONS/](DECISIONS/). The plan is [ROADMAP.md](ROADMAP.md). The next step is Phase 0, step 0.1.

## 2. Resume prompt (full version)

```
Resume the OneBox project (Python 3.12, FastAPI async, PostgreSQL via SQLAlchemy async + Alembic,
Redis, Gemini on Vertex AI, Google OAuth and Pub/Sub). Read .lock/README.md, then .lock/PRD.md,
.lock/ARCHITECTURE.md, .lock/ROADMAP.md and the ADRs in .lock/DECISIONS/ before changing anything.

Task: execute ROADMAP Phase 0, one commit per step directly on main (no branches, no pull requests), starting at step 0.1.
Workflow per step: state the step and its exit criteria in one line; write the failing test first
and run it to confirm it fails for the intended reason (RED); implement the smallest fix to GREEN;
refactor with tests green; run lint, the OpenAPI drift check, migrations check and the full suite;
review the diff (and security-review anything touching auth, tokens or external writes); commit
with a conventional message.

Constraints: the AI only proposes and a human approves (ADR-0001); every external write goes
through the ledger; no secrets or personal data in code, docs or logs; commit directly to main but
do not push without explicit approval; do not add anything outside the roadmap (freeze rule); keep
changes behavior-neutral unless the step says otherwise.

Acceptance: each step's exit criteria shown by a passing test or command output; suite green;
docs in .lock updated in the same change; report what was run and what was not.
Model: a strong model for planning and security-sensitive steps; a standard coding model for
implementation.
```

## 3. Resume prompt (quick version)

```
Read .lock/ROADMAP.md. Do Phase 0 step by step: failing test first, minimal fix, refactor,
lint + OpenAPI check + full suite, conventional commit. No push without approval.
```

## 4. Environment traps

- Work happens directly on `main`; confirm it is up to date with `origin/main` before starting, and that the working tree is clean.
- Integration tests need a throwaway PostgreSQL and Redis; the required environment variable **names** are in `.github/workflows/backend.yml` and `.env.example`. Never commit values.
- **Integration tests that need PostgreSQL skip silently when it is unreachable** (about 41 of them). Confirm they actually ran before trusting a green result.
- CI has a PostgreSQL service but no Redis service, and no coverage or dependency-scanning tooling.
- Local automation hooks may ask for stated facts before the first shell command or new file; state them and retry.
- Run databases in disposable containers and remove them afterwards.
- Generated OpenAPI (`docs/openapi.json`) must be regenerated whenever routes change.

## 5. Doc debt (do early)

- [ ] **Re-fetch the sources behind [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) §9** (provider quotas, the 16,501-posting hiring count, prior-art licenses, platform-risk and privacy claims). The original notes were lost and §9 is marked unverified; `ARCHITECTURE.md` §12 depends on the quotas.
- [ ] Render the Mermaid diagrams on a GitHub preview and fix any syntax errors; they were not rendered locally.
- [ ] Run the commands in `ENGINEERING_PRACTICES.md` once; they were derived from the repo files, not executed.

## 6. Working agreements

- Docs in `.lock/` change in the same pull request as the behavior they describe.
- A locked decision changes only through a new ADR.
- Claims follow [THREAT_MODEL.md](THREAT_MODEL.md) §8.
- Evidence lives in [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md); anything not there is marked unverified.
