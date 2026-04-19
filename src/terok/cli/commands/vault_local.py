# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Local vault subcommands attached to the sibling-wired ``vault`` group.

``vault serve`` is terok-packaging plumbing (systemd unit + daemon launcher)
rather than a credential-broker management verb, so it lives here instead
of in ``terok_executor.VAULT_COMMANDS``.  Attached via the hybrid extension
pattern in :mod:`terok.cli.main` — argparse routes ``vault serve`` to this
module's :func:`dispatch`, which strips the group prefix and delegates to
the token broker's own ``main()``.
"""

from __future__ import annotations

import argparse

_SENTINEL = "vault_serve"


def register(group_sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Attach ``serve`` under the sibling-wired ``vault`` group.

    Must be called with the ``group_sub`` returned by
    :func:`wire_group` when ``return_action=True``.  Sets both a sentinel
    attribute for local dispatch and ``_wired_cmd=None`` so
    :func:`wire_dispatch` falls through instead of printing group help.
    """
    p_serve = group_sub.add_parser(
        "serve",
        help="Run vault token broker in foreground (used by systemd)",
        add_help=False,
    )
    p_serve.set_defaults(_terok_local_cmd=_SENTINEL, _wired_cmd=None)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the locally-attached ``vault serve``.  Returns True if handled."""
    if getattr(args, "_terok_local_cmd", None) != _SENTINEL:
        return False

    import sys as _sys

    # Strip the ``vault serve`` prefix so the token broker's argparse
    # sees only its own flags (--socket-path, --db-path, etc.).
    vault_idx = _sys.argv.index("vault")
    serve_idx = _sys.argv.index("serve", vault_idx + 1)
    saved = _sys.argv
    try:
        _sys.argv = ["terok-vault-serve", *_sys.argv[serve_idx + 1 :]]
        from terok_sandbox.vault.token_broker import main as _serve

        _serve()
    finally:
        _sys.argv = saved
    return True
