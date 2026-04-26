# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Per-task ACP endpoint commands: ``terok acp list`` / ``terok acp connect``.

The ``acp`` group surfaces the host-side ACP host-proxy: a per-task
Unix socket that aggregates in-container ACP agents (claude, codex, …)
behind a single endpoint, exposing them through ACP's standard model
selector as namespaced ``agent:model`` ids.

``acp list`` is a cheap discovery view — one filesystem check per
running task plus one credential-DB read.  ``acp connect`` exec's
``socat`` (or an in-process pump fallback) at the chosen socket,
spawning the proxy daemon if it is not already up.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from terok_executor import ACPEndpointStatus

from ...lib.core.paths import acp_socket_path
from ...lib.domain.facade import list_projects
from ._completers import add_project_id, add_task_id

if TYPE_CHECKING:
    from ...lib.domain.project import Project


_DAEMON_BIND_TIMEOUT_SEC = 6.0
"""How long ``acp connect`` waits for the daemon to bind the socket
after spawning it.  Daemon startup is hundreds of milliseconds at
most on typical hardware; six seconds is a forgiving ceiling."""


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``acp`` subcommand group with ``list`` / ``connect``."""
    p = subparsers.add_parser(
        "acp",
        help="Per-task ACP (Agent Client Protocol) endpoint management",
    )
    sub = p.add_subparsers(dest="acp_cmd", required=True)

    p_list = sub.add_parser("list", help="List ACP endpoints across running tasks")
    add_project_id(p_list, nargs="?", default=None)

    p_connect = sub.add_parser(
        "connect",
        help="Connect stdio to a task's ACP socket (spawning the daemon if needed)",
    )
    add_project_id(p_connect)
    add_task_id(p_connect)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``acp`` commands; return ``True`` when consumed."""
    if args.cmd != "acp":
        return False
    if args.acp_cmd == "list":
        _cmd_list(getattr(args, "project_id", None))
    elif args.acp_cmd == "connect":
        _cmd_connect(args.project_id, args.task_id)
    return True


# ── list ─────────────────────────────────────────────────────────────────


def _cmd_list(project_id_filter: str | None) -> None:
    """Print one row per ACP endpoint, grouped by project."""
    projects = _projects_to_show(project_id_filter)
    rows: list[tuple[str, str, ACPEndpointStatus, str | None, Path]] = []
    for project in projects:
        for ep in project.acp_endpoints():
            rows.append((ep.project_id, ep.task_id, ep.status, ep.bound_agent, ep.socket_path))

    if not rows:
        print("No ACP endpoints found (no running tasks).")
        return

    # Render: project / task / status / bound-agent / path.  Status drives
    # the colour-free hint at the end of each row so the user knows what
    # they can do.
    width_pid = max(len("PROJECT"), *(len(p) for p, _t, _s, _b, _ in rows))
    width_tid = max(len("TASK"), *(len(t) for _p, t, _s, _b, _ in rows))
    width_sta = max(len("STATUS"), *(len(s.value) for _p, _t, s, _b, _ in rows))
    print(f"{'PROJECT':<{width_pid}}  {'TASK':<{width_tid}}  {'STATUS':<{width_sta}}  AGENT  PATH")
    for pid, tid, status, bound, path in rows:
        bound_disp = bound or "-"
        print(
            f"{pid:<{width_pid}}  {tid:<{width_tid}}  "
            f"{status.value:<{width_sta}}  {bound_disp:<6} {path}"
        )


def _projects_to_show(project_id_filter: str | None) -> list[Project]:
    """Resolve project filter to the list of project objects to walk."""
    from ...lib.domain.facade import get_project

    if project_id_filter:
        return [get_project(project_id_filter)]
    project_infos = list_projects()
    return [get_project(info.id) for info in project_infos]


# ── connect ──────────────────────────────────────────────────────────────


def _cmd_connect(project_id: str, task_id: str) -> None:
    """Bridge the caller's stdio to a task's ACP socket.

    Spawns the proxy daemon if the socket does not yet exist.  Then
    exec's ``socat`` (preferred — transparent, full-duplex) or falls
    back to a tiny in-process pump if socat is missing.  The exec
    pattern matches :func:`task_login`, so the caller's terminal flow
    is preserved.
    """
    sock_path = acp_socket_path(project_id, task_id)
    if not sock_path.exists():
        _spawn_daemon(project_id, task_id)
        _wait_for_socket(sock_path, timeout=_DAEMON_BIND_TIMEOUT_SEC)

    if shutil.which("socat") is not None:
        os.execvp("socat", ["socat", "-", f"UNIX-CONNECT:{sock_path}"])
        return  # pragma: no cover — execvp never returns
    _inprocess_pump(sock_path)


def _spawn_daemon(project_id: str, task_id: str) -> None:
    """Start the proxy daemon detached, so it survives the CLI exit."""
    subprocess.Popen(  # noqa: S603 — argv built from interpreter + module ref
        [sys.executable, "-m", "terok.cli.acp_proxy", project_id, task_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _wait_for_socket(path: Path, *, timeout: float) -> None:
    """Block until *path* appears or *timeout* elapses (then exits)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    print(
        f"terok: ACP daemon did not bind {path} within {timeout:.1f}s",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _inprocess_pump(sock_path: Path) -> None:
    """Bridge stdin/stdout to *sock_path* in-process — socat fallback.

    Used only when socat is unavailable; covers the basic shape needed
    for ``terok acp connect`` to work end-to-end.  Switches to
    non-blocking IO and uses :func:`select` to multiplex stdin and the
    socket; closes everything on either side reaching EOF.
    """
    import select

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(sock_path))
    sock.setblocking(False)
    stdin_fd = sys.stdin.buffer.fileno()
    stdout_fd = sys.stdout.buffer.fileno()
    try:
        while True:
            ready, _, _ = select.select([sock, stdin_fd], [], [])
            if stdin_fd in ready:
                data = os.read(stdin_fd, 4096)
                if not data:
                    sock.shutdown(socket.SHUT_WR)
                else:
                    sock.sendall(data)
            if sock in ready:
                try:
                    data = sock.recv(4096)
                except BlockingIOError:
                    continue
                if not data:
                    return
                os.write(stdout_fd, data)
    finally:
        try:
            sock.close()
        except OSError:
            pass
