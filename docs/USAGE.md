# luskctl User Guide

A prefix-/XDG-aware tool to manage containerized AI agent projects using Podman. Provides a CLI (`luskctl`) and a Textual TUI (`luskctl-tui`).

## Table of Contents

- [Installation](#installation)
- [Runtime Locations](#runtime-locations)
- [Global Configuration](#global-configuration)
- [From Zero to First Run](#from-zero-to-first-run)
- [Headless Agent Runs (Autopilot)](#headless-agent-runs-autopilot)
- [Presets](#presets)
- [GPU Passthrough](#gpu-passthrough)
- [Tips](#tips)
- [FAQ](#faq)

---

## Installation

### Via pip

```bash
# Build from source
python -m pip install --upgrade build
python -m build
pip install dist/luskctl-*.whl

# Or install directly (editable for development)
pip install -e .

# With TUI support
pip install '.[tui]'
```

After install, you should have console scripts: `luskctl`, `luskctl-tui`

### Bash Completion

Bash completion is powered by argcomplete.

- If your system has bash-completion installed (common on most distros), completion is enabled automatically
- Manual setup:
  - One-time system-wide: `sudo activate-global-python-argcomplete`
  - Per-shell: `eval "$(register-python-argcomplete luskctl)"`
  - Per-user: add to `~/.bashrc`: `eval "$(register-python-argcomplete luskctl)"`
- Zsh users:
  ```bash
  autoload -U bashcompinit && bashcompinit
  eval "$(register-python-argcomplete --shell zsh luskctl)"
  ```

### Custom Install Paths

**User-local (no root):**
```bash
pip install --user .
# Binaries go to ~/.local/bin (ensure it's on PATH)
```

**Custom prefix (Debian/Ubuntu):**

On Debian/Ubuntu, pip uses the `posix_local` scheme which inserts `/local` under the prefix.

```bash
# Correct - let pip add /local:
pip install --prefix=/virt/podman .
# Result: /virt/podman/local/bin/luskctl

# Wrong - don't add /local yourself:
pip install --prefix=/virt/podman/local .
# Result: /virt/podman/local/local/bin/luskctl
```

**Virtual environment (recommended):**
```bash
python -m venv .venv && . .venv/bin/activate && pip install .
```

---

## Runtime Locations

### Config/Projects

| Install Type | Path |
|--------------|------|
| Root | `/etc/luskctl/projects` |
| User | `~/.config/luskctl/projects` |
| Override | `LUSKCTL_CONFIG_DIR=/path/to/config` |

### State (writable: tasks, build, gate)

| Install Type | Path |
|--------------|------|
| Root | `/var/lib/luskctl` |
| User | `${XDG_DATA_HOME:-~/.local/share}/luskctl` |
| Override | `LUSKCTL_STATE_DIR=/path/to/state` |

---

## Global Configuration

The tool looks for a global config file in this order (first found wins):

1. `${XDG_CONFIG_HOME:-~/.config}/luskctl/config.yml` (user override)
2. `sys.prefix/etc/luskctl/config.yml` (pip/venv installs)
3. `/etc/luskctl/config.yml` (system default)

### Example Config

Copy from `examples/luskctl-config.yml`:
```bash
mkdir -p ~/.config/luskctl
cp examples/luskctl-config.yml ~/.config/luskctl/config.yml
```

### Minimum Settings

```yaml
ui:
  base_port: 7860           # Default port for UI mode

paths:
  user_projects_root: ~/.config/luskctl/projects
  state_root: ~/.local/share/luskctl
  build_root: ~/.local/share/luskctl/build

git:
  human_name: "Your Name"
  human_email: "your@email.com"
```

---

## From Zero to First Run

### Prerequisites

- Podman installed and working
- OpenSSH client tools (ssh, ssh-keygen) for private Git over SSH
- tmux (optional, for `luskctl-tui --tmux` and persistent container sessions)
- ttyd (optional, for web-mode terminal access)

### Step 1: Create Project Directory

```bash
mkdir -p ~/.config/luskctl/projects/myproj
```

### Step 2: Create project.yml

```yaml
# ~/.config/luskctl/projects/myproj/project.yml
project:
  id: myproj
  security_class: online    # or "gatekeeping" for restricted mode

git:
  upstream_url: git@github.com:yourorg/yourrepo.git  # or https://...
  default_branch: main

# Optional: SSH hints for containers
ssh:
  key_name: id_ed25519_myproj  # matches key created by ssh-init

# Optional: Docker include snippet
docker:
  user_snippet_file: user.dockerinclude
```

### Step 3: (Optional) Docker Include Snippet

Create `~/.config/luskctl/projects/myproj/user.dockerinclude`:
```dockerfile
RUN apt-get update && apt-get install -y ripgrep jq && rm -rf /var/lib/apt/lists/*
```

This text is pasted near the end of your project image (L2) Dockerfile.

### Step 4: Generate Dockerfiles

```bash
luskctl generate myproj
```

### Step 5: Build Images

```bash
# Build only L2 project images (fast, reuses existing L0/L1 layers)
luskctl build myproj

# Rebuild L0+L1+L2 with fresh agent installs (codex, claude, opencode, vibe)
luskctl build --agents myproj

# Full rebuild with no cache (includes base image pull and apt packages)
luskctl build --full-rebuild myproj

# Optional: build a dev image from L0 as well
luskctl build myproj --dev
luskctl build --agents myproj --dev
```

Build modes:
- **Default** (`build`): Only rebuilds L2 project images, reuses existing L0/L1. Use for project config changes.
- **Agents** (`--agents`): Rebuilds L0+L1+L2 with fresh agent downloads. Use to update AI agents to latest versions.
- **Full rebuild** (`--full-rebuild`): Complete rebuild with `--no-cache` and `--pull=always`. Use when base image or apt packages need updating.

### Step 6: Initialize SSH (for private repos)

```bash
luskctl ssh-init myproj
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
luskctl task new myproj
# Output: Created task 1 for project myproj

# List tasks
luskctl task list myproj

# Run in CLI mode (headless agent)
luskctl task run-cli myproj 1

# Or run in UI mode (web interface)
luskctl task run-ui myproj 1 --backend codex
```

### Step 8: Log into a Running Container

```bash
# Open a shell in a running task container (persistent tmux session)
luskctl login myproj 1
```

This opens a tmux session inside the container. The session persists across
disconnects — re-running `luskctl login` reattaches to the same session.

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
luskctl-tui --tmux
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
luskctl run myproj "Fix the authentication bug in login.py"

# Override model and set a timeout
luskctl run myproj "Add unit tests for utils.py" --model opus --max-turns 50 --timeout 3600

# Detach immediately (don't stream output)
luskctl run myproj "Refactor the database layer" --no-follow

# Use a specific provider
luskctl run myproj "Fix the auth bug" --provider codex
luskctl run myproj "Add tests" --provider copilot
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
feature, luskctl applies an analogue where possible.  For example, `max_turns`
for providers without `--max-turns` support is injected as guidance text in the
prompt.  Features with no analogue produce a warning but don't block the run.

### Sub-Agent Configuration

Define sub-agents in your `project.yml` under the `agent:` section. Each
sub-agent gets a `default` flag — default agents are always included, others
are available on demand via `--agent`.

```yaml
# ~/.config/luskctl/projects/myproj/project.yml
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
luskctl run myproj "Find and fix the memory leak" --provider claude --agent debugger

# Include multiple non-default agents
luskctl run myproj "Debug and plan a fix" --provider claude --agent debugger --agent planner
```

The `--agent` flag also works with interactive modes:

```bash
luskctl task run-cli myproj 1 --agent debugger
luskctl task run-web myproj 1 --agent debugger
luskctl task start myproj --agent debugger
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
luskctl run myproj "Review the PR" --config /path/to/extra-agents.yml
```

The file should contain a `subagents:` list in the same format as `project.yml`.

### Global Agents and MCPs

Global agents and MCP servers are managed natively by Claude — luskctl does not
interfere with them:

| What | Where |
|------|-------|
| Global agents | `~/.claude/agents/` |
| Global MCPs | `~/.claude/settings.json` (`mcpServers` section) |
| Project agents | `<workspace>/.claude/agents/` |
| Project MCPs | `<workspace>/.claude/settings.json` |

Per-sub-agent MCPs can be defined inline using the `mcpServers` field in the
agent definition (same format as Claude's native agent JSON).

Run `luskctl config` to see the actual paths on your system.

---

### UI Mode Configuration

| Backend | API Key Environment Variable | Optional Model Variable |
|---------|------------------------------|------------------------|
| codex | (uses OpenAI from codex config) | - |
| claude | `LUSKUI_CLAUDE_API_KEY` or `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` | `LUSKUI_CLAUDE_MODEL` |
| mistral | `LUSKUI_MISTRAL_API_KEY` or `MISTRAL_API_KEY` | `LUSKUI_MISTRAL_MODEL` |

---

## Presets

Presets are reusable agent configurations you apply with `--preset <name>`.
Three are bundled and work immediately — no setup needed.

### Bundled Presets

The current bundled set is a starting point — run `luskctl presets list <project>`
to see what's available. The names and contents may change in future versions;
global presets you create will shadow them automatically.

| Preset | What it does | When to use |
|--------|-------------|-------------|
| `solo` | Single Sonnet agent, 25 turns | Quick fixes, small features |
| `review` | Read-only Opus reviewer | Code review, architecture analysis |
| `team` | Multi-agent team (architect + engineers + testers) | Larger features, refactors |

```bash
# Quick fix — single fast agent
luskctl run myproj "Fix the typo in login.py" --preset solo

# Code review — read-only analysis
luskctl run myproj "Review the auth module for security issues" --preset review

# Full dev team — architect plans, engineers implement, testers verify
luskctl run myproj "Add pagination to the /users endpoint" --preset team

# Team preset with an on-demand agent enabled
luskctl run myproj "Update the CLI help text" --preset team --agent cli-engineer
```

Presets work with all task modes:

```bash
luskctl task start myproj --preset review
luskctl task run-cli myproj 1 --preset team
luskctl task run-web myproj 1 --preset solo
```

### See What's Available

```bash
# List all presets (bundled + global + project)
luskctl presets list myproj

# Show what a preset resolves to
luskctl config-show myproj --preset team
```

### Customize: Global Presets

To tweak a bundled preset or create your own, put a YAML file in the
global presets directory. It's shared across all projects.

```bash
# Create the directory (first time only)
mkdir -p ~/.config/luskctl/presets

# Copy a bundled preset and customize it
luskctl config | grep "Bundled presets"   # find the path
cp <bundled-path>/solo.yml ~/.config/luskctl/presets/solo.yml
# Edit to taste — your version now shadows the bundled one
```

Or create one from scratch:

```bash
cat > ~/.config/luskctl/presets/quick-review.yml << 'EOF'
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

Now use it anywhere: `luskctl run anyproject "Review PR #42" --preset quick-review`

### Preset Search Order

When you use `--preset fast`, luskctl searches:

1. **Project** — `<project>/presets/fast.yml` (per-project override)
2. **Global** — `~/.config/luskctl/presets/fast.yml` (shared across projects)
3. **Bundled** — shipped with luskctl (always available)

First match wins. This means a global preset shadows a bundled one with the
same name, and a project preset shadows both.

### Task Teams

Run multiple tasks in the same project, each with a different preset:

```bash
# Task 1: architect reviews the codebase
luskctl task start myproj --preset review
# Task 2: team implements the feature
luskctl task start myproj --preset team
# Task 3: solo agent writes docs
luskctl task start myproj --preset solo
```

Each task remembers its preset — `luskctl task restart` reuses it automatically.

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
docker:
  base_image: nvcr.io/nvidia/nvhpc:25.9-devel-cuda13.0-ubuntu24.04
```

When enabled, luskctl adds:
- `--device nvidia.com/gpu=all`
- `NVIDIA_VISIBLE_DEVICES=all`
- `NVIDIA_DRIVER_CAPABILITIES=all`

---

## Tips

- **Show resolved paths:** `luskctl config`
- **Where envs live:** `~/.local/share/luskctl/envs` (or `/var/lib/luskctl/envs` if root, or as configured under `envs.base_dir`)
- **Shared directories:** See [SHARED_DIRS.md](SHARED_DIRS.md)
- **Security modes:** See [GIT_CACHE_AND_SECURITY_MODES.md](GIT_CACHE_AND_SECURITY_MODES.md)

---

## FAQ

### How do I install with a custom prefix?

See [Custom Install Paths](#custom-install-paths) above.

### Where are templates and scripts stored?

Loaded from Python package resources bundled with the wheel (under `luskctl/resources/`). The application never reads from `/usr/share`.

### How do I enable the TUI?

```bash
pip install 'luskctl[tui]'
```

Then run: `luskctl-tui`

### How do I package for Debian/RPM?

See [PACKAGING.md](PACKAGING.md).

---

## See Also

- [Developer Guide](DEVELOPER.md) - Internal architecture and contributor docs
- [Shared Directories](SHARED_DIRS.md) - Volume mounts and SSH configuration
- [Container Layers](CONTAINER_LAYERS.md) - Docker image architecture
- [Security Modes](GIT_CACHE_AND_SECURITY_MODES.md) - Online vs gatekeeping modes
