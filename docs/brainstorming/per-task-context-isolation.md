# Per-Task Context Isolation

## Problem Statement

The shared `~/.claude`, `~/.codex`, and `~/.vibe` directories serve dual purposes:
1. **Authentication** - API credentials that must be shared across all tasks
2. **Context/Sessions** - Conversation history that ideally should be scoped per-task

Currently, all containers mount workspace at `/workspace`, so Claude Code stores all session history in `~/.claude/projects/-workspace/` regardless of which project or task created it. This creates two issues:

- When multiple tasks run concurrently, their contexts are interleaved with no way to determine which task created which session
- When browsing context history, sessions from unrelated projects/tasks add noise

## Requirements

1. **Authentication must remain shared** - Agents need to stay authenticated across all tasks
2. **Contexts should be attributable to tasks** - Either physically isolated or mapped via metadata
3. **Context import within same project** - Allow importing contexts from one task to another (same project only)
4. **Cleanup policy**:
   - For physical isolation: Archive contexts when task is deleted
   - For logical isolation: No deletion needed, rely on filtering

## Design Options

### Option 1: OverlayFS (Not Recommended)

Use Linux OverlayFS to create a layered view:
- Lower layer (read-only): Shared auth from `_claude-config/`
- Upper layer (read-write): Per-task directory for new files
- Merged view: Mounted as `~/.claude` in container

**Why not recommended**: Requires root privileges or fuse-overlayfs setup. Complex to configure reliably with rootless podman.

### Option 2: Per-Task Directories with Copied Auth (Recommended for Physical Isolation)

Create separate config directories per task, with authentication files copied from global location.

#### Architecture

```text
~/.local/share/terok/envs/ (or /var/lib/terok/envs/ if root)
├── _claude-config/                    # Global auth (source of truth)
│   ├── .credentials.json
│   └── settings.json
├── _claude-config-myproject-1/        # Task 1's isolated config
│   ├── .credentials.json              # Copied from global
│   ├── settings.json                  # Copied from global
│   └── projects/-workspace/           # Task 1's sessions only
│       └── abc123.jsonl
├── _claude-config-myproject-2/        # Task 2's isolated config
│   └── ...
└── _claude-archive/                   # Archived contexts from deleted tasks
    └── myproject-1/
        └── projects/-workspace/
            └── abc123.jsonl
```

#### Implementation

**New helper function** in `tasks.py`:
```python
AGENT_AUTH_FILES = {
    "claude": [".credentials.json", "settings.json", "statsig"],
    "codex": ["auth.json", "settings.json"],
    "vibe": [".env"],
    "blablador": ["config.json"],
}

def _setup_task_config_dir(base_name: str, project_id: str, task_id: str, auth_files: list[str]) -> Path:
    """Create per-task config directory with auth files copied from global."""
    envs_base = get_envs_base_dir()
    global_config = envs_base / f"_{base_name}-config"
    task_config = envs_base / f"_{base_name}-config-{project_id}-{task_id}"

    task_config.mkdir(parents=True, exist_ok=True)

    for auth_file in auth_files:
        src = global_config / auth_file
        dst = task_config / auth_file
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst) if src.is_file() else shutil.copytree(src, dst)

    return task_config
```

**Modify volume mounts** in `_build_task_env_and_volumes()`:
```python
# Instead of:
volumes.append(f"{envs_base}/_claude-config:/home/dev/.claude:Z")

# Use:
claude_task_dir = _setup_task_config_dir("claude", project.id, task_id, AGENT_AUTH_FILES["claude"])
volumes.append(f"{claude_task_dir}:/home/dev/.claude:Z")
```

#### CLI Commands

- `terok task contexts <project> <task>` - List sessions created by task
- `terok task import-context <project> <dest-task> --from <source-task>` - Copy sessions
- `terok auth sync [--project <project>]` - Sync auth files after credential update

#### Pros/Cons

| Pros | Cons |
|------|------|
| Perfect isolation, no race conditions | Auth files copied (need sync on update) |
| Works with rootless podman | Slight disk overhead |
| Easy to debug (just ls the directory) | More directories to manage |
| Simple implementation | |

---

### Option 3: Audit/Cgroup Tracking (Not Recommended)

Use Linux audit subsystem (`auditd`) or timestamp correlation to track which files were created during a task's execution window.

**Why not recommended**:
- `auditd` requires root privileges
- Timestamp-based correlation has race conditions with concurrent tasks
- `inotifywait` doesn't work reliably with host-mounted directories in rootless podman due to user namespace isolation

---

