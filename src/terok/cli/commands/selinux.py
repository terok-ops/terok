# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok selinux setup`` — install the SELinux policy module.

The shield + clearance daemons interact with podman's user-namespace
sockets, which require an SELinux policy module on enforcing hosts.
This subcommand surfaces the install path that terok-sandbox already
ships, so the operator does not need to know where the policy script
lives — they just run ``terok selinux setup`` (which calls
``sudo bash <script>``) and then re-run ``terok setup``.

On hosts where SELinux is absent or policy-handling is not needed the
command prints a one-line note and exits cleanly.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 — sudo is the documented install path
import sys


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``selinux`` subcommand group."""
    p = subparsers.add_parser(
        "selinux",
        help="Install SELinux policy module (requires sudo)",
        description=(
            "Install the SELinux policy module that the shield and clearance "
            "services need on enforcing hosts.  Wraps the install script "
            "shipped by terok-sandbox; runs as ``sudo bash <script>`` so the "
            "operator is prompted for their sudo password."
        ),
    )
    selinux_sub = p.add_subparsers(dest="selinux_cmd", required=True)
    selinux_sub.add_parser(
        "setup",
        help="Run the SELinux install script (sudo)",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``terok selinux setup``.  Returns True if handled."""
    if args.cmd != "selinux":
        return False
    if getattr(args, "selinux_cmd", None) != "setup":
        return False

    from terok_sandbox import selinux_install_script

    script = selinux_install_script()
    if not script.exists():
        print(
            f"SELinux install script not found at {script}.  This terok-sandbox "
            "build does not ship a policy module; nothing to do.",
            file=sys.stderr,
        )
        return True

    print(f"Running: sudo bash {script}")
    try:
        subprocess.run(["sudo", "bash", str(script)], check=True)  # nosec B603
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"SELinux install failed (exit {exc.returncode})") from exc
    print("SELinux policy installed.  Re-run `terok setup` to finish.")
    return True
