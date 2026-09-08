PYTHON ?= python3.12
VENV ?= .venv
PIP := $(VENV)/bin/pip
PYTHON_BIN := $(VENV)/bin/python

.PHONY: clean distclean install lint test check run run-dev compose-up compose-down migrate image-smoke

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

test:
	$(PYTHON_BIN) -m pytest -q

check: lint test

run:
	$(PYTHON_BIN) -m uvicorn server.main:app --log-config server/logging.ini --host 0.0.0.0 --port 8000

run-dev:
	$(PYTHON_BIN) -m uvicorn server.main:app --reload --log-config server/logging.ini --host 0.0.0.0 --port 8000

compose-up:
	docker compose up --build --detach

compose-down:
	docker compose down --remove-orphans

migrate:
	docker compose --profile migrate run --rm migrate

image-smoke:
	docker build --no-cache -t onebox:test .
	docker run --rm --entrypoint sh onebox:test -c 'test "$$(id -u)" != 0; test ! -e /app/.env; test ! -e /app/.git; test ! -e /app/.agents; test ! -e /app/tests; test -z "$$(find /app -type f -name "*.json" -print -quit)"'

