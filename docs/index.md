# terok

An open, Podman-native runtime for AI coding agents you can let off
the leash — without giving them the leash to your machine.

terok runs each agent task inside a hardened, rootless container with
default-deny outbound networking, a credential vault that keeps real
keys on the host, a per-task git checkpoint, and a desktop
notification path for live allow/deny decisions.  It ships a CLI
(`terok`) and a Textual TUI (`terok tui`) on top of a focused stack
of independently-released Python packages.

![terok ecosystem at a glance](img/architecture.svg)

## What you get

#### Hardening

- **Rootless Podman** — no daemon, no setuid, no escalation surface
- **Default-deny egress firewall** with curated allowlist profiles
  and per-container audit logs (via
  [terok-shield](https://github.com/terok-ai/terok-shield))
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
  the box; `team` orchestrates specialised sub-agents (architect,
  library-engineer, tests, docs…) in parallel
- **Headless / interactive / multi-agent web** — pick the launch
  mode per task; same agents, same hardening
- **Layered images** — base distro · agent CLIs · per-project
  snippet, cached and reused across projects; Ubuntu / Debian / RPM
  out of the box, GPU passthrough for projects whose base image
  supports it
- **Sickbay + panic** — health checks with auto-remediation and an
  emergency kill-switch
- **Multi-vendor agents** — Claude Code, Codex, Copilot, Vibe, plus
  custom LLM endpoints via OpenCode (Helmholtz, university, or your
  own endpoint — bundled defaults included)

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
terok shield install-hooks --user               # install OCI hooks for terok-shield
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
terok project wizard                    # interactive setup
terok auth claude myproj                # authenticate agent
terok task run myproj                 # start a CLI agent task
terok task run myproj --mode toad     # Toad multi-agent TUI (browser access)
terok login myproj a3                   # attach to a running task by hex ID prefix
```

For manual project configuration or CI, see the [User Guide](usage.md).

### Headless Agent Runs (Autopilot)

```bash
# Run an agent headlessly with a prompt
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
terok task run myproj "Review auth module" --preset review   # read-only analysis
terok task run myproj "Add pagination" --preset team         # multi-agent team
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
