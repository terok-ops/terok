# Login Feature — Design

## Problem

terok manages containerized AI coding agent tasks. Users need to open interactive
shells inside running containers — to debug, inspect, or interact with the agent
directly. Without the login feature, this would require manually typing
`podman exec -it <name> bash`, remembering container names, and losing sessions on disconnect.

## Requirements

### R1: One-command login

A single command (`terok login <project> <task>`) should open an interactive shell.
No container name needed — terok resolves it from project/task metadata.

### R2: Persistent sessions

Sessions should survive disconnects. Reconnecting should reattach to the same session
with all state (running processes, environment, working directory) preserved.

### R3: TUI integration

The TUI should allow login without leaving the interface. When possible, the login
session should open in a separate window/tab so the TUI remains usable.

### R4: Work across environments

Users run terok in diverse environments. Login should work well in all of them:
terminal (bare), terminal (under tmux), desktop (GNOME, KDE), and browser (web-served TUI).

### R5: Minimize cognitive load for nested tmux

Host-level tmux (for managing TUI windows) and container-level tmux (for session
persistence) use different prefix keys and visual indicators to avoid confusion.

## Architecture

### Container layer: tmux for session persistence (R2)

Every container ships tmux with a custom config (`/etc/tmux.conf`). The login command
is always the same regardless of how the user reaches the container:

    podman exec -it <container> tmux new-session -A -s main

`-A` means "attach if session exists, create if not." This is idempotent — first login
creates, subsequent logins reattach.

### Host layer: environment-aware terminal delivery (R3, R4)

The login command is the same; what varies is how the user gets a terminal to run it.
A dispatch chain selects the best available method:

    ┌──────────────────────────────────────────────────┐
    │ 1. Inside tmux?  → tmux new-window              │
    │ 2. Desktop DE?   → gnome-terminal / konsole      │
    │ 3. Web mode?     → ttyd + open new browser tab   │
    │ 4. Fallback      → suspend TUI, run directly     │
    └──────────────────────────────────────────────────┘

Methods 1-3 keep the TUI visible. Method 4 (suspend) blocks the TUI but works everywhere.

### tmux UX: visual disambiguation (R5)

Two independent tmux servers coexist: one on the host, one in the container. They are
in different PID namespaces and do not interact. The only overlap is the prefix key,
which is resolved by using different defaults:

| Level | Prefix | Status bar | Color |
|---|---|---|---|
| Host | ^b (default) | `HOST tmux (^b)` | Blue |
| Container | ^a (custom) | `CONTAINER tmux (^a)` | Green |

The container status bar cross-references the host prefix (`host: ^b`) so the user
always knows how to switch context. Color provides instant visual identification.

### CLI: top-level command (R1)

`terok login <project> <task>` replaces the current process with
`podman exec -it <container> tmux new-session -A -s main` via `os.execvp()`.
Validation (task exists, has been run, container is running) happens before exec.

### TUI: `l` keybind with dispatch (R3)

The TUI calls `get_login_command()` to get the validated command, then `launch_login()`
to dispatch via the chain above. The return value indicates which method was used,
and the TUI shows a notification or falls back to suspend accordingly.

### Web mode: ttyd for browser-tab terminals (R4)

When the TUI is served via `textual serve`, `suspend()` is silently ignored (no real
terminal to suspend). Instead, ttyd (a lightweight HTTP terminal server using xterm.js)
is started on the host, and `App.open_url()` opens a new browser tab pointing to it.
The user gets a real terminal in the new tab while the TUI remains in the original tab.

### `--tmux` opt-in wrapper (R3, R5)

`terok --tmux` wraps the TUI in a managed tmux session with the host config
(blue status bar, usage hints). Login sessions become additional tmux windows.
This is opt-in — without the flag, the TUI runs directly in the terminal as before.

## Future Directions

- Embedded terminal widget in the TUI (pyte-based, requires significant effort)
- Support for additional terminal emulators beyond gnome-terminal and konsole
- Auto-wrap in tmux by default (pending user feedback on the opt-in flag)
- Toad/ACP integration as alternative agent frontend
