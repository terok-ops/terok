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

### L1 — Agent Image (`terok-l1-cli:<base-tag>`)

Built `FROM` L0.

- Installs Codex, Claude Code, GitHub Copilot, Mistral Vibe, OpenCode, and supporting tools.

### L2 — Project Image (`<project>:l2-cli`)

Built `FROM` the L1 image.

- Adds project-specific defaults (`CODE_REPO`, `SSH_KEY_NAME`, `GIT_BRANCH`) and the user snippet.
- Optional dev image (`<project>:l2-dev`) built `FROM` L0 when `--dev` is used.

## Build Flow

`terokctl generate <project>` renders Dockerfiles into the per-project build directory:
`L0.Dockerfile`, `L1.cli.Dockerfile`, `L2.Dockerfile`.

| Command | Layers Built | When to Use |
|---------|-------------|-------------|
| `terokctl build <project>` | L2 only | Project config changes |
| `terokctl build --agents <project>` | L0 + L1 + L2 | Rebuild from L0 with fresh agents |
| `terokctl build --full-rebuild <project>` | L0 + L1 + L2 (no cache) | Rebuild from L0 (no cache) with fresh base image + apt packages |
| `terokctl build --dev <project>` | + L2-dev image | Manual debugging container |

The `--agents` flag rebuilds from L0 and passes a unique `AGENT_CACHE_BUST` build arg to L1, invalidating the cache for agent install layers while preserving cache for apt packages where possible.

The `--full-rebuild` flag rebuilds from L0 with `--no-cache` and `--pull=always`, forcing a fresh base-image pull and fresh apt-package layers.

`<base-tag>` is derived from `docker.base_image` (sanitized), e.g. `ubuntu:24.04` becomes `ubuntu-24.04`.

## Runtime Behavior

- `terokctl task run-cli` starts `<project>:l2-cli`.
- Mounts a per-task workspace to `/workspace`, shared credential directories, and optionally SSH config.
- The init script clones or syncs the project repository into `/workspace`.

See [Shared Directories](shared-dirs.md) for mount details.

## GPU Support

GPU passthrough is opt-in per project (`run.gpus` in `project.yml`). When enabled, terok adds the necessary Podman flags for NVIDIA GPUs. See [GPU Passthrough](usage.md#gpu-passthrough) for details.
