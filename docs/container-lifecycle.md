# Container and Image Lifecycle

## Overview

terok manages two types of resources:
- **Images** — immutable, built once, shared across tasks
- **Containers** — mutable instances of images, one per task

```text
┌─────────────────────────────────────────────────────────────────┐
│                         IMAGES (immutable)                      │
│  ┌─────────┐    ┌─────────────┐    ┌─────────────────────────┐  │
│  │   L0    │───▶│     L1      │───▶│          L2             │  │
│  │  (dev)  │    │   (cli)     │    │     (project-cli)       │  │
│  └─────────┘    └─────────────┘    └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      CONTAINERS (mutable)                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  project-cli-1  │  │  project-cli-2  │  │  project-cli-3  │  │
│  │    (task 1)     │  │    (task 2)     │  │    (task 3)     │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Container Lifecycle

### Task = Workspace + Metadata + Container

A task consists of three persistent components:

```text
Task #1
├── Workspace    ~/.local/share/terok/tasks/<project>/1/workspace-dangerous/
├── Metadata     ~/.local/share/terok/projects/<project>/tasks/1.yml
└── Container    <project>-cli-1
```

All three persist independently and survive:
- Container stops
- Machine reboots
- terok restarts

### Container States

```text
                    ┌──────────────────┐
                    │   (not exists)   │
                    └────────┬─────────┘
                             │
                    task run-cli
                    (first time)
                             │
                             ▼
    ┌────────────────────────────────────────────────────┐
    │                                                    │
    │  ┌──────────┐   task stop    ┌──────────────────┐  │
    │  │ RUNNING  │ ──────────────▶│ STOPPED / EXITED │  │
    │  │          │                │                  │  │
    │  │          │◀────────────── │                  │  │
    │  └──────────┘  task restart  └──────────────────┘  │
    │       │        task run-cli          │              │
    │       │                             │              │
    │       └──────────┬──────────────────┘              │
    │                  │                                 │
    │             task delete                            │
    │                  │                                 │
    │                  ▼                                 │
    │         ┌──────────────┐                           │
    │         │   REMOVED    │                           │
    │         └──────────────┘                           │
    │                                                    │
    └────────────────────────────────────────────────────┘
```

### CLI Commands

| Command | Container Exists & Running | Container Exists & Stopped | Container Doesn't Exist |
|---------|---------------------------|---------------------------|------------------------|
| `task run-cli` | Shows "already running" | `podman start` | `podman run` (create) |
| `task stop` | `podman stop` | Error: not running | Error: not running |
| `task restart` | Shows "already running" | `podman start` | `podman run` (create)  |
| `task status`  | Shows state            | Shows state     | Shows "not found"      |
| `task delete` | `podman rm -f` + cleanup | `podman rm -f` + cleanup | Cleanup only |

### Container Naming

```text
<project-id>-<mode>-<task-id>

Examples:
  myproject-cli-1       # CLI container for task 1
  myproject-auth-codex  # Auth container (ephemeral, uses --rm)
```

### Ephemeral vs Persistent Containers

| Type | Containers | Lifetime | `--rm` flag |
|------|------------|----------|-------------|
| Task | `*-cli-*` | Persistent | No |
| Auth | `*-auth-*` | Ephemeral | Yes |

Task containers persist to allow:
- Fast restart (`podman start` vs full `podman run`)
- Preserved in-container state (apt installs, pip packages, shell history)
- Consistent task = workspace + metadata + container model

Auth containers are ephemeral because:
- One-time authentication flow
- No state to preserve
- Clean up automatically after use

---

## Image Lifecycle

### Build Hierarchy

```text
┌───────────────────────────────────────────────────────────────────┐
│ L0: terok-l0:<base-tag>                                         │
│ ┌───────────────────────────────────────────────────────────────┐ │
│ │ Ubuntu 24.04 + common tools (git, ssh, vim, ripgrep, ...)     │ │
│ │ + dev user + /workspace                                       │ │
│ └───────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────┐
│ L1: terok-l1-cli:<base-tag>   │
│ ┌───────────────────────────┐ │
│ │ + Codex CLI               │ │
│ │ + Claude Code             │ │
│ │ + GitHub Copilot          │ │
│ │ + Mistral Vibe            │ │
│ │ + OpenCode (blablador)    │ │
│ └───────────────────────────┘ │
└───────────────────────────────┘
                │
                ▼
┌───────────────────────────────┐
│ L2: <project>:l2-cli          │
│ ┌───────────────────────────┐ │
│ │ + Project-specific env    │ │
│ │ + CODE_REPO, GIT_BRANCH   │ │
│ │ + SSH_KEY_NAME            │ │
│ │ + User snippet            │ │
│ └───────────────────────────┘ │
└───────────────────────────────┘
```

### Build Commands

| Command | What it builds | When to use |
|---------|---------------|-------------|
| `terokctl build <project>` | L2 only | Normal use (reuses L0/L1) |
| `terokctl build --agents <project>` | L0 + L1 + L2 | Rebuild from L0 with fresh agents |
| `terokctl build --full-rebuild <project>` | L0 + L1 + L2 (no cache) | Rebuild from L0 (no cache) with fresh base image + apt packages |
| `terokctl build --dev <project>` | + L2-dev image | Manual debugging container |

### Image Staleness Detection

The TUI detects when a task's container uses an outdated image:

```text
Container image hash ≠ Current project build hash
        │
        ▼
  "Image: old" warning in TUI
        │
        ▼
  User should: terokctl build <project>
               then: task delete + task run-cli
               or:   task stop + podman rm <container> + task run-cli
```

---

## Quick Reference

### Starting a Task

```bash
# First time (creates container)
terokctl task new myproject        # Create task metadata + workspace
terokctl task run-cli myproject 1  # Create and start container

# Subsequent times (reuses container)
terokctl task run-cli myproject 1  # Starts existing container
```

### Stopping and Restarting

```bash
terokctl task stop myproject 1     # Graceful stop (container persists)
terokctl task restart myproject 1  # Fast restart (podman start)
```

### Checking Status

```bash
terokctl task status myproject 1   # Shows metadata vs actual container state
terokctl task list myproject       # Lists all tasks with status
```

### Cleaning Up

```bash
terokctl task delete myproject 1   # Removes container + workspace + metadata
```

### Manual Container Management

```bash
# These work because containers persist
podman ps -a --filter name=myproject  # List all project containers
podman logs myproject-cli-1           # View container logs
podman exec -it myproject-cli-1 bash  # Enter running container
podman stop myproject-cli-1           # Stop container
podman start myproject-cli-1          # Start stopped container
podman rm myproject-cli-1             # Remove container (keeps workspace)
```
