# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for :mod:`terok.tui.askpass_service`.

Split into three groups:

- :class:`TestBuildEnv` — pure-function checks on the env builder
  (respect pre-existing GUI askpass, inject ours otherwise, always set
  ``SSH_ASKPASS_REQUIRE=force``).
- :class:`TestServiceLifecycle` — :class:`AskpassService` start / stop
  / idempotency and socket permissions.
- :class:`TestServiceRoundTrip` — end-to-end helper → service → modal
  round trip driven through ``App.run_test()``.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.app import App

from terok.tui import askpass_protocol as proto
from terok.tui.askpass_service import (
    AskpassModal,
    AskpassService,
    build_askpass_env,
    default_socket_path,
    gui_askpass_usable,
)

# ── build_askpass_env ─────────────────────────────────────────────────


class TestBuildEnv:
    """Env-injection policy must respect existing GUI askpass when usable."""

    def test_injects_helper_when_no_existing_askpass(self) -> None:
        """With no ``SSH_ASKPASS``, our helper + socket are set."""
        env = build_askpass_env(
            {"HOME": "/home/u"},
            socket_path="/run/x.sock",
            helper_bin="/usr/bin/terok-askpass",
        )
        assert env["SSH_ASKPASS"] == "/usr/bin/terok-askpass"
        assert env["TEROK_ASKPASS_SOCKET"] == "/run/x.sock"
        assert env["SSH_ASKPASS_REQUIRE"] == "force"

    def test_respects_existing_askpass_when_display_is_set(self) -> None:
        """User's seahorse / gnome-keyring wins when a GUI is reachable."""
        env = build_askpass_env(
            {"SSH_ASKPASS": "/usr/libexec/seahorse-askpass", "DISPLAY": ":0"},
            socket_path="/run/x.sock",
            helper_bin="/usr/bin/terok-askpass",
        )
        assert env["SSH_ASKPASS"] == "/usr/libexec/seahorse-askpass"
        assert "TEROK_ASKPASS_SOCKET" not in env
        # REQUIRE=force still applies so the GUI dialog is used even with a tty.
        assert env["SSH_ASKPASS_REQUIRE"] == "force"

    def test_respects_existing_askpass_under_wayland(self) -> None:
        """``WAYLAND_DISPLAY`` counts as a GUI the same way ``DISPLAY`` does."""
        env = build_askpass_env(
            {"SSH_ASKPASS": "/usr/bin/custom-askpass", "WAYLAND_DISPLAY": "wayland-0"},
            socket_path="/run/x.sock",
            helper_bin="/usr/bin/terok-askpass",
        )
        assert env["SSH_ASKPASS"] == "/usr/bin/custom-askpass"
        assert "TEROK_ASKPASS_SOCKET" not in env

    def test_takes_over_when_existing_askpass_has_no_gui(self) -> None:
        """``SSH_ASKPASS`` set but no ``DISPLAY``/``WAYLAND_DISPLAY`` — ours wins.

        This is the headless-ssh-session / container scenario.  A GUI
        askpass inherited from the login env can't actually render, so
        we step in with the Textual modal instead of silently failing.
        """
        env = build_askpass_env(
            {"SSH_ASKPASS": "/usr/libexec/seahorse-askpass"},
            socket_path="/run/x.sock",
            helper_bin="/usr/bin/terok-askpass",
        )
        assert env["SSH_ASKPASS"] == "/usr/bin/terok-askpass"
        assert env["TEROK_ASKPASS_SOCKET"] == "/run/x.sock"

    def test_base_env_is_not_mutated(self) -> None:
        """The function returns a copy — callers' dicts stay pristine."""
        base = {"HOME": "/home/u"}
        build_askpass_env(base, socket_path="/s", helper_bin="/h")
        assert base == {"HOME": "/home/u"}

    def test_require_force_always_set(self) -> None:
        """Even when respecting an existing askpass, REQUIRE=force applies.

        Without this, OpenSSH prefers ``/dev/tty`` over any askpass when
        a controlling tty is present — undoing the TUI-isolation work
        from PR #821.
        """
        assert (
            build_askpass_env({}, socket_path="/s", helper_bin="/h")["SSH_ASKPASS_REQUIRE"]
            == "force"
        )


# ── default_socket_path ───────────────────────────────────────────────


