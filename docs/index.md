# terok

A tool for managing containerized AI coding agent projects using Podman. Provides both a CLI (`terokctl`) and a Textual TUI (`terok`).

## Features

- **Multiple Agents**: Claude Code, Codex, GitHub Copilot, Mistral Vibe, Blablador, and OpenCode
- **Headless Autopilot**: Run agents non-interactively with a prompt — useful for CI/CD and scripted workflows
- **Presets**: Bundled and custom reusable agent configurations (`solo`, `review`, `team`)
- **Multi-Agent Teams**: Run multiple specialized sub-agents in a single task
- **Task Lifecycle**: Create, run, stop, restart, follow up, and archive tasks
- **Security Modes**: Online and gatekeeping modes for different trust levels
- **Container Layers**: Efficient three-layer Docker image architecture (L0/L1/L2)
- **Gate Server**: Host-side git gate with systemd socket activation for gatekeeping workflows
- **SSH Integration**: Automatic SSH key management for private Git repositories
- **GPU Passthrough**: Per-project NVIDIA GPU support
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
terokctl generate myproj
terokctl build myproj

# 4. (Optional) Set up SSH for private repos
terokctl ssh-init myproj

# 5. Create and run a task
terokctl task new myproj
terokctl task run-cli myproj 1    # CLI mode
```

### Headless Agent Runs (Autopilot)

```bash
# Run an agent headlessly with a prompt
terokctl run myproj "Fix the authentication bug"

# With model override and timeout
terokctl run myproj "Add tests" --model opus --timeout 3600

# Use a specific provider
terokctl run myproj "Fix the bug" --provider codex
```

### Presets

Three presets work out of the box — no config needed:

```bash
terokctl run myproj "Fix the typo" --preset solo          # single fast agent
terokctl run myproj "Review auth module" --preset review   # read-only analysis
terokctl run myproj "Add pagination" --preset team         # multi-agent team
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