### Option 4: In-Container File Monitor (Recommended for Logical Isolation)

Track context file creation from within the container, writing the mapping to a host-mounted location.

#### Architecture

```text
~/.local/share/terok/envs/ (or /var/lib/terok/envs/ if root)
└── _claude-config/                    # Shared (unchanged)
    └── projects/-workspace/           # All sessions live here
        ├── abc123.jsonl               # Created by task 1
        └── def456.jsonl               # Created by task 2

<tasks_root>/myproject/1/workspace/
└── .terok/
    └── task-1-contexts.log            # Maps task 1 to its sessions
        # 2025-01-10T14:30:00 abc123.jsonl

<tasks_root>/myproject/2/workspace/
└── .terok/
    └── task-2-contexts.log            # Maps task 2 to its sessions
        # 2025-01-10T15:45:00 def456.jsonl
```

#### Implementation

**New script** `monitor-context-files.sh` (runs in container):
```bash
#!/bin/bash
WATCH_DIR="/home/dev/.claude/projects/-workspace"
LOG_FILE="/workspace/.terok/task-${TASK_ID}-contexts.log"
POLL_INTERVAL=2

mkdir -p "$(dirname "$LOG_FILE")" "$WATCH_DIR"
BASELINE=$(find "$WATCH_DIR" -maxdepth 1 -name "*.jsonl" -type f 2>/dev/null | sort)

while true; do
    sleep "$POLL_INTERVAL"
    CURRENT=$(find "$WATCH_DIR" -maxdepth 1 -name "*.jsonl" -type f 2>/dev/null | sort)
    NEW=$(comm -13 <(echo "$BASELINE") <(echo "$CURRENT"))

    if [[ -n "$NEW" ]]; then
        while IFS= read -r file; do
            [[ -n "$file" ]] && echo "$(date -Iseconds) $(basename "$file")" >> "$LOG_FILE"
        done <<< "$NEW"
        BASELINE="$CURRENT"
    fi
done
```

**Start monitor in `init-ssh-and-repo.sh`**:
```bash
if [[ -n "${TASK_ID:-}" ]]; then
    nohup monitor-context-files.sh >/dev/null 2>&1 &
fi
```

**Host-side parsing** in `tasks.py`:
```python
def get_task_contexts(project_id: str, task_id: str) -> list[str]:
    """Read context log and return list of session IDs for this task."""
    log_file = project.tasks_root / task_id / "workspace" / ".terok" / f"task-{task_id}-contexts.log"
    if not log_file.exists():
        return []

    sessions = []
    for line in log_file.read_text().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            sessions.append(parts[1].replace(".jsonl", ""))
    return sessions
```

#### CLI Commands

- `terok task contexts <project> <task>` - List sessions mapped to task
- `terok task import-context <project> <dest-task> --from <source-task>` - Add source task's sessions to dest's mapping
- `terok context list [--project <project>] [--task <task>]` - List all contexts with optional filtering

#### Pros/Cons

| Pros | Cons |
|------|------|
| No mount structure changes | Polling-based (2s delay) |
| Shared storage, easy cross-task access | Requires monitor process |
| No deletion needed, just filter | Log file in workspace |
| Simplest container setup | |

---

## Comparison

| Aspect | Option 2: Per-Task Dirs | Option 4: In-Container Monitor |
|--------|------------------------|-------------------------------|
| **Isolation type** | Physical | Logical (mapping) |
| **Race conditions** | None | None (polling) |
| **Privileges required** | None | None |
| **Container setup changes** | Medium | Low |
| **Auth handling** | Copied per-task | Shared globally |
| **Cleanup on delete** | Archive contexts | No cleanup needed |
| **Cross-task recall** | Copy files | Filter by mapping |
| **Complexity** | Low-Medium | Low-Medium |

## Recommendation

- **Option 2** if you want clean physical separation and easy inspection/debugging
- **Option 4** if you want minimal changes and prefer shared storage with logical filtering

Both options work well with rootless podman and have no race conditions.

## Files to Modify

### Option 2
- `/workspace/src/terok/lib/tasks.py` - Add `_setup_task_config_dir()`, modify volumes, add archival
- `/workspace/src/terok/cli/main.py` - Add CLI commands

### Option 4
- `/workspace/src/terok/resources/scripts/monitor-context-files.sh` - New script
- `/workspace/src/terok/resources/scripts/init-ssh-and-repo.sh` - Start monitor
- `/workspace/src/terok/lib/tasks.py` - Add `get_task_contexts()`
- `/workspace/src/terok/cli/main.py` - Add CLI commands
