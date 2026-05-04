# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terok acp`` command's stdio-bridge helpers.

The full ``_cmd_connect`` path ends in ``os.execv``/socket I/O against
a live daemon, but its three building blocks are pure functions over a
socket fd and OS pipes — easy to drive with a ``socketpair`` and an
``os.pipe``.  Covering them here keeps the in-process pump exercised
by unit tests instead of relying on the manual integration walk-through.
"""

from __future__ import annotations

import os
import socket

import pytest

from terok.cli.commands.acp import (
    _forward_socket_to_stdout,
    _forward_stdin_to_socket,
    _send_all,
)


@pytest.fixture
def sock_pair() -> tuple[socket.socket, socket.socket]:
    """Connected non-blocking ``AF_UNIX`` pair: (caller-side, peer-side)."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    a.setblocking(False)
    b.setblocking(False)
    try:
        yield a, b
    finally:
        a.close()
        b.close()


class TestSendAll:
    """``_send_all`` keeps writing until every byte is in the kernel buffer."""

    def test_writes_payload_in_one_shot(self, sock_pair) -> None:
        """A small payload fits in a single ``send`` and arrives intact."""
        caller, peer = sock_pair
        _send_all(caller, b"hello")
        assert peer.recv(64) == b"hello"

    def test_loops_past_short_send(self, sock_pair) -> None:
        """Short writes are retried until the whole view drains.

        ``socket.socket`` slots forbid ``setattr``, so wrap the caller
        end in a thin proxy that always sends one byte at a time and
        delegates everything else.
        """
        caller, peer = sock_pair

        class OneByteSock:
            def __init__(self, inner: socket.socket) -> None:
                self.inner = inner
                self.calls: list[int] = []

            def send(self, view: memoryview) -> int:
                n = self.inner.send(view[:1])
                self.calls.append(n)
                return n

        proxy = OneByteSock(caller)
        _send_all(proxy, b"abc")  # type: ignore[arg-type]
        assert proxy.calls == [1, 1, 1]
        assert peer.recv(64) == b"abc"


class TestForwardStdinToSocket:
    """``_forward_stdin_to_socket`` returns False on EOF, True on data."""

    def test_data_is_sent_and_keeps_stdin_open(self, sock_pair) -> None:
        """Non-empty stdin returns True and the bytes reach the socket peer."""
        caller, peer = sock_pair
        r, w = os.pipe()
        try:
            os.write(w, b"frame\n")
            still_open = _forward_stdin_to_socket(r, caller)
        finally:
            os.close(r)
            os.close(w)
        assert still_open is True
        assert peer.recv(64) == b"frame\n"

    def test_eof_signals_shut_wr_and_returns_false(self, sock_pair) -> None:
        """An empty read triggers ``SHUT_WR`` and ends the stdin half-loop."""
        caller, peer = sock_pair
        r, w = os.pipe()
        os.close(w)  # immediate EOF on r
        try:
            still_open = _forward_stdin_to_socket(r, caller)
        finally:
            os.close(r)
        assert still_open is False
        # SHUT_WR makes the peer see EOF on its read side.
        assert peer.recv(64) == b""

    def test_eof_tolerates_already_closed_peer(self, sock_pair) -> None:
        """``shutdown`` raising on a half-closed socket is swallowed."""
        caller, peer = sock_pair
        peer.close()
        r, w = os.pipe()
        os.close(w)
        try:
            still_open = _forward_stdin_to_socket(r, caller)
        finally:
            os.close(r)
        assert still_open is False


class TestForwardSocketToStdout:
    """``_forward_socket_to_stdout`` writes recv'd bytes and signals daemon EOF."""

    def test_data_is_written_to_stdout_fd(self, sock_pair) -> None:
        """A frame from the peer is copied to the supplied stdout fd."""
        caller, peer = sock_pair
        peer.send(b"reply\n")
        r, w = os.pipe()
        try:
            keep_going = _forward_socket_to_stdout(caller, w)
            os.close(w)
            assert keep_going is True
            assert os.read(r, 64) == b"reply\n"
        finally:
            os.close(r)

    def test_blocking_io_is_a_no_op(self, sock_pair) -> None:
        """A spurious wakeup with no data returns True without touching stdout."""
        caller, _peer = sock_pair
        r, w = os.pipe()
        try:
            assert _forward_socket_to_stdout(caller, w) is True
        finally:
            os.close(r)
            os.close(w)

    def test_peer_close_returns_false(self, sock_pair) -> None:
        """When the peer closes, ``recv`` yields b'' and the helper signals EOF."""
        caller, peer = sock_pair
        peer.close()
        r, w = os.pipe()
        try:
            keep_going = _forward_socket_to_stdout(caller, w)
        finally:
            os.close(r)
            os.close(w)
        assert keep_going is False
