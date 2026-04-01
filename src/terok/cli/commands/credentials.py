# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Credential proxy serve command — foreground passthrough for systemd/debug.

The main credential proxy commands (start, stop, status, install, uninstall,
routes) are mounted from terok-agent's ``PROXY_COMMANDS`` via ``wire_group``
in :mod:`terok.cli.main`.  This module only provides the ``credential-proxy-serve``
top-level command, which passes through to the proxy server's own argparse.
"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``credential-proxy-serve`` command."""
    subparsers.add_parser(
        "credential-proxy-serve",
        help="Run credential proxy in foreground (used by systemd)",
        add_help=False,
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle credential-proxy-serve.  Returns True if handled."""
    if args.cmd != "credential-proxy-serve":
        return False
    _cmd_serve(args)
    return True


def _cmd_serve(_args: argparse.Namespace) -> None:
    """Run the credential proxy server in the foreground.

    Delegates to the server's own ``main()`` which handles its own
    argparse.  Used by systemd service units and ``start_daemon()``.
    """
    import sys as _sys

    # Strip the "credential-proxy-serve" prefix so the server's argparse
    # sees only its own flags (--socket-path, --db-path, etc.).
    idx = _sys.argv.index("credential-proxy-serve")
    saved = _sys.argv
    try:
        _sys.argv = ["terok-credential-proxy-serve", *_sys.argv[idx + 1 :]]
        from terok_sandbox.credential_proxy.server import main as _serve

        _serve()
    finally:
        _sys.argv = saved
