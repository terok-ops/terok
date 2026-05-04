# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Top-level hardening orchestrator — chains the per-package tools.

Out-of-band tooling, separate from the daily ``terok`` CLI.  Lives
under ``terok.tools.hardening`` because the optional MAC layer is
a packager-side concern (deb/rpm postinst, ansible, …), not a user-
facing feature.  In dev / pipx deployments the operator runs:

    python -m terok.tools.hardening install
    python -m terok.tools.hardening remove
    python -m terok.tools.hardening status

Delegates to ``terok_sandbox.tools.hardening`` and
``terok_clearance.tools.hardening`` — those modules own the per-
package logic.  This orchestrator caches the sudo credential once
at the top so all subsequent privileged calls (``semodule``,
``semanage``, ``apparmor_parser``) reuse the cached prompt instead
of asking 10+ times.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def _ensure_sudo_cached() -> None:
    """Prompt for sudo password once so per-package calls run silently.

    Each per-package install/remove issues several ``sudo``
    subprocesses.  ``sudo -v`` validates (or refreshes) the cached
    credential up front so the user types their password once total
    instead of per privileged call.
    """
    print("==> sudo credentials")
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        sys.exit("error: sudo authentication failed")


def _run_install() -> None:
    """Delegate install to each sibling package's tool, in dep order."""
    from terok_clearance.tools.hardening import install as clearance_install
    from terok_sandbox.tools.hardening import install as sandbox_install

    _ensure_sudo_cached()
    sandbox_install()
    clearance_install()
    print()
    print("Done.  Verify with: python -m terok.tools.hardening status")


def _run_remove() -> None:
    """Tear down both layers in reverse dep order."""
    from terok_clearance.tools.hardening import remove as clearance_remove
    from terok_sandbox.tools.hardening import remove as sandbox_remove

    _ensure_sudo_cached()
    clearance_remove()
    sandbox_remove()
    print()
    print("Done.  Hardening removed; rootless containers will lose connectto")
    print("access to host sockets unless you reinstall or fall back to TCP mode.")


def _run_status() -> None:
    """Aggregate status from each per-package tool."""
    from terok_clearance.tools.hardening import status as clearance_status
    from terok_sandbox.tools.hardening import status as sandbox_status

    print("== sandbox ==")
    sandbox_status()
    print()
    print("== clearance ==")
    clearance_status()


def main() -> None:
    """Argparse dispatcher for ``python -m terok.tools.hardening``."""
    p = argparse.ArgumentParser(
        prog="python -m terok.tools.hardening",
        description=(
            "Optional MAC hardening orchestrator (chains the sandbox + "
            "clearance per-package tools).  Out-of-band — not a user "
            "command.  In distro-packaged deployments hardening is "
            "installed by the package's postinst hook; this is the "
            "manual fallback for dev / pipx deployments."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="Load modules + write systemd drop-ins (sandbox + clearance)")
    sub.add_parser("remove", help="Tear down modules + drop-ins")
    sub.add_parser("status", help="Aggregate per-package load status")
    args = p.parse_args()
    {"install": _run_install, "remove": _run_remove, "status": _run_status}[args.cmd]()


if __name__ == "__main__":  # pragma: no cover
    main()
