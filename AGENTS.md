# Agent Guide (terok)

## Purpose

`terok` orchestrates and instruments containerized AI coding agents using Podman. It ships both a CLI (`terokctl`) and a Textual TUI (`terok`). The hardened container runtime (shield, gate, SSH, podman lifecycle) is provided by the `terok-sandbox` package.

## Technology Stack

- **Language**: Python 3.12+
- **Package Manager**: Poetry
- **Container Runtime**: Podman
- **Testing**: pytest with coverage
- **Linting/Formatting**: ruff
- **Module Boundaries**: tach (enforced in CI via `tach.toml`)
- **Documentation**: MkDocs with Material theme
- **TUI Framework**: Textual

## Repo layout

- `src/terok/`: Python package (CLI in `src/terok/cli/`, TUI in `src/terok/tui/`)
- `tests/`: `pytest` test suite
- `docs/`: user + developer documentation
- `examples/`, `completions/`: sample configs and shell completions

## Build, Lint, and Test Commands

**Before committing:**
```bash
make lint      # Run linter (required before every commit)
make format    # Auto-fix lint issues if lint fails
```

**Before pushing:**
```bash
make test       # Run full test suite with coverage
make tach       # Check module boundary rules (tach.toml)
make docstrings # Check docstring coverage (minimum 95%)
make reuse      # Check REUSE (SPDX license/copyright) compliance
make check      # Run lint + test + tach + docstrings + deadcode + reuse (equivalent to CI)
```

**When `pyproject.toml` changes** (added/removed/changed dependencies):

```bash
poetry lock --no-update   # Regenerate lockfile without upgrading existing deps
make install-dev          # Apply the updated lockfile to your local environment
# Commit both pyproject.toml and poetry.lock together
```

**Other useful commands:**
```bash
make install-dev  # Install all development dependencies
make docs         # Serve documentation locally
make clean        # Remove build artifacts
make spdx NAME="Real Human Name" FILES="src/terok/new_file.py"  # Add SPDX header
```

## Coding Standards

- **Style**: Follow ruff configuration in `pyproject.toml`
- **Line length**: 100 characters (ruff formatter target; `E501` is disabled so long strings that cannot be auto-wrapped are tolerated)
- **Imports**: Sorted with isort (part of ruff)
- **Type hints**: Use Python 3.12+ type hints
- **Docstrings**: Required for all public functions, classes, and modules (enforced by `docstr-coverage` at 95% minimum in CI)
- **Testing**: Add tests for new functionality; maintain coverage
- **SPDX headers**: Every source file (`.py`, `.sh`, etc.) must have an SPDX header. Use `make spdx` to add or update it — it handles both new files and existing files correctly:
  ```bash
  make spdx NAME="Real Human Name" FILES="path/to/file.py"
  ```
  - **New file** → creates the header:
    ```python
    # SPDX-FileCopyrightText: 2025 Jiri Vyskocil
    # SPDX-License-Identifier: Apache-2.0
    ```
  - **Existing file** → adds an additional copyright line (preserves the original):
    ```python
    # SPDX-FileCopyrightText: 2025 Jiri Vyskocil
    # SPDX-FileCopyrightText: 2026 New Contributor
    # SPDX-License-Identifier: Apache-2.0
    ```
  When modifying an existing file, always run `make spdx` with the contributor's name to add their copyright line. NAME must be a real person's name (ASCII-only), not a project name. Use a single year (year of first contribution), not a range. Ask the user for their name if unknown. Files covered by `REUSE.toml` glob patterns (`.md`, `.yml`, `.toml`, `.json`, etc.) do not need inline headers. `make reuse` checks compliance but does not generate headers.
- **Emojis**: Must be natively wide (`East_Asian_Width=W`) — no VS16 (U+FE0F) sequences. Use `draw_emoji()` from `terok.lib.util.emoji` for aligned output. See `docs/developer.md` → "Emoji width constraints" for details
- **No magic literals**: Never use literal IPs, URLs, ports, or filesystem paths directly in code. Define them as named constants and import from there — `tests/constants.py` for test code, appropriate module-level constants for production code. This centralises magic values and makes future changes trivial. In tests, mock filesystem paths must use a subdirectory under `MOCK_BASE` (e.g. `/tmp/terok-testing/...`) — never `/tmp` directly

## Development Workflow

1. Make changes in appropriate module (`src/terok/`)
2. Run `make lint` frequently during development
3. Add/update tests in `tests/` directory
4. Run `make test` to verify changes
5. If you added or changed cross-module imports, run `make tach` to verify module boundary rules
6. Update documentation in `docs/` if needed
7. Run `make check` before pushing

## Key Guidelines

- **Container Readiness**: When modifying init scripts or server startup, preserve readiness markers (see `docs/developer.md`)
- **Security Modes**: Understand online vs gatekeeping modes when working with git operations
- **Agent Instructions**: When modifying container setup (Dockerfile templates, init scripts, installed tools), check if `src/terok/resources/instructions/default.md` needs updating
- **Minimal Changes**: Make surgical, focused changes
- **Existing Tests**: Never remove or modify unrelated tests
- **Dependencies**: Use Poetry for dependency management; avoid adding unnecessary dependencies

