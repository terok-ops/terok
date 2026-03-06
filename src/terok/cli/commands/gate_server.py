# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Gate server management commands: install, uninstall, start, stop, status.

Manages the ``terok-gate`` HTTP server with per-task token authentication.
"""

from __future__ import annotations

import argparse
import sys

from ...lib.facade import (
    GateServerStatus,
    get_server_status,
    install_systemd_units,
    is_daemon_running,
    is_systemd_available,
    start_daemon,
    stop_daemon,
    uninstall_systemd_units,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``gate-server`` subcommand group."""
    p = subparsers.add_parser("gate-server", help="Manage the git gate server")
    sub = p.add_subparsers(dest="gate_server_cmd", required=True)

    sub.add_parser("install", help="Install and start systemd socket activation")
    sub.add_parser("uninstall", help="Stop and remove systemd units")

    p_start = sub.add_parser("start", help="Start gate server (non-systemd fallback)")
    p_start.add_argument("--port", type=int, default=None, help="Override port")

    sub.add_parser("stop", help="Stop the managed gate server")
    sub.add_parser("status", help="Show gate server status")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle gate-server commands.  Returns True if handled."""
    if args.cmd != "gate-server":
        return False

    cmd = args.gate_server_cmd
    if cmd == "install":
        _cmd_install()
    elif cmd == "uninstall":
        _cmd_uninstall()
    elif cmd == "start":
        _cmd_start(port=getattr(args, "port", None))
    elif cmd == "stop":
        _cmd_stop()
    elif cmd == "status":
        _cmd_status()
    else:
        return False
    return True


def _cmd_install() -> None:
    """Install systemd socket activation units."""
    if not is_systemd_available():
        print(
            "Error: systemd user services are not available on this host.\n"
            "Use 'terokctl gate-server start' to run the gate server without systemd."
        )
        sys.exit(1)
    install_systemd_units()
    print("Systemd gate socket installed and started.")


def _cmd_uninstall() -> None:
    """Uninstall systemd units."""
    if not is_systemd_available():
        print("Error: systemd user services are not available on this host.\nNothing to uninstall.")
        sys.exit(1)
    uninstall_systemd_units()
    print("Systemd gate units removed.")


def _cmd_start(port: int | None) -> None:
    """Start the managed gate server."""
    status = get_server_status()
    if status.running:
        print(f"Gate server is already running ({status.mode}).")
        sys.exit(1)
    start_daemon(port=port)
    print("Gate server started.")


def _cmd_stop() -> None:
    """Stop the managed gate server."""
    status = get_server_status()
    if status.mode == "systemd" and status.running:
        print("Gate server is managed by systemd. Use 'terokctl gate-server uninstall'.")
        return
    if not is_daemon_running():
        print("Gate server is not running.")
        return
    stop_daemon()
    print("Gate server stopped.")


def _cmd_status() -> None:
    """Show gate server status."""
    status: GateServerStatus = get_server_status()
    state = "running" if status.running else "stopped"
    print(f"Mode:   {status.mode}")
    print(f"Status: {state}")
    print(f"Port:   {status.port}")
    if not status.running and status.mode == "none" and is_systemd_available():
        print(
            "\nHint: systemd is available — run 'terokctl gate-server install' to set up socket activation."
        )
