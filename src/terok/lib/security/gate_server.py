# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Gate server lifecycle management.

Replaces the host-writable volume mount of gate repos with a ``git daemon``
bound to ``127.0.0.1``.  Containers reach the gate via ``git://`` URLs
through ``host.containers.internal`` — standard git protocol, no bind-mount
escape vector.

Networking across Podman versions:

- **pasta** (Podman 5+) sets ``host.containers.internal`` to a link-local
  address (``169.254.1.2``) that does *not* reach the host's loopback.
  The task runner injects ``--network pasta:-T,<port>`` (namespace-to-host
  TCP forwarding) and ``--add-host host.containers.internal:127.0.0.1``
  so the ``git://`` URL is forwarded to the host's loopback.
- **slirp4netns** (Podman 4.x) routes the container gateway ``10.0.2.2`` to
  ``127.0.0.1`` when ``allow_host_loopback=true`` is set.  The task runner
  injects ``--network slirp4netns:allow_host_loopback=true`` and
  ``--add-host host.containers.internal:10.0.2.2`` automatically (see
  ``_podman_network_args`` in ``terok.lib.util.podman``).

Phase 2 adds HTTP with per-task token authentication.

**Deployment modes (ordered by preference):**

1. **Systemd socket activation** — zero-idle-cost, crash resilience, and no
   PID management.  Recommended for any Linux host with ``systemctl --user``.

2. **Managed ``git daemon`` process** — best-effort fallback.  Works correctly
   but has a simpler lifecycle (manual start/stop, PID file).

**No auto-start.**  Task creation checks reachability and fails with an
actionable error rather than silently starting a daemon.
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.config import get_global_section, state_root
from ..core.paths import runtime_root

# ---------- Constants ----------

_DEFAULT_PORT = 9418
_UNIT_VERSION = 1
"""Bump when the systemd unit templates change.  ``ensure_server_reachable``
checks the installed version and refuses to start tasks if it is stale."""


# ---------- Config helpers ----------


def _get_port() -> int:
    """Return the configured gate server port (default 9418)."""
    return int(get_global_section("gate_server").get("port", _DEFAULT_PORT))


def _get_gate_base_path() -> Path:
    """Return the base path for ``git daemon`` (where gate repos live)."""
    return state_root() / "gate"


def _pid_file() -> Path:
    """Return the path to the PID file for the managed daemon."""
    return runtime_root() / "gate-server.pid"


def _systemd_unit_dir() -> Path:
    """Return the systemd user unit directory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "systemd" / "user"


# ---------- Systemd helpers ----------


def is_systemd_available() -> bool:
    """Check whether ``systemctl --user`` is usable.

    Uses ``is-system-running`` which returns well-defined exit codes:
    0 = running, 1 = degraded/starting/stopping — both mean systemd is
    present.  Any other code (or missing binary) means unavailable.
    """
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # "running" (0), "degraded" (1), "starting" (1), "stopping" (1)
        # all indicate a usable user session.
        return result.returncode in (0, 1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_socket_installed() -> bool:
    """Check whether the ``terok-gate.socket`` unit file exists."""
    unit_dir = _systemd_unit_dir()
    return (unit_dir / "terok-gate.socket").is_file()


def is_socket_active() -> bool:
    """Check whether the ``terok-gate.socket`` unit is active (listening)."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "terok-gate.socket"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _installed_unit_version() -> int | None:
    """Return the version stamp from the installed socket unit, or ``None``."""
    unit_file = _systemd_unit_dir() / "terok-gate.socket"
    if not unit_file.is_file():
        return None
    try:
        for line in unit_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("# terok-gate-version:"):
                return int(line.split(":", 1)[1].strip())
    except (ValueError, OSError):
        pass
    return None


def install_systemd_units() -> None:
    """Render and install systemd socket+service units, then enable+start the socket."""
    from ..util.template_utils import render_template

    unit_dir = _systemd_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)

    resource_dir = Path(__file__).resolve().parent.parent.parent / "resources" / "systemd"
    variables = {
        "PORT": str(_get_port()),
        "GATE_BASE_PATH": str(_get_gate_base_path()),
        "UNIT_VERSION": str(_UNIT_VERSION),
    }

    for template_name in ("terok-gate.socket", "terok-gate@.service"):
        template_path = resource_dir / template_name
        if not template_path.is_file():
            raise SystemExit(f"Missing systemd template: {template_path}")
        content = render_template(template_path, variables)
        (unit_dir / template_name).write_text(content, encoding="utf-8")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=10)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "terok-gate.socket"],
        check=True,
        timeout=10,
    )


