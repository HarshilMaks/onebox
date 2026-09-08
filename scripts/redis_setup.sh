#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required to start the local Redis service." >&2
    exit 1
fi

docker compose up --detach redis
docker compose ps redis