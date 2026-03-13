# Container Layers

## Overview

terok builds project containers in three logical layers. L0 (dev) and L1 (agent) are project-agnostic and cache well; L2 is project-specific.

## Layers

### L0 — Development Base (`terok-l0:<base-tag>`)

- Based on Ubuntu 24.04 by default (override via `docker.base_image`).
- Installs common tooling (git, openssh-client, ripgrep, vim, etc.).
- Creates `/workspace` and sets `WORKDIR` to `/workspace`.
- Creates a `dev` user with passwordless sudo and runs containers as that user.
- Stages `init-ssh-and-repo.sh` at `/usr/local/bin` and makes it the default `CMD`.
- Environment defaults: `REPO_ROOT=/workspace`, `GIT_RESET_MODE=none`.

### L1 — Agent Images (`terok-l1-cli:<base-tag>`, `terok-l1-ui:<base-tag>`)

Built `FROM` L0.

- **CLI image** installs Codex, Claude Code, GitHub Copilot, Mistral Vibe, OpenCode, and supporting tools.
- **UI image** installs UI dependencies, prefetches the Terok Web UI distribution, and sets `CMD` to `terok-ui-entry.sh`.
  - `terok-ui-entry.sh` runs `init-ssh-and-repo.sh` first, then starts the UI server using the pre-built distribution (downloaded at runtime only if missing).

### L2 — Project Images (`<project>:l2-cli`, `<project>:l2-ui`)

Built `FROM` the corresponding L1 image.

- Adds project-specific defaults (`CODE_REPO`, `SSH_KEY_NAME`, `GIT_BRANCH`) and the user snippet.
- Optional dev image (`<project>:l2-dev`) built `FROM` L0 when `--dev` is used.

The UI backend is configurable (Codex, Claude, or Mistral). Precedence:

1. CLI flag: `terokctl task run-web --backend <backend>`
2. Per-project config: `default_agent` in `project.yml`
3. Global config: `default_agent` in `config.yml`
4. Default: `codex`

## Build Flow

`terokctl generate <project>` renders four Dockerfiles into the per-project build directory:
`L0.Dockerfile`, `L1.cli.Dockerfile`, `L1.ui.Dockerfile`, `L2.Dockerfile`.

| Command | Layers Built | When to Use |
|---------|-------------|-------------|
| `terokctl build <project>` | L2 only | Project config changes |
| `terokctl build --agents <project>` | L0 + L1 + L2 | Update agents to latest versions |
| `terokctl build --full-rebuild <project>` | L0 + L1 + L2 (no cache) | Update base image or apt packages |
| `terokctl build --dev <project>` | + L2-dev image | Manual debugging container |

The `--agents` flag passes a unique `AGENT_CACHE_BUST` build arg to L1, invalidating the cache for agent install layers while preserving cache for apt packages.

The `--full-rebuild` flag adds `--no-cache` and `--pull=always` to rebuild everything from scratch.

`<base-tag>` is derived from `docker.base_image` (sanitized), e.g. `ubuntu:24.04` becomes `ubuntu-24.04`.

## Runtime Behavior

- `terokctl task run-cli` starts `<project>:l2-cli`; `terokctl task run-web` starts `<project>:l2-ui`.
- Both modes mount a per-task workspace to `/workspace`, shared credential directories, and optionally SSH config.
- The init script clones or syncs the project repository into `/workspace`.

See [Shared Directories](shared-dirs.md) for mount details.

## GPU Support

GPU passthrough is opt-in per project (`run.gpus` in `project.yml`). When enabled, terok adds the necessary Podman flags for NVIDIA GPUs. See [GPU Passthrough](usage.md#gpu-passthrough) for details.
