# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Health check command (DS9-themed diagnostic bay).

Runs a series of checks and reports their status.  Exit codes:
- 0: all checks passed
- 1: warnings present
- 2: errors present
"""

from __future__ import annotations

import argparse
import sys

from ...lib.facade import get_server_status, is_systemd_available


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``sickbay`` subcommand."""
    subparsers.add_parser("sickbay", help="Run health checks")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the sickbay command.  Returns True if handled."""
    if args.cmd != "sickbay":
        return False
    _cmd_sickbay()
    return True


def _check_gate_server() -> tuple[str, str, str]:
    """Check gate server status.

    Returns (status, label, detail) where status is 'ok', 'warn', or 'error'.
    """
    status = get_server_status()
    label = "Gate server"
    if status.running:
        return ("ok", label, f"{status.mode}, port {status.port}")
    if status.mode == "systemd":
        return ("error", label, "socket installed but not active")
    if is_systemd_available():
        return ("warn", label, "not running — run 'terokctl gate-server install'")
    return ("warn", label, "not running — run 'terokctl gate-server start'")


_CHECKS = [
    _check_gate_server,
]

_STATUS_MARKERS = {
    "ok": "ok",
    "warn": "WARN",
    "error": "ERROR",
}


def _cmd_sickbay() -> None:
    """Run all health checks and report results."""
    worst = "ok"
    for check in _CHECKS:
        status, label, detail = check()
        marker = _STATUS_MARKERS.get(status, status)
        print(f"  {label} .... {marker} ({detail})")
        if status == "error":
            worst = "error"
        elif status == "warn" and worst != "error":
            worst = "warn"

    if worst == "error":
        sys.exit(2)
    elif worst == "warn":
        sys.exit(1)
