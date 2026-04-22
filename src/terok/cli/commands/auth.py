# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``auth`` top-level command — authenticate an agent or tool.

Three invocation shapes, in increasing specificity:

- ``terok auth``                         — interactive chained menu.
- ``terok auth <provider>``              — host-wide auth for one provider.
- ``terok auth <provider> --project ID`` — project-scoped escape hatch.

Credentials land in the vault provider-scoped regardless of shape, so
switching between host-wide and project-scoped runs does not duplicate
or overwrite stored tokens.
"""

from __future__ import annotations

import argparse
import sys

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
        help="Authenticate an agent/tool (host-wide by default; --project scopes it)",
        description=(
            f"Available providers: {providers_help}\n\n"
            "Without arguments, opens an interactive menu to authenticate one "
            "or more providers in sequence.  ``terok auth <provider>`` "
            "authenticates host-wide — credentials are shared across every "
            "project that uses the same agent.  Pass ``--project <id>`` to "
            "scope the auth container to a specific project's image."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_auth.add_argument(
        "provider",
        nargs="?",
        default=None,
        choices=provider_names,
        metavar="provider",
    )
    set_completer(
        p_auth.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="(Legacy positional — prefer --project.)",
        ),
        _complete_project_ids,
    )
    set_completer(
        p_auth.add_argument(
            "--project",
            dest="project_flag",
            default=None,
            help="Scope auth to a specific project's image (vault stays provider-scoped)",
        ),
        _complete_project_ids,
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``terok auth``.  Returns True if handled."""
    if args.cmd != "auth":
        return False
    # --project wins over the legacy positional if both happen to be given.
    project_id = args.project_flag or args.project_id
    if args.provider is None:
        _run_interactive(project_id)
    else:
        _run_one(args.provider, project_id)
    return True


# ── Implementation helpers ────────────────────────────────────────────


def _run_one(provider: str, project_id: str | None) -> None:
    """Authenticate a single provider, optionally scoped to a project."""
    if project_id is not None:
        # Project-scoped: verify the L2 image actually has the agent baked
        # in before launching.  Host-wide auth resolves the image in the
        # facade and does its own checks there.
        require_agent_installed(load_project(project_id), provider, noun="Provider")
    authenticate(provider, project_id)


def _run_interactive(project_id: str | None) -> None:
    """Interactively pick one or more providers and authenticate each in turn."""
    provider_names = list(AUTH_PROVIDERS)
    print("Authenticate agents — pick one or more (comma-separated):")
    for i, name in enumerate(provider_names, 1):
        info = AUTH_PROVIDERS[name]
        modes = []
        if info.supports_oauth:
            modes.append("oauth")
        if info.supports_api_key:
            modes.append("api-key")
        print(f"  {i:>2}. {name:<12} {info.label}  [{', '.join(modes)}]")

    try:
        answer = input("\nChoice (numbers or names, comma-separated; empty = cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not answer:
        return

    selected = _parse_provider_selection(answer, provider_names)
    if not selected:
        print("Nothing selected.", file=sys.stderr)
        return

    for provider in selected:
        print(f"\n── {provider} ─────────────────────")
        _run_one(provider, project_id)


def _parse_provider_selection(raw: str, provider_names: list[str]) -> list[str]:
    """Parse a comma-separated pick-list into a de-duped ordered provider list.

    Accepts either numeric indices (1-based, matching the displayed menu) or
    provider names.  Unknown tokens are reported on stderr and skipped —
    partial success is preferable to aborting the whole menu interaction.
    """
    selected: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        resolved: str | None = None
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(provider_names):
                resolved = provider_names[idx]
        elif token in provider_names:
            resolved = token
        if resolved is None:
            print(f"  Skipped unknown provider: {token!r}", file=sys.stderr)
            continue
        if resolved not in seen:
            selected.append(resolved)
            seen.add(resolved)
    return selected
