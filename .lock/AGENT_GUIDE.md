# OneBox — Agent working guide

**Status:** living · 2026-11-06 · how AI agents (and people) do work in this repository. It follows the agentic-engineering approach: define "done" first, decompose into small verifiable units, route work to the right model, measure with checks.

## 1. Read first

[README.md](README.md) → [PRD.md](PRD.md) → [ARCHITECTURE.md](ARCHITECTURE.md) → [ROADMAP.md](ROADMAP.md) and [TASKS.md](TASKS.md) → the ADRs in [DECISIONS/](DECISIONS/) → [HANDOFF.md](HANDOFF.md). Do not change code before reading the task, its dependencies and the ADRs it touches.

## 2. Rules that never bend

1. The AI only proposes; every external write goes through the ledger ([ADR-0001](DECISIONS/ADR-0001-approve-first.md)).
2. A model that reads untrusted content has no tools ([ADR-0007](DECISIONS/ADR-0007-zero-tool-reader.md)).
3. No secrets, tokens, mail content or personal data in code, tests, docs, logs or commit messages.
4. Never push, change git configuration or edit tool settings without being asked.
5. Minor work on `main`, major work on a plainly named branch, never `claude/` ([ENGINEERING_PRACTICES.md](ENGINEERING_PRACTICES.md) §3).
6. Nothing outside the roadmap (freeze rule). If something new seems necessary, write it down and stop.
7. Never claim success without command output. Say what was run and what was not.

## 3. The loop for one task

1. **Define.** Restate the task from [TASKS.md](TASKS.md): done-when, invariants (`I-n`), threats (`T-n`), dependencies, commit plan.
2. **Baseline.** Run the suite and note failures that already exist. Confirm the PostgreSQL-backed tests ran.
3. **RED.** Write the failing test and confirm it fails for the intended reason, not a setup error.
4. **GREEN.** Smallest change that passes.
5. **Refactor** with tests green.
6. **Gates.** Lint, the OpenAPI drift check, the migrations check, the full suite. For auth, tokens or external writes, also a security review of the diff.
7. **Commit** according to §6, then update the task's status and any doc whose behavior changed, in the same change.
8. **Report:** what changed, which gates ran and their result, what did not run, retries, and anything surprising.

## 4. Task decomposition: the 15-minute unit

Split every task into units where each is **independently verifiable**, has **one dominant risk**, and has **a clear done condition**. A unit that needs two different kinds of thinking (for example schema design and prompt design) is two units. Units map to commits in §6; a task listed as M or L in [TASKS.md](TASKS.md) is several units.

## 5. Model routing

| Work | Tier |
|---|---|
| Formatting, boilerplate, fixtures, docs touch-ups, narrow edits | Smallest tier |
| Implementation, refactors, writing tests | Middle tier |
| Architecture changes, approval and ledger semantics, token and session design, root-cause analysis, multi-file invariants | Strongest tier |

Escalate only when a lower tier has failed with a clear reasoning gap. Do not escalate for style.

## 6. Commit granularity

**One commit is one logical step that builds and passes the suite on its own.** Tests travel with the code they test. Typically a feature is three to six commits, in this order when relevant: schema or migration → core logic → wiring → API contract and client types → docs.

- **Not one lump.** A whole feature in one commit cannot be reviewed or reverted.
- **Not noise.** No commit for a typo, a rename in the same area or a forgotten import; fold those into the commit they belong to before committing.
- **No red commits on `main`.** A failing test is committed together with the fix that makes it pass.
- **Subjects:** lowercase conventional (`feat`, `fix`, `test`, `docs`, `refactor`, `chore`), imperative, no scope; the body explains *why*.

**Worked example — task 3.2, the validator** (four commits, each green):

1. `feat: resolve reply recipients from the verified sender only` — the rule, its tests (Reply-To and Cc never become recipients), and the call site.
2. `feat: judge calendar conflicts on the user's local day` — the rule and the daylight-saving table test.
3. `feat: cap suggestions per user per day` — the cap and its test.
4. `feat: dedupe suggestions by message and type` — the key, the unique constraint if needed, and the idempotency test.

A bad version of the same work: one commit "add validator" (too big), or eleven commits including "fix test", "oops", "rename var" (noise).

## 7. Session strategy

- Stay in one session for tightly coupled units of the same task.
- Start a fresh session after a phase boundary, resuming from [HANDOFF.md](HANDOFF.md).
- Compact after a milestone, never during active debugging.
- Update [HANDOFF.md](HANDOFF.md) when the baseline changes (test count, commit, new traps).

## 8. Review focus for AI-written code

Spend review time on **invariants and edge cases, error boundaries, auth and security assumptions, hidden coupling, and rollout risk** (migrations, backfills). Do not spend it on style that lint already enforces.

## 9. Cost discipline

Report per task: model tier, rough token use, retries, wall-clock time, success or failure. If a task needs more than two retries, stop and re-plan the units instead of pushing harder.

## 10. Environment notes

Local automation hooks may ask for stated facts before the first shell command or new file; state them and retry. Disposable PostgreSQL and Redis containers only, removed afterwards. Prefer the main checkout over an automatically created worktree.
