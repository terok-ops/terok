# terok

Orchestration and instrumentation platform for containerized AI coding agents.
Provides both a CLI (`terok`) and a Textual TUI (`terok-tui`, or `terok tui`).

terok manages the *what* — which agents run, how they're configured, and
what code they work on.  The hardened container runtime
([terok-sandbox](https://github.com/terok-ai/terok-sandbox)) manages the
*how* — Podman isolation, egress firewalling, gated git access, and SSH
provisioning.

## Features

- **Multiple Agents**: Claude Code, Codex, GitHub Copilot, Mistral Vibe, Blablador, Kisski, and OpenCode
- **Selectable Agents**: Pick which agents bake into your image via `image.agents` in `project.yml` or `--agents claude,codex` on the command line — different selections coexist as separate L1 images
- **Headless Autopilot**: Run agents non-interactively with a prompt — useful for CI/CD and scripted workflows
- **Presets**: Bundled and custom reusable agent configurations (`solo`, `review`, `team`)
- **Multi-Agent Teams**: Run multiple specialized sub-agents in a single task
- **Task Lifecycle**: Create, run, stop, restart, follow up, and archive tasks
- **Security Modes**: Online and gatekeeping modes for different trust levels
- **Container Layers**: Efficient three-layer container image architecture (L0/L1/L2)
- **Distro-Agnostic Base Images**: Ubuntu/Debian (apt) and Fedora/RPM (dnf) base images supported out of the box; `image.family` override for anything outside the allowlist
- **Nested Containers**: `run.nested_containers: true` declares projects that run podman/docker inside their container — terok adds the SELinux `label=nested` type and `/dev/fuse` device, keeping the outer container's SELinux boundary intact
- **Hardened Runtime**: Defence-in-depth via [terok-sandbox](https://github.com/terok-ai/terok-sandbox) — egress firewall, gated git access, SSH isolation, GPU passthrough, socket-based credential and SSH transport with SELinux support
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

### One-time setup

Install OCI hooks for the egress firewall, start the host-side services
(vault and git gate), and optionally add shell completions:

```bash
terok shield setup --user               # install OCI hooks for terok-shield
terok vault install                     # install systemd socket activation
terok vault start                       # start the vault daemon
terok gate start                        # start the git gate server
terok completions install               # (optional) tab completion
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
terok project-wizard                    # interactive setup
terok auth claude myproj                # authenticate agent
terok task start myproj                 # start a CLI agent task
terok task start myproj --toad          # Toad multi-agent TUI (browser access)
terok login myproj a3                   # attach to a running task by hex ID prefix
```

For manual project configuration or CI, see the [User Guide](usage.md).

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
- [Container Layers](container-layers.md) — Container image architecture
- [Container Lifecycle](container-lifecycle.md) — Container and image lifecycle
- [Shared Directories](shared-dirs.md) — Volume mounts and vault
- [Security Modes](git-gate-and-security-modes.md) — Online vs gatekeeping modes
- [Shield](shield-security.md) — Egress firewall (terok-shield)
- [Agent Compatibility Matrix](agent-compat-matrix.md) — Per-agent feature support
- [Login Design](login-design.md) — Login session architecture
- [Docker](docker.md) — Running terok inside Docker (experimental)
- [Developer Guide](developer.md) — Architecture and contributing
- [API Reference](reference/) — Auto-generated API documentation

## License

See [LICENSE](https://github.com/terok-ai/terok/blob/master/LICENSE) file.
