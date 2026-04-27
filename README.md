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
and a Textual TUI on top of a focused stack of independently-released
Python packages.

<p align="center">
  <img src="docs/img/architecture.svg" alt="terok ecosystem at a glance">
</p>

## What you get

### Hardening

- **Rootless Podman** — no daemon, no privileged user namespace
- **Default-deny egress firewall** with curated allowlist profiles
  and per-container audit logs (via
  [terok-shield](https://github.com/terok-ai/terok-shield))
- **Credential vault** — secrets stay on the host
- **Per-task git gate** — a git mirror that the agent pushes through,
  giving you a human-review point before changes leave your machine
- **Live Allow / Deny prompts** — desktop notifications on blocked
  outbound traffic, turned into immediate firewall rules

### Workflow

- **Projects ⊃ Tasks** — long-lived project config, ephemeral task
  containers; many tasks per project
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

## The five-package stack

| Package | Role |
|---------|------|
| **terok** (this repo) | Project orchestration, TUI, sickbay |
| [terok-executor](https://github.com/terok-ai/terok-executor) | Per-task agent runner, image factory, auth flows |
| [terok-sandbox](https://github.com/terok-ai/terok-sandbox) | Hardened Podman runtime, credential vault, git gate |
| [terok-shield](https://github.com/terok-ai/terok-shield) | nftables egress firewall + audit |
| [terok-clearance](https://github.com/terok-ai/terok-clearance) | Live allow/deny prompts via D-Bus + varlink |

## Quick Start

### Prerequisites

Hard dependencies:

- **Podman** (rootless)
- **`nft`** (nftables CLI)
- **Python 3.12+**
- **OpenSSH client** — for private git repos

Optional but recommended:

- **systemd** user session — runs the gate / vault / clearance daemons
- **`dnsmasq`** and **`dig`** — DNS plumbing the egress firewall uses
- A desktop **notification daemon** — for the Allow / Deny popups path

### Installation

```bash
# Install the latest release wheel (download from GitHub Releases page)
pipx install ./terok-*.whl
```

### One-time setup

```bash
terok setup                             # idempotent; safe to re-run after upgrades
```

`setup` installs the shield OCI hooks, the vault, the git gate, the
D-Bus clearance bridge, the XDG desktop entry for the TUI, and shell
completions for your detected shell.

To remove everything later:

```bash
terok uninstall                         # reverse of setup; preserves credential DB
```

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
terok auth                              # interactive menu — pick multiple providers
terok project wizard                    # interactive project setup
terok task run myproj                   # create a CLI task and attach (default on TTY)
terok task run myproj --mode toad       # web interface (browser access)
terok login myproj a3                   # re-attach later by task ID prefix
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

### Common Commands

```bash
terok project list                      # List projects
terok config paths                      # Show resolved paths and config
terok task list <project>               # List tasks
terok task delete <project> <task_id>   # Delete a task
terok login <project> <id_prefix>       # Attach to running task
terok project init <project>            # Full setup: ssh + generate + build + gate
terok project wizard                    # Interactive project creation
terok image usage                       # Disk usage across projects and images
terok sickbay                           # In-container health checks
terok panic                             # Emergency kill-switch
terok image list [project]              # List terok images
terok image cleanup [--dry-run]         # Remove orphaned images
terok completions install               # Re-install shell completions
```

## Notes

- **SELinux hosts**: install the policy module before `terok setup`,
  otherwise the shield + clearance services bind sockets as
  `unconfined_t` and podman will refuse to talk to them.  The exact
  install command (a `sudo bash` over the script terok-sandbox
  ships) is printed by `terok sickbay` when the policy is missing —
  run it once, then re-run `terok setup`.
- **Clipboard**: If mouse selection doesn't copy to your clipboard,
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

If `git.human_name` and `git.human_email` are omitted, terok falls
through to your host `git config`.  Setting them in `config.yml` is
the way to override the host-level identity for container commits.

To see what you can pick from for `image.agents`:

```bash
terok agents                            # list available AI coding agents
```

Officially-tested base images for `image.base_image`: `ubuntu:24.04`,
`fedora:43`, `quay.io/podman/stable`, `nvcr.io/nvidia/nvhpc`.  Other
images in the same family (`ubuntu:*`, `debian:*`, `fedora:*`,
`nvcr.io/nvidia/*`, `quay.io/podman/*`) work via auto-detection;
anything else needs an explicit `image.family: deb|rpm` override.
See [docs/usage.md](docs/usage.md#choosing-which-agents-to-bake-in)
for the full mechanics.

## Contributing

See the [Developer Guide](docs/developer.md).

## License

See [LICENSE](LICENSE) file.
