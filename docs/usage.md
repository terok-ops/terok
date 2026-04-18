# terok User Guide

Complete guide to installing, configuring, and using terok.

## Table of Contents

- [Installation](#installation)
- [Runtime Locations](#runtime-locations)
- [Global Configuration](#global-configuration)
- [From Zero to First Run](#from-zero-to-first-run)
- [Headless Agent Runs (Autopilot)](#headless-agent-runs-autopilot)
- [Presets](#presets)
- [Task Lifecycle Hooks](#task-lifecycle-hooks)
- [Image Management](#image-management)
- [Project Management](#project-management)
- [GPU Passthrough](#gpu-passthrough)
- [Tips](#tips)
- [FAQ](#faq)

---

## Installation

### Recommended: pipx

```bash
# Download the latest .whl from the GitHub Releases page, then:
pipx install ./terok-*.whl
```

### Alternative: pip

```bash
pip install ./terok-*.whl
```

After install, you should have console scripts: `terok` (TUI), `terok` (CLI).

### Global Flags

Both `terok` and `terok` support:

| Flag | Description |
|------|-------------|
| `--no-emoji` | Replace emojis with text labels (e.g. `[gate]` instead of emoji) |

### Shell Completion

Tab completion is powered by [argcomplete](https://github.com/kislyuk/argcomplete).

**Recommended: auto-install to your shell's completion directory**

```bash
# Auto-detect shell from $SHELL and install
terok completions install

# Or specify the shell explicitly
terok completions install --shell bash
```

Install locations (auto-loaded, no RC file edits needed):

| Shell | Path |
|-------|------|
| bash  | `~/.local/share/bash-completion/completions/terok` |
| zsh   | `~/.local/share/zsh/site-functions/_terok` |
| fish  | `~/.config/fish/completions/terok.fish` |

**Alternative: print raw completion script**

```bash
terok completions bash   # Print bash completion script to stdout
terok completions zsh    # Print zsh completion script to stdout
terok completions fish   # Print fish completion script to stdout
```

Run `terok config` to check whether completions are detected as installed.

---

## Runtime Locations

### Config/Projects

| Install Type | Path |
|--------------|------|
| Root | `/etc/terok/projects` |
| User | `~/.config/terok/projects` |
| Override | `TEROK_CONFIG_DIR=/path/to/config` |

### State (writable: tasks, build, gate)

| Install Type | Path |
|--------------|------|
| Root | `/var/lib/terok` |
| User | `${XDG_DATA_HOME:-~/.local/share}/terok` |
| Override | `TEROK_STATE_DIR=/path/to/state` |

---

## Global Configuration

The tool looks for a global config file in this order (first found wins):

1. `${XDG_CONFIG_HOME:-~/.config}/terok/config.yml` (user override)
2. `sys.prefix/etc/terok/config.yml` (pip/venv installs)
3. `/etc/terok/config.yml` (system default)

### Example Config

Copy from `examples/terok-config.yml`:
```bash
mkdir -p ~/.config/terok
cp examples/terok-config.yml ~/.config/terok/config.yml
```

### Minimum Settings

Every project needs these four fields in its `project.yml`:

```yaml
project:
  id: myproj
  security_class: online        # or "gatekeeping"

image:
  base_image: docker.io/library/ubuntu:24.04

git:
  upstream_url: https://github.com/yourorg/yourrepo.git
```

### Nice-to-Have Settings

These can be set in `project.yml` (per-project) or `config.yml` (global default):

```yaml
git:
  human_name: "Your Name"
  human_email: "your@email.com"
  default_branch: main
  authorship: agent-human    # or: human-agent, human, agent
```

<!-- markdownlint-disable MD046 -->
!!! info "Auto-deduction from host git config"

    If `human_name` and `human_email` are not set, terok deduces them from
    the host's `git config user.name` and `git config user.email`.
<!-- markdownlint-enable MD046 -->

---

## From Zero to First Run

The quickest way to manage projects is through the TUI — run `terok` after
install.  The steps below show the equivalent CLI workflow.

### Prerequisites

- Podman installed and working
- OpenSSH client tools (ssh, ssh-keygen) for private Git over SSH
- tmux (optional, for `terok --tmux` and persistent container sessions)

### Step 1: Create Project Directory

```bash
mkdir -p ~/.config/terok/projects/myproj
```

### Step 2: Create project.yml

```yaml
# ~/.config/terok/projects/myproj/project.yml
project:
  id: myproj
  security_class: online    # or "gatekeeping" for restricted mode

image:
  base_image: docker.io/library/ubuntu:24.04
  user_snippet_file: user.dockerinclude  # optional

git:
  upstream_url: git@github.com:yourorg/yourrepo.git  # or https://...
  default_branch: main
  # authorship: human-agent  # optional: author = human, committer = agent
```

### Step 3: (Optional) Image Snippet

Create `~/.config/terok/projects/myproj/user.dockerinclude`:
```dockerfile
RUN apt-get update && apt-get install -y ripgrep jq && rm -rf /var/lib/apt/lists/*
```

This text is pasted near the end of your project image (L2) Dockerfile.

### Step 4: Generate Dockerfiles

```bash
terok generate myproj
```

### Step 5: Build Images

```bash
# Build only L2 project images (fast, reuses existing L0/L1 layers)
terok build myproj

# Refresh just the agent-install layers (cache bust from the AGENT_CACHE_BUST point)
terok build myproj --refresh-agents

# Rebuild from L0 (no cache) (includes base image pull and system packages)
terok build myproj --full-rebuild

# Pick which agents get baked into L1 for this build (one-shot override)
terok build myproj --agents claude,codex
terok build myproj --agents all

# Optional: build a dev image from L0 as well
terok build myproj --dev
```

Build modes:
- **Default** (`build`): Only rebuilds L2 project images, reuses existing L0/L1. Use for project config changes.
- **`--refresh-agents`**: Rebuilds L0+L1+L2 and cache-busts the per-agent install layers, leaving the system-package layer intact. Use when an agent CLI has a new release.
- **`--full-rebuild`**: Rebuilds L0+L1+L2 with `--no-cache --pull=always`. Use when the base image or system packages need updating.
- **`--agents <list>|all`**: One-shot override of the agent selection for this build. Does not modify `project.yml`.

### Choosing which agents to bake in

The L1 (agent) image can be built with a subset of the roster instead of "everything". The selection flows from (narrowest wins):

1. **Per-build CLI override** — `terok build --agents claude,codex` (above).
2. **Per-project default** — `project.yml`:
   ```yaml
   image:
     agents: "claude,codex,gh"   # or "all"
   ```
3. **Global default** — `~/.config/terok/config.yml`:
   ```yaml
   image:
     agents: "all"   # default for every project that doesn't set its own
   ```

Different selections produce different L1 image tags (`terok-l1-cli:<base>-claude-codex`, `...-gh-glab`, ...) so multiple selections can coexist in the local image store. The OCI label `ai.terok.agents` on each L1 image records the exact selection for introspection; inside the container the same list is in `/etc/terok/installed.env` and the `hilfe` banner filters its output to match.

Transitive dependencies are expanded automatically — picking `blablador` or `kisski` also pulls in `opencode`.

### Step 6: Initialize SSH (for private repos)

```bash
terok ssh-init myproj
```

This creates:
- An ed25519 keypair named `id_ed25519_myproj`
- A default SSH config with:
  - `IdentitiesOnly yes`
  - `StrictHostKeyChecking accept-new` (avoids interactive prompts)
  - `IdentityFile <generated_private_key>`
  - Host github.com section with `User git`

Use the printed `.pub` key to add a deploy key on your Git host.

**Advanced:** Customize SSH config via template in `project.yml`:
```yaml
ssh:
  config_template: ssh_config.template  # relative or absolute path
```
Supported tokens: `{{IDENTITY_FILE}}`, `{{KEY_NAME}}`, `{{PROJECT_ID}}`

### Step 7: Create and Run a Task

```bash
# Create a new task
terok task new myproj
# Output: Created task 1 for project myproj

# List tasks
terok task list myproj

# Run in CLI mode (headless agent)
terok task run-cli myproj 1
```

#### Additional Task Operations

```bash
# Create and immediately run a task in one step
terok task start myproj

# Rename a task
terok task rename myproj 1 fix-auth-bug

# Follow up on a completed/failed headless task with a new prompt
terok task followup myproj 1 -p "Now add tests for the fix"

# View formatted container logs
terok task logs myproj 1              # Latest logs
terok task logs myproj 1 -f           # Follow live output
terok task logs myproj 1 --tail 50    # Last 50 lines
terok task logs myproj 1 --raw        # Raw podman output

# Stop or restart a task
terok task stop myproj 1
terok task restart myproj 1

# Delete a task
terok task delete myproj 1

# View archived (deleted) tasks and their logs
terok task archive list myproj
terok task archive logs myproj 20260305T143000Z
```

### Step 8: Log into a Running Container

```bash
# Open a shell in a running task container (persistent tmux session)
terok login myproj 1
```

This opens a tmux session inside the container. The session persists across
disconnects — re-running `terok login` reattaches to the same session.
Interactive shells show `hilfe --kurz` on entry; run `hilfe` inside the
container for the fuller in-container help.

#### From the TUI

Press `l` on any running task to open a login session. The TUI picks the best
method automatically:

| Environment | What happens |
|---|---|
| Inside tmux | Opens new tmux window (TUI stays visible) |
| Desktop (GNOME/KDE) | Opens new terminal window |
| Web (textual serve) | Opens new browser tab (via ttyd) |
| Plain terminal | Suspends TUI, opens shell, resumes on exit |

#### Running the TUI under tmux (recommended)

```bash
terok --tmux
```

This wraps the TUI in a managed tmux session with a blue status bar showing
keyboard shortcuts. Login sessions open as additional tmux windows — press
`^b n`/`^b p` to switch between TUI and container shells.

#### tmux Quick Reference

| Context | Prefix | Status bar color | Common keys |
|---|---|---|---|
| Host tmux | `^b` | Blue | `^b n/p` switch windows, `^b d` detach |
| Container tmux | `^a` | Green | `^a n/p` switch windows, `^a c` new window |

The container's tmux prefix (`^a`) is different from the host's (`^b`) to avoid
conflicts. The container status bar shows `host: ^b` as a reminder.

---

## Headless Agent Runs (Autopilot)

Run any supported agent headlessly in a container — no interactive session needed.
Useful for CI/CD pipelines, batch tasks, or scripted workflows.

Supported providers: `claude`, `codex`, `copilot`, `vibe`, `blablador`, `opencode`.

### Basic Usage

```bash
# Run with a prompt (uses default provider — claude unless configured otherwise)
terok run myproj "Fix the authentication bug in login.py"

# Override model and set a timeout
terok run myproj "Add unit tests for utils.py" --model opus --max-turns 50 --timeout 3600

# Detach immediately (don't stream output)
terok run myproj "Refactor the database layer" --no-follow

# Use a specific provider
terok run myproj "Fix the auth bug" --provider codex
terok run myproj "Add tests" --provider copilot
```

The command creates a new task, starts a container, runs the agent with the given
prompt, and streams the output. When the agent finishes, the task is marked as
completed and a diff summary is printed.

### Default Provider

The provider is resolved in this order:
1. `--provider` flag (if given)
2. `default_agent` in project config (`project.yml`)
3. `default_agent` in global config (`config.yml`)
4. `claude` (ultimate fallback)

```yaml
# Set per-project default in project.yml (top-level key)
default_agent: codex

# Set global default in config.yml
default_agent: claude
```

### Provider Feature Matrix

| Feature | claude | codex | copilot | vibe | blablador | opencode |
|---------|--------|-------|---------|------|-----------|----------|
| `--model` | Yes | Yes | Yes | Yes (`--agent`) | No | Yes |
| `--max-turns` | Yes | No | No | Yes | No | No |
| Session resume | Yes | No | No | Yes | Yes | Yes |
| Sub-agents (`--agent`) | Yes | No | No | No | No | No |
| Structured log output | Yes | No | No | No | No | No |

### Per-Provider Config Values

Config keys like `model`, `max_turns`, and `timeout` can be set per-provider
using a dict syntax.  A flat value applies to all providers (backward compatible):

```yaml
# Flat value — same for all providers
model: sonnet
max_turns: 25
```

A dict maps each provider to its own value, with `_default` as fallback:

```yaml
# Per-provider values
model:
  claude: opus
  codex: codex-mini
  vibe: mistral-small
  _default: fast
max_turns:
  claude: 50
  vibe: 30
  _default: 25
timeout: 1800  # flat values still work
```

Providers not listed in the dict (and without `_default`) use their own built-in
default.  CLI flags (`--model`, `--max-turns`, `--timeout`) always override
config values.

**Best-effort feature mapping**: When a provider doesn't support a configured
feature, terok applies an analogue where possible.  For example, `max_turns`
for providers without `--max-turns` support is injected as guidance text in the
prompt.  Features with no analogue produce a warning but don't block the run.

### Permission Mode (Unrestricted / Restricted)

By default, terok starts agents in **unrestricted** mode — all safety prompts
are auto-approved so the agent can work fully autonomously.  You can switch to
**restricted** mode, which launches the agent with its vendor-default permission
settings (the agent will ask for confirmation before dangerous operations like
file writes or shell commands).

#### CLI flags

```bash
# Run restricted (agent uses vendor defaults — asks before dangerous ops)
terok run myproj "Fix the bug" --restricted

# Explicitly unrestricted (default behavior)
terok run myproj "Fix the bug" --unrestricted
```

The flags are mutually exclusive.  When neither is given, the value comes from
config (see below), defaulting to unrestricted.

#### Config

Like other config keys, `unrestricted` lives inside the `agent:` section
and follows the resolution stack:
global config → project config → preset → CLI flag.

```yaml
# In project.yml or global config
agent:
  # Flat value — same for all providers
  unrestricted: false  # all agents start restricted
```

Per-provider dict syntax is supported:

```yaml
agent:
  # Per-provider values
  unrestricted:
    claude: true
    codex: false
    _default: true
```

Providers not listed in the dict (and without `_default`) default to
unrestricted (`true`).

#### What each mode does per agent

| Provider | Unrestricted | Restricted (vendor default) |
|----------|-------------|---------------------------|
| claude | `--dangerously-skip-permissions` | Normal interactive prompts |
| codex | `--dangerously-bypass-approvals-and-sandbox` | Sandboxed with approval prompts |
| copilot | `--allow-all-tools` | Tool confirmation prompts |
| vibe | `--auto-approve` | Approval prompts |
| opencode / blablador | `OPENCODE_PERMISSION='{"*":"allow"}'` | Default permission policy |

#### Checking the current mode

```bash
terok task status myproj 1
# Output includes: Permissions: unrestricted
```

The TUI task detail panel also shows the permission mode.

### Sub-Agent Configuration

Define sub-agents in your `project.yml` under the `agent:` section. Each
sub-agent gets a `default` flag — default agents are always included, others
are available on demand via `--agent`.

```yaml
# ~/.config/terok/projects/myproj/project.yml
project:
  id: myproj
  security_class: online

git:
  upstream_url: git@github.com:yourorg/yourrepo.git
  default_branch: main

agent:
  subagents:
    # Always included in every task
    - name: code-reviewer
      description: Reviews code for quality and correctness
      tools: [Read, Grep, Glob]
      model: sonnet
      default: true
      system_prompt: |
        You are a code reviewer. Focus on correctness, security, and clarity.

    # Only included when explicitly selected with --agent
    - name: debugger
      description: Debugging specialist
      tools: [Read, Edit, Bash, Grep]
      model: opus
      default: false
      system_prompt: |
        You are an expert debugger. Use systematic analysis.

    # Reference a .md file (YAML frontmatter + body as prompt)
    - file: agents/planner.md
      default: false
```

#### Selecting Non-Default Agents

```bash
# Include the debugger agent for this run (sub-agents require --provider claude)
terok run myproj "Find and fix the memory leak" --provider claude --agent debugger

# Include multiple non-default agents
terok run myproj "Debug and plan a fix" --provider claude --agent debugger --agent planner
```

The `--agent` flag also works with interactive modes:

```bash
terok task run-cli myproj 1 --agent debugger
terok task start myproj --agent debugger
```

#### Agent .md File Format

Agent definitions can be stored as `.md` files with YAML frontmatter:

```markdown
---
name: planner
description: Architecture and planning specialist
tools: [Read, Grep, Glob]
model: sonnet
---
You are an architecture planner. Analyze the codebase and propose
structured implementation plans before writing code.
```

Reference them in `project.yml` with `file:` (paths relative to project root).

#### Providing Extra Agents via Config File

Pass an additional YAML file with `--config` to add more sub-agents at runtime:

```bash
terok run myproj "Review the PR" --config /path/to/extra-agents.yml
```

The file should contain a `subagents:` list in the same format as `project.yml`.

### Global Agents and MCPs

Global agents and MCP servers are managed natively by Claude — terok does not
interfere with them:

| What | Where |
|------|-------|
| Global agents | `~/.claude/agents/` |
| Global MCPs | `~/.claude/settings.json` (`mcpServers` section) |
| Project agents | `<workspace>/.claude/agents/` |
| Project MCPs | `<workspace>/.claude/settings.json` |

Per-sub-agent MCPs can be defined inline using the `mcpServers` field in the
agent definition (same format as Claude's native agent JSON).

Run `terok config` to see the actual paths on your system.

---

## Agent Instructions

terok provides layered agent instructions that describe the container environment to AI agents. Instructions are delivered automatically — no setup required.

### How It Works

Every task container receives instructions explaining the workspace layout, available tools, sudo access, git workflow, and conventions. Two independent layers control what a task receives:

1. **YAML `instructions` key** — controls the inheritance chain via config stack. Uses `_inherit` in list form to splice the bundled default at that position. Absent = bundled default.
2. **Standalone `instructions.md` file** in the project root — always appended at the end of whatever the YAML chain resolved. Purely additive. If empty or absent, nothing is appended.

- **Claude**: injected via `--append-system-prompt` (system-level context)
- **Codex**: loaded from `/home/dev/.terok/instructions.md` via `-c model_instructions_file=...`
  in the wrapper (applies to both `terok run` and `terok task run-cli`)
- **Other providers**: prepended to the task prompt (headless `terok run`)

### Scenarios

| YAML `instructions` | File | Result |
|---|---|---|
| absent | absent | bundled default |
| absent | has content | bundled default + file |
| `["_inherit"]` | has content | bundled default + file (same, explicit) |
| `["_inherit", "extra"]` | has content | bundled default + extra + file |
| `["custom only"]` | absent | custom only (no default) |
| `["custom only"]` | has content | custom only + file |
| `[]` | has content | file only |
| `"flat string"` | has content | flat string + file |

### Customizing Instructions

#### Option 1: Standalone file (recommended for most users)

Create `instructions.md` in your project root with project-specific notes. These are appended to the bundled default automatically:

```markdown
# Project Notes
This project uses Poetry. Run `make check` before committing.
```

#### Option 2: YAML config

Set the `instructions` key in your project's `agent:` config or in a preset:

```yaml
# project.yml — flat string (replaces default)
agent:
  instructions: |
    You are in a Podman container. This project uses Poetry.
    Run `make check` before committing.
```

Per-provider instructions:

```yaml
agent:
  instructions:
    claude: |
      Use Claude-specific conventions...
    codex: |
      Use Codex-specific conventions...
    _default: |
      Generic instructions for all providers.
```

Extend (rather than replace) parent instructions using `_inherit`:

```yaml
# Project config — appends to global/default instructions
agent:
  instructions:
    - _inherit
    - |
      ## Project-specific additions
      This project uses Poetry for dependency management.
      Run `make check` before committing.
```

To suppress defaults entirely, use an empty list:

```yaml
agent:
  instructions: []
```

### CLI Flag

Override all config-stack instructions with a file:

```bash
terok run myproj "Fix the bug" --instructions path/to/instructions.md
```

### TUI

The project details panel shows an **Instruct:** badge with three states:

- **default** (dim) — no custom instructions
- **custom + inherited** (green) — has custom content with defaults included
- **custom only** (cyan) — has custom content, defaults overridden

Available actions from the project details screen:

- **Shift+I** — edit project `instructions.md` in `$EDITOR`
- **t** — toggle instructions inheritance (include/exclude bundled defaults)
- **v** — view fully resolved instructions as a task would receive them

Command palette (`Ctrl+P`) actions:

- **Edit Global Instructions** — edit the global `instructions.md` in `$EDITOR`
- **Show Default Instructions** — view the bundled default instructions (read-only)

### Debugging

Resolved instructions are always written to `<tasks_root>/<task_id>/agent-config/instructions.md` on the host for inspection.

---

## Presets

Presets are reusable agent configurations you apply with `--preset <name>`.
Three are bundled and work immediately — no setup needed.

### Bundled Presets

The current bundled set is a starting point — run `terok presets list <project>`
to see what's available. The names and contents may change in future versions;
global presets you create will shadow them automatically.

| Preset | What it does | When to use |
|--------|-------------|-------------|
| `solo` | Single Sonnet agent, 25 turns | Quick fixes, small features |
| `review` | Read-only Opus reviewer | Code review, architecture analysis |
| `team` | Multi-agent team (architect + engineers + testers) | Larger features, refactors |

```bash
# Quick fix — single fast agent
terok run myproj "Fix the typo in login.py" --preset solo

# Code review — read-only analysis
terok run myproj "Review the auth module for security issues" --preset review

# Full dev team — architect plans, engineers implement, testers verify
terok run myproj "Add pagination to the /users endpoint" --preset team

# Team preset with an on-demand agent enabled
terok run myproj "Update the CLI help text" --preset team --agent cli-engineer
```

Presets work with all task modes:

```bash
terok task start myproj --preset review
terok task run-cli myproj 1 --preset team
```

### See What's Available

```bash
# List all presets (bundled + global + project)
terok presets list myproj

# Show what a preset resolves to
terok config-show myproj --preset team
```

### Customize: Global Presets

To tweak a bundled preset or create your own, put a YAML file in the
global presets directory. It's shared across all projects.

```bash
# Create the directory (first time only)
mkdir -p ~/.config/terok/core/presets

# Copy a bundled preset and customize it
terok config | grep "Bundled presets"   # find the path
cp <bundled-path>/solo.yml ~/.config/terok/core/presets/solo.yml
# Edit to taste — your version now shadows the bundled one
```

Or create one from scratch:

```bash
cat > ~/.config/terok/core/presets/quick-review.yml << 'EOF'
model: sonnet
max_turns: 10
subagents:
  - name: reviewer
    description: Fast code review
    tools: [Read, Grep, Glob]
    model: sonnet
    default: true
    system_prompt: |
      Review the code for bugs and suggest fixes. Be concise.
EOF
```

Now use it anywhere: `terok run anyproject "Review PR #42" --preset quick-review`

### Preset Search Order

When you use `--preset fast`, terok searches:

1. **Project** — `<project>/presets/fast.yml` (per-project override)
2. **Global** — `~/.config/terok/core/presets/fast.yml` (shared across projects)
3. **Bundled** — shipped with terok (always available)

First match wins. This means a global preset shadows a bundled one with the
same name, and a project preset shadows both.

### Task Teams

Run multiple tasks in the same project, each with a different preset:

```bash
# Task 1: architect reviews the codebase
terok task start myproj --preset review
# Task 2: team implements the feature
terok task start myproj --preset team
# Task 3: solo agent writes docs
terok task start myproj --preset solo
```

Each task remembers its preset — `terok task restart` reuses it automatically.

---

## Task Lifecycle Hooks

Hooks run user-configured shell commands on the **host** at key points during
a task container's lifecycle.  They are useful for port forwarding, notifications,
logging, or custom setup/teardown.

### Hook points

| Hook | When | Use case |
|------|------|----------|
| `pre_start` | Before the container is created | Validate prerequisites, set up host resources |
| `post_start` | After the container is running | Start sidecars, register with service discovery |
| `post_ready` | After the application is ready (CLI ready marker / toad serving) | Port forwarding, open browser, notify |
| `post_stop` | After the container stops | Clean up port forwards, notify, archive logs |

### Configuration

Hooks can be set globally (all projects) or per-project:

```yaml
# ~/.config/terok/config.yml (global)
hooks:
  post_ready: ~/.config/terok/hooks/on-ready.sh
  post_stop: ~/.config/terok/hooks/on-stop.sh
```

```yaml
# project.yml (per-project, overrides global)
run:
  hooks:
    post_ready: ./hooks/on-ready.sh
```

### Environment variables

Hook commands receive task context via environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `TEROK_HOOK` | Hook name | `post_ready` |
| `TEROK_PROJECT_ID` | Project ID | `myproject` |
| `TEROK_TASK_ID` | Task number | `3` |
| `TEROK_TASK_MODE` | Task mode | `cli`, `toad`, `run` |
| `TEROK_CONTAINER_NAME` | Podman container name | `myproject-toad-3` |
| `TEROK_WEB_PORT` | Web port (toad only) | `7861` |
| `TEROK_TASK_DIR` | Host-side task directory | `/home/user/.local/share/terok/tasks/myproject/3` |

### Hook tracking and sickbay

Hooks are tracked in task metadata (`hooks_fired` list).  If a task stops
without its `post_stop` hook running (e.g. after a crash or host reboot),
`terok sickbay` detects the inconsistency:

```bash
terok sickbay                    # check all projects
terok sickbay myproject          # check one project
terok sickbay myproject 3        # check one task
terok sickbay --fix              # auto-reconcile (run missed hooks)
```

### Example: task lifecycle logging

See `examples/hooks/task-notify.sh` for a simple example that logs
lifecycle events with task context to stderr.

---

## Image Management

Manage terok container images (L0/L1/L2 layers) with the `image` subcommand.

```bash
# List all terok images with sizes
terok image list

# List images for a specific project
terok image list myproj

# Remove orphaned and dangling terok images
terok image cleanup

# Preview what would be removed without actually deleting
terok image cleanup --dry-run
```

---

## Project Management

### Deleting a Project

Remove a project and all its associated data (tasks, containers, images):

```bash
# Delete with confirmation prompt
terok project-delete myproj

# Skip confirmation
terok project-delete myproj --force
```

### Deriving a Project

Create a new project from an existing one (shared infrastructure, fresh agent config):

```bash
terok project-derive myproj myproj-v2
```

### OpenCode Config Import

Import an OpenCode config file into the shared mount:

```bash
terok config import-opencode /path/to/opencode.json
```

---

## GPU Passthrough

GPU passthrough is a per-project opt-in feature (disabled by default).

### Enable in project.yml

```yaml
run:
  gpus: all   # or true
```

### Requirements

- NVIDIA drivers installed on host
- `nvidia-container-toolkit` with Podman integration
- A CUDA/NVIDIA-capable base image (e.g., NVIDIA HPC SDK or CUDA)

Set the base image in `project.yml`:
```yaml
image:
  base_image: nvcr.io/nvidia/nvhpc:25.9-devel-cuda13.0-ubuntu24.04
```

When enabled, terok adds:
- `--device nvidia.com/gpu=all`
- `NVIDIA_VISIBLE_DEVICES=all`
- `NVIDIA_DRIVER_CAPABILITIES=all`

---

## Running Containers Inside Your Container

Projects that run `podman` or `docker` inside their terok container (for example, projects developing container tooling, or those needing `fuse-overlayfs`) need the outer container launched with two extra flags. Declare this once in `project.yml`:

```yaml
run:
  nested_containers: true
```

When set, terok appends to the outer `podman run`:

- `--security-opt label=nested` — the SELinux type that confines the outer container but permits nested container operations (devpts mount, rootless overlay setup). This is *not* `label=disable` — SELinux stays enforced.
- `--device /dev/fuse` — required by rootless podman's `fuse-overlayfs` storage driver.

Verify inside the container:

```console
$ podman run alpine echo hello
hello
```

### Requirements

- Podman ≥ v4.5.0 on the **host** (introduced `label=nested`, April 2023 — every current distro ships 4.5+).
- A base image with podman preinstalled; the bundled `online-podman` / `gatekeeping-podman` presets point at `quay.io/podman/stable:latest` (Fedora-based, rootless-ready).
- On SELinux-enforcing hosts: `container-selinux` package (usually already installed on Fedora/RHEL).

If the image doesn't have podman preinstalled, `nested_containers: true` still sets the capabilities — your project's user snippet can install the runtime:

```yaml
image:
  base_image: fedora:43
  user_snippet_inline: RUN dnf install -y podman fuse-overlayfs
run:
  nested_containers: true
```

---

## Tips

- **Show resolved paths:** `terok config`
- **Where credentials live:** `~/.local/share/terok/credentials` (or `/var/lib/terok/credentials` if root, or as configured under `credentials.dir`)
- **Shared directories:** See [shared-dirs.md](shared-dirs.md)
- **Security modes:** See [git-gate-and-security-modes.md](git-gate-and-security-modes.md)
- **Copying text from the terminal:** TUI and tmux can intercept mouse
  events, preventing normal text selection from reaching the clipboard.
  Hold **Shift** while selecting, then **Shift+Ctrl+C** to copy.

---

## FAQ

### Where are templates and scripts stored?

Loaded from Python package resources bundled with the wheel (under `terok/resources/`). The application never reads from `/usr/share`.
