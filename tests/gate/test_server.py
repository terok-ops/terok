# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the standalone gate HTTP server."""

import base64
import io
import json
import tempfile
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from terok.gate.server import (
    _ROUTE,
    TokenStore,
    _extract_basic_auth_token,
    _make_handler_class,
    _parse_cgi_headers,
    _parse_content_length,
    _validate_token_data,
)


class TestTokenStore(unittest.TestCase):
    """Tests for TokenStore."""

    def test_validate_valid_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text(json.dumps({"abc123": {"project": "proj-a", "task": "1"}}))
            store = TokenStore(tf)
            self.assertEqual(store.validate("abc123"), "proj-a")

    def test_validate_invalid_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text(json.dumps({"abc123": {"project": "proj-a", "task": "1"}}))
            store = TokenStore(tf)
            self.assertIsNone(store.validate("wrong"))

    def test_missing_file_returns_none(self) -> None:
        store = TokenStore(Path("/nonexistent/tokens.json"))
        self.assertIsNone(store.validate("any"))

    def test_corrupt_json_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text("not json{{{")
            store = TokenStore(tf)
            self.assertIsNone(store.validate("any"))

    def test_mtime_reload(self) -> None:
        """Token store reloads when file mtime changes."""
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text(json.dumps({"t1": {"project": "p1", "task": "1"}}))
            store = TokenStore(tf)
            self.assertEqual(store.validate("t1"), "p1")

            # Overwrite with new token (force different mtime)
            import os
            import time

            time.sleep(0.05)
            tf.write_text(json.dumps({"t2": {"project": "p2", "task": "2"}}))
            # Force mtime change
            st = tf.stat()
            os.utime(tf, (st.st_atime, st.st_mtime + 1))

            self.assertIsNone(store.validate("t1"))
            self.assertEqual(store.validate("t2"), "p2")

    def test_malformed_token_entry_skipped(self) -> None:
        """Token entries with wrong structure are ignored."""
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text(json.dumps({"bad": "not-a-dict", "ok": {"project": "p", "task": "1"}}))
            store = TokenStore(tf)
            self.assertIsNone(store.validate("bad"))
            self.assertEqual(store.validate("ok"), "p")

    def test_non_dict_json_returns_none(self) -> None:
        """Non-dict top-level JSON is treated as empty."""
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text(json.dumps(["a", "b"]))
            store = TokenStore(tf)
            self.assertIsNone(store.validate("a"))


class TestValidateTokenData(unittest.TestCase):
    """Tests for _validate_token_data."""

    def test_valid_data(self) -> None:
        data = {"t1": {"project": "p", "task": "1"}}
        self.assertEqual(_validate_token_data(data), data)

    def test_non_dict_returns_empty(self) -> None:
        self.assertEqual(_validate_token_data([1, 2]), {})
        self.assertEqual(_validate_token_data("string"), {})

    def test_skips_non_dict_values(self) -> None:
        data = {"good": {"project": "p", "task": "1"}, "bad": "string"}
        result = _validate_token_data(data)
        self.assertEqual(len(result), 1)
        self.assertIn("good", result)

    def test_skips_missing_fields(self) -> None:
        data = {"no_task": {"project": "p"}, "no_proj": {"task": "1"}}
        self.assertEqual(_validate_token_data(data), {})


class TestExtractBasicAuthToken(unittest.TestCase):
    """Tests for _extract_basic_auth_token."""

    def test_valid_basic_auth(self) -> None:
        creds = base64.b64encode(b"mytoken:password").decode()
        self.assertEqual(_extract_basic_auth_token(f"Basic {creds}"), "mytoken")

    def test_none_header(self) -> None:
        self.assertIsNone(_extract_basic_auth_token(None))

    def test_non_basic_scheme(self) -> None:
        self.assertIsNone(_extract_basic_auth_token("Bearer xyz"))

    def test_invalid_base64(self) -> None:
        self.assertIsNone(_extract_basic_auth_token("Basic !!!"))

    def test_no_colon(self) -> None:
        creds = base64.b64encode(b"nocolon").decode()
        self.assertIsNone(_extract_basic_auth_token(f"Basic {creds}"))

    def test_empty_username(self) -> None:
        creds = base64.b64encode(b":password").decode()
        self.assertIsNone(_extract_basic_auth_token(f"Basic {creds}"))


