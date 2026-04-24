# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""TUI-side askpass listener, passphrase modal, and env-injection helper.

Four pieces that together let ``use_personal_ssh: true`` projects
prompt for passphrases through the TUI instead of corrupting the
terminal frame or silently failing:

- :class:`AskpassService` — the asyncio-based unix-socket server.
  Bound lazily from :meth:`start`, torn down by :meth:`stop`; exposes
  the socket path so callers can inject it into subprocess env.
- :class:`AskpassModal` — one-line passphrase prompt as a Textual
  :class:`ModalScreen`.  Dismisses with the passphrase, or ``None`` on
  cancel.
- :func:`gui_askpass_usable` — predicate used by callers to decide
  whether to start the service at all.  When a user's desktop askpass
  is reachable, we'd rather use it than our modal.
- :func:`build_askpass_env` — pure function that injects the helper
  env vars when we *do* want our own askpass to run.

The service is reachable only via a unix socket under terok's runtime
namespace (:func:`terok_sandbox.paths.namespace_runtime_dir`) with
mode ``0600`` — the filesystem is the auth boundary.  No socket is
ever bound until a project with ``use_personal_ssh=true`` actually
spawns a subprocess *and* lacks a usable GUI askpass, so users who
don't opt in pay nothing for this feature.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import socket as _socket
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from terok_sandbox.paths import namespace_runtime_dir
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from . import askpass_protocol as proto

if TYPE_CHECKING:
    from textual.app import App

logger = logging.getLogger(__name__)


# ── env builder ───────────────────────────────────────────────────────


