# Makefile

.PHONY: all format check validate

# Default target: runs format and check
all: validate

# Format the code using ruff
format:
	ruff format --check --diff .

reformat-ruff:
	ruff format .

# Check the code using ruff
check:
	ruff check .

fix-ruff:
	ruff check . --fix

fix: reformat-ruff fix-ruff
	@echo "Updated code."

# Validate the code (format + check)
validate: format check
	@echo "Validation passed. Your code is ready to push."