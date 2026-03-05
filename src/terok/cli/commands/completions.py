# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""CLI subcommand for generating and installing shell completions."""

from __future__ import annotations

import argparse
from pathlib import Path

from argcomplete import shellcode

_SHELLS = ("bash", "zsh", "fish")

_BASH_COMPLETION_DIRS = (
    Path.home() / ".local" / "share" / "bash-completion" / "completions",
    Path("/usr") / "share" / "bash-completion" / "completions",
    Path("/etc") / "bash_completion.d",
)

_SHELL_RC_FILES = (
    Path.home() / ".bashrc",
    Path.home() / ".zshrc",
    Path.home() / ".config" / "fish" / "config.fish",
)

_RC_MARKERS = (
    "terokctl completions",
    "register-python-argcomplete terokctl",
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``completions`` subcommand."""
    p = subparsers.add_parser(
        "completions",
        help="Generate shell completion scripts",
        description="Generate shell completion scripts for terokctl.",
    )
    p.add_argument(
        "shell",
        choices=_SHELLS,
        help="Target shell (bash, zsh, or fish)",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the completions command.  Returns True if handled."""
    if args.cmd != "completions":
        return False
    print(shellcode(["terokctl"], shell=args.shell, use_defaults=True))
    return True


def is_completion_installed() -> bool:
    """Check whether terokctl completions are set up (file or rc-file marker)."""
    if any((d / "terokctl").is_file() for d in _BASH_COMPLETION_DIRS):
        return True
    for rc in _SHELL_RC_FILES:
        try:
            content = rc.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(m in content for m in _RC_MARKERS):
            return True
    return False
