PYTHON ?= python3.12
VENV ?= .venv
PIP := $(VENV)/bin/pip
PYTHON_BIN := $(VENV)/bin/python

.PHONY: clean distclean install lint test contract contract-check check run run-dev run-worker compose-up compose-down compose-config migrate image-smoke

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .tox .nox .coverage htmlcov coverage.xml build dist
	find . -path './.git' -prune -o -path './$(VENV)' -prune -o \
		-type d \( -name '__pycache__' -o -name '*.egg-info' \) -prune -exec rm -rf {} +
	find . -path './.git' -prune -o -path './$(VENV)' -prune -o \
		-type f \( -name '*.py[co]' -o -name '.coverage.*' \) -exec rm -f {} +

distclean: clean
	rm -rf $(VENV)

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --require-hashes -r requirements-dev.txt

lint:
	$(PYTHON_BIN) -m ruff check .

contract:
	$(PYTHON_BIN) scripts/generate_openapi.py

contract-check:
	$(PYTHON_BIN) scripts/generate_openapi.py --check

test:
	$(PYTHON_BIN) -m pytest -q

check: lint contract-check test

run:
	$(PYTHON_BIN) -m uvicorn server.main:app --host 0.0.0.0 --port 8000

run-dev:
	$(PYTHON_BIN) -m uvicorn server.main:app --reload --host 0.0.0.0 --port 8000

run-worker:
	SERVICE_ROLE=automation_worker AUTOMATION_ENABLED=true $(PYTHON_BIN) -m server.workers

compose-up:
	docker compose up --build --detach

compose-down:
	docker compose down --remove-orphans

compose-config:
	docker compose config --quiet

migrate:
	docker compose run --rm migrate

image-smoke:
	docker build --no-cache -t onebox:test .
	docker run --rm --entrypoint sh onebox:test -c 'test "$$(id -u)" != 0; test ! -e /app/.env; test ! -e /app/.git; test ! -e /app/.agents; test ! -e /app/tests; test -z "$$(find /app -type f -name "*.json" -print -quit)"'