class TestParseContentLength(unittest.TestCase):
    """Tests for _parse_content_length."""

    def test_valid_length(self) -> None:
        length, err = _parse_content_length("42")
        self.assertEqual(length, 42)
        self.assertIsNone(err)

    def test_none_header(self) -> None:
        length, err = _parse_content_length(None)
        self.assertEqual(length, 0)
        self.assertIsNone(err)

    def test_negative(self) -> None:
        _, err = _parse_content_length("-5")
        self.assertIsNotNone(err)

    def test_non_numeric(self) -> None:
        _, err = _parse_content_length("abc")
        self.assertIsNotNone(err)


class TestParseCgiHeaders(unittest.TestCase):
    """Tests for _parse_cgi_headers."""

    def test_parses_status_and_headers(self) -> None:
        stdout = io.BytesIO(b"Status: 404 Not Found\r\nContent-Type: text/plain\r\n\r\nbody")
        status, headers = _parse_cgi_headers(stdout)
        self.assertEqual(status, 404)
        self.assertEqual(headers, [("Content-Type", "text/plain")])

    def test_defaults_to_200(self) -> None:
        stdout = io.BytesIO(b"Content-Type: text/html\r\n\r\n")
        status, _ = _parse_cgi_headers(stdout)
        self.assertEqual(status, 200)

    def test_empty_response(self) -> None:
        stdout = io.BytesIO(b"\r\n")
        status, headers = _parse_cgi_headers(stdout)
        self.assertEqual(status, 200)
        self.assertEqual(headers, [])


class TestRouting(unittest.TestCase):
    """Tests for the route regex."""

    def test_info_refs(self) -> None:
        m = _ROUTE.match("/proj-a.git/info/refs")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("repo"), "proj-a.git")
        self.assertEqual(m.group("path"), "/info/refs")

    def test_upload_pack(self) -> None:
        m = _ROUTE.match("/proj-a.git/git-upload-pack")
        self.assertIsNotNone(m)

    def test_receive_pack(self) -> None:
        m = _ROUTE.match("/proj-a.git/git-receive-pack")
        self.assertIsNotNone(m)

    def test_head(self) -> None:
        m = _ROUTE.match("/proj-a.git/HEAD")
        self.assertIsNotNone(m)

    def test_invalid_path_returns_none(self) -> None:
        self.assertIsNone(_ROUTE.match("/proj-a.git/objects/pack/pack-abc.pack"))
        self.assertIsNone(_ROUTE.match("/some/random/path"))
        self.assertIsNone(_ROUTE.match("/"))

    def test_repo_without_git_suffix_fails(self) -> None:
        self.assertIsNone(_ROUTE.match("/proj-a/info/refs"))


class _FakeSocket:
    """Minimal socket-like object for testing."""

    def __init__(self, request_bytes: bytes) -> None:
        self._input = io.BytesIO(request_bytes)
        self._output = io.BytesIO()

    def makefile(self, mode: str, buffering: int = -1) -> io.BytesIO:
        """Return a file-like object for reading or writing."""
        if "r" in mode:
            return self._input
        return self._output

    def getpeername(self) -> tuple[str, int]:
        """Return a fake peer address."""
        return ("127.0.0.1", 12345)

    def close(self) -> None:
        """No-op close."""


