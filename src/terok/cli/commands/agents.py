# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok agents`` — list AI coding agents the project can pick from.

Thin wrapper over :func:`terok_executor.get_roster`.  Lives in terok
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
    from terok_executor.roster.loader import _load_bundled_agents, _load_user_agents

    roster = get_roster()
    names = roster.all_names if getattr(args, "all", False) else roster.agent_names

    if not names:
        print("No agents registered.", file=sys.stderr)
        return True

    raw = _load_bundled_agents()
    raw.update(_load_user_agents())

    rows: list[tuple[str, str, str]] = []
    for name in sorted(names):
        provider = roster.providers.get(name)
        auth = roster.auth_providers.get(name)
        label = provider.label if provider else (auth.label if auth else name)
        kind = raw.get(name, {}).get("kind", "native")
        rows.append((name, label, kind))

    w_name = max(len("NAME"), max(len(r[0]) for r in rows))
    w_label = max(len("LABEL"), max(len(r[1]) for r in rows))

    print(f"{'NAME':<{w_name}}  {'LABEL':<{w_label}}  TYPE")
    for name, label, kind in rows:
        print(f"{name:<{w_name}}  {label:<{w_label}}  {kind}")
    return True
