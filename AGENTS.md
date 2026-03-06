# Agent Guide (terok)

## Purpose

`terok` manages containerized AI coding agent projects and per-run tasks using Podman. It ships both a CLI (`terokctl`) and a Textual TUI (`terok`).

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
make spdx NAME="Your Name" FILES="src/terok/new_file.py"  # Add SPDX header
```

## Coding Standards

- **Style**: Follow ruff configuration in `pyproject.toml`
- **Line length**: 100 characters (ruff formatter target; `E501` is disabled so long strings that cannot be auto-wrapped are tolerated)
- **Imports**: Sorted with isort (part of ruff)
- **Type hints**: Use Python 3.12+ type hints
- **Docstrings**: Required for all public functions, classes, and modules (enforced by `docstr-coverage` at 95% minimum in CI)
- **Testing**: Add tests for new functionality; maintain coverage
- **SPDX headers**: Every source file (`.py`, `.sh`, etc.) must start with a compact two-line SPDX header — no blank line between them:
  ```python
  # SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil
  # SPDX-License-Identifier: Apache-2.0
  ```
  Use `make spdx NAME="Your Name" FILES="path/to/file.py"` to add headers (uses the compact template in `.reuse/templates/`). For files that already have a header, this adds a second copyright line — it does not replace the existing one. Files covered by `REUSE.toml` glob patterns (`.md`, `.yml`, `.toml`, `.json`, etc.) do not need inline headers. `make reuse` checks compliance but does not generate headers.
- **Emojis**: Must be natively wide (`East_Asian_Width=W`) — no VS16 (U+FE0F) sequences. Use `draw_emoji()` from `terok.lib.util.emoji` for aligned output. See `docs/DEVELOPER.md` → "Emoji width constraints" for details

## Development Workflow

1. Make changes in appropriate module (`src/terok/`)
2. Run `make lint` frequently during development
3. Add/update tests in `tests/` directory
4. Run `make test` to verify changes
5. If you added or changed cross-module imports, run `make tach` to verify module boundary rules
6. Update documentation in `docs/` if needed
7. Run `make check` before pushing

## Key Guidelines

- **Container Readiness**: When modifying init scripts or server startup, preserve readiness markers (see `docs/DEVELOPER.md`)
- **Security Modes**: Understand online vs gatekeeping modes when working with git operations
- **Agent Instructions**: When modifying container setup (Dockerfile templates, init scripts, installed tools), check if `src/terok/resources/instructions/default.md` needs updating
- **Minimal Changes**: Make surgical, focused changes
- **Existing Tests**: Never remove or modify unrelated tests
- **Dependencies**: Use Poetry for dependency management; avoid adding unnecessary dependencies

## Module Boundaries (tach)

The project uses [tach](https://github.com/gauge-sh/tach) to enforce module boundary rules defined in `tach.toml`. Each module declares its allowed dependencies and public interface. When adding new cross-module imports:

- If importing from an existing dependency, ensure the symbol is in that module's `[[interfaces]]` `expose` list
- If adding a new dependency between modules, add it to the `depends_on` list and update `[[interfaces]]` as needed
- Run `make tach` (or `tach check`) to verify; CI will reject boundary violations

## Important Files

- `docs/DEVELOPER.md`: Detailed architecture and implementation guide
- `docs/USAGE.md`: Complete user documentation
- `Makefile`: Build and test automation
- `pyproject.toml`: Project configuration and dependencies
- `tach.toml`: Module boundary rules (enforced in CI)