class TestAuth(unittest.TestCase):
    """Tests for authentication handling."""

    def _make_request(
        self,
        path: str,
        token: str | None = None,
        method: str = "GET",
        extra_headers: str = "",
    ) -> tuple[int, BaseHTTPRequestHandler]:
        """Build a fake HTTP request and return (status_code, handler)."""
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "tokens.json"
            tf.write_text(json.dumps({"validtoken": {"project": "proj-a", "task": "1"}}))
            store = TokenStore(tf)
            handler_class = _make_handler_class(Path(td), store)

            headers = "Host: localhost\r\n"
            if token is not None:
                creds = base64.b64encode(f"{token}:x".encode()).decode()
                headers += f"Authorization: Basic {creds}\r\n"
            headers += extra_headers

            raw_request = f"{method} {path} HTTP/1.1\r\n{headers}\r\n".encode()

            # Create a mock handler to capture the response
            handler = handler_class.__new__(handler_class)
            handler.request = None
            handler.client_address = ("127.0.0.1", 12345)
            handler.server = type(
                "FakeServer", (), {"server_name": "localhost", "server_port": 9418}
            )()
            handler.rfile = io.BytesIO(raw_request)
            handler.wfile = io.BytesIO()
            handler.raw_requestline = handler.rfile.readline(65537)
            handler.parse_request()

            # Capture send_response calls
            responses = []
            original_send_response = handler.send_response

            def capture_response(code, *args):
                responses.append(code)
                original_send_response(code, *args)

            handler.send_response = capture_response
            handler.send_error = lambda code, *args: responses.append(code)

            handler._handle()
            return responses[0] if responses else 0, handler

    def test_no_auth_returns_401(self) -> None:
        code, _ = self._make_request("/proj-a.git/info/refs", token=None)
        self.assertEqual(code, 401)

    def test_wrong_token_returns_403(self) -> None:
        code, _ = self._make_request("/proj-a.git/info/refs", token="wrongtoken")
        self.assertEqual(code, 403)

    def test_wrong_project_returns_403(self) -> None:
        code, _ = self._make_request("/proj-b.git/info/refs", token="validtoken")
        self.assertEqual(code, 403)

    def test_invalid_path_returns_404(self) -> None:
        code, _ = self._make_request("/invalid/path", token="validtoken")
        self.assertEqual(code, 404)

    @unittest.mock.patch("subprocess.Popen")
    def test_valid_auth_delegates_to_cgi(self, mock_popen: unittest.mock.Mock) -> None:
        """Valid token + matching project delegates to git http-backend."""
        # Mock the subprocess to avoid needing real git
        mock_proc = unittest.mock.Mock()
        mock_proc.stdin = io.BytesIO()
        mock_proc.stdout = io.BytesIO(b"Status: 200 OK\r\nContent-Type: text/plain\r\n\r\nok")
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        code, _ = self._make_request(
            "/proj-a.git/info/refs?service=git-upload-pack", token="validtoken"
        )
        self.assertEqual(code, 200)
        mock_popen.assert_called_once()
        # Verify CGI env includes GIT_PROJECT_ROOT
        call_kwargs = mock_popen.call_args
        cgi_env = call_kwargs[1]["env"]
        self.assertIn("GIT_PROJECT_ROOT", cgi_env)
        self.assertEqual(cgi_env["GIT_HTTP_EXPORT_ALL"], "1")
        # Defense in depth: hooks disabled
        self.assertEqual(cgi_env["GIT_CONFIG_KEY_0"], "core.hooksPath")
        self.assertEqual(cgi_env["GIT_CONFIG_VALUE_0"], "/dev/null")

    def test_invalid_content_length_returns_400(self) -> None:
        """Malformed Content-Length header returns 400."""
        code, _ = self._make_request(
            "/proj-a.git/git-receive-pack",
            token="validtoken",
            method="POST",
            extra_headers="Content-Length: notanumber\r\n",
        )
        self.assertEqual(code, 400)

    def test_negative_content_length_returns_400(self) -> None:
        """Negative Content-Length header returns 400."""
        code, _ = self._make_request(
            "/proj-a.git/git-receive-pack",
            token="validtoken",
            method="POST",
            extra_headers="Content-Length: -5\r\n",
        )
        self.assertEqual(code, 400)


class TestDetach(unittest.TestCase):
    """Tests for daemon (detach) mode."""

    def test_child_calls_serve_forever(self) -> None:
        """Child process (fork returns 0) should call serve_forever."""
        from terok.gate.server import _serve_daemon

        with tempfile.TemporaryDirectory() as td:
            mock_server = unittest.mock.Mock()
            mock_server.serve_forever.side_effect = SystemExit(0)

            with (
                unittest.mock.patch(
                    "terok.gate.server._ThreadingHTTPServer", return_value=mock_server
                ),
                unittest.mock.patch("terok.gate.server.os.fork", return_value=0),
                unittest.mock.patch("terok.gate.server.signal.signal") as mock_signal,
                unittest.mock.patch("terok.gate.server.os.setsid") as mock_setsid,
                unittest.mock.patch("terok.gate.server.os.open", return_value=3),
                unittest.mock.patch("terok.gate.server.os.dup2"),
                unittest.mock.patch("terok.gate.server.os.close"),
            ):
                store = TokenStore(Path(td) / "tokens.json")
                with self.assertRaises(SystemExit):
                    _serve_daemon(Path(td), store, 9418, None)

                mock_setsid.assert_called_once()
                mock_signal.assert_called_once()
                mock_server.serve_forever.assert_called_once()

    @unittest.mock.patch("terok.gate.server._ThreadingHTTPServer")
    @unittest.mock.patch("terok.gate.server.os.fork", return_value=42)
    def test_parent_writes_pid_file(
        self, mock_fork: unittest.mock.Mock, mock_server_class: unittest.mock.Mock
    ) -> None:
        """Parent process (fork returns child PID) should write PID file and exit."""
        from terok.gate.server import _serve_daemon

        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "gate.pid"
            store = TokenStore(Path(td) / "tokens.json")
            with self.assertRaises(SystemExit):
                _serve_daemon(Path(td), store, 9418, pid_file)
            self.assertEqual(pid_file.read_text(), "42")
