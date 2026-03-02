# terok

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![REUSE compliant](https://api.reuse.software/badge/github.com/terok-ops/terok)](https://api.reuse.software/info/github.com/terok-ops/terok)

A tool for managing containerized AI coding agent projects using Podman. Provides both a CLI (`terokctl`) and a Textual TUI (`terok`).

> **Future plans and design documents** are in [`docs/brainstorming/`](docs/brainstorming/).

> Similar project also listed on https://github.com/milisp/awesome-codex-cli

## Documentation

| Document | Description |
|----------|-------------|
| [Full Usage Guide](docs/USAGE.md) | Complete user documentation |
| [Developer Guide](docs/DEVELOPER.md) | Internal architecture and contributor docs |
| [Shared Directories](docs/SHARED_DIRS.md) | Volume mounts and SSH configuration |
| [Container Layers](docs/CONTAINER_LAYERS.md) | Docker image architecture |
| [Security Modes](docs/GIT_CACHE_AND_SECURITY_MODES.md) | Online vs gatekeeping modes |
| [Packaging](docs/PACKAGING.md) | pip, deb, and rpm packaging |

## Quick Start

### Prerequisites

- Podman installed and configured
- Python 3.12+
- OpenSSH client (for private git repos)

### Installation

```bash
# Clone and install
git clone git@github.com:terok-ops/terok.git
cd terok
pip install .

# With TUI support
pip install '.[tui]'
```

### Basic Workflow

```bash
# 1. Create project directory
mkdir -p ~/.config/terok/projects/myproj

# 2. Create project.yml (see docs/USAGE.md for full schema)
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
# or
terokctl task run-ui myproj 1     # Web UI mode
```

### Headless Agent Runs (Autopilot)

```bash
# Run an agent headlessly with a prompt (uses default_agent config; falls back to claude)
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
[Presets Guide](docs/USAGE.md#presets) for details.

### Common Commands

```bash
terokctl projects              # List projects
terokctl config                # Show resolved paths
terokctl task list <project>   # List tasks
terokctl task delete <project> <task_id>  # Delete a task
```

## Configuration

### Global Config

Location: `~/.config/terok/config.yml`

```yaml
ui:
  base_port: 7860

paths:
  user_projects_root: ~/.config/terok/projects
  state_root: ~/.local/share/terok

git:
  human_name: "Your Name"
  human_email: "your@email.com"
```

### Environment Overrides

| Variable | Purpose |
|----------|---------|
| `TEROK_CONFIG_DIR` | Projects directory |
| `TEROK_STATE_DIR` | Writable state root |
| `TEROK_CONFIG_FILE` | Global config file path |

## Requirements

- **Podman** is required for build/run commands
- **TUI** is optional: `pip install 'terok[tui]'`

## Contributing

```bash
# Setup
git clone git@github.com:terok-ops/terok.git && cd terok
make install-dev

# Before committing
make lint      # Run linter (required)
make format    # Auto-fix issues if lint fails

# Before pushing
make test      # Run tests
```

See [Developer Guide](docs/DEVELOPER.md) for full details.

## License

See [LICENSE](LICENSE) file.
