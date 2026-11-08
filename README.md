# OneBox

> Production-grade asynchronous AI agent orchestration platform integrating Google Workspace (Gmail, Calendar, Tasks) with Gemini models, durable Pub/Sub workers, and human-in-the-loop action governance.

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-336791.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7+-DC382D.svg)](https://redis.io/)
[![Contract](https://img.shields.io/badge/OpenAPI-v3.1.0-green.svg)](docs/openapi.json)

[Overview](#overview) • [Features](#features) • [Architecture & Roles](#architecture--service-roles) • [Quick Start](#quick-start) • [Configuration](#configuration-reference) • [API & Event Streams](#api--event-stream-reference) • [Operational Policies](#operational--security-policies) • [Docker Deployment](#docker-deployment) • [Testing & Verification](#testing--release-gates) • [Project Structure](#project-structure)

---

## Overview

Managing high-volume executive communication, meeting scheduling, and task coordination requires continuous context switching and carries high operational risk if automated blindly.

**OneBox** provides a resilient, secure foundation for AI-assisted executive operations. It pairs Google Gemini models with Google Workspace APIs (Gmail, Google Calendar, Google Tasks) while enforcing strict operational boundaries:

1. **Interactive Agent Planning:** Users engage with executive and streaming agents to inspect schedules, search messages, and compose plans. Any external mutation requested through an interactive agent (sending an email, creating a calendar event, adding a task) is staged as an immutable, typed **pending action** requiring explicit user approval.
2. **Durable Inbound Automation:** An asynchronous background worker receives authenticated Google Cloud Pub/Sub push notifications for a configured mailbox, acquiring singleton PostgreSQL leases and recovering message history safely. Inbound automation operates with **zero tool permissions**—it analyzes incoming messages according to user triage rules without autonomously modifying mail or issuing writes.
3. **Enterprise Resilience & Governance:** Built with encrypted OAuth token keyrings, classified provider retry policies, generational Redis caching, and operator reconciliation workflows for uncertain provider writes.

---

## Features

- **Human-in-the-Loop Safeguards:** Agent-initiated external mutations (email sends/replies, calendar events, tasks) are staged as immutable pending actions in PostgreSQL; no agent-driven destructive effect executes without explicit authorization (`POST /actions/{action_id}/approve`).
- **Durable Gmail Pub/Sub Worker:** Scalable background notification daemon with PostgreSQL row-level locks, bounded lease recovery, automatic watch renewal, and safe history resynchronization.
- **Strict Separation of Concerns:** Inbound automated triage is decoupled from interactive agent execution. Automated triage has zero tools, preventing unauthorized automated replies or state changes.
- **Encrypted OAuth Keyring:** User tokens are protected at rest with AES-256-GCM using an active key ID from a mounted, read-only JSON keyring (`OAUTH_TOKEN_KEYRING_PATH`).
- **Classified Provider Retries:** Google API requests are categorized into permanent, authentication, quota, retryable transport/read, and ambiguous write. Ambiguous writes are dispatched once and never blind-retried.
- **Operator Reconciliation Workflow:** Ambiguous external writes enter a `reconciliation_required` state, allowing authorized operators to inspect deterministic provider markers and resolve state safely.
- **Generational Caching:** Redis caching for mail details (5-minute TTL) and folder/search pages (60-second TTL) using atomic per-user generation keys to invalidate stale data without wildcard key scans.
- **Multi-Role Process Topology:** First-class support for separate `api`, `automation_worker`, `migrate`, and local `combined` service roles.
- **Migration-Aware Health Checks:** Dedicated dependency-free `/livez` endpoint alongside `/readyz` that verifies schema migration heads and Redis connectivity without making external Google calls.

---

## Architecture & Service Roles

OneBox isolates API ingress, durable background processing, database migrations, and external provider integrations into distinct, single-responsibility components:

```mermaid
flowchart TD
    User(["👤 User / Client"])
    GCP(["☁️ Google Cloud Pub/Sub"])

    subgraph API ["FastAPI — role: api"]
        R1["/mail · /executive\n/generate-stream · /actions"]
        R2["/mail/agent/health · /readyz · /livez"]
        R3["/agent/oauth · /mail/notifications"]
    end

    subgraph PG ["PostgreSQL 16"]
        T["onebox_tokens\n(AES-256-GCM)"]
        PA["pending_actions\n+ audit_events"]
        JQ["gmail_notification_jobs\n(durable leases)"]
        MB["gmail_mailbox_states\n(watch cursor)"]
    end

    subgraph Cache ["Redis 7"]
        RC["Generational mail cache\n5 min body · 60 s pages"]
    end

    subgraph Worker ["python -m server.workers — role: automation_worker"]
        W1["Notification job claimer\n(singleton PG lease)"]
        W2["Watch renewal\n(exponential backoff)"]
        W3["Retention cleanup"]
    end

    subgraph Gemini ["Google Gemini — Vertex AI"]
        AG["ExecutiveAgent\nGeneralAgentStreamer"]
    end

    subgraph GWS ["Google Workspace APIs"]
        GM["Gmail API"]
        GC["Google Calendar API"]
        GT["Google Tasks API"]
    end

    User -->|"JWT Bearer"| R1
    User -->|"OAuth authorization code + state"| R3
    GCP -->|"OIDC-signed push"| R3

    R1 --> PG
    R1 --> Cache
    R1 --> Gemini
    R3 --> JQ

    AG -->|"stage only — no live write"| PA
    PA -->|"POST /actions/{id}/approve"| GWS

    W1 -->|"claim lease"| JQ
    W1 -->|"zero tools — read only"| GM
    W2 --> MB
    W3 --> PG

    T -->|"encrypted credential"| GWS
```

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
