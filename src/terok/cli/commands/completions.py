# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""CLI subcommand for generating and installing shell completions."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from argcomplete import shellcode

_SHELLS = ("bash", "zsh", "fish")

_XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

_INSTALL_TARGETS: dict[str, Path] = {
    "bash": _XDG_DATA_HOME / "bash-completion" / "completions" / "terokctl",
    "zsh": _XDG_DATA_HOME / "zsh" / "site-functions" / "_terokctl",
    "fish": _XDG_CONFIG_HOME / "fish" / "completions" / "terokctl.fish",
}

_BASH_COMPLETION_DIRS = (
    _XDG_DATA_HOME / "bash-completion" / "completions",
    Path("/usr") / "share" / "bash-completion" / "completions",
    Path("/etc") / "bash_completion.d",
)

_ZSH_COMPLETION_DIRS = (_XDG_DATA_HOME / "zsh" / "site-functions",)

_FISH_COMPLETION_DIRS = (_XDG_CONFIG_HOME / "fish" / "completions",)

_SHELL_RC_FILES = (
    Path.home() / ".bashrc",
    Path.home() / ".zshrc",
    _XDG_CONFIG_HOME / "fish" / "config.fish",
)

_RC_MARKERS = (
    "terokctl completions",
    "register-python-argcomplete terokctl",
)


def _detect_shell() -> str:
    """Detect the current shell from ``$SHELL``.

    Raises:
        SystemExit: If the shell cannot be detected or is unsupported.
    """
    name = os.path.basename(os.environ.get("SHELL", ""))
    if name in _SHELLS:
        return name
    raise SystemExit(
        f"Cannot detect shell from $SHELL={os.environ.get('SHELL', '')!r}.\n"
        f"Use --shell to specify one of: {', '.join(_SHELLS)}"
    )


def _install_completions(shell: str | None) -> None:
    """Write the completion script to the auto-load directory for *shell*."""
    if shell is None:
        shell = _detect_shell()
    target = _INSTALL_TARGETS[shell]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        shellcode(["terokctl"], shell=shell, use_defaults=True) + "\n",  # nosec B604
        encoding="utf-8",
    )
    print(f"Installed {shell} completions to {target}")
    print("Restart your shell or open a new terminal to activate.")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``completions`` subcommand."""
    p = subparsers.add_parser(
        "completions",
        help="Generate or install shell completion scripts",
        description=(
            "Generate or install shell completion scripts for terokctl.\n\n"
            "Install locations:\n"
            f"  bash: {_INSTALL_TARGETS['bash']}\n"
            f"  zsh:  {_INSTALL_TARGETS['zsh']}\n"
            f"  fish: {_INSTALL_TARGETS['fish']}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "action",
        choices=(*_SHELLS, "install"),
        metavar="{bash,zsh,fish,install}",
        help="Shell name to print completion script, or 'install' to auto-install",
    )
    p.add_argument(
        "--shell",
        choices=_SHELLS,
        default=None,
        help="Target shell for 'install' (default: auto-detect from $SHELL)",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the completions command.  Returns True if handled."""
    if args.cmd != "completions":
        return False
    if args.action == "install":
        _install_completions(getattr(args, "shell", None))
    else:
        print(shellcode(["terokctl"], shell=args.action, use_defaults=True))  # nosec B604
    return True


def is_completion_installed() -> bool:
    """Check whether terokctl completions are set up (file or rc-file marker)."""
    if any((d / "terokctl").is_file() for d in _BASH_COMPLETION_DIRS):
        return True
    if any((d / "_terokctl").is_file() for d in _ZSH_COMPLETION_DIRS):
        return True
    if any((d / "terokctl.fish").is_file() for d in _FISH_COMPLETION_DIRS):
        return True
    for rc in _SHELL_RC_FILES:
        try:
            content = rc.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(m in content for m in _RC_MARKERS):
            return True
    return False
