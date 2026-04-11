# Makefile

.PHONY: all format check validate test test-cov test-integration test-all irc-up irc-down test-integration-local vulture complexity xenon bandit pyright fix reformat-ruff fix-ruff

# Default target: runs format and check
all: validate test

# Format the code using ruff
format:
	uv run ruff format --check --diff .

reformat-ruff:
	uv run ruff format .

# Check the code using ruff
check:
	uv run ruff check .

fix-ruff:
	uv run ruff check . --fix

fix: reformat-ruff fix-ruff
	@echo "Updated code."

test:
	uv run pytest tests/unit

test-cov:
	uv run pytest tests/unit --cov --cov-report=xml --cov-report=term-missing

test-integration:
	uv run pytest tests/integration -v -m integration --timeout=120

test-all: test-cov
	uv run pytest tests/integration -v -m integration --timeout=120

# Integration test helpers
irc-up:
	docker compose up -d ircd
	@echo "Waiting for IRC server to be ready..."
	@timeout 60 bash -c 'until nc -z localhost 6667 2>/dev/null; do sleep 1; done' || echo "Timeout waiting for IRC server"
	@echo "IRC server is ready!"

irc-down:
	docker compose down

test-integration-local: irc-up
	uv run pytest tests/integration -v -m integration --timeout=120
	$(MAKE) irc-down

vulture:
	uv run vulture . --exclude .venv,migrations,tests --make-whitelist

complexity:
	uv run radon cc . -a -nc

xenon:
	uv run xenon -b D -m B -a B .

bandit:
	uv run bandit -c pyproject.toml -r .

pyright:
	uv run pyright

# Validate the code (format + check)
validate: format check complexity bandit pyright vulture
	@echo "Validation passed. Your code is ready to push."