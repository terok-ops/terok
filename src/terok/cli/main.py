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
    auth,
    clearance,
    completions,
    dbus,
    image,
    info,
    panic,
    project,
    setup,
    shield,
    sickbay,
    task,
    vault_local,
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
    setup.dispatch,
    auth.dispatch,
    project.dispatch,
    task.dispatch,
    image.dispatch,
    vault_local.dispatch,  # must precede wire_dispatch — handles `vault serve`
    wire_dispatch,
    shield.dispatch,
    dbus.dispatch,
    clearance.dispatch,
    sickbay.dispatch,
    info.dispatch,
    completions.dispatch,
]


def main(prog: str = "terok") -> None:
    """Parse CLI arguments and dispatch to the appropriate command handler.

    ``prog`` selects which surface this invocation presents:

    * ``"terok"`` — human-friendly entry point.  Bare ``terok`` in an
      interactive terminal execs ``terok-tui`` instead of printing the
      usage error.
    * ``"terokctl"`` — scriptable surface.  Always parses arguments, so
      no-args produces the argparse ``required subcommand`` error (stable,
      predictable exit code).  The command tree is identical today; the
      split exists so ``terok`` can evolve richer UX while ``terokctl``
      preserves backwards compatibility.
    """
    # Fast-path: bare ``terok`` in a terminal launches the TUI.  Scripts
    # piping ``terok`` get the argparse usage error instead — the TTY
    # check keeps the convenience shortcut from surprising automation.
    # If ``terok-tui`` isn't on PATH (partial install, exotic layout), fall
    # through to argparse so the user sees a usage error rather than a
    # traceback from ``execlp``.
    if prog == "terok" and len(sys.argv) == 1 and sys.stdin.isatty() and sys.stdout.isatty():
        import os

        try:
            os.execlp("terok-tui", "terok-tui")
            return  # pragma: no cover — execlp never returns on success
        except FileNotFoundError:
            pass

    # Get version info for --version flag
    version, branch = get_version_info()
    version_string = format_version_string(version, branch)

    parser = argparse.ArgumentParser(
        prog=prog,
        description="terok – generate/build images and run per-project task containers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick start:\n"
            f"  1. Bootstrap:  {prog} setup                       (install host services)\n"
            f"  2. Project:    {prog} project wizard              (create a project)\n"
            f"  3. Auth:       {prog} auth claude <project>       (authenticate agents)\n"
            f"  4. Work:       {prog} task run <project_id>       (attach into a new CLI task)\n"
            "\n"
            "Standalone agent (no project):\n"
            f"  {prog} executor run claude .          (headless against cwd)\n"
            f"  {prog} executor run claude . -p 'fix' (with prompt)\n"
            "\n"
            f"Tip: enable tab completion with: {prog} completions install\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{prog} {version_string}\nLicense: Apache-2.0\nCopyright: 2025 Jiri Vyskocil",
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

    # Register subcommands.  Order matters — it's the order they appear in
    # ``--help``.  Emergency and bootstrap first, then the daily-workflow
    # verbs (auth → project → task → login), then operator tools, then
    # sibling-wired groups, then dev/shell niceties.
    panic.register(sub)
    setup.register(sub)
    auth.register(sub)
    project.register(sub)
    task.register(
        sub, prog=prog
    )  # task group + flat ``login`` shortcut; ``prog`` gates terokctl-only verbs
    image.register(sub)
    clearance.register(sub)
    sickbay.register(sub)
    shield.register(sub)
    info.register(sub)

    # Mount sub-package command registries under scoped prefixes.
    # Groups that touch SandboxConfig paths receive config_factory so the
    # wiring layer injects terok's make_sandbox_config() as ``cfg``.
    from terok_executor import AGENT_COMMANDS, VAULT_COMMANDS as AGENT_VAULT_COMMANDS
    from terok_sandbox import GATE_COMMANDS, SSH_COMMANDS

    from ..lib.core.config import make_sandbox_config

    wire_group(sub, "executor", AGENT_COMMANDS, help="Task container executor commands")
    wire_group(
        sub,
        "gate",
        GATE_COMMANDS,
        help="Git gate commands",
        config_factory=make_sandbox_config,
    )
    vault_wiring = wire_group(
        sub,
        "vault",
        AGENT_VAULT_COMMANDS,
        help="Vault commands",
        config_factory=make_sandbox_config,
        return_action=True,
    )
    assert vault_wiring is not None  # return_action=True guarantees a tuple
    _, vault_sub = vault_wiring
    vault_local.register(vault_sub)
    wire_group(
        sub,
        "ssh",
        SSH_COMMANDS,
        help="SSH key management",
        config_factory=make_sandbox_config,
    )

    # Dev / shell niceties at the bottom of the help listing.
    dbus.register(sub)
    completions.register(sub)

    # TUI launcher — only on the human-facing ``terok`` binary.  ``terokctl``
    # is the scripting surface; launching an interactive TUI from there is
    # never useful and just clutters the help listing.
    if prog == "terok":
        sub.add_parser("tui", help="Launch the Textual TUI")

    # Enable bash completion if argcomplete is present and activated
    if argcomplete is not None:  # pragma: no cover - shell integration
        try:
            argcomplete.autocomplete(parser)  # type: ignore[attr-defined]
        except (TypeError, AttributeError):
            pass

    # Fast-path: ``terok tui [args...]`` bypasses argparse and execs terok-tui.
    # This avoids argparse rejecting TUI-specific flags like --tmux.
    # Gated on prog="terok" so ``terokctl tui`` falls through to argparse
    # (which will error — tui is not registered on the scripting surface).
    if prog == "terok" and len(sys.argv) >= 2 and sys.argv[1] == "tui":
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


def terokctl_main() -> None:
    """Entry point for the ``terokctl`` scriptable surface.

    Same command tree as ``terok``, but no-args prints the argparse
    usage error instead of launching the TUI — the stable, predictable
    behavior scripts and automation want.
    """
    main(prog="terokctl")


if __name__ == "__main__":
    main()
