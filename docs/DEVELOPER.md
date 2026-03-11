# Developer Guide

This document covers internal architecture and implementation details for contributors and maintainers of terok.

## Domain Model Architecture

terok's library layer (`src/terok/lib/`) follows Domain-Driven Design (DDD) conventions with a clear separation between **value objects** (pure data), **entities** (identity + behavior), and **services** (stateful helpers).

### Object Graph

```text
facade.get_project("myproj")  →  Project          (Aggregate Root)
    .config                    →  ProjectConfig    (Value Object — dataclass)
    .gate                      →  GitGate          (Repository + Gateway)
    .ssh                       →  SSHManager       (Service)
    .agents                    →  AgentManager     (Strategy + Config Stack)
    .create_task(name="x")     →  Task             (Entity)
    .get_task("1")             →  Task             (Entity)
        .meta                  →  TaskMeta         (Value Object)
```

### Key Types

| Type | Module | DDD Role | Description |
|------|--------|----------|-------------|
| `Project` | `lib.project` | Aggregate Root | Entry point for all project-scoped operations. Wraps `ProjectConfig` with behavior. |
| `Task` | `lib.task` | Entity | Wraps `TaskMeta` with lifecycle methods (run, stop, delete, rename, logs). |
| `ProjectConfig` | `lib.core.project_model` | Value Object | Configuration dataclass loaded from `project.yml`. No behavior. |
| `TaskMeta` | `lib.containers.tasks` | Value Object | Task metadata snapshot (ID, mode, status, workspace path). |
| `GitGate` | `lib.security.git_gate` | Repository + Gateway | Manages the bare git mirror; wraps git CLI. |
| `SSHManager` | `lib.security.ssh` | Service | Generates SSH keypairs and config for container mounts. |
| `AgentManager` | `lib.project` | Strategy + Config Stack | Resolves layered agent configuration and provider selection. |

### Design Principles

**Value Objects vs Rich Objects.** `ProjectConfig` and `TaskMeta` are dataclass data holders — they carry configuration and metadata but have no behavior beyond computed properties. The rich `Project` and `Task` objects wrap these and delegate to service functions, providing a natural OOP interface.

**Snapshot Semantics.** `Task` captures a point-in-time snapshot of `TaskMeta`. Mutations (`rename()`, `stop()`) update persistent storage but do *not* refresh the in-memory snapshot. To observe new state, obtain a fresh `Task` via `project.get_task(id)`. This keeps entities free of implicit I/O.

**Lazy Initialization.** `Project` subsystems (`gate`, `ssh`, `agents`) are created on first access, not at construction. This avoids I/O when only a subset of functionality is needed. Since `Project` uses `__slots__`, `cached_property` is not available — the manual pattern (`if self._gate is None: ...`) is used instead.

**Identity-Based Equality.** `Project.__eq__` compares by project ID; `Task.__eq__` compares by `(project_id, task_id)`. Both are hashable, so they work correctly in sets and dicts.

**Facade Pattern.** `lib.facade` provides factory functions (`get_project`, `list_projects`, `derive_project`) as the stable entry point. It also re-exports low-level service functions for CLI commands that operate on raw `project_id` strings.

### Module Boundaries

