# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Helpers for launching interactive login shells from the TUI.

Provides tmux detection, desktop terminal detection, web-mode ttyd spawning,
and an orchestrator that picks the best available method.
"""

import os
import shlex
import shutil
import socket
import subprocess


def is_inside_tmux() -> bool:
    """Return True if the current process is running inside a tmux session."""
    return bool(os.environ.get("TMUX"))


def is_inside_gnome_terminal() -> bool:
    """Return True if the current process is running inside GNOME Terminal.

    Checks multiple methods for detection:
    1. TERM_PROGRAM environment variable
    2. GNOME_TERMINAL_SERVICE environment variable
    3. Parent process name (fallback only if above are not set)
    """
    if os.environ.get("TERM_PROGRAM") == "gnome-terminal":
        return True
    if os.environ.get("GNOME_TERMINAL_SERVICE"):
        return True
    if os.environ.get("TERM_PROGRAM"):
        return False
    return _parent_process_has_name("gnome-terminal")


def is_inside_konsole() -> bool:
    """Return True if the current process is running inside Konsole.

    Checks multiple methods for detection:
    1. TERM_PROGRAM environment variable
    2. Parent process name (fallback only if TERM_PROGRAM is not set)
    """
    if os.environ.get("TERM_PROGRAM") == "konsole":
        return True
    if os.environ.get("TERM_PROGRAM"):
        return False
    return _parent_process_has_name("konsole")


def _parent_process_has_name(name: str) -> bool:
    """Check if any parent process has the given name."""
    try:
        pid = os.getppid()
        result = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            proc_name = result.stdout.strip()
            if proc_name == name:
                return True
        for _ in range(3):
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode != 0:
                return False
            ppid_str = result.stdout.strip()
            if not ppid_str:
                return False
            if ppid_str == "1":
                return False
            try:
                pid = int(ppid_str)
            except ValueError:
                return False
            result = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode != 0:
                return False
            proc_name = result.stdout.strip()
            if proc_name == name:
                return True
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return False


def tmux_new_window(command: list[str], title: str | None = None) -> bool:
    """Open a new tmux window running the given command.

    Returns True if the tmux command succeeded, False otherwise.
    The caller must verify that we are inside tmux before calling this.
    """
    shell_cmd = " ".join(shlex.quote(c) for c in command)
    tmux_cmd: list[str] = ["tmux", "new-window"]
    if title:
        tmux_cmd += ["-n", title]
    tmux_cmd.append(shell_cmd)
    try:
        subprocess.run(tmux_cmd, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def spawn_terminal_with_command(command: list[str], title: str | None = None) -> bool:
    """Spawn a new terminal tab running the given command.

    Only spawns if already running inside a supported terminal emulator.
    Opens a new tab in the existing window.

    Returns True if the terminal was spawned, False if not running inside
    a supported terminal or if the spawn failed.
    """
    shell_cmd = " ".join(shlex.quote(c) for c in command)

    try:
        if is_inside_gnome_terminal():
            args = ["--tab"]
            if title:
                args.extend(["--title", title])
            args.extend(["--", "bash", "-c", shell_cmd])
            subprocess.Popen(
                ["gnome-terminal"] + args,
                start_new_session=True,
            )
            return True
        if is_inside_konsole():
            args = ["--new-tab"]
            if title:
                args.extend(["--title", title])
            args.extend(["-e", "bash", "-c", shell_cmd])
            subprocess.Popen(
                ["konsole"] + args,
                start_new_session=True,
            )
            return True
        return False
    except (FileNotFoundError, OSError):
        return False


def is_web_mode() -> bool:
    """Detect if the app is running under textual-serve (web mode).

    When served via ``textual serve``, the TERM_PROGRAM environment
    variable is typically absent and the textual driver changes.  We
    check for the presence of an env var set by textual-serve.
    """
    # textual-serve sets TEXTUAL_DRIVER when running in web mode
    driver = os.environ.get("TEXTUAL_DRIVER", "")
    return "web" in driver.lower()


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spawn_ttyd(command: list[str], port: int = 0) -> int | None:
    """Start ttyd serving the given command on a local port.

    Binds to the loopback interface only (``-i lo``) so the terminal is not
    exposed beyond localhost.  Returns the port number on success, or None
    if ttyd is not installed.  If port is 0, a free port is selected
    automatically.
    """
    if not shutil.which("ttyd"):
        return None

    if port == 0:
        port = _find_free_port()

    ttyd_cmd = ["ttyd", "-W", "-o", "-i", "lo", "-p", str(port)] + command
    try:
        subprocess.Popen(ttyd_cmd, start_new_session=True)
        return port
    except (FileNotFoundError, OSError):
        return None


def launch_login(
    command: list[str],
    title: str | None = None,
) -> tuple[str, int | None]:
    """Launch a login session using the best available method.

    Returns a tuple of (method, port):
    - ("tmux", None): opened in a new tmux window
    - ("terminal", None): opened in a new desktop terminal window
    - ("web", port): started ttyd on the given port (caller should open_url)
    - ("none", None): no external method available; caller should suspend
    """
    if is_inside_tmux():
        if tmux_new_window(command, title=title):
            return ("tmux", None)

    if not is_web_mode():
        if spawn_terminal_with_command(command, title=title):
            return ("terminal", None)

    if is_web_mode():
        port = spawn_ttyd(command)
        if port is not None:
            return ("web", port)

    return ("none", None)
