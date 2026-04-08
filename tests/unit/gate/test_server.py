# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the standalone gate HTTP server."""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import time
import unittest.mock
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from terok_sandbox.gate.server import (
    _ROUTE,
    TokenStore,
    _extract_basic_auth_token,
    _make_handler_class,
    _parse_cgi_headers,
    _parse_content_length,
    _validate_token_data,
)

from tests.testfs import NONEXISTENT_TOKENS_PATH
from tests.testnet import GATE_PORT, LOCALHOST_PEER

VALID_TOKEN_DATA = {"validtoken": {"scope": "proj-a", "task": "1"}}
SUCCESS_CGI_RESPONSE = b"Status: 200 OK\r\nContent-Type: text/plain\r\n\r\nok"


@contextmanager
def token_store_file(data: object | None = None) -> Iterator[Path]:
    """Create a temporary tokens.json file and yield its path."""
    with tempfile.TemporaryDirectory() as td:
        token_file = Path(td) / "tokens.json"
        if data is not None:
            token_file.write_text(data if isinstance(data, str) else json.dumps(data))
        yield token_file


def make_cgi_process(
    *,
    stdout: bytes = SUCCESS_CGI_RESPONSE,
    stderr: bytes = b"",
    wait_return: int = 0,
) -> unittest.mock.Mock:
    """Create a mocked ``git http-backend`` subprocess."""
    process = unittest.mock.Mock()
    process.stdin = io.BytesIO()
    process.stdout = io.BytesIO(stdout)
    process.stderr = io.BytesIO(stderr)
    process.wait.return_value = wait_return
    return process