class TestSocketPath:
    """Per-process socket path under terok's namespace runtime dir.

    We delegate to :func:`terok_sandbox.paths.namespace_runtime_dir`
    for the XDG resolution order — patching it out lets us pin the
    path regardless of the host's env and ensures no ``/tmp`` fallback
    leaks back in.
    """

    def test_uses_namespace_runtime_dir(self, tmp_path: Path) -> None:
        """Socket sits inside whatever ``namespace_runtime_dir`` returns — always.

        This is the anti-regression for bandit B108: the resolver is
        the single source of truth for where ephemeral terok state
        lives, and it has no ``/tmp`` leaf in its fallback chain
        (``$XDG_RUNTIME_DIR/terok`` → ``$XDG_STATE_HOME/terok`` →
        ``~/.local/state/terok``).  Delegating here means any future
        change to that chain lands transparently.
        """
        with patch("terok.tui.askpass_service.namespace_runtime_dir", return_value=tmp_path):
            path = default_socket_path(pid=12345)
        assert path == tmp_path / "askpass-12345.sock"

    def test_does_not_read_xdg_runtime_dir_directly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``default_socket_path`` goes through the resolver, not the raw env var.

        Proves we're not reimplementing the XDG chain locally.
        """
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/unused-should-be-ignored")
        with patch("terok.tui.askpass_service.namespace_runtime_dir", return_value=tmp_path):
            assert default_socket_path(pid=1).parent == tmp_path


# ── gui_askpass_usable ────────────────────────────────────────────────


class TestGuiAskpassUsable:
    """Predicate that decides whether to bother spinning up our socket."""

    def test_needs_both_askpass_and_gui(self) -> None:
        """A GUI helper without a display can't actually render — skip it."""
        assert not gui_askpass_usable({"SSH_ASKPASS": "/usr/libexec/seahorse-askpass"})
        assert not gui_askpass_usable({"DISPLAY": ":0"})
        assert not gui_askpass_usable({})

    def test_x11_session_is_usable(self) -> None:
        """``DISPLAY`` + ``SSH_ASKPASS`` is the classic desktop path."""
        assert gui_askpass_usable({"SSH_ASKPASS": "/usr/libexec/seahorse-askpass", "DISPLAY": ":0"})

    def test_wayland_session_is_usable(self) -> None:
        """Wayland's ``WAYLAND_DISPLAY`` is accepted alongside X's ``DISPLAY``."""
        assert gui_askpass_usable(
            {"SSH_ASKPASS": "/usr/libexec/seahorse-askpass", "WAYLAND_DISPLAY": "wayland-0"}
        )


# ── AskpassService lifecycle ──────────────────────────────────────────


class _BareApp(App):
    """Minimal ``App`` that doesn't push any screen — good enough to host a service."""


