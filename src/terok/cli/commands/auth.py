# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``auth`` top-level command — authenticate an agent/tool.

Lives at the top level (not under ``project``) because upcoming work
will enable project-less authentication — the happy path is ``terok
auth <provider>``, with a project argument as the optional scoping
mechanism rather than the primary axis.
"""

from __future__ import annotations

import argparse

from terok_executor import AUTH_PROVIDERS

from ...lib.core.images import require_agent_installed
from ...lib.core.projects import load_project
from ...lib.domain.facade import authenticate
from ._completers import complete_project_ids as _complete_project_ids, set_completer


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``auth`` top-level command."""
    provider_names = list(AUTH_PROVIDERS)
    providers_help = ", ".join(f"{p.name} ({p.label})" for p in AUTH_PROVIDERS.values())
    p_auth = subparsers.add_parser(
        "auth",
        help="Authenticate an agent/tool for a project",
        description=f"Available providers: {providers_help}",
    )
    p_auth.add_argument("provider", choices=provider_names, metavar="provider")
    set_completer(p_auth.add_argument("project_id"), _complete_project_ids)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``terok auth``.  Returns True if handled."""
    if args.cmd != "auth":
        return False
    require_agent_installed(load_project(args.project_id), args.provider, noun="Provider")
    authenticate(args.project_id, args.provider)
    return True
