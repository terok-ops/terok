# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok agents`` — list AI coding agents the project can pick from.

Thin wrapper over [`terok_executor.get_roster`][].  Lives in terok
so users discover the catalogue without having to know that
``terok-executor`` is a separately-installable package or call it
directly from the command line.
"""

from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``agents`` subcommand."""
    p = subparsers.add_parser(
        "agents",
        help="List available AI coding agents",
        description=(
            "List the AI coding agents and tools the executor knows about. "
            "Use the printed names with ``image.agents`` in project.yml or "
            "``--agents`` on ``terok task run``."
        ),
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Include non-agent tool entries (e.g. gh, glab, sidecar tools)",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``terok agents``.  Returns True if handled."""
    if args.cmd != "agents":
        return False

    from terok_executor import get_roster

    roster = get_roster()
    names = roster.all_names if getattr(args, "all", False) else roster.agent_names

    if not names:
        print("No agents registered.", file=sys.stderr)
        return True

    rows: list[tuple[str, str]] = []
    for name in sorted(names):
        provider = roster.providers.get(name)
        auth = roster.auth_providers.get(name)
        if provider is not None:
            label = provider.label
        elif auth is not None:
            label = auth.label
        else:
            label = name
        rows.append((name, label))

    w_name = max(len("NAME"), max(len(r[0]) for r in rows))
    print(f"{'NAME':<{w_name}}  LABEL")
    for name, label in rows:
        print(f"{name:<{w_name}}  {label}")
    return True