def uninstall_systemd_units() -> None:
    """Disable+stop the socket and remove unit files."""
    unit_dir = _systemd_unit_dir()

    subprocess.run(
        ["systemctl", "--user", "disable", "--now", "terok-gate.socket"],
        check=False,
        timeout=10,
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=10)

    for name in ("terok-gate.socket", "terok-gate@.service"):
        unit_file = unit_dir / name
        if unit_file.is_file():
            unit_file.unlink()

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=10)


# ---------- Daemon fallback ----------


def start_daemon(port: int | None = None) -> None:
    """Start a ``git daemon`` process (non-systemd fallback).

    Writes a PID file to ``runtime_root() / "gate-server.pid"``.
    """
    effective_port = port or _get_port()
    gate_base = _get_gate_base_path()
    gate_base.mkdir(parents=True, exist_ok=True)
    pidfile = _pid_file()
    pidfile.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "git",
            "daemon",
            "--listen=127.0.0.1",
            f"--port={effective_port}",
            f"--base-path={gate_base}",
            "--export-all",
            "--enable=receive-pack",
            "--detach",
            f"--pid-file={pidfile}",
        ],
        check=True,
        timeout=10,
    )


def _is_managed_git_daemon(pid: int) -> bool:
    """Return whether *pid* belongs to the managed git daemon.

    Reads ``/proc/<pid>/cmdline`` and verifies that the process is a
    ``git daemon`` launched with *our* PID file, guarding against both
    PID reuse and unrelated ``git daemon`` processes.
    """
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.is_file():
        return False
    try:
        raw = cmdline_path.read_bytes()
    except OSError:
        return False
    args = raw.rstrip(b"\x00").split(b"\x00")
    if len(args) < 2:
        return False
    args_str = [a.decode("utf-8", errors="ignore") for a in args]
    # argv[0] must be git (possibly a full path like /usr/bin/git)
    if not args_str[0].endswith("git"):
        return False
    if args_str[1] != "daemon":
        return False
    # Verify our PID file is among the arguments
    expected_pid_flag = f"--pid-file={_pid_file()}"
    return expected_pid_flag in args_str


def stop_daemon() -> None:
    """Stop the managed daemon by reading the PID file and sending SIGTERM."""
    pidfile = _pid_file()
    if not pidfile.is_file():
        return
    try:
        pid = int(pidfile.read_text().strip())
        if _is_managed_git_daemon(pid):
            os.kill(pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError):
        pass
    finally:
        if pidfile.is_file():
            pidfile.unlink()


def is_daemon_running() -> bool:
    """Check whether the managed daemon process is alive via its PID file."""
    pidfile = _pid_file()
    if not pidfile.is_file():
        return False
    try:
        pid = int(pidfile.read_text().strip())
        if not _is_managed_git_daemon(pid):
            return False
        os.kill(pid, 0)  # signal 0 = existence check
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


# ---------- Status ----------


@dataclass(frozen=True)
class GateServerStatus:
    """Current state of the gate server."""

    mode: str
    """``"systemd"``, ``"daemon"``, or ``"none"``."""

    running: bool
    """Whether the server is currently reachable."""

    port: int
    """Configured port."""


def get_server_status() -> GateServerStatus:
    """Return the current gate server status."""
    port = _get_port()

    if is_socket_installed():
        if is_socket_active():
            return GateServerStatus(mode="systemd", running=True, port=port)
        # Socket installed but inactive — check if the daemon fallback is running
        if is_daemon_running():
            return GateServerStatus(mode="daemon", running=True, port=port)
        return GateServerStatus(mode="systemd", running=False, port=port)

    if is_daemon_running():
        return GateServerStatus(mode="daemon", running=True, port=port)

    return GateServerStatus(mode="none", running=False, port=port)


def get_gate_base_path() -> Path:
    """Return the gate base path (public API)."""
    return _get_gate_base_path()


def get_gate_server_port() -> int:
    """Return the configured gate server port."""
    return _get_port()


def ensure_server_reachable() -> None:
    """Verify the gate server is running; raise ``SystemExit`` if not.

    Called before task creation to fail early with an actionable message.
    """
    status = get_server_status()
    if status.running:
        if status.mode == "systemd":
            installed = _installed_unit_version()
            if installed is None or installed < _UNIT_VERSION:
                installed_label = "unversioned" if installed is None else f"v{installed}"
                raise SystemExit(
                    "Gate server systemd units are outdated "
                    f"(installed {installed_label}, expected v{_UNIT_VERSION}).\n"
                    "Run 'terokctl gate-server install' to update."
                )
        return

    msg = (
        "Gate server is not running.\n"
        "\n"
        "The gate server serves git repos to task containers over the network,\n"
        "replacing the previous volume-mount approach.\n"
        "\n"
    )
    if is_systemd_available():
        msg += (
            "Recommended: install and start the systemd socket:\n  terokctl gate-server install\n"
        )
    else:
        msg += "Start the gate daemon:\n  terokctl gate-server start\n"
    raise SystemExit(msg)