## External Package Dependencies

terok depends on three sibling packages, each pinned to a GitHub release wheel:

```text
terok → terok-agent → terok-sandbox → terok-shield
```

Both `terok-agent` and `terok-sandbox` are listed as **explicit** dependencies in `pyproject.toml` — even though sandbox is pulled transitively via agent. This is intentional: terok imports directly from both packages, so the dependency must be declared, not just inherited.

**Version sync rules:**

- When bumping `terok-sandbox` in terok, the same version must be pinned in `terok-agent`. Otherwise Poetry will reject conflicting URL pins. Bump terok-agent first, release it, then bump both in terok.
- When bumping `terok-shield` in terok-sandbox, no direct impact on terok (transitive). But if terok-sandbox re-exports shield types that terok uses, the shield version must be compatible.
- After any version bump: run `poetry lock` and commit both `pyproject.toml` and `poetry.lock`.

**Import convention:** always import from the top-level package API (`from terok_agent import X`, `from terok_sandbox import X`), never from internal submodules. This keeps the coupling surface minimal and allows internal reorganization without breaking consumers.

## Module Boundaries (tach)

The project uses [tach](https://github.com/gauge-sh/tach) to enforce module boundary rules defined in `tach.toml`. Each module declares its allowed dependencies and public interface. When adding new cross-module imports:

- If importing from an existing dependency, ensure the symbol is in that module's `[[interfaces]]` `expose` list
- If adding a new dependency between modules, add it to the `depends_on` list and update `[[interfaces]]` as needed
- Run `make tach` (or `tach check`) to verify; CI will reject boundary violations

## SonarCloud

The project is analyzed by [SonarCloud](https://sonarcloud.io/summary/new_code?id=terok-ai_terok) on every push to master. Unlike CodeRabbit (which posts actionable PR comments), SonarCloud findings often require triage — many are low-priority style issues or false positives rather than real bugs. Treat them as input for decisions, not as a checklist.

**Fetching new issues for a PR** (replace `PR_NUMBER`):
```bash
curl -s 'https://sonarcloud.io/api/issues/search?projects=terok-ai_terok&pullRequest=PR_NUMBER&issueStatuses=OPEN&ps=100' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
for i in d.get('issues',[]):
    sev=i['severity']; rule=i['rule']; msg=i.get('message','')
    comp=i['component'].split(':',1)[-1]; line=i.get('textRange',{}).get('startLine','?')
    print(f'[{sev}] {rule}: {msg}')
    print(f'  {comp}:{line}')
"
```

**Fetching recent issues on the main branch** (replace date as needed):
```bash
curl -s 'https://sonarcloud.io/api/issues/search?projects=terok-ai_terok&issueStatuses=OPEN&createdAfter=2026-03-01&ps=100&s=CREATION_DATE&asc=false' \
  | python3 -c "import json,sys; [print(f'[{i[\"severity\"]}] {i[\"rule\"]}: {i.get(\"message\",\"\")}\n  {i[\"component\"].split(\":\",1)[-1]}:{i.get(\"textRange\",{}).get(\"startLine\",\"?\")}') for i in json.load(sys.stdin).get('issues',[])]"
```

No authentication is needed (public project). The PR number can be found in the GitHub PR URL or from `gh pr view --json number`.

## Git & GitHub Workflow

- **Upstream repo**: `terok-ai/terok` (canonical; PRs target here)
- **Origin repo**: `sliwowitz/terok` (fork; branches are pushed here)
- **Remote setup**: `upstream` = `terok-ai/terok`, `origin` = `sliwowitz/terok`
- **PR target**: Always open PRs against `upstream/master` (`terok-ai/terok:master`)
- **Branch workflow**: Create feature branch locally, rebase on `upstream/master`, push to `origin`, open PR against `upstream/master`
- **Branch naming**: Use conventional prefixes: `feat/`, `fix/`, `chore/`, `docs/`
- **Commit messages**: Use [Conventional Commits](https://www.conventionalcommits.org/) format (`feat:`, `fix:`, `chore:`, `docs:`, etc.)
- **Issue tracker**: GitHub Issues on `terok-ai/terok`

```bash
# Typical PR workflow
git fetch upstream
git checkout -b feat/my-feature upstream/master
# ... make changes, commit ...
git push origin feat/my-feature
gh pr create --repo terok-ai/terok --base master --head sliwowitz:feat/my-feature
```

## Important Files

- `docs/developer.md`: Detailed architecture and implementation guide
- `docs/usage.md`: Complete user documentation
- `Makefile`: Build and test automation
- `pyproject.toml`: Project configuration and dependencies
- `tach.toml`: Module boundary rules (enforced in CI)