def gui_askpass_usable(env: Mapping[str, str]) -> bool:
    """Return ``True`` when *env* advertises a GUI askpass that can actually render.

    A ``SSH_ASKPASS`` without a reachable display (``DISPLAY`` /
    ``WAYLAND_DISPLAY``) is almost certainly a dud inherited from a
    desktop login env — a container or headless SSH session would
    never see the prompt.  In that case we want our Textual modal to
    take over instead.

    Callers use this to short-circuit before spinning up an
    :class:`AskpassService` whose socket would never be consulted.
    """
    return bool(env.get("SSH_ASKPASS") and (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")))


def build_askpass_env(
    base_env: Mapping[str, str],
    *,
    socket_path: str | os.PathLike[str],
    helper_bin: str | os.PathLike[str],
) -> dict[str, str]:
    """Return a copy of *base_env* with askpass vars injected when appropriate.

    Policy:

    - If *base_env* already has ``SSH_ASKPASS`` set *and* a graphical
      session is reachable (``DISPLAY`` or ``WAYLAND_DISPLAY``), leave
      the user's helper alone — their GUI dialog will pop up and is a
      nicer UX than our Textual modal.
    - Otherwise, point ``SSH_ASKPASS`` at *helper_bin* and set
      ``TEROK_ASKPASS_SOCKET`` so the helper can find us.
    - Always set ``SSH_ASKPASS_REQUIRE=force`` so OpenSSH uses askpass
      even when a tty is attached — the wizard path has
      ``start_new_session=True`` so there's no tty anyway, but
      ``terok-web serve`` and future non-wizard subprocesses may still
      inherit one and we don't want OpenSSH falling back to it.
    """
    env = dict(base_env)
    if not gui_askpass_usable(env):
        env["SSH_ASKPASS"] = str(helper_bin)
        env["TEROK_ASKPASS_SOCKET"] = str(socket_path)
    env["SSH_ASKPASS_REQUIRE"] = "force"
    return env


# ── socket path discovery ─────────────────────────────────────────────


def default_socket_path(*, pid: int | None = None) -> Path:
    """Return a per-process socket path under terok's runtime namespace.

    Delegates to :func:`terok_sandbox.paths.namespace_runtime_dir`
    which owns the XDG resolution order (``$XDG_RUNTIME_DIR/terok/``
    → ``$XDG_STATE_HOME/terok/`` → ``~/.local/state/terok/``) and
    creates the directory with safe permissions.  No ``/tmp``
    fallback means no predictable-temp-path surface.
    """
    return namespace_runtime_dir() / f"askpass-{pid or os.getpid()}.sock"


def locate_helper_bin() -> Path:
    """Find ``terok-askpass`` on ``PATH``, raising if it isn't installed."""
    found = shutil.which("terok-askpass")
    if not found:
        raise RuntimeError(
            "terok-askpass helper not found on PATH — was the package installed "
            "with its [tool.poetry.scripts] entries?"
        )
    return Path(found)


# ── modal ─────────────────────────────────────────────────────────────


class AskpassModal(ModalScreen["str | None"]):
    """Passphrase prompt — dismisses with the string, or ``None`` on cancel.

    The prompt text comes straight from OpenSSH (e.g.
    ``"Enter passphrase for key '/home/.../id_ed25519':"``) and is shown
    verbatim so users recognise which key is being unlocked.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    AskpassModal {
        align: center middle;
    }

    #askpass-dialog {
        width: 70;
        max-width: 100%;
        height: auto;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #askpass-prompt {
        margin-bottom: 1;
        color: $text-muted;
    }

    #askpass-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #askpass-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, prompt: str) -> None:
        """Build the modal with *prompt* as the shown label."""
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        """Lay out the prompt label, password input, and two buttons."""
        dialog = Vertical(id="askpass-dialog")
        dialog.border_title = "SSH passphrase"
        with dialog:
            # markup=False — OpenSSH prompts contain paths like
            # "Enter passphrase for '/home/.../id_ed25519':" and Rich would
            # otherwise interpret any "[...]" inside the path as its markup
            # syntax.  We want to show the prompt verbatim.
            yield Static(self._prompt, id="askpass-prompt", markup=False)
            yield Input(password=True, id="askpass-input")
            with Horizontal(id="askpass-buttons"):
                yield Button("Cancel", id="askpass-cancel", variant="default")
                yield Button("Unlock", id="askpass-ok", variant="primary")

    def on_mount(self) -> None:
        """Focus the input so the user can type immediately."""
        self.query_one("#askpass-input", Input).focus()

    def action_cancel(self) -> None:
        """Dismiss as cancel — helper exits non-zero, ssh aborts."""
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the two buttons — Cancel returns None, Unlock returns the input value."""
        if event.button.id == "askpass-cancel":
            self.dismiss(None)
        elif event.button.id == "askpass-ok":
            self.dismiss(self.query_one("#askpass-input", Input).value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the input field accepts the passphrase — same as clicking Unlock."""
        self.dismiss(event.value)


# ── service ───────────────────────────────────────────────────────────


class AskpassService:
    """Asyncio unix-socket server that dispatches passphrase modals on the TUI.

    One instance per :class:`~textual.app.App`.  Started lazily by the
    wizard / subprocess layer only when a project with
    ``use_personal_ssh=true`` is about to spawn a child — users who
    don't opt in never trigger a socket bind.

    Concurrency: the listener serialises modal pushes with an
    :class:`asyncio.Lock`, so if two helpers connect at once they queue
    up naturally.  This is deliberate — Textual only renders one modal
    at a time anyway.
    """

    def __init__(
        self,
        app: App,
        *,
        socket_path: Path | None = None,
        helper_bin: Path | None = None,
    ) -> None:
        """Construct the service — does not bind the socket or locate the helper.

        Both *socket_path* and *helper_bin* are resolved lazily at
        :meth:`start` / :attr:`helper_bin` access — the service is
        created at TUI mount but often never started, so we shouldn't
        fail the whole TUI if the helper happens to be missing.
        """
        self._app = app
        self._socket_path = socket_path or default_socket_path()
        self._helper_bin = helper_bin
        self._server: asyncio.AbstractServer | None = None
        self._modal_lock = asyncio.Lock()

    @property
    def socket_path(self) -> Path:
        """Filesystem path of the unix socket (bound iff :meth:`start` has run)."""
        return self._socket_path

    @property
    def helper_bin(self) -> Path:
        """Path to the ``terok-askpass`` binary that subprocesses should invoke.

        Resolved on first access — raises if the binary isn't on PATH,
        which lets the caller surface a user-friendly error at the
        moment askpass is actually needed, not at TUI boot.
        """
        if self._helper_bin is None:
            self._helper_bin = locate_helper_bin()
        return self._helper_bin

    @property
    def is_running(self) -> bool:
        """``True`` iff the asyncio server is bound and serving."""
        return self._server is not None

    async def start(self) -> None:
        """Bind the unix socket with mode ``0600`` and start accepting connections.

        Idempotent — calling ``start`` on an already-running service is
        a no-op.  Any stale socket file at the target path is removed
        first; we assume the old owner is gone because the path embeds
        our pid.

        The bind happens synchronously under a local umask so the
        socket inode is created ``0600`` atomically.  We deliberately
        do *not* wrap the whole ``asyncio.start_unix_server`` call in
        ``os.umask(…); await …; os.umask(old)`` because ``os.umask``
        is process-global — any other coroutine that runs during the
        ``await`` would see our tightened umask and create files with
        unexpectedly restrictive permissions.
        """
        if self._server is not None:
            return
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        old_umask = os.umask(0o077)
        try:
            sock.bind(str(self._socket_path))
        except BaseException:
            sock.close()
            raise
        finally:
            os.umask(old_umask)
        sock.listen(16)
        try:
            self._server = await asyncio.start_unix_server(self._handle_client, sock=sock)
        except BaseException:
            sock.close()
            self._socket_path.unlink(missing_ok=True)
            raise
        logger.debug("askpass service listening on %s", self._socket_path)

    async def stop(self) -> None:
        """Close the server and unlink the socket.  Idempotent."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._socket_path.unlink(missing_ok=True)
        logger.debug("askpass service stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Read one request, dispatch a modal, write the reply, close.

        One request per connection.  The helper is synchronous and
        short-lived, so there's no benefit to keeping the socket open
        for a second prompt — simpler to let it reconnect.  The helper
        tears its end down as soon as it reads our reply, so
        ``wait_closed`` can race with that teardown — suppressed below.
        """
        try:
            raw = await reader.readline()
            if not raw:
                return
            try:
                request_id, prompt = proto.parse_request(proto.decode(raw))
            except proto.AskpassProtocolError as exc:
                logger.warning("askpass: bad request: %s", exc)
                return

            async with self._modal_lock:
                # ``push_screen_wait`` requires a running worker context,
                # which makes the service awkward to drive from plain
                # asyncio (tests, non-wizard callers).  The callback form
                # works from any coroutine — bridge it through a future.
                loop = asyncio.get_running_loop()
                future: asyncio.Future[str | None] = loop.create_future()

                def _on_dismissed(result: str | None) -> None:
                    if not future.done():
                        future.set_result(result)

                self._app.push_screen(AskpassModal(prompt), _on_dismissed)
                answer = await future

            reply = (
                proto.make_cancel(request_id)
                if answer is None
                else proto.make_answer(request_id, answer)
            )
            writer.write(proto.encode(reply))
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()
