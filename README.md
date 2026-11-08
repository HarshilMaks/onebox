# OneBox

**An approve-first assistant for mail, calendar and tasks.** The AI proposes, a person approves each action, and an approved action runs at most once.

[![Backend checks](https://github.com/HarshilMaks/onebox/actions/workflows/backend.yml/badge.svg)](https://github.com/HarshilMaks/onebox/actions/workflows/backend.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![OpenAPI](https://img.shields.io/badge/OpenAPI-contract-green.svg)](docs/openapi.json)

> **Status:** a working backend, not a finished product. Gmail, Calendar and Tasks actions, the approval ledger, background mail summaries and CI exist and are tested. Sign-in, connecting more apps, suggestions from new mail, a web client and Outlook are planned, not built.

## What it does today

- **Proposes, then waits.** Ask the assistant to send an email, reply, book an event or add a task and it stores a *pending action* instead of doing it. You approve it with `POST /actions/{id}/approve` and it runs at most once. Creating a draft and marking mail as read are the only things it does without asking.
- **Summarizes new mail in the background.** A worker receives Gmail push notifications for one configured mailbox and writes a short summary of each new message with a model that has no tools, so a hostile email cannot make it act. Results are at `GET /mail/triage`.
- **Says so when it is unsure.** If a provider call times out, the action is marked `reconciliation_required` ("needs checking") and is never retried blindly. An operator can look the result up with `POST /actions/{id}/reconcile`.
- **Keeps credentials encrypted.** Google tokens are encrypted at rest (AES-256-GCM) with a key that lives outside the database.

## How it works

```mermaid
flowchart LR
  you(["You"]) -->|"ask"| agent["Interactive agent (Gemini)"]
  agent -->|"proposes sends, replies, events, tasks"| ledger[("Approval ledger (PostgreSQL)")]
  you -->|"approve"| ledger
  ledger -->|"runs at most once"| google["Gmail, Calendar, Tasks"]
  pubsub(["New mail (Pub/Sub push)"]) --> worker["Worker (no tools)"]
  worker -->|"short summaries"| results["Triage results"]
```

An agent never writes to Google itself. It stores a typed proposal; only the ledger executes, and only after you approve.

---

## Quick Start

Install, start PostgreSQL and Redis, configure `.env`, migrate and run: **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#local-setup-step-by-step)**.

---

## Configuration Reference

Every setting (name, whether it is required, its default and what it does), the three secret files, and the agent profile are in **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

---

## API & Event Stream Reference

Eight endpoint groups (health, automation diagnostics, the interactive agent, event streaming, approve, reconcile, list actions, triage results) and the pending-action state machine are documented, with examples, in **[docs/API.md](docs/API.md)**. The machine-readable contract is [`docs/openapi.json`](docs/openapi.json).

---

## Operational & Security Policies

Input limits, the generational mail cache, the per-run tool allowlist, provider retry classes, account identity and data retention are described in **[docs/POLICIES.md](docs/POLICIES.md)**.

---

## Docker Deployment

The Compose topology (`postgres`, `redis`, a one-shot `migrate`, the `app` and the `worker`), the required environment variables and the process roles are described in the **[operations runbook](docs/OPERATIONS.md)**.

---

## Testing & Release Gates

`make check` runs Ruff, the OpenAPI drift check and the test suite. What each gate does, and what a green run does and does not cover, is in **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#testing-and-release-gates)**.

---

## Project Structure

The annotated file tree is in **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#project-structure)**.
