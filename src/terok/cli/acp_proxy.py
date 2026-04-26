# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Per-task ACP proxy daemon — spawned on first ``terok acp connect``.

Binds a Unix socket on the host that aggregates a task's in-container
ACP agents behind a single endpoint.  Lifetime is tied to the task's
container: the daemon polls ``runtime.container(name).state`` and exits
cleanly once the container is gone.

Invoked as a detached process (``python -m terok.acp_proxy <project>
<task>``); the ``terok acp connect`` CLI handles the spawn and waits
for the socket to appear.  One ACP client per socket — a second
concurrent connection is rejected during ``initialize`` with a JSON-RPC
error from :class:`terok_executor.ACPRoster`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys

from terok_executor import ACPRoster
from terok_sandbox import Sandbox

from ..lib.core.config import make_sandbox_config
from ..lib.core.paths import acp_socket_path
from ..lib.orchestration.tasks import container_name as resolve_container_name, get_task_meta

_logger = logging.getLogger(__name__)

CONTAINER_POLL_INTERVAL_SEC = 2.0
"""How often the daemon checks whether the container is still alive.

Two seconds is fast enough that ``acp list`` reflects shutdown
without lag, slow enough that the polling overhead is invisible.
"""


def main(argv: list[str] | None = None) -> int:
    """Entry point — bind socket, run the proxy until the container exits."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(
            "usage: python -m terok.cli.acp_proxy <project_id> <task_id>",
            file=sys.stderr,
        )
        return 2
    project_id, task_id = args
    logging.basicConfig(level=logging.INFO, format="acp-proxy[%(levelname)s] %(message)s")
    return asyncio.run(_run(project_id, task_id))


async def _run(project_id: str, task_id: str) -> int:
    """Bind, accept, supervise, clean up.  Always exits cleanly."""
    sock_path = acp_socket_path(project_id, task_id)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    # Best-effort cleanup of a stale socket from a previous crashed run.
    try:
        sock_path.unlink()
    except FileNotFoundError:
        pass

    task_meta = get_task_meta(project_id, task_id)
    cname = resolve_container_name(project_id, task_meta.mode, task_id)

    sandbox = Sandbox(config=make_sandbox_config())
    container = sandbox.runtime.container(cname)
    image = container.image
    if image is None:
        _logger.error("task %s container %r has no image — aborting", task_id, cname)
        return 1
    image_id = image.id or image.ref

    roster = ACPRoster(
        task_id=task_id,
        container_name=cname,
        image_id=image_id,
        sandbox=sandbox,
    )

    # Avoid a world-readable socket: umask off group/other before bind.
    old_umask = os.umask(0o077)
    try:
        server = await asyncio.start_unix_server(
            _make_handler(roster),
            path=str(sock_path),
        )
    finally:
        os.umask(old_umask)
    _logger.info("ACP proxy listening at %s for project=%s task=%s", sock_path, project_id, task_id)

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        """Signal the main loop to exit cleanly (SIGTERM/SIGINT handler)."""
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover — non-POSIX
            pass

    supervisor = asyncio.create_task(_watch_container(sandbox, cname, stop_event, task_id=task_id))
    try:
        await stop_event.wait()
    finally:
        supervisor.cancel()
        server.close()
        await server.wait_closed()
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        _logger.info("ACP proxy for %s/%s exited cleanly", project_id, task_id)
    return 0


def _make_handler(roster: ACPRoster):
    """Return an ``asyncio.start_unix_server`` callback bound to *roster*."""
    busy = asyncio.Lock()

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Per-connection handler — runs the proxy attach loop for one client."""
        # v1 = one client per socket.  Reject overlap with a clean close
        # so the second client sees an immediate disconnect instead of
        # racing on the proxy's session state.
        if busy.locked():
            writer.close()
            try:
                await writer.wait_closed()
            except Exception as exc:  # noqa: BLE001
                _logger.debug("proxy: spurious second-client close error: %s", exc)
            return
        async with busy:
            try:
                await roster.attach(reader, writer)
            except Exception:
                # Attach-loop crashes are bugs, not "expected" disconnect
                # paths — surface with traceback at error level so the
                # daemon log makes the cause obvious.
                _logger.exception("proxy: attach loop crashed")
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception as exc:  # noqa: BLE001
                    _logger.debug("proxy: writer close error: %s", exc)

    return _handler


async def _watch_container(
    sandbox: Sandbox,
    container_name: str,
    stop_event: asyncio.Event,
    *,
    task_id: str,
) -> None:
    """Set *stop_event* when the container is no longer running.

    Polls every :data:`CONTAINER_POLL_INTERVAL_SEC` until the state is
    not ``"running"`` (covers both ``exited`` and the no-such-container
    case).  *task_id* is logged for debuggability when a daemon stalls.
    """
    while not stop_event.is_set():
        try:
            state = sandbox.runtime.container(container_name).state
        except Exception as exc:  # noqa: BLE001
            _logger.warning("proxy: container state probe failed: %s", exc)
            stop_event.set()
            return
        if state != "running":
            _logger.info(
                "proxy: container %s state=%r for task %s — shutting down",
                container_name,
                state,
                task_id,
            )
            stop_event.set()
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=CONTAINER_POLL_INTERVAL_SEC)
        except TimeoutError:
            continue


# ── Helpers exposed for tests ─────────────────────────────────────────


def connect_for_test(socket_path) -> socket.socket:
    """Return a connected AF_UNIX client socket.  Used by integration tests."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(socket_path))
    return s


if __name__ == "__main__":  # pragma: no cover — module entry point
    sys.exit(main())
