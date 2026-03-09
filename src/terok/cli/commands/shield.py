# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield egress firewall management commands.

Manages the terok-shield OCI hook that provides per-container nftables
egress filtering: setup, status, profiles, rules, allow/deny, logs.
"""

from __future__ import annotations

import argparse
import json

from ...lib.security.shield import (
    allow,
    deny,
    get_log_containers,
    get_profiles,
    logs,
    rules,
    setup,
    status,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``shield`` subcommand group."""
    p = subparsers.add_parser("shield", help="Manage egress firewall (terok-shield)")
    sub = p.add_subparsers(dest="shield_cmd", required=True)

    sub.add_parser("setup", help="Install the OCI hook")
    sub.add_parser("status", help="Show shield status")
    sub.add_parser("profiles", help="List available profiles")

    p_rules = sub.add_parser("rules", help="Show nft rules for a container")
    p_rules.add_argument("container", help="Container name")

    p_allow = sub.add_parser("allow", help="Dynamically allow a target")
    p_allow.add_argument("container", help="Container name")
    p_allow.add_argument("target", help="IP, CIDR, or domain to allow")

    p_deny = sub.add_parser("deny", help="Dynamically deny a target")
    p_deny.add_argument("container", help="Container name")
    p_deny.add_argument("target", help="IP, CIDR, or domain to deny")

    p_logs = sub.add_parser("logs", help="Show audit log for a container")
    p_logs.add_argument("container", nargs="?", help="Container name (omit to list containers)")
    p_logs.add_argument("-n", type=int, default=50, help="Number of entries (default 50)")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle shield commands.  Returns True if handled."""
    if args.cmd != "shield":
        return False

    cmd = args.shield_cmd
    if cmd == "setup":
        _cmd_setup()
    elif cmd == "status":
        _cmd_status()
    elif cmd == "profiles":
        _cmd_profiles()
    elif cmd == "rules":
        _cmd_rules(args.container)
    elif cmd == "allow":
        _cmd_allow(args.container, args.target)
    elif cmd == "deny":
        _cmd_deny(args.container, args.target)
    elif cmd == "logs":
        _cmd_logs(args.container, args.n)
    else:
        return False
    return True


def _cmd_setup() -> None:
    """Install the OCI hook."""
    setup()
    print("Shield OCI hook installed.")


def _cmd_status() -> None:
    """Show shield status."""
    st = status()
    for key, value in st.items():
        print(f"{key}: {value}")


def _cmd_profiles() -> None:
    """List available shield profiles."""
    for name in get_profiles():
        print(name)


def _cmd_rules(container: str) -> None:
    """Show nft rules for a container."""
    print(rules(container))


def _cmd_allow(container: str, target: str) -> None:
    """Dynamically allow a target."""
    result = allow(container, target)
    for line in result:
        print(line)


def _cmd_deny(container: str, target: str) -> None:
    """Dynamically deny a target."""
    result = deny(container, target)
    for line in result:
        print(line)


def _cmd_logs(container: str | None, n: int) -> None:
    """Show audit log entries."""
    if container is None:
        containers = get_log_containers()
        if not containers:
            print("No audit logs found.")
            return
        for c in containers:
            print(c)
        return
    for entry in logs(container, n=n):
        print(json.dumps(entry))
