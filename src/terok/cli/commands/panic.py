# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Emergency panic command — cut all resource access immediately.

Raises shields on every running container, stops the credential proxy
and gate server.  Optionally stops the containers themselves (which
can be slow on some platforms).  All actions are reversible.

Exit codes:
- 0: all operations succeeded
- 1: one or more operations failed
"""

from __future__ import annotations

import argparse
import sys

from ...lib.domain.panic import (
    PanicResult,
    clear_panic_lock,
    execute_panic,
    format_panic_report,
    is_panicked,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``panic`` subcommand."""
    p = subparsers.add_parser(
        "panic",
        help="Emergency kill-switch: cut all resource access immediately",
    )
    p.add_argument(
        "--stop",
        action="store_true",
        help="Also stop all containers (skip confirmation prompt)",
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help="Clear panic state",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the panic command.  Returns True if handled."""
    if args.cmd != "panic":
        return False

    if getattr(args, "clear", False):
        _cmd_clear()
    else:
        _cmd_panic(stop=getattr(args, "stop", False))
    return True


def _cmd_clear() -> None:
    """Clear the panic lock file."""
    if is_panicked():
        clear_panic_lock()
        print("Panic state cleared.")
        print("Note: shields are still raised and services remain stopped.")
        print("Restart services manually: terok gate start / terok credential-proxy start")
    else:
        print("No panic state to clear.")


def _cmd_panic(*, stop: bool) -> None:
    """Execute the full panic sequence."""
    print("PANIC — cutting all resource access", file=sys.stderr)
    print(file=sys.stderr)

    result = execute_panic(stop_containers=stop)

    print()
    print(format_panic_report(result))

    if not stop and result.total_running > 0:
        print()
        try:
            answer = input("Also stop all containers? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in ("y", "yes"):
            print("Stopping containers...", file=sys.stderr)
            _stop_remaining(result)

    if result.has_errors:
        sys.exit(1)


def _stop_remaining(result: PanicResult) -> None:
    """Stop containers that were left running after Phase 1."""
    from ...lib.domain.panic import panic_stop_containers

    stopped, errors = panic_stop_containers()
    if not stopped and not errors:
        print("No running containers to stop.")
        return
    result.containers_stopped = stopped
    result.container_stop_errors = errors
    print(f"Stopped {len(stopped)} container(s)")
    for cname, err in errors:
        print(f"  FAILED {cname}: {err}")
