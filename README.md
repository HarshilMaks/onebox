# OneBox

AI agent orchestration system with email, calendar, and task management.

## Prerequisites

- Python 3.12+
- Docker (for Redis)
- PostgreSQL database

## Quick Start

```bash
# 1. Clone and enter
git clone https://github.com/HarshilMaks/onebox.git
cd onebox

# 2. Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env with your actual credentials

# 5. Start Redis
bash scripts/redis_setup.sh
# OR: docker run -d --name redis -p 6379:6379 redis

# 6. Run database migrations
alembic upgrade head

# 7. Start the server
make run
# OR: uvicorn server.main:app --reload --log-config server/logging.ini --host 0.0.0.0 --port 8000
```

## Required Secrets (not in repo)

Create these files after cloning (see `.env.example`):

| File | Purpose |
|------|---------|
| `.env` | Environment variables (DB, JWT, OAuth config) |
| `executive-agent.json` | Google service account key |
| `onebox_oauth.json` | Google OAuth client secrets |
| `credentials.json` | Gmail API credentials |
| `token.json` | OAuth tokens (auto-generated) |

## Docker (Alternative)

```bash
docker compose up --build
```

## Project Structure

```
server/          # FastAPI backend
tools/           # Agent tools (email, calendar, tasks)
alembic/         # Database migrations
clients/         # Client libraries
scripts/         # Utility scripts
```
