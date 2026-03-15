.PHONY: all lint format test test-unit ruff-report bandit-report sonar-inputs test-integration test-integration-host test-integration-network test-integration-podman test-integration-map ci-map tach security docstrings complexity deadcode reuse check install install-dev docs docs-build clean spdx

REPORTS_DIR ?= reports
COVERAGE_XML ?= $(REPORTS_DIR)/coverage.xml
UNIT_JUNIT_XML ?= $(REPORTS_DIR)/unit.junit.xml
INTEGRATION_HOST_JUNIT_XML ?= $(REPORTS_DIR)/integration-host.junit.xml
INTEGRATION_NETWORK_JUNIT_XML ?= $(REPORTS_DIR)/integration-network.junit.xml
INTEGRATION_PODMAN_JUNIT_XML ?= $(REPORTS_DIR)/integration-podman.junit.xml
INTEGRATION_JUNIT_XML ?= $(REPORTS_DIR)/integration.junit.xml
RUFF_REPORT ?= $(REPORTS_DIR)/ruff-report.json
BANDIT_REPORT ?= $(REPORTS_DIR)/bandit-report.json

all: check

# Run linter and format checker (fast, run before commits)
lint:
	mkdir -p $(REPORTS_DIR)
	poetry run ruff check --exit-zero --output-format=json --output-file=$(RUFF_REPORT) .
	poetry run ruff check .
	poetry run ruff format --check .

# Auto-fix lint issues and format code
format:
	poetry run ruff check --fix .
	poetry run ruff format .

# Run tests with coverage (excludes integration tests)
test: test-unit

test-unit:
	mkdir -p $(REPORTS_DIR)
	poetry run pytest tests/unit/ --cov=terok --cov-report=term-missing --cov-report=xml:$(COVERAGE_XML) --junitxml=$(UNIT_JUNIT_XML) -o junit_family=legacy

# Write Ruff's JSON report without failing on findings.
ruff-report:
	mkdir -p $(REPORTS_DIR)
	poetry run ruff check --exit-zero --output-format=json --output-file=$(RUFF_REPORT) .

# Write Bandit's JSON report without failing on findings.
bandit-report:
	mkdir -p $(REPORTS_DIR)
	poetry run bandit -r src/terok/ --exit-zero -f json -o $(BANDIT_REPORT)

# Generate the files SonarQube Cloud imports from reports/.
sonar-inputs: test-unit ruff-report bandit-report

# Run integration tests (tier 2 auto-skips without podman)
test-integration:
	mkdir -p $(REPORTS_DIR)
	poetry run pytest tests/integration/ -v --junitxml=$(INTEGRATION_JUNIT_XML) -o junit_family=legacy

# Run host-only integration tests (filesystem/process workflows; no podman/network)
test-integration-host:
	mkdir -p $(REPORTS_DIR)
	poetry run pytest tests/integration/ -m "needs_host_features and not needs_internet and not needs_podman" -v --junitxml=$(INTEGRATION_HOST_JUNIT_XML) -o junit_family=legacy

# Run network integration tests (no podman)
test-integration-network:
	mkdir -p $(REPORTS_DIR)
	@status=0; \
	poetry run pytest tests/integration/ -m "needs_internet and not needs_podman" -v --junitxml=$(INTEGRATION_NETWORK_JUNIT_XML) -o junit_family=legacy || status=$$?; \
	test $$status -eq 0 -o $$status -eq 5

# Run only podman integration tests (for local runs with podman)
test-integration-podman:
	mkdir -p $(REPORTS_DIR)
	poetry run pytest tests/integration/ -m "needs_podman" -v --junitxml=$(INTEGRATION_PODMAN_JUNIT_XML) -o junit_family=legacy

# Generate integration test map (Markdown table grouped by directory)
test-integration-map:
	poetry run python docs/test_map.py

# Generate CI workflow map (Markdown tables from .github/workflows/*.yml)
ci-map:
	poetry run python docs/ci_map.py

# Check module boundary rules (tach.toml)
tach:
	poetry run tach check

# Run SAST scan on the terok source tree
security:
	mkdir -p $(REPORTS_DIR)
	poetry run bandit -r src/terok/ --exit-zero -f json -o $(BANDIT_REPORT)
	poetry run bandit -r src/terok/ -ll

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

# Add SPDX header to files.
# NAME must be the real name of the person responsible for creating the file (not a project name).
# Example: make spdx NAME="Real Human Name" FILES="src/terok/new_file.py"
spdx:
ifndef NAME
	$(error NAME is required — use the real name of the copyright holder, e.g. make spdx NAME="Real Human Name" FILES="src/terok/new_file.py")
endif
	poetry run reuse annotate --template compact --copyright "$(NAME)" --license Apache-2.0 $(FILES)

# Run all checks (equivalent to CI)
check: lint test tach security docstrings deadcode reuse

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
	rm -rf dist/ site/ reports/ .coverage coverage.xml .pytest_cache/ .ruff_cache/ .complexipy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
