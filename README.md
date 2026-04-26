# terok

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![REUSE compliant](https://api.reuse.software/badge/github.com/terok-ai/terok)](https://api.reuse.software/info/github.com/terok-ai/terok)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=terok-ai_terok&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=terok-ai_terok)

An open, Podman-native runtime for AI coding agents you can let off
the leash — without giving them the leash to your machine.

terok runs each agent task inside a hardened, rootless container with
default-deny outbound networking, a credential vault that keeps real
keys on the host, a per-task git checkpoint, and a desktop
notification path for live allow/deny decisions.  It ships a CLI
(`terok`) and a Textual TUI (`terok tui`) on top of a focused stack
of independently-released Python packages.

<p align="center">
  <img src="docs/img/architecture.svg" alt="terok ecosystem at a glance">
</p>

## What you get

#### Hardening

- **Rootless Podman** — no daemon, no setuid, no escalation surface
- **Default-deny egress firewall** with curated allowlist profiles
  and per-container audit logs (via `terok-shield`)
- **Credential vault** — long-lived secrets stay on the host; the
  container handles short-lived phantom tokens that are exchanged
  per request
- **Per-task git gate** — token-authenticated HTTP mirror; agents
  push only through the gate, optionally with a human-review
  checkpoint before promotion to upstream
- **Live Allow / Deny prompts** — desktop notifications on blocked
  outbound traffic, surfaced through a varlink hub and turned into
  immediate firewall rules

#### Workflow

- **Projects ⊃ Tasks** — long-lived project config, ephemeral task
  containers; many tasks per project, each fully isolated
- **Multi-agent presets** — `solo`, `review`, `team` ship out of
  the box; the `team` preset orchestrates specialised sub-agents
  (architect, library-engineer, tests, docs…) in parallel
- **Headless / interactive / multi-agent web** — pick the launch
  mode per task; same agents, same hardening
- **Layered images** — base distro · agent CLIs · per-project
  snippet, cached and reused across projects
- **GPU passthrough** — for projects whose base image supports it
- **Sickbay + panic** — health checks with auto-remediation, plus an
  emergency kill-switch
- **Multi-vendor agents** — Claude Code, Codex, Copilot, Vibe, plus
  custom LLM endpoints via OpenCode (Helmholtz, university, or your
  own endpoint — bundled defaults included)

## The five-package stack

| Package | Role |
|---------|------|
| **terok** (this repo) | Project orchestration, presets, TUI, sickbay |
| [terok-executor](https://github.com/terok-ai/terok-executor) | Per-task agent runner, image factory, auth flows |
| [terok-sandbox](https://github.com/terok-ai/terok-sandbox) | Hardened Podman runtime, credential vault, git gate |
| [terok-shield](https://github.com/terok-ai/terok-shield) | nftables egress firewall + audit |
| [terok-clearance](https://github.com/terok-ai/terok-clearance) | Live allow/deny prompts via D-Bus + varlink |

Each package is independently usable and independently released.
Most users only ever interact with `terok` itself.

## Documentation

| Document | Description |
|----------|-------------|
| [Concepts](docs/concepts.md) | Architecture, security model, design rationale |
| [Full Usage Guide](docs/usage.md) | Complete user documentation |
| [Developer Guide](docs/developer.md) | Internal architecture and contributor docs |
| [Container Layers](docs/container-layers.md) | Container image architecture |
| [Container Lifecycle](docs/container-lifecycle.md) | Container and image lifecycle |
| [Shared Directories](docs/shared-dirs.md) | Volume mounts and vault |
| [Security Modes](docs/git-gate-and-security-modes.md) | Online vs gatekeeping modes |
| [Shield](docs/shield-security.md) | Egress firewall (terok-shield) |
| [Agent Compatibility](docs/agent-compat-matrix.md) | Per-agent feature support matrix |
| [Login Design](docs/login-design.md) | Login session architecture |
| [Docker](docs/docker.md) | Running terok inside Docker (experimental) |

## Quick Start

### Prerequisites

- Podman installed and configured (rootless recommended)
- Python 3.12+
- OpenSSH client (for private git repos)

> **No Podman?** A [Docker-based setup](docs/docker.md) is available for
> evaluation, but native Podman is recommended for regular use.

### Installation

```bash
# Install the latest release wheel (download from GitHub Releases page)
pipx install ./terok-*.whl
```

### One-time setup

One command installs everything — shield OCI hooks, vault, git gate,
D-Bus clearance bridge, and an XDG desktop entry for the TUI:

```bash
terok setup                             # idempotent; safe to re-run after upgrades
terok completions install               # (optional) tab completion
```

To remove everything later:

```bash
terok uninstall                         # reverse of setup; preserves credential DB
terok uninstall --purge-credentials     # also delete stored tokens + SSH keys
```

### First project

Launch the TUI and create your first project from there:

```bash
terok tui
```

- Press **n** to run the project wizard (creates config, builds images, sets up SSH + gate)
- Select your new project, press **a** to authenticate your agent
- **Tab** to the task list, press **c** to start a CLI task

Or do the same from the command line:

```bash
terok auth claude                       # authenticate host-wide (no project needed)
terok auth                              # interactive menu — pick multiple providers
terok project wizard                    # interactive project setup
terok task run myproj                   # create a CLI task and attach (default on TTY)
terok task run myproj --no-attach       # start it detached; print login instructions
terok task run myproj --mode toad       # multi-agent web TUI (browser access)
terok login myproj a3                   # re-attach later by hex ID prefix
terok auth claude --project myproj      # project-scoped escape hatch (uses its L2 image)
```

For manual project configuration or CI, see the [User Guide](docs/usage.md).

### Headless agent runs (autopilot)

```bash
# Run an agent headlessly with a prompt (uses default_agent config; falls back to claude)
terok task run myproj "Fix the authentication bug"

# With model override and timeout
terok task run myproj "Add tests" --model opus --timeout 3600

# Use a specific provider
terok task run myproj "Fix the bug" --provider codex
```

### Presets

Three presets work out of the box — no config needed:

```bash
terok task run myproj "Fix the typo" --preset solo          # single fast agent
terok task run myproj "Review auth module" --preset review  # read-only analysis
terok task run myproj "Add pagination" --preset team        # multi-agent team
```

Create your own in `~/.config/terok/presets/` (shared across projects) or
per-project in `<project>/presets/`. See the
[Presets Guide](docs/usage.md#presets) for details.

### Common Commands

```bash
terok project list                      # List projects
terok config paths                      # Show resolved paths and config
terok task list <project>               # List tasks (hex IDs)
terok task delete <project> <task_id>   # Delete a task
terok login <project> <id_prefix>       # Attach to running task
terok project init <project>            # Full setup: ssh + generate + build + gate
terok project wizard                    # Interactive project creation
terok image usage                       # Disk usage across projects and images
terok sickbay                           # In-container health checks
terok panic                             # Emergency kill-switch
terok image list [project]              # List terok images
terok image cleanup [--dry-run]         # Remove orphaned images
terok completions install               # Install shell completions
```

## Tips

- **Clipboard:** If mouse selection doesn't copy to your clipboard,
  hold **Shift** while selecting, then **Shift+Ctrl+C** to copy.
  See [Tips](docs/usage.md#tips) for details.

## Configuration

### Global Config

Location: `~/.config/terok/config.yml`

```yaml
git:
  human_name: "Your Name"
  human_email: "your@email.com"

image:
  agents: "all"   # default roster selection for every project
```

Per-project overrides live in `project.yml` under `image:` —
`base_image`, `family` (`deb`/`rpm`, auto-detected for
ubuntu/fedora/podman/nvcr.io/nvidia), and `agents` (which roster
entries to bake into L1).  See
[docs/usage.md](docs/usage.md#choosing-which-agents-to-bake-in) for
the full precedence and selection mechanics.

### Environment Overrides

| Variable | Purpose |
|----------|---------|
| `TEROK_CONFIG_DIR` | Configuration directory (`~/.config/terok`) |
| `TEROK_CONFIG_FILE` | Global config file path |
| `TEROK_ROOT` | Shared namespace root for all ecosystem packages |
| `TEROK_STATE_DIR` | Host-only state (builds, metadata) |
| `TEROK_VAULT_DIR` | Vault store (vault database, routes, key registry) |

## Requirements

- **Podman** is required for build/run commands

## Contributing

```bash
# Setup
git clone git@github.com:terok-ai/terok.git && cd terok
make install-dev

# Before committing
make lint      # Run linter (required)
make format    # Auto-fix issues if lint fails

# Before pushing
make check     # Run all checks (lint + test + tach + docstrings + deadcode + reuse)
```

See [Developer Guide](docs/developer.md) for full details.

## License

See [LICENSE](LICENSE) file.
