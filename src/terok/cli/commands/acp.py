# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Per-task ACP endpoint commands: ``terok acp list`` / ``terok acp connect``.

The ``acp`` group is the user-facing surface for the per-task ACP
proxy: each running task gets a Unix socket that aggregates the
container's in-image agents (claude, codex, …) behind ACP's standard
model selector as namespaced ``agent:model`` ids.

``acp list`` is a cheap discovery view — one filesystem check per
running task plus one credential-DB read.  ``acp connect`` exec's
``socat`` (or an in-process pump fallback) at the chosen socket,
spawning the proxy daemon if it is not already up.
"""

from __future__ import annotations

import argparse
import os
import select
import shutil
import socket
import subprocess  # nosec B404 — only used with explicit argv (no shell, no untrusted input)
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ...lib.core.paths import acp_socket_is_live, acp_socket_path
from ...lib.domain.facade import list_projects
from ._completers import add_project_id, add_task_id

if TYPE_CHECKING:
    from terok_executor import ACPEndpointStatus

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
    # ``from __future__ import annotations`` (top of module) makes the
    # ACPEndpointStatus reference below a deferred string — no runtime
    # import needed, so the executor stays out of the cold-start path.
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
    if not acp_socket_is_live(sock_path):
        daemon = _spawn_daemon(project_id, task_id)
        _wait_for_socket(sock_path, timeout=_DAEMON_BIND_TIMEOUT_SEC, daemon=daemon)

    socat = shutil.which("socat")
    if socat is not None:
        # Pass the resolved absolute path so we don't re-walk PATH in
        # ``execv``.  Static analysers also stop flagging "partial path".
        os.execv(socat, [socat, "-", f"UNIX-CONNECT:{sock_path}"])  # nosec B606 — replacing the CLI with socat is the design
        return  # pragma: no cover — execv never returns
    _inprocess_pump(sock_path)


def _spawn_daemon(project_id: str, task_id: str) -> subprocess.Popen:
    """Start the proxy daemon detached, so it survives the CLI exit.

    ``sys.executable`` resolves to the running interpreter's absolute
    path; the remaining argv elements are the module path (a
    constant) and the project / task ids parsed by argparse.  No shell,
    no untrusted input — the bandit S603 / S607 warnings on subprocess
    + partial-path analyses are documented false positives here.

    Returns the ``Popen`` handle so the caller can poll for an early
    exit while waiting on the socket — a daemon that crashes during
    startup should fail fast, not stall the full bind timeout.
    """
    return subprocess.Popen(  # nosec B603 — argv = [interpreter, -m, module, argparse-validated ids]
        [sys.executable, "-m", "terok.cli.acp_proxy", project_id, task_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _wait_for_socket(path: Path, *, timeout: float, daemon: subprocess.Popen | None = None) -> None:
    """Block until *path* is bound by a live daemon or *timeout* elapses.

    A bare existence check would race against a stale ``.sock`` left by
    a previous crash — ``connect`` would think the daemon is up and
    skip the spawn.  Probing accept-readiness instead lets the wait
    loop drive a real handshake.

    When *daemon* is supplied, also poll its exit status: a startup
    crash exits the loop immediately with the daemon's return code,
    rather than stalling the full *timeout* and reporting a misleading
    "did not bind" error.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon is not None and daemon.poll() is not None:
            print(
                f"terok: ACP daemon exited before binding {path} (exit code {daemon.returncode})",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if acp_socket_is_live(path):
            return
        time.sleep(0.05)
    print(
        f"terok: ACP daemon did not bind {path} within {timeout:.1f}s",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _inprocess_pump(sock_path: Path) -> None:
    """Bridge stdin/stdout to *sock_path* in-process — socat fallback.

    Used only when socat is unavailable.  Stdin EOF triggers a
    ``SHUT_WR`` on the socket so the daemon sees the half-close and
    can drain its final reply; daemon EOF returns from the loop and
    the ``finally`` block closes the socket.  Non-blocking IO with
    :func:`select` to multiplex; partial sends/writes are looped until
    drained.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(sock_path))
    sock.setblocking(False)
    stdin_fd = sys.stdin.buffer.fileno()
    stdout_fd = sys.stdout.buffer.fileno()
    stdin_open = True
    try:
        while True:
            # Drop stdin from the watch set after EOF — otherwise
            # ``select`` keeps marking it ready and we'd attempt a
            # second ``shutdown(SHUT_WR)``, dropping the daemon's
            # final reply on the floor.
            read_fds: list[object] = [sock]
            if stdin_open:
                read_fds.append(stdin_fd)
            ready, _, _ = select.select(read_fds, [], [])
            if stdin_open and stdin_fd in ready:
                stdin_open = _forward_stdin_to_socket(stdin_fd, sock)
            if sock in ready and not _forward_socket_to_stdout(sock, stdout_fd):
                return
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _forward_stdin_to_socket(stdin_fd: int, sock: socket.socket) -> bool:
    """Forward one stdin chunk to *sock*; return ``False`` once stdin hits EOF."""
    data = os.read(stdin_fd, 4096)
    if not data:
        # ``shutdown`` may raise if the daemon already closed its end —
        # tolerate that, the SHUT_WR is advisory anyway.
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        return False
    _send_all(sock, data)
    return True


def _send_all(sock: socket.socket, data: bytes) -> None:
    """Drain *data* into the non-blocking *sock*, looping past short sends.

    A non-blocking ``send`` may write fewer bytes than requested under
    backpressure, and ``BlockingIOError`` signals "kernel send buffer
    full" — wait for write-readiness via a single-fd ``select`` and
    retry until the whole frame is committed.
    """
    view = memoryview(data)
    while view:
        try:
            sent = sock.send(view)
            view = view[sent:]
        except BlockingIOError:
            select.select([], [sock], [])


def _forward_socket_to_stdout(sock: socket.socket, stdout_fd: int) -> bool:
    """Forward one socket chunk to *stdout_fd*; return ``False`` on daemon EOF."""
    try:
        data = sock.recv(4096)
    except BlockingIOError:
        return True
    if not data:
        return False
    # ``os.write`` may also write fewer bytes than supplied (rare on
    # regular fds, but possible on pipes/ptys).
    view = memoryview(data)
    while view:
        written = os.write(stdout_fd, view)
        view = view[written:]
    return True
