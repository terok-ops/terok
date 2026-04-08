#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point and argument parser for terok.

Subcommand registration and dispatch are delegated to focused modules
under ``commands/``.  This file owns only the root parser, version flag,
argcomplete integration, and top-level dispatch loop.
"""

import argparse
import sys

from ..lib.core.config import set_experimental
from ..lib.core.version import format_version_string, get_version_info
from .commands import (
    clearance,
    completions,
    credentials,
    dbus,
    image,
    info,
    panic,
    project,
    setup,
    shield,
    sickbay,
    task,
)
from .wiring import wire_dispatch, wire_group

# Optional: bash completion via argcomplete
try:
    import argcomplete  # type: ignore
except ImportError:  # pragma: no cover - optional dep
    argcomplete = None  # type: ignore

# Dispatch chain — tried in order; first True wins.
# wire_dispatch handles commands mounted via wire_group (agent, gate).
_DISPATCHERS = [
    panic.dispatch,
    task.dispatch,
    project.dispatch,
    credentials.dispatch,
    setup.dispatch,
    image.dispatch,
    wire_dispatch,
    shield.dispatch,
    dbus.dispatch,
    clearance.dispatch,
    sickbay.dispatch,
    info.dispatch,
    completions.dispatch,
]


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate command handler."""
    # Get version info for --version flag
    version, branch = get_version_info()
    version_string = format_version_string(version, branch)

    parser = argparse.ArgumentParser(
        prog="terok",
        description="terok – generate/build images and run per-project task containers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick start:\n"
            "  1. Setup:  terok project-init <project_id>\n"
            "  2. Work:   terok task start <project_id>         (new CLI task)\n"
            "  3. Login:  terok login <project_id> <task_id>\n"
            "\n"
            "Standalone agent (no project):\n"
            "  terok agent run claude .          (headless against cwd)\n"
            "  terok agent run claude . -p 'fix' (with prompt)\n"
            "\n"
            "Step-by-step (order of operations):\n"
            "  Online (HTTPS): generate → build → gate-sync (optional)"
            " → task new → task run-*\n"
            "  Online (SSH):   generate → build → ssh-init"
            " → gate-sync (recommended) → task new → task run-*\n"
            "  Gatekeeping:    generate → build → ssh-init"
            " → gate-sync (required) → task new → task run-*\n"
            "\n"
            "Tip: enable tab completion with: terok completions install\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"terok {version_string}\nLicense: Apache-2.0\nCopyright: 2025 Jiri Vyskocil",
    )
    parser.add_argument(
        "--experimental",
        action="store_true",
        default=False,
        help="Enable experimental features (e.g. web tasks)",
    )
    parser.add_argument(
        "--no-emoji",
        action="store_true",
        default=False,
        help="Replace emojis with text labels (e.g. [gate] instead of \U0001f6aa)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Register subcommands from each module
    panic.register(sub)
    task.register(sub)
    project.register(sub)
    credentials.register(sub)  # credential-proxy-serve (standalone)
    setup.register(sub)
    image.register(sub)
    shield.register(sub)
    dbus.register(sub)
    clearance.register(sub)
    sickbay.register(sub)
    info.register(sub)
    completions.register(sub)

    # Mount sub-package command registries under scoped prefixes.
    # Groups that touch SandboxConfig paths receive config_factory so the
    # wiring layer injects terok's make_sandbox_config() as ``cfg``.
    from terok_agent import AGENT_COMMANDS, PROXY_COMMANDS as AGENT_PROXY_COMMANDS
    from terok_sandbox import GATE_COMMANDS, SSH_COMMANDS

    from ..lib.core.config import make_sandbox_config

    wire_group(sub, "agent", AGENT_COMMANDS, help="Agent container commands")
    wire_group(
        sub,
        "gate",
        GATE_COMMANDS,
        help="Gate server commands",
        config_factory=make_sandbox_config,
    )
    wire_group(
        sub,
        "credential-proxy",
        AGENT_PROXY_COMMANDS,
        help="Credential proxy commands",
        config_factory=make_sandbox_config,
    )
    wire_group(
        sub,
        "ssh",
        SSH_COMMANDS,
        help="SSH key management",
        config_factory=make_sandbox_config,
    )

    # TUI launcher — delegates to terok-tui entry point (dispatched before argparse)
    sub.add_parser("tui", help="Launch the Textual TUI (same as terok-tui)")

    # Enable bash completion if argcomplete is present and activated
    if argcomplete is not None:  # pragma: no cover - shell integration
        try:
            argcomplete.autocomplete(parser)  # type: ignore[attr-defined]
        except (TypeError, AttributeError):
            pass

    # Fast-path: ``terok tui [args...]`` bypasses argparse and execs terok-tui.
    # This avoids argparse rejecting TUI-specific flags like --tmux.
    if len(sys.argv) >= 2 and sys.argv[1] == "tui":
        import os

        os.execlp("terok-tui", "terok-tui", *sys.argv[2:])
        return  # pragma: no cover — execlp never returns

    args = parser.parse_args()
    set_experimental(args.experimental)

    if args.no_emoji:
        from ..lib.util.emoji import set_emoji_enabled

        set_emoji_enabled(False)

    # Post-parse tui handler: covers ``terok --no-emoji tui ...`` where the
    # fast-path (argv[1] == "tui") doesn't fire because root flags come first.
    if getattr(args, "cmd", None) == "tui":
        import os

        os.execlp("terok-tui", "terok-tui", *sys.argv[sys.argv.index("tui") + 1 :])
        return  # pragma: no cover — execlp never returns

    for dispatch in _DISPATCHERS:
        if dispatch(args):
            return

    parser.error("Unknown command")


if __name__ == "__main__":
    main()
