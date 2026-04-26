# terok

An open, Podman-native runtime for AI coding agents you can let off
the leash — without giving them the leash to your machine.

terok runs each agent task inside a hardened, rootless container with
default-deny outbound networking, a credential vault that keeps real
keys on the host, a per-task git checkpoint, and a desktop
notification path for live allow/deny decisions.  It ships a CLI
and a Textual TUI on top of a focused stack of independently-released
Python packages.

![terok ecosystem at a glance](img/architecture.svg)

## What you get

#### Hardening

- **Rootless Podman** — no daemon, no privileged user namespace
- **Default-deny egress firewall** with curated allowlist profiles
  and per-container audit logs (via
  [terok-shield](https://github.com/terok-ai/terok-shield))
- **Credential vault** — secrets stay on the host
- **Per-task git gate** — a git mirror that the agent pushes through,
  giving you a human-review point before changes leave your machine
- **Live Allow / Deny prompts** — desktop notifications on blocked
  outbound traffic, turned into immediate firewall rules

#### Workflow

- **Projects ⊃ Tasks** — long-lived project config, ephemeral task
  containers; many tasks per project, each fully isolated
- **Headless / interactive / web interface** — pick the launch mode
  per task; same agents, same hardening
- **Layered images** — base distro · agent CLIs · per-project
  snippet, cached and reused across projects; Ubuntu / Debian /
  Fedora / nvidia/cuda out of the box, GPU passthrough for projects
  whose base image supports it
- **Sickbay + panic** — health checks with auto-remediation and an
  emergency kill-switch
- **Multi-vendor agents** — Claude Code, Codex, Copilot, Vibe, plus
  custom LLM endpoints via OpenCode (Helmholtz, university, or your
  own endpoint — bundled defaults included)

## Quick Start

### Prerequisites

- **Podman** (rootless) and **`nft`** (nftables CLI) — the two hard
  dependencies
- **Python 3.12+**
- **OpenSSH client** — for private git repos
- Optional but recommended: **systemd** user session, **`dnsmasq`**
  or **`dig`**, a desktop **notification daemon**

### Installation

```bash
# Download the latest .whl from the GitHub Releases page, then:
pipx install ./terok-*.whl
```

### One-time setup

```bash
terok setup
```

`setup` installs the shield OCI hooks, the vault, the git gate, the
D-Bus clearance bridge, the XDG desktop entry for the TUI, and shell
completions for your detected shell.  Idempotent — safe to re-run.

### First project

Launch the TUI:

```bash
terok                                   # bare `terok` on a TTY runs the TUI
```

- Press **n** to run the project wizard (creates config, builds images, sets up SSH + gate)
- Select your new project, press **a** to authenticate your agent
- **Tab** to the task list, press **c** to start a CLI task

Or do the same from the command line:

```bash
terok auth claude                       # authenticate host-wide
terok project wizard                    # interactive project setup
terok task run myproj                   # create a CLI task and attach (default on TTY)
terok task run myproj --mode toad       # web interface (browser access)
terok login myproj a3                   # re-attach later by task ID prefix
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
