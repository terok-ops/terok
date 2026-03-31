# terok

Orchestration and instrumentation platform for containerized AI coding agents.
Provides both a CLI (`terok`) and a Textual TUI (`terok-tui`, or `terok tui`).

terok manages the *what* — which agents run, how they're configured, and
what code they work on.  The hardened container runtime
([terok-sandbox](https://github.com/terok-ai/terok-sandbox)) manages the
*how* — Podman isolation, egress firewalling, gated git access, and SSH
provisioning.

## Features

- **Multiple Agents**: Claude Code, Codex, GitHub Copilot, Mistral Vibe, Blablador, and OpenCode
- **Headless Autopilot**: Run agents non-interactively with a prompt — useful for CI/CD and scripted workflows
- **Presets**: Bundled and custom reusable agent configurations (`solo`, `review`, `team`)
- **Multi-Agent Teams**: Run multiple specialized sub-agents in a single task
- **Task Lifecycle**: Create, run, stop, restart, follow up, and archive tasks
- **Security Modes**: Online and gatekeeping modes for different trust levels
- **Container Layers**: Efficient three-layer Docker image architecture (L0/L1/L2)
- **Hardened Runtime**: Defence-in-depth via [terok-sandbox](https://github.com/terok-ai/terok-sandbox) — egress firewall, gated git access, SSH isolation, GPU passthrough
- **Agent Instructions**: Layered, inheritable instruction system delivered to every task
- **Interactive TUI**: Full-featured Textual interface with project/task management, log viewing, and login sessions

## Quick Start

### Prerequisites

- Podman installed and configured
- Python 3.12+
- OpenSSH client (for private git repos)

### Installation

```bash
# Download the latest .whl from the GitHub Releases page, then:
pipx install ./terok-*.whl
```

### Basic Workflow

```bash
# 1. Create project directory
mkdir -p ~/.config/terok/projects/myproj

# 2. Create project.yml (see User Guide for full schema)
cat > ~/.config/terok/projects/myproj/project.yml << 'EOF'
project:
  id: myproj
  security_class: online
git:
  upstream_url: https://github.com/yourorg/yourrepo.git
  default_branch: main
EOF

# 3. Generate and build images
terok generate myproj
terok build myproj

# 4. (Optional) Set up SSH for private repos
terok ssh-init myproj

# 5. Create and run a task
terok task new myproj
terok task run-cli myproj 1    # CLI mode
```

### Headless Agent Runs (Autopilot)

```bash
# Run an agent headlessly with a prompt
terok run myproj "Fix the authentication bug"

# With model override and timeout
terok run myproj "Add tests" --model opus --timeout 3600

# Use a specific provider
terok run myproj "Fix the bug" --provider codex
```

### Presets

Three presets work out of the box — no config needed:

```bash
terok run myproj "Fix the typo" --preset solo          # single fast agent
terok run myproj "Review auth module" --preset review   # read-only analysis
terok run myproj "Add pagination" --preset team         # multi-agent team
```

Create your own in `~/.config/terok/presets/` (shared across projects) or
per-project in `<project>/presets/`. See the
[Presets Guide](usage.md#presets) for details.

## Documentation

- [Concepts](concepts.md) — Architecture, security model, and design rationale
- [User Guide](usage.md) — Complete user documentation
- [Container Layers](container-layers.md) — Docker image architecture
- [Container Lifecycle](container-lifecycle.md) — Container and image lifecycle
- [Shared Directories](shared-dirs.md) — Volume mounts and SSH configuration
- [Security Modes](git-gate-and-security-modes.md) — Online vs gatekeeping modes
- [Login Design](login-design.md) — Login session architecture
- [Developer Guide](developer.md) — Architecture and contributing
- [Agent Compatibility Matrix](agent-compat-matrix.md) — Per-agent feature support
- [API Reference](reference/) — Auto-generated API documentation

## License

See [LICENSE](https://github.com/terok-ai/terok/blob/master/LICENSE) file.
