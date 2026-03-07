.PHONY: all lint format test tach docstrings complexity deadcode reuse check install install-dev clean spdx

all: check

# Run linter and format checker (fast, run before commits)
lint:
	poetry run ruff check .
	poetry run ruff format --check .

# Auto-fix lint issues and format code
format:
	poetry run ruff check --fix .
	poetry run ruff format .

# Run tests with coverage
test:
	poetry run pytest --cov=terok --cov-report=term-missing

# Check module boundary rules (tach.toml)
tach:
	poetry run tach check

# Check docstring coverage (minimum 95%)
docstrings:
	poetry run docstr-coverage src/terok/ --fail-under=95

# Check cognitive complexity (advisory — lists functions exceeding threshold)
complexity:
	poetry run complexipy src/terok/ --max-complexity-allowed 15 --failed; true

# Find dead code (cross-file, min 80% confidence)
deadcode:
	poetry run vulture src/terok/ vulture_whitelist.py --min-confidence 80

# Check REUSE (SPDX license/copyright) compliance
reuse:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	poetry run reuse lint

# Add SPDX header to files: make spdx FILES="src/terok/new_file.py" [NAME="Your Name"]
spdx:
ifndef NAME
	$(error NAME is required. Usage: make spdx NAME="Your Name" FILES="src/terok/new_file.py")
endif
	poetry run reuse annotate --template compact --copyright "$(NAME)" --license Apache-2.0 $(FILES)

# Run all checks (equivalent to CI)
check: lint test tach docstrings deadcode reuse

# Install runtime dependencies only
install:
	poetry install --only main

# Install all dependencies (dev, test, docs)
install-dev:
	poetry install --with dev,test,docs

# Build documentation locally
docs:
	poetry run mkdocs serve

# Build documentation for deployment
docs-build:
	poetry run mkdocs build --strict

# Clean build artifacts
clean:
	rm -rf dist/ site/ .coverage coverage.xml .pytest_cache/ .ruff_cache/ .complexipy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