Module dependencies are enforced by [tach](https://github.com/gauge-sh/tach) via `tach.toml`. The key constraint: **presentation modules** (CLI, TUI) depend on the facade and domain objects, but never reach into container/security internals directly. The domain objects depend on service modules but not on presentation. Presentation modules may also import `lib.core.projects` directly for raw config access (`load_project`, `ProjectConfig`).

```text
Presentation (CLI, TUI)
    ├── depends on → lib.facade, lib.project, lib.task
    │                    └── depends on → lib.containers.*, lib.security.*, lib.core.*
    └── allowed for raw config → lib.core.projects (load_project, ProjectConfig)
```

---

## Container Readiness and Log Streaming

terok shows the initial container logs to the user when starting task containers and then automatically detaches once a "ready" condition is met. This improves UX but introduces dependencies that developers must be aware of when changing entry scripts or server behavior.

### CLI Mode (task run-cli)

Readiness is determined from log output. The container initialization script emits marker lines:
- `">> init complete"` (from `resources/scripts/init-ssh-and-repo.sh`)
- `"__CLI_READY__"` (echoed by the run command just before keeping the container alive)

The host follows logs and detaches when either of these markers appears, or after 60 seconds timeout.

**If you modify the init script**, ensure a stable readiness line is preserved, or update the detection in `src/terok/lib/containers/task_runners.py` (`task_run_cli`) and `src/terok/lib/containers/runtime.py` (`stream_initial_logs`).

### UI Mode (task run-web)

Readiness is determined by log markers, not port probing. The host follows container logs and detaches when it sees the startup marker from Terok Web UI:
- Marker: `"Terok Web UI started"` (emitted when the HTTP server is ready to accept connections)

This approach avoids false positives from port binding before actual server readiness. The default entry script is `resources/scripts/terok-ui-entry.sh` which runs the pre-installed CodexUI distribution (downloaded into the L1 UI image at build time) and only fetches it at runtime if it is missing.

**If the UI server changes its startup behavior or output format**, you may need to adjust:
- The readiness markers in `src/terok/lib/containers/runtime.py` (`stream_initial_logs`)
- The exposed/internal port and host port mapping in `src/terok/lib/containers/task_runners.py` (`task_run_web`)

### Timeout Behavior

- **CLI**: detaches after readiness marker or 60s timeout
- **UI**: detaches after readiness marker (no timeout by default; follows logs until ready)
- Even on timeout, containers remain running in the background. Users can continue watching logs with `podman logs -f <container>`.

### Key Source Files

| File | Purpose |
|------|---------|
| `src/terok/lib/containers/task_runners.py` | Host-side logic: `task_run_cli`, `task_run_web`, `task_run_headless` |
| `src/terok/lib/containers/runtime.py` | Container state, log streaming: `stream_initial_logs`, `wait_for_exit` |
| `src/terok/resources/scripts/init-ssh-and-repo.sh` | CLI init marker, SSH setup, repo sync |
| `src/terok/resources/scripts/terok-ui-entry.sh` | UI entry script (runs the UI server) |

**Important**: Changes to startup output or listening ports can affect readiness detection. Keep the readiness semantics stable or adjust terok's detection accordingly.

---

## Container Layer Architecture

terok builds project containers in three logical layers:

| Layer | Image Name | Purpose |
|-------|------------|---------|
| L0 | `terok-l0:<base-tag>` | Development base (Ubuntu 24.04, git, ssh, dev user) |
| L1-CLI | `terok-l1-cli:<base-tag>` | Agent tools (Claude Code, Codex, GitHub Copilot, OpenCode, Mistral Vibe) |
| L1-UI | `terok-l1-ui:<base-tag>` | UI dependencies and entry script |
| L2 | `<project>:l2-cli`, `<project>:l2-ui` | Project-specific config and user snippets |

L0 and L1 are project-agnostic and cache well; L2 is project-specific.

See [CONTAINER_LAYERS.md](CONTAINER_LAYERS.md) for detailed documentation.

---

## Volume Mounts at Runtime

When a task container starts, terok mounts:

| Container Path | Host Source | Purpose |
|----------------|-------------|---------|
| `/workspace` | `<state_root>/tasks/<project>/<task>/workspace-dangerous` | Per-task workspace |
| `/home/dev/.codex` | `<envs_base>/_codex-config` | Codex credentials |
| `/home/dev/.claude` | `<envs_base>/_claude-config` | Claude Code credentials |
| `/home/dev/.vibe` | `<envs_base>/_vibe-config` | Mistral Vibe credentials |
| `/home/dev/.blablador` | `<envs_base>/_blablador-config` | Blablador credentials + isolated OpenCode config (via `OPENCODE_CONFIG`) |
| `/home/dev/.config/opencode` | `<envs_base>/_opencode-config` | Plain OpenCode config (use `terokctl config import-opencode`) |
| `/home/dev/.local/share/opencode` | `<envs_base>/_opencode-data` | OpenCode data (shared by Blablador and plain OpenCode) |
| `/home/dev/.local/state` | `<envs_base>/_opencode-state` | OpenCode/Bun state (shared by both) |
| `/home/dev/.config/gh` | `<envs_base>/_gh-config` | GitHub CLI config |
| `/home/dev/.config/glab-cli` | `<envs_base>/_glab-config` | GitLab CLI config |
| `/home/dev/.ssh` (optional) | `<envs_base>/_ssh-config-<project>` | SSH keys/config |

See [SHARED_DIRS.md](SHARED_DIRS.md) for detailed documentation.

---

## Environment Variables Set by terok

### Core Variables (always set)

| Variable | Value | Purpose |
|----------|-------|---------|
| `PROJECT_ID` | Project ID from config | Identify current project |
| `TASK_ID` | Numeric task ID | Identify current task |
| `REPO_ROOT` | `/workspace` | Init script clone target |
| `CLAUDE_CONFIG_DIR` | `/home/dev/.claude` | Claude Code config location |
| `GIT_RESET_MODE` | `none` (default) | Controls workspace reset behavior |
| `TEROK_GIT_AUTHORSHIP` | `agent-human` (default) | Maps human/agent identities onto author/committer |
| `HUMAN_GIT_NAME` | From config or "Nobody" | Human Git identity name |
| `HUMAN_GIT_EMAIL` | From config or "nobody@localhost" | Human Git identity email |
| `TEROK_UNRESTRICTED` | `1` when unrestricted | Tells shell wrappers to inject auto-approve flags |

### Conditional Variables (based on security mode)

| Variable | When Set | Purpose |
|----------|----------|---------|
| `CODE_REPO` | Always | Git URL (upstream or gate depending on mode) |
| `GIT_BRANCH` | Always | Target branch name |
| `CLONE_FROM` | Online mode with gate | Alternate clone source for faster init |
| `EXTERNAL_REMOTE_URL` | Relaxed gatekeeping | Upstream URL for "external" remote |

---

## Security Modes

### Online Mode
- `CODE_REPO` points to upstream URL
- Container can push directly to upstream
- Git gate (if present) is used as read-only clone accelerator

### Gatekeeping Mode
- `CODE_REPO` points to `file:///git-gate/gate.git`
- Container cannot access upstream directly
- Human review required before changes reach upstream

See [GIT_CACHE_AND_SECURITY_MODES.md](GIT_CACHE_AND_SECURITY_MODES.md) for detailed documentation.

---

## Agent Permission Mode Architecture

Agents can run in **unrestricted** (fully autonomous) or **restricted**
(vendor-default permissions) mode.  The design goal is a single unified code
path: the host makes one decision and all agents — regardless of how they
accept permission flags — behave consistently.

### Decision flow

```text
CLI flag (--unrestricted / --restricted)
  │  ↓ if not given
Config stack: global → project → preset  (resolve_provider_value)
  │  ↓ if not configured
Default: unrestricted (True)
  │
  ▼
TEROK_UNRESTRICTED=1 env var  ← single decision carrier
  │
  ▼  (inside container)
Shell wrapper function reads $TEROK_UNRESTRICTED
  │
  ├─ claude():   _args+=(--dangerously-skip-permissions)
  ├─ codex():    _approve_args+=(--dangerously-bypass-approvals-and-sandbox)
  ├─ copilot():  _approve_args+=(--allow-all-tools)
  ├─ vibe():     _approve_args+=(--auto-approve)
  └─ opencode()/blablador():  export OPENCODE_PERMISSION='{"*":"allow"}'
```

### Key decision points

1. **Host-side resolution** (`task_runners.py`): Each task runner
   (`task_run_headless`, `task_run_cli`, `task_run_web`) resolves the
   unrestricted flag via `resolve_provider_value("unrestricted", ...)` against
   the effective agent/backend.  The resolved boolean is persisted to
   `meta.yml` and — if `True` — `TEROK_UNRESTRICTED=1` is added to the
   container's environment.

2. **In-container application** (`headless_providers.py`, `agents.py`): The
   shell wrapper functions generated by `_generate_generic_wrapper()` and
   `_generate_claude_wrapper()` check `$TEROK_UNRESTRICTED` at runtime.  When
   set to `"1"`, provider-specific flags are injected into the agent's command
   line (or env vars are exported for env-based agents like OpenCode).  When
   unset, the agent starts with its vendor defaults.

### Why `TEROK_UNRESTRICTED` and not direct flag injection?

The host cannot inject CLI flags directly into the agent invocation — it only
controls the container's environment.  The actual agent binary is launched by a
bash wrapper function inside the container (generated at image build time).
Using a single env var as the decision carrier keeps the host logic
provider-agnostic: it doesn't need to know *which* flags each agent needs.

### Implementation details

| Concern | Where | How |
|---------|-------|-----|
| Config resolution | `agent_config.py` → `resolve_provider_value()` | Walks global → project → preset; supports flat values and per-provider dicts |
| Host-side env injection | `task_runners.py` → `task_run_*()` | Sets `env["TEROK_UNRESTRICTED"] = "1"` before container start |
| Meta persistence | `task_runners.py` | `meta["unrestricted"]` written to `meta.yml` (headless: always; CLI: on start; web: only on new container creation) |
| CLI flag wiring | `cli/commands/task.py` | Mutually exclusive `--unrestricted` / `--restricted` mapped to tri-state `bool \| None` |
| Claude wrapper | `agents.py` → `_generate_claude_wrapper()` | `if [ "$TEROK_UNRESTRICTED" = "1" ]; then _args+=(--dangerously-skip-permissions); fi` |
| Generic wrappers | `headless_providers.py` → `_generate_generic_wrapper()` | Builds `_approve_args` array from `auto_approve_flags`; exports `auto_approve_env` vars |
| Provider flag registry | `headless_providers.py` → `HeadlessProvider` dataclass | `auto_approve_flags: tuple[str, ...]` + `auto_approve_env: dict[str, str]` |
| Status display | `tasks.py` → `task_status()`, `task_detail.py` | Reads `meta["unrestricted"]` and shows "unrestricted" / "restricted" |

### Adding a new agent

To add permission-mode support for a new agent:

1. Set `auto_approve_flags` (for CLI flags) or `auto_approve_env` (for
   env-var-based approval) on the `HeadlessProvider` definition.
2. No other changes needed — the generic wrapper generator and host-side
   resolution handle everything automatically.

---

## Development Workflow

### Initial Setup

```bash
# Clone the repository
git clone git@github.com:terok-ai/terok.git
cd terok

# Install all development dependencies
make install-dev
```

### Before You Commit

**Always run the linter before committing:**

```bash
make lint      # Check for issues (fast, ~1 second)
```

If linting fails, auto-fix with:

```bash
make format    # Auto-fix lint issues and format code
```

Tests are written using `unittest` and run with `pytest`.

**Run tests before pushing** (or at least before opening a PR):

```bash
make test      # Run full test suite with coverage
```

**Check module boundaries** if you changed cross-module imports:

```bash
make tach      # Verify tach.toml boundary rules
```

To run all checks (equivalent to CI):

```bash
make check     # Runs lint + test + tach + docstrings + deadcode + reuse
```

### Available Make Targets

| Command | Description | When to Use |
|---------|-------------|-------------|
| `make lint` | Check linting and formatting | Before every commit |
| `make format` | Auto-fix lint issues and format | When lint fails |
| `make test` | Run tests with coverage | Before pushing |
| `make tach` | Check module boundary rules | After changing imports |
| `make docstrings` | Check docstring coverage (95% min) | After adding public APIs |
| `make deadcode` | Detect unused code | Before opening a PR |
| `make reuse` | Check REUSE/SPDX license compliance | Before opening a PR |
| `make check` | Run all checks (lint + test + tach + docstrings + deadcode + reuse) | Before opening a PR |
| `make docs` | Serve documentation locally | When editing docs |
| `make install-dev` | Install all dependencies | Initial setup |
| `make clean` | Remove build artifacts | When needed |

### Running from Source

```bash
# Set up environment to use example projects
export TEROK_CONFIG_DIR=$PWD/examples
export TEROK_STATE_DIR=$PWD/tmp/dev-runtime/var-lib-terok

# Run CLI commands
python -m terok.cli projects
python -m terok.cli task new uc
python -m terok.cli generate uc
python -m terok.cli build uc

# Run TUI
python -m terok.tui
```

### TUI Notes

#### Emoji width constraints

Terminal emulators and Rich/Textual disagree on the width of emojis that use
Variation Selector-16 (U+FE0F).  Rich reports 2 cells (per Unicode spec); most
terminals render 1 cell.  This breaks Textual's layout engine — columns shift,
panel edges misalign — and **cannot be fixed by padding alone**.

**Rules:**

1. All emojis must be **natively wide** (`East_Asian_Width=W`,
   `Emoji_Presentation=Yes`).  No VS16 (U+FE0F) sequences.
2. Verify candidates: `python3 -c "import unicodedata; print(unicodedata.east_asian_width('🟢'))"` → must print `W`.
3. **Never use emoji literals directly in code.** Always define emojis in a
   central dict whose values carry both `emoji` and `label` attributes (e.g.
   `StatusInfo`, `ModeInfo`, `ProjectBadge`, `WorkStatusInfo`).
4. Always render via `render_emoji(info)` from `terok.lib.util.emoji`.
   Pass the dict entry directly — the function reads `.emoji` and `.label`
   itself.  No `width` or `label` parameter needed at the call site.
5. Emoji definitions live in `terok.lib.containers.task_display`
   (`STATUS_DISPLAY`, `MODE_DISPLAY`, `WEB_BACKEND_DISPLAY`,
   `SECURITY_CLASS_DISPLAY`, `GPU_DISPLAY`) and
   `terok.lib.containers.work_status` (`WORK_STATUS_DISPLAY`).
6. Guard tests in `tests/lib/test_emoji.py` verify all project emojis are
   natively 2 cells wide — adding a VS16 emoji will fail CI.  Tests also
   verify that all emoji dicts have non-empty labels for `--no-emoji` mode.
7. Emojis are **on by default**.  Pass `--no-emoji` to `terok` (TUI) or
   `terokctl` (CLI) to replace all emojis with `[label]` text badges.

See `src/terok/lib/util/emoji.py` module docstring for full background,
references, and future terminal developments to watch (Kitty text sizing
protocol, Mode 2027, terminal convergence).

### IDE Setup (PyCharm/VSCode)

1. Open the repo and set up a Python 3.12+ interpreter
2. Set environment variables:
   - `TEROK_CONFIG_DIR` = `/path/to/this/repo/examples`
   - Optional: `TEROK_STATE_DIR` = writable path
3. For PyCharm Run/Debug configuration:
   - CLI: Module name = `terok.cli`, Parameters = `projects` (or other subcommands)
   - TUI: Module name = `terok.tui` (no args)

### Building Wheels

```bash
# Build wheel
python -m pip install --upgrade build
python -m build

# Install in development mode (editable)
pip install -e .
```

---

## Agent Instructions Architecture

Agent instructions use a "YAML config + standalone file" two-layer pattern:

1. **YAML `instructions` key** — controls what base to use (bundled default, global, custom, or a mix via `_inherit`). Absent = bundled default.
2. **Standalone `instructions.md` file** in the project root — always appended at the end of whatever the YAML chain resolved. Purely additive.

The `_inherit` sentinel in a YAML list is replaced with the bundled default content at that position (splicing), rather than being stripped. This lets projects compose instructions as: default + project-specific YAML + file addendum.

Key implementation details:
- `resolve_instructions()` in `instructions.py` accepts `project_root` to locate the standalone file
- `has_custom_instructions()` checks both YAML key and file existence
- The TUI badge shows three states: `default`, `custom + inherited`, `custom only`
- Task runners pass `project_root=project.root` to ensure file content is included

This pattern (config key for inheritance control + file for additive content) is recommended for future similar functionality where users need both structured overrides and free-form additions. See PR #272 for the design discussion.

---

## Making a Release

### Steps

1. Update `version` in `pyproject.toml` to the new version (e.g. `0.5.0`)
2. Commit: `release: bump version to 0.5.0`
3. Merge the version bump to `master`
4. Go to **Releases → New release** on GitHub
5. Create a new tag `v0.5.0` targeting `master`
6. Click **Generate release notes**, review, and publish

The release workflow triggers on `v*` tags automatically — it builds the wheel/sdist and attaches them to the GitHub Release.

### Version Display

Between releases, `poetry-dynamic-versioning` generates PEP 440 versions from git tags automatically (e.g. `0.4.0.post3.dev0+gabcdef`). The TUI title bar shows a shortened form: `v0.4.0+` when past a release, `v0.4.0` at a tagged release.

---

## Packaging

See [PACKAGING.md](PACKAGING.md) for details on:
- Python packaging (pip/Poetry)
- Distribution packages (deb/rpm)
- FHS compliance
- Runtime lookup strategy
