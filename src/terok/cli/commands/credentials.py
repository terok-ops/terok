# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Credential proxy management commands: install, start, stop, status.

Wraps the terok-sandbox proxy lifecycle with route generation from the
agent registry — ``terokctl credentials start`` writes ``routes.json``
before launching the daemon so the proxy is always up-to-date with the
YAML agent definitions.  Systemd socket activation is the recommended
mode on modern Linux hosts.
"""

from __future__ import annotations

import argparse
import sys

from terok_sandbox import (
    CredentialProxyStatus,
    get_proxy_status,
    install_proxy_systemd,
    is_proxy_running,
    is_proxy_systemd_available,
    start_proxy,
    stop_proxy,
    uninstall_proxy_systemd,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``credentials`` subcommand group."""
    p = subparsers.add_parser("credentials", help="Credential proxy commands")
    sub = p.add_subparsers(dest="credentials_cmd", required=True)

    sub.add_parser("install", help="Install and start systemd socket activation")
    sub.add_parser("uninstall", help="Stop and remove systemd units")
    sub.add_parser("start", help="Start credential proxy daemon (non-systemd fallback)")
    sub.add_parser("stop", help="Stop the credential proxy daemon")
    sub.add_parser("status", help="Show credential proxy status")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle credentials commands.  Returns True if handled."""
    if args.cmd != "credentials":
        return False

    cmd = args.credentials_cmd
    if cmd == "install":
        _cmd_install()
    elif cmd == "uninstall":
        _cmd_uninstall()
    elif cmd == "start":
        _cmd_start()
    elif cmd == "stop":
        _cmd_stop()
    elif cmd == "status":
        _cmd_status()
    else:
        return False
    return True


def _ensure_routes() -> None:
    """Generate routes.json from the agent registry."""
    from terok_agent import ensure_proxy_routes

    ensure_proxy_routes()


def _cmd_install() -> None:
    """Install systemd socket activation units."""
    if not is_proxy_systemd_available():
        print(
            "Error: systemd user services are not available on this host.\n"
            "Use 'terokctl credentials start' to run the proxy without systemd."
        )
        sys.exit(1)
    _ensure_routes()
    install_proxy_systemd()
    print("Credential proxy systemd socket installed and started.")


def _cmd_uninstall() -> None:
    """Uninstall systemd units."""
    if not is_proxy_systemd_available():
        print("Error: systemd user services are not available.\nNothing to uninstall.")
        sys.exit(1)
    uninstall_proxy_systemd()
    print("Credential proxy systemd units removed.")


def _cmd_start() -> None:
    """Generate routes and start the credential proxy daemon."""
    if is_proxy_running():
        print("Credential proxy is already running.")
        sys.exit(1)
    _ensure_routes()
    start_proxy()
    print("Credential proxy started.")


def _cmd_stop() -> None:
    """Stop the credential proxy daemon."""
    if not is_proxy_running():
        print("Credential proxy is not running.")
        return
    stop_proxy()
    print("Credential proxy stopped.")


def _cmd_status() -> None:
    """Show credential proxy status."""
    status: CredentialProxyStatus = get_proxy_status()
    state = "running" if status.running else "stopped"
    print(f"Mode:        {status.mode}")
    print(f"Status:      {state}")
    print(f"Socket:      {status.socket_path}")
    print(f"DB:          {status.db_path}")
    print(f"Routes:      {status.routes_path} ({status.routes_configured} configured)")
    if status.credentials_stored:
        print(f"Credentials: {', '.join(status.credentials_stored)}")
    else:
        print("Credentials: none stored")
    if not status.running and status.mode == "none" and is_proxy_systemd_available():
        print("\nHint: run 'terokctl credentials install' to set up systemd socket activation.")