def make_request(
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    extra_headers: str = "",
    token_data: dict[str, dict[str, str]] | str | None = None,
) -> tuple[int, BaseHTTPRequestHandler]:
    """Build a fake HTTP request and return ``(status_code, handler)``."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        token_file = base / "tokens.json"
        if isinstance(token_data, str):
            serialized = token_data
        else:
            payload = VALID_TOKEN_DATA if token_data is None else token_data
            serialized = json.dumps(payload)
        token_file.write_text(serialized)
        store = TokenStore(token_file)
        handler_class = _make_handler_class(base, store)

        headers = "Host: localhost\r\n"
        if token is not None:
            creds = base64.b64encode(f"{token}:x".encode()).decode()
            headers += f"Authorization: Basic {creds}\r\n"
        headers += extra_headers

        raw_request = f"{method} {path} HTTP/1.1\r\n{headers}\r\n".encode()
        handler = handler_class.__new__(handler_class)
        handler.request = None
        handler.client_address = LOCALHOST_PEER
        handler.server = type(
            "FakeServer", (), {"server_name": "localhost", "server_port": GATE_PORT}
        )()
        handler.rfile = io.BytesIO(raw_request)
        handler.wfile = io.BytesIO()
        handler.raw_requestline = handler.rfile.readline(65537)
        handler.parse_request()

        responses: list[int] = []
        original_send_response = handler.send_response

        def capture_response(code: int, *args: object) -> None:
            responses.append(code)
            original_send_response(code, *args)

        handler.send_response = capture_response
        handler.send_error = lambda code, *args: responses.append(code)
        handler._handle()
        return (responses[0] if responses else 0), handler


class TestTokenStore:
    """Tests for TokenStore."""

    @pytest.mark.parametrize(
        ("data", "token", "expected"),
        [
            (VALID_TOKEN_DATA, "abc123", None),
            (VALID_TOKEN_DATA, "validtoken", "proj-a"),
            (["a", "b"], "a", None),
            ("not json{{{", "any", None),
        ],
        ids=["wrong-token", "valid-token", "non-dict-json", "corrupt-json"],
    )
    def test_validate_various_inputs(
        self,
        data: object,
        token: str,
        expected: str | None,
    ) -> None:
        with token_store_file(data) as token_file:
            assert TokenStore(token_file).validate(token) == expected

    def test_missing_file_returns_none(self) -> None:
        assert TokenStore(NONEXISTENT_TOKENS_PATH).validate("any") is None

    def test_mtime_reload(self) -> None:
        """Token store reloads when file mtime changes."""
        with token_store_file({"t1": {"scope": "p1", "task": "1"}}) as token_file:
            store = TokenStore(token_file)
            assert store.validate("t1") == "p1"

            time.sleep(0.05)
            token_file.write_text(json.dumps({"t2": {"scope": "p2", "task": "2"}}))
            stat_result = token_file.stat()
            os.utime(token_file, (stat_result.st_atime, stat_result.st_mtime + 1))

            assert store.validate("t1") is None
            assert store.validate("t2") == "p2"

    def test_malformed_token_entry_skipped(self) -> None:
        """Token entries with wrong structure are ignored."""
        with token_store_file(
            {"bad": "not-a-dict", "ok": {"scope": "p", "task": "1"}}
        ) as token_file:
            store = TokenStore(token_file)
            assert store.validate("bad") is None
            assert store.validate("ok") == "p"


class TestValidateTokenData:
    """Tests for _validate_token_data."""

    def test_valid_data(self) -> None:
        data = {"t1": {"scope": "p", "task": "1"}}
        assert _validate_token_data(data) == data

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ([1, 2], {}),
            ("string", {}),
            (
                {"good": {"scope": "p", "task": "1"}, "bad": "string"},
                {"good": {"scope": "p", "task": "1"}},
            ),
            ({"no_task": {"scope": "p"}, "no_scope": {"task": "1"}}, {}),
        ],
        ids=["non-dict-list", "non-dict-string", "skip-non-dict-values", "skip-missing-fields"],
    )
    def test_invalid_token_shapes(self, data: object, expected: dict[str, dict[str, str]]) -> None:
        assert _validate_token_data(data) == expected


class TestExtractBasicAuthToken:
    """Tests for _extract_basic_auth_token."""

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (f"Basic {base64.b64encode(b'mytoken:password').decode()}", "mytoken"),
            (None, None),
            ("Bearer xyz", None),
            ("Basic !!!", None),
            (f"Basic {base64.b64encode(b'nocolon').decode()}", None),
            (f"Basic {base64.b64encode(b':password').decode()}", None),
        ],
        ids=[
            "valid",
            "missing",
            "wrong-scheme",
            "invalid-base64",
            "missing-colon",
            "empty-username",
        ],
    )
    def test_extract_basic_auth_token(self, header: str | None, expected: str | None) -> None:
        assert _extract_basic_auth_token(header) == expected


class TestParseContentLength:
    """Tests for _parse_content_length."""

    @pytest.mark.parametrize(
        ("header", "expected_length", "has_error"),
        [("42", 42, False), (None, 0, False), ("-5", 0, True), ("abc", 0, True)],
        ids=["valid", "missing", "negative", "non-numeric"],
    )
    def test_parse_content_length(
        self,
        header: str | None,
        expected_length: int,
        has_error: bool,
    ) -> None:
        length, error = _parse_content_length(header)
        assert length == expected_length
        assert (error is not None) is has_error


class TestParseCgiHeaders:
    """Tests for _parse_cgi_headers."""

    @pytest.mark.parametrize(
        ("stdout", "expected_status", "expected_headers"),
        [
            (
                b"Status: 404 Not Found\r\nContent-Type: text/plain\r\n\r\nbody",
                404,
                [("Content-Type", "text/plain")],
            ),
            (b"Content-Type: text/html\r\n\r\n", 200, [("Content-Type", "text/html")]),
            (b"\r\n", 200, []),
        ],
        ids=["status-and-header", "default-200", "empty"],
    )
    def test_parse_cgi_headers(
        self,
        stdout: bytes,
        expected_status: int,
        expected_headers: list[tuple[str, str]],
    ) -> None:
        status, headers = _parse_cgi_headers(io.BytesIO(stdout))
        assert status == expected_status
        assert headers == expected_headers


class TestRouting:
    """Tests for the route regex."""

    @pytest.mark.parametrize(
        ("path", "should_match"),
        [
            ("/proj-a.git/info/refs", True),
            ("/proj-a.git/git-upload-pack", True),
            ("/proj-a.git/git-receive-pack", True),
            ("/proj-a.git/HEAD", True),
            ("/proj-a.git/objects/pack/pack-abc.pack", False),
            ("/some/random/path", False),
            ("/", False),
            ("/proj-a/info/refs", False),
        ],
        ids=[
            "info-refs",
            "upload-pack",
            "receive-pack",
            "head",
            "pack-object",
            "random",
            "root",
            "missing-git-suffix",
        ],
    )
    def test_route_matches_expected_paths(self, path: str, should_match: bool) -> None:
        match = _ROUTE.match(path)
        assert (match is not None) is should_match
        if path == "/proj-a.git/info/refs":
            assert match is not None
            assert match.group("repo") == "proj-a.git"
            assert match.group("path") == "/info/refs"


class _FakeSocket:
    """Minimal socket-like object for testing."""

    def __init__(self, request_bytes: bytes) -> None:
        self._input = io.BytesIO(request_bytes)
        self._output = io.BytesIO()

    def makefile(self, mode: str, buffering: int = -1) -> io.BytesIO:
        """Return a file-like object for reading or writing."""
        return self._input if "r" in mode else self._output

    def getpeername(self) -> tuple[str, int]:
        """Return a fake peer address."""
        return LOCALHOST_PEER

    def close(self) -> None:
        """No-op close."""


class TestAuth:
    """Tests for authentication handling."""

    @pytest.mark.parametrize(
        ("path", "token", "expected"),
        [
            ("/proj-a.git/info/refs", None, 401),
            ("/proj-a.git/info/refs", "wrongtoken", 403),
            ("/proj-b.git/info/refs", "validtoken", 403),
            ("/invalid/path", "validtoken", 404),
        ],
        ids=["no-auth", "wrong-token", "wrong-scope", "invalid-path"],
    )
    def test_auth_failures(self, path: str, token: str | None, expected: int) -> None:
        code, _handler = make_request(path, token=token)
        assert code == expected

    @unittest.mock.patch("subprocess.Popen")
    def test_valid_auth_delegates_to_cgi(self, mock_popen: unittest.mock.Mock) -> None:
        """Valid token + matching scope delegates to git http-backend."""
        mock_popen.return_value = make_cgi_process()
        code, _handler = make_request(
            "/proj-a.git/info/refs?service=git-upload-pack",
            token="validtoken",
        )
        assert code == 200
        cgi_env = mock_popen.call_args.kwargs["env"]
        assert cgi_env["GIT_HTTP_EXPORT_ALL"] == "1"
        assert cgi_env["GIT_CONFIG_KEY_0"] == "core.hooksPath"
        assert cgi_env["GIT_CONFIG_VALUE_0"] == "/dev/null"
        assert "GIT_PROJECT_ROOT" in cgi_env

    @unittest.mock.patch("terok_sandbox.gate.server._logger")
    @unittest.mock.patch("subprocess.Popen")
    def test_cgi_stderr_is_logged(
        self,
        mock_popen: unittest.mock.Mock,
        mock_logger: unittest.mock.Mock,
    ) -> None:
        """CGI stderr output is logged via the module logger."""
        mock_popen.return_value = make_cgi_process(stderr=b"warning: something happened")
        code, _handler = make_request(
            "/proj-a.git/info/refs?service=git-upload-pack",
            token="validtoken",
        )
        assert code == 200
        mock_logger.warning.assert_called_once()
        assert "something happened" in mock_logger.warning.call_args[0][1]

    @pytest.mark.parametrize(
        "extra_headers",
        ["Content-Length: notanumber\r\n", "Content-Length: -5\r\n"],
        ids=["invalid-content-length", "negative-content-length"],
    )
    def test_invalid_content_length_returns_400(self, extra_headers: str) -> None:
        code, _handler = make_request(
            "/proj-a.git/git-receive-pack",
            token="validtoken",
            method="POST",
            extra_headers=extra_headers,
        )
        assert code == 400

    @pytest.mark.parametrize(
        ("extra_headers", "expected_env"),
        [
            (
                "Content-Encoding: gzip\r\nContent-Length: 0\r\n",
                {"HTTP_CONTENT_ENCODING": "gzip"},
            ),
            (
                "Git-Protocol: version=2\r\n",
                {"HTTP_GIT_PROTOCOL": "version=2"},
            ),
            ("", {"HTTP_CONTENT_ENCODING": None, "HTTP_GIT_PROTOCOL": None}),
        ],
        ids=["content-encoding", "git-protocol", "headers-absent"],
    )
    @unittest.mock.patch("subprocess.Popen")
    def test_optional_headers_forwarding(
        self,
        mock_popen: unittest.mock.Mock,
        extra_headers: str,
        expected_env: dict[str, str | None],
    ) -> None:
        """Optional request headers are forwarded to the CGI env when present."""
        mock_popen.return_value = make_cgi_process(stdout=b"Status: 200 OK\r\n\r\n")
        code, _handler = make_request(
            "/proj-a.git/info/refs?service=git-upload-pack"
            if "Git-Protocol" in extra_headers or not extra_headers
            else "/proj-a.git/git-upload-pack",
            token="validtoken",
            method="POST" if "Content-Length" in extra_headers else "GET",
            extra_headers=extra_headers,
        )
        assert code == 200
        cgi_env = mock_popen.call_args.kwargs["env"]
        for key, value in expected_env.items():
            if value is None:
                assert key not in cgi_env
            else:
                assert cgi_env[key] == value


class TestDetach:
    """Tests for daemon (detach) mode."""

    def test_child_calls_serve_forever(self) -> None:
        """Child process (fork returns 0) should call serve_forever."""
        from terok_sandbox.gate.server import _serve_daemon

        with tempfile.TemporaryDirectory() as td:
            mock_server = unittest.mock.Mock()
            mock_server.serve_forever.side_effect = SystemExit(0)

            with (
                unittest.mock.patch(
                    "terok_sandbox.gate.server._ThreadingHTTPServer",
                    return_value=mock_server,
                ),
                unittest.mock.patch("terok_sandbox.gate.server.os.fork", return_value=0),
                unittest.mock.patch("terok_sandbox.gate.server.signal.signal") as mock_signal,
                unittest.mock.patch("terok_sandbox.gate.server.os.setsid") as mock_setsid,
                unittest.mock.patch("terok_sandbox.gate.server.os.open", return_value=3),
                unittest.mock.patch("terok_sandbox.gate.server.os.dup2"),
                unittest.mock.patch("terok_sandbox.gate.server.os.close"),
            ):
                store = TokenStore(Path(td) / "tokens.json")
                with pytest.raises(SystemExit):
                    _serve_daemon(Path(td), store, GATE_PORT, None)

            mock_setsid.assert_called_once()
            mock_signal.assert_called_once()
            mock_server.serve_forever.assert_called_once()

    @unittest.mock.patch("terok_sandbox.gate.server._ThreadingHTTPServer")
    @unittest.mock.patch("terok_sandbox.gate.server.os.fork", return_value=42)
    def test_parent_writes_pid_file(
        self,
        _mock_fork: unittest.mock.Mock,
        _mock_server_class: unittest.mock.Mock,
    ) -> None:
        """Parent process (fork returns child PID) should write PID file and exit."""
        from terok_sandbox.gate.server import _serve_daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "gate.pid"
            store = TokenStore(Path(td) / "tokens.json")
            with pytest.raises(SystemExit):
                _serve_daemon(Path(td), store, GATE_PORT, pid_file)
            assert pid_file.read_text() == "42"
