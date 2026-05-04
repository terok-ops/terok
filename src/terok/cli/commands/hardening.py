# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok hardening install|remove`` — orchestrate the optional MAC layer.

Thin orchestrator: caches ``sudo`` credentials once up front, then
delegates to each sibling package's own ``install`` / ``remove``
function (`terok_sandbox.hardening.install`,
`terok_clearance.hardening.install`).  No per-package logic lives
here — that stays with the package that owns the assets.

Run as the calling user.  Each package's install function shells
out to ``sudo`` only for the four operations that genuinely need
root: ``semodule -i``, ``semanage permissive``, ``install`` to
``/etc/apparmor.d/``, and ``apparmor_parser -r``.  Drop-ins under
``~/.config/systemd/user/`` and ``systemctl --user`` restarts run
unprivileged.

Replaces the old "list sudo bash invocations" command (which
required the user to manually run two separate scripts at long
venv-relative paths).  See ``terok sickbay`` for current load status
of every domain / profile.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``hardening`` subcommand tree."""
    p = subparsers.add_parser(
        "hardening",
        help="Install / remove optional MAC hardening (SELinux + AppArmor)",
        description=(
            "Install or remove the optional MAC hardening layer (confined "
            "SELinux domains and AppArmor profiles for terok's host "
            "daemons).  Runs as the calling user; invokes ``sudo`` "
            "internally only for the kernel-policy operations.  See "
            "``terok sickbay`` for current load status."
        ),
    )
    sub = p.add_subparsers(dest="hardening_action", required=True)
    sub.add_parser("install", help="Load modules + write systemd drop-ins")
    sub.add_parser("remove", help="Tear down modules + drop-ins")
    p.set_defaults(_handler=_dispatch)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the ``hardening`` subcommand.  Returns True if handled."""
    if args.cmd != "hardening":
        return False
    _dispatch(args)
    return True


def _dispatch(args: argparse.Namespace) -> None:
    """Route to install/remove based on the subcommand."""
    if args.hardening_action == "install":
        _run_install()
    elif args.hardening_action == "remove":
        _run_remove()
    else:  # pragma: no cover — argparse enforces choices
        sys.exit(f"unknown action: {args.hardening_action}")


def _ensure_sudo_cached() -> None:
    """Prompt for sudo password once so subsequent ``sudo`` calls are silent.

    Each per-package install issues several ``sudo`` calls; without
    pre-caching, each one would prompt independently and the user
    would type their password 10+ times during a single
    ``terok hardening install``.  ``sudo -v`` validates (and refreshes)
    the cached credential or prompts once.
    """
    print("==> sudo credentials")
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        sys.exit("error: sudo authentication failed")


def _run_install() -> None:
    """Delegate to each sibling package's install function in dep order."""
    # Imports inside the function: keeps ``terok hardening --help`` from
    # paying the import cost of every sibling at CLI startup.
    from terok_clearance.hardening import install as clearance_install
    from terok_sandbox.hardening import install as sandbox_install

    _ensure_sudo_cached()
    sandbox_install()
    clearance_install()
    print()
    print("Done.  Verify with: terok sickbay")


def _run_remove() -> None:
    """Tear down both layers in reverse dependency order."""
    from terok_clearance.hardening import remove as clearance_remove
    from terok_sandbox.hardening import remove as sandbox_remove

    _ensure_sudo_cached()
    clearance_remove()
    sandbox_remove()
    print()
    print("Done.  Hardening removed; rootless containers will lose connectto")
    print("access to host sockets unless you reinstall or fall back to TCP mode.")