class TestServiceLifecycle:
    """Service start/stop, idempotency, and socket permissions."""

    @pytest.mark.asyncio
    async def test_start_binds_socket_and_stop_unlinks_it(self, tmp_path: Path) -> None:
        """After ``start`` the socket file exists; after ``stop`` it's gone."""
        sock = tmp_path / "askpass.sock"
        async with _BareApp().run_test():
            service = AskpassService(_BareApp(), socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                assert sock.exists()
                assert stat.S_ISSOCK(sock.stat().st_mode)
            finally:
                await service.stop()
            assert not sock.exists()

    @pytest.mark.asyncio
    async def test_socket_mode_is_0600(self, tmp_path: Path) -> None:
        """Socket permissions must not grant access beyond the owner."""
        sock = tmp_path / "askpass.sock"
        async with _BareApp().run_test():
            service = AskpassService(_BareApp(), socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                mode = sock.stat().st_mode & 0o777
                # Owner has rw; group and other must be empty.
                assert mode & 0o077 == 0
            finally:
                await service.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, tmp_path: Path) -> None:
        """Two calls to ``start`` don't rebind or error."""
        sock = tmp_path / "askpass.sock"
        async with _BareApp().run_test():
            service = AskpassService(_BareApp(), socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                assert service.is_running
                await service.start()  # second call is a no-op
                assert service.is_running
            finally:
                await service.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        """``stop`` on a never-started / already-stopped service is harmless."""
        sock = tmp_path / "askpass.sock"
        service = AskpassService(_BareApp(), socket_path=sock, helper_bin=tmp_path / "bin")
        await service.stop()  # never started — must not raise
        async with _BareApp().run_test():
            await service.start()
            await service.stop()
            await service.stop()  # second stop is a no-op


# ── end-to-end service round-trip ─────────────────────────────────────


class _ModalAnsweringApp(App):
    """App that auto-dismisses any :class:`AskpassModal` that gets pushed.

    ``canned_answer`` is the value it dismisses with: a string means
    "user typed this passphrase"; ``None`` means "user clicked Cancel".
    """

    def __init__(self, canned_answer: str | None) -> None:
        super().__init__()
        self._canned_answer = canned_answer

    async def on_screen_resume(self) -> None:  # pragma: no cover — edge path
        pass

    def on_mount(self) -> None:
        self.install_screen = None  # placeholder — real work in client code

    async def _auto_answer(self) -> None:
        """Wait for an :class:`AskpassModal` and dismiss it after a short pause."""
        # Busy-wait briefly until the modal lands on top of the stack.
        for _ in range(50):
            if isinstance(self.screen, AskpassModal):
                self.screen.dismiss(self._canned_answer)
                return
            await asyncio.sleep(0.02)


class TestServiceRoundTrip:
    """End-to-end: a unix-socket client drives the modal through the service."""

    @pytest.mark.asyncio
    async def test_answer_reaches_client(self, tmp_path: Path) -> None:
        """Client sends a request, modal is auto-answered, reply carries the passphrase."""
        sock = tmp_path / "askpass.sock"
        app = _ModalAnsweringApp(canned_answer="letmein")
        async with app.run_test() as pilot:
            service = AskpassService(app, socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                auto_task = asyncio.create_task(app._auto_answer())

                reader, writer = await asyncio.open_unix_connection(str(sock))
                req = proto.make_request("Enter passphrase:", request_id="rid-1")
                writer.write(proto.encode(req))
                await writer.drain()
                await pilot.pause()  # give the service + modal a chance to run

                raw = await reader.readline()
                reply = proto.decode(raw)
                assert reply == {"request_id": "rid-1", "answer": "letmein"}

                writer.close()
                await writer.wait_closed()
                await auto_task
            finally:
                await service.stop()

    @pytest.mark.asyncio
    async def test_cancel_is_relayed_to_client(self, tmp_path: Path) -> None:
        """User hits Cancel → reply carries ``cancel: true``."""
        sock = tmp_path / "askpass.sock"
        app = _ModalAnsweringApp(canned_answer=None)
        async with app.run_test() as pilot:
            service = AskpassService(app, socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                auto_task = asyncio.create_task(app._auto_answer())

                reader, writer = await asyncio.open_unix_connection(str(sock))
                req = proto.make_request("Enter passphrase:", request_id="rid-2")
                writer.write(proto.encode(req))
                await writer.drain()
                await pilot.pause()

                raw = await reader.readline()
                reply = proto.decode(raw)
                assert reply == {"request_id": "rid-2", "cancel": True}

                writer.close()
                await writer.wait_closed()
                await auto_task
            finally:
                await service.stop()


# ── helper exit-code contract ─────────────────────────────────────────


class TestHelperMain:
    """Cover the :func:`terok.tui.askpass.main` code paths via direct calls.

    These use a tiny blocking-socket stand-in for the service so we don't
    have to drag asyncio in — the protocol layer is identical.
    """

    @pytest.mark.asyncio
    async def test_helper_exits_zero_and_prints_answer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Accept path: helper prints the passphrase and exits 0."""
        import socket as _socket

        from terok.tui import askpass as helper

        sock_path = tmp_path / "helper.sock"
        server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)

        async def _fake_tui() -> None:
            conn, _ = await asyncio.get_running_loop().sock_accept(server)
            loop = asyncio.get_running_loop()
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = await loop.sock_recv(conn, 4096)
                if not chunk:
                    break
                raw += chunk
            request_id, _ = proto.parse_request(proto.decode(raw))
            await loop.sock_sendall(conn, proto.encode(proto.make_answer(request_id, "secret")))
            conn.close()

        try:
            tui_task = asyncio.create_task(_fake_tui())
            monkeypatch.setenv("TEROK_ASKPASS_SOCKET", str(sock_path))

            # Run the blocking helper off the event loop so the fake TUI can service it.
            rc = await asyncio.to_thread(helper.main, ["terok-askpass", "Enter passphrase:"])
            await tui_task
        finally:
            server.close()

        assert rc == 0
        assert capsys.readouterr().out.strip() == "secret"

    @pytest.mark.asyncio
    async def test_helper_exits_nonzero_on_cancel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancel path: helper exits non-zero, ssh aborts immediately."""
        import socket as _socket

        from terok.tui import askpass as helper

        sock_path = tmp_path / "helper.sock"
        server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)

        async def _fake_tui() -> None:
            conn, _ = await asyncio.get_running_loop().sock_accept(server)
            loop = asyncio.get_running_loop()
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = await loop.sock_recv(conn, 4096)
                if not chunk:
                    break
                raw += chunk
            request_id, _ = proto.parse_request(proto.decode(raw))
            await loop.sock_sendall(conn, proto.encode(proto.make_cancel(request_id)))
            conn.close()

        try:
            tui_task = asyncio.create_task(_fake_tui())
            monkeypatch.setenv("TEROK_ASKPASS_SOCKET", str(sock_path))
            rc = await asyncio.to_thread(helper.main, ["terok-askpass", "prompt"])
            await tui_task
        finally:
            server.close()

        assert rc != 0

    def test_helper_exits_nonzero_when_socket_env_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without ``TEROK_ASKPASS_SOCKET`` the helper can't do anything — bail."""
        from terok.tui import askpass as helper

        monkeypatch.delenv("TEROK_ASKPASS_SOCKET", raising=False)
        assert helper.main(["terok-askpass", "prompt"]) != 0

    def test_helper_exits_nonzero_when_socket_unreachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pointed at a non-existent path, the helper errors cleanly."""
        from terok.tui import askpass as helper

        monkeypatch.setenv("TEROK_ASKPASS_SOCKET", str(tmp_path / "never-bound.sock"))
        assert helper.main(["terok-askpass", "prompt"]) != 0


# silence pytest about the unused fixture scaffold
_ = os
