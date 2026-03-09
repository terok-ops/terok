# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield firewall management commands: setup, status, rules, allow, deny, logs."""

from __future__ import annotations

import argparse

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

    sub.add_parser("setup", help="Install/verify shield firewall")
    sub.add_parser("status", help="Show shield status")
    sub.add_parser("profiles", help="List available DNS profiles")

    p_rules = sub.add_parser("rules", help="Show nft rules for a container")
    p_rules.add_argument("container", help="Container name")

    p_allow = sub.add_parser("allow", help="Live-allow a domain or IP")
    p_allow.add_argument("container", help="Container name")
    p_allow.add_argument("target", help="Domain name or IPv4 address/CIDR")

    p_deny = sub.add_parser("deny", help="Live-deny a domain or IP")
    p_deny.add_argument("container", help="Container name")
    p_deny.add_argument("target", help="Domain name or IPv4 address/CIDR")

    p_logs = sub.add_parser("logs", help="Show audit log entries")
    p_logs.add_argument(
        "--container", default=None, help="Container name (omit to list containers)"
    )
    p_logs.add_argument("-n", type=int, default=50, help="Number of entries (default: 50)")


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
    """Install or verify the shield firewall."""
    setup()
    print("Shield setup complete.")


def _cmd_status() -> None:
    """Print shield status summary."""
    info = status()
    print(f"Mode:    {info['mode']}")
    print(f"Audit:   {'enabled' if info['audit_enabled'] else 'disabled'}")
    print(f"Profiles: {', '.join(info['profiles']) or '(none)'}")
    log_count = len(info["log_files"])
    print(f"Logs:    {log_count} container{'s' if log_count != 1 else ''}")


def _cmd_profiles() -> None:
    """List available DNS profiles."""
    for name in get_profiles():
        print(f"  {name}")


def _cmd_rules(container: str) -> None:
    """Print nft rules for a container."""
    print(rules(container))


def _cmd_allow(container: str, target: str) -> None:
    """Live-allow a domain or IP for a running container."""
    ips = allow(container, target)
    if ips:
        print(f"Allowed {len(ips)} IP(s) for {target}: {', '.join(ips)}")
    else:
        print(f"No IPs resolved/allowed for {target}")


def _cmd_deny(container: str, target: str) -> None:
    """Live-deny a domain or IP for a running container."""
    ips = deny(container, target)
    if ips:
        print(f"Denied {len(ips)} IP(s) for {target}: {', '.join(ips)}")
    else:
        print(f"No IPs resolved/denied for {target}")


def _cmd_logs(container: str | None, n: int) -> None:
    """Print audit log entries, or list containers with logs."""
    if container is None:
        containers = get_log_containers()
        if not containers:
            print("No audit logs found.")
            return
        print("Containers with audit logs:")
        for c in containers:
            print(f"  {c}")
        return

    import json

    for entry in logs(container, n=n):
        print(json.dumps(entry))
