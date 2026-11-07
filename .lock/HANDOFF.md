# OneBox — Handoff

**Status:** living · 2026-11-06. Read this first when resuming. It states where the project is and gives a prompt for the next working session.

## 1. Where we are

- **Code baseline:** the code is unchanged since commit `4599ff1` (the PR #6 merge, identical to `55b6dbd`); later commits touch only `.gitignore` and `.lock/`.
- **Verified on 2026-11-05 at that baseline:** lint clean; OpenAPI drift check passes (34 paths); one Alembic head with no schema drift; **246 tests pass** (unit plus integration on real PostgreSQL); the API boots and answers `/livez`, `/readyz`, `/actions` (401 without a token) and `/mail/triage`.
- **Built:** the approval ledger and its states; Gmail, Calendar and Tasks writes with deterministic IDs and markers; reconciliation; Pub/Sub push, notification jobs with leases, triage with zero tools; encrypted credential storage; list endpoints; CI.
- **Not built:** sign-in and sessions, connections and switches, email-to-proposals bridge, web client, Outlook, demo mode, deploy, rate limits, disconnect.
- **Decisions are locked in** [DECISIONS/](DECISIONS/). The plan is [ROADMAP.md](ROADMAP.md) (phases and totals) and [TASKS.md](TASKS.md) (every task). The next task is 0.1.

## 2. Resume prompt (full version)

```
Resume the OneBox project (Python 3.12, FastAPI async, PostgreSQL via SQLAlchemy async + Alembic,
Redis, Gemini on Vertex AI, Google OAuth and Pub/Sub). Read .lock/README.md, then .lock/PRD.md,
.lock/ARCHITECTURE.md, .lock/ROADMAP.md, .lock/TASKS.md, .lock/AGENT_GUIDE.md and the ADRs in
.lock/DECISIONS/ before changing anything.

Task: execute TASKS Phase 0 in the listed order starting at task 0.1, using the commit plan under each task. Minor tasks commit directly
on main; major tasks go on a new branch with a plain name (feat/, fix/, refactor/, docs/ or chore/
prefix, never claude/), as set out in .lock/ENGINEERING_PRACTICES.md section 3 and the "Lands"
column of .lock/TASKS.md.
Workflow per task: state the task and its done-when in one line; write the failing test first
and run it to confirm it fails for the intended reason (RED); implement the smallest fix to GREEN;
refactor with tests green; run lint, the OpenAPI drift check, migrations check and the full suite;
review the diff (and security-review anything touching auth, tokens or external writes); commit
with a conventional message.

Constraints: the AI only proposes and a human approves (ADR-0001); every external write goes
through the ledger; no secrets or personal data in code, docs or logs; minor changes on main and major
changes on a plainly named branch (never claude/); do not push without explicit approval; do not add anything outside the roadmap (freeze rule); keep
changes behavior-neutral unless the step says otherwise.

Acceptance: each step's exit criteria shown by a passing test or command output; suite green;
docs in .lock updated in the same change; report what was run and what was not.
Model: a strong model for planning and security-sensitive steps; a standard coding model for
implementation.
```

## 3. Resume prompt (quick version)

```
Read .lock/TASKS.md. Do Phase 0 task by task, in the listed order: failing test first, minimal fix, refactor,
lint + OpenAPI check + full suite, conventional commit. Minor on main, major on a plain branch
(never claude/). No push without approval.
```

## 4. Environment traps

- Minor work happens on `main`, major work on a branch (see ENGINEERING_PRACTICES section 3). Before starting, confirm `main` is up to date with `origin/main` and the working tree is clean. Prefer the main checkout over an auto-created worktree, whose branch name would start with `claude/`.
- Tests need a throwaway PostgreSQL (not Redis); the required environment variable **names** are in `.github/workflows/backend.yml` and `.env.example`. Never commit values.
- **Integration tests that need PostgreSQL skip silently when it is unreachable** (44 collected tests: without a database a run shows 202 passed and 44 skipped). Confirm they actually ran before trusting a green result.
- CI has a PostgreSQL service but no Redis service, and no coverage or dependency-scanning tooling.
- Local automation hooks may ask for stated facts before the first shell command or new file; state them and retry.
- Run databases in disposable containers and remove them afterwards.
- Generated OpenAPI (`docs/openapi.json`) must be regenerated whenever routes change.

## 5. Doc debt (do early)

- [ ] **Finish re-fetching the sources behind [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md) §9.** Done on 2026-11-07: provider limits, platform costs and model prices (§9.1, part of §9.4, `ARCHITECTURE.md` §12). Still unverified: the 16,501-posting hiring count (§9.2), prior-art licenses (§9.3), and the consolidation, privacy and demand claims (§9.4).
- [ ] Render the Mermaid diagrams on a GitHub preview and fix any syntax errors; they were not rendered locally.
- [ ] Run the commands in `ENGINEERING_PRACTICES.md` once; they were derived from the repo files, not executed.

## 6. Working agreements

- Docs in `.lock/` change in the same change (commit or branch) as the behavior they describe.
- A locked decision changes only through a new ADR.
- Claims follow [THREAT_MODEL.md](THREAT_MODEL.md) §8.
- Evidence lives in [RESEARCH_EVIDENCE.md](RESEARCH_EVIDENCE.md); anything not there is marked unverified.
