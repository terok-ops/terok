# Container Layers

## Overview

terok builds project containers in three logical layers. L0 (dev) and L1 (agent) are project-agnostic and cache well; L2 is project-specific.

## Layers

### L0 — Development Base (`terok-l0:<base-tag>`)

- Based on Ubuntu 24.04 by default (override via `image.base_image`).
- Installs common tooling (git, openssh-client, ripgrep, vim, etc.).
- Creates `/workspace` and sets `WORKDIR` to `/workspace`.
- Creates a `dev` user with passwordless sudo and runs containers as that user.
- Stages `init-ssh-and-repo.sh` at `/usr/local/bin` and makes it the default `CMD`.
- Environment defaults: `REPO_ROOT=/workspace`, `GIT_RESET_MODE=none`.

#### Base image families

Officially tested base images: `ubuntu:24.04`, `fedora:43`, `quay.io/podman/stable`, `nvcr.io/nvidia/nvhpc`. The package-manager branch (`apt`/`dnf`) is auto-detected from the image name. For images outside the allowlist, set `image.family: deb` or `image.family: rpm` explicitly:

```yaml
image:
  base_image: rockylinux:9
  family: rpm
```

### L1 — Agent Image (`terok-l1-cli:<base-tag>`)

Built `FROM` L0.

- Installs Codex, Claude Code, GitHub Copilot, Mistral Vibe, OpenCode, and supporting tools.

### L2 — Project Image (`<project>:l2-cli`)

Built `FROM` the L1 image.

- Adds project-specific defaults (`CODE_REPO`, `SSH_KEY_NAME`, `GIT_BRANCH`) and the user snippet.
- Optional dev image (`<project>:l2-dev`) built `FROM` L0 when `--dev` is used.

## Build Flow

`terok project generate <project>` renders Dockerfiles into the per-project build directory:
`L0.Dockerfile`, `L1.cli.Dockerfile`, `L2.Dockerfile`.

| Command | Layers Built | When to Use |
|---------|-------------|-------------|
| `terok project build <project>` | L2 only | Project config changes |
| `terok project build <project> --refresh-agents` | L0 + L1 + L2 | Bust the agent-install cache |
| `terok project build <project> --full-rebuild` | L0 + L1 + L2 (no cache) | Refresh base image + system packages |
| `terok project build <project> --agents <list>\|all` | L0 + L1 + L2 | One-shot override of which agents bake into L1 |
| `terok project build <project> --dev` | + L2-dev image | Manual debugging container |

`--refresh-agents` rebuilds from L0 with a fresh `AGENT_CACHE_BUST` build-arg; the per-agent install layers below the cache-bust point are re-executed, the system-package layer above it is reused.

`--full-rebuild` passes `--no-cache --pull=always`, forcing a fresh base-image pull and re-running system-package installs.

`--agents <list>\|all` selects which roster entries get baked into L1 for this build only. Defaults come from `image.agents` in `project.yml` (or the global `config.yml`). Different selections produce different L1 tag suffixes (`…-claude-codex`, `…-gh-glab`) and coexist in the local image store.

`<base-tag>` is derived from `image.base_image` (sanitized), e.g. `ubuntu:24.04` becomes `ubuntu-24.04`. When the selection suffix would push the tag past the OCI 128-char limit, the agent portion is replaced with a SHA1 digest of the sorted selection.

## Runtime Behavior

- `terok task run <project>` (default `--mode cli`) creates a fresh task and
  runs a container from `<project>:l2-cli`.  `--mode toad` uses the same
  L2 image with the Toad browser TUI entry point.
- `terokctl task attach <project> <task> --mode cli` re-runs an existing
  task in the same L2 image (scripting surface).
- Mounts a per-task workspace to `/workspace` and shared credential directories.
- The init script clones or syncs the project repository into `/workspace`.

See [Shared Directories](shared-dirs.md) for mount details.

## GPU Support

GPU passthrough is opt-in per project (`run.gpus` in `project.yml`). When enabled, terok adds the necessary Podman flags for NVIDIA GPUs. See [GPU Passthrough](usage.md#gpu-passthrough) for details.
