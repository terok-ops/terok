#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point and argument parser for terok.

Subcommand registration and dispatch are delegated to focused modules
under ``commands/``.  This file owns only the root parser, version flag,
argcomplete integration, and top-level dispatch loop.
"""

import argparse

from ..lib.core.config import set_experimental
from ..lib.core.version import format_version_string, get_version_info
from .commands import info, project, setup, task

# Optional: bash completion via argcomplete
try:
    import argcomplete  # type: ignore
except ImportError:  # pragma: no cover - optional dep
    argcomplete = None  # type: ignore

# Dispatch chain — tried in order; first True wins.
_DISPATCHERS = [task.dispatch, project.dispatch, setup.dispatch, info.dispatch]


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate command handler."""
    # Get version info for --version flag
    version, branch = get_version_info()
    version_string = format_version_string(version, branch)

    parser = argparse.ArgumentParser(
        prog="terokctl",
        description="terokctl – generate/build images and run per-project task containers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick start:\n"
            "  1. Setup:  terokctl project-init <project_id>\n"
            "  2. Work:   terokctl task start <project_id>         (new CLI task)\n"
            "  3. Login:  terokctl login <project_id> <task_id>\n"
            "\n"
            "Step-by-step (order of operations):\n"
            "  Online (HTTPS): generate → build → gate-sync (optional) → task new → task run-*\n"
            "  Online (SSH):   generate → build → ssh-init → gate-sync (recommended) "
            "→ task new → task run-*\n"
            "  Gatekeeping:    generate → build → ssh-init → gate-sync (required) "
            "→ task new → task run-*\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"terokctl {version_string}\nLicense: Apache-2.0\nCopyright: 2025-2026 Jiri Vyskocil",
    )
    parser.add_argument(
        "--experimental",
        action="store_true",
        default=False,
        help="Enable experimental features (e.g. web tasks)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Register subcommands from each module
    task.register(sub)
    project.register(sub)
    setup.register(sub)
    info.register(sub)

    # Enable bash completion if argcomplete is present and activated
    if argcomplete is not None:  # pragma: no cover - shell integration
        try:
            argcomplete.autocomplete(parser)  # type: ignore[attr-defined]
        except (TypeError, AttributeError):
            pass

    args = parser.parse_args()
    set_experimental(args.experimental)

    for dispatch in _DISPATCHERS:
        if dispatch(args):
            return

    parser.error("Unknown command")


if __name__ == "__main__":
    main()
