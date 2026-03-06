# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Standalone HTTP gate server wrapping ``git http-backend`` with token auth.

This module has **zero terok imports**.  It is a self-contained security
component equivalent to ``git daemon``: a separate process that serves repos.

Token validation:
    Each request must carry HTTP Basic Auth with the token as the username
    (password is ignored).  The token is looked up in a JSON file mapping
    tokens to project IDs.  The requested repo must match the token's project.

Modes:
    --inetd   Handle one request on an inherited socket (fd 0), then exit.
              Used by systemd ``Accept=yes`` socket activation.
    --detach  Bind, fork, accept loop in child.  Daemon fallback.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

# ---------------------------------------------------------------------------
# Token store — inlined read-only logic, no terok imports
# ---------------------------------------------------------------------------

_ROUTE = re.compile(
    r"^/(?P<repo>[A-Za-z0-9._-]+\.git)"
    r"(?P<path>/info/refs|/git-upload-pack|/git-receive-pack|/HEAD)$"
)

_CGI_WAIT_TIMEOUT = 30


def _validate_token_data(data: object) -> dict[str, dict[str, str]]:
    """Filter parsed JSON to only valid ``{token: {project, task}}`` entries."""
    if not isinstance(data, dict):
        return {}
    return {
        tok: info
        for tok, info in data.items()
        if isinstance(tok, str)
        and isinstance(info, dict)
        and isinstance(info.get("project"), str)
        and isinstance(info.get("task"), str)
    }


class TokenStore:
    """Read-only view of ``tokens.json`` with lazy reload on mtime change."""

    def __init__(self, token_file: Path) -> None:
        """Initialize with the path to the tokens JSON file."""
        self._path = token_file
        self._mtime_ns: int = 0
        self._tokens: dict[str, dict[str, str]] = {}

    def _maybe_reload(self) -> None:
        """Reload the token file if its mtime has changed."""
        try:
            st = self._path.stat()
        except OSError:
            self._tokens = {}
            self._mtime_ns = 0
            return
        if st.st_mtime_ns != self._mtime_ns:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = None
            self._tokens = _validate_token_data(data)
            self._mtime_ns = st.st_mtime_ns

    def validate(self, token: str) -> str | None:
        """Return project_id if *token* is valid, else ``None``.

        Reloads the token file when its mtime changes.
        """
        self._maybe_reload()
        info = self._tokens.get(token)
        if info is None:
            return None
        project = info.get("project")
        return project if isinstance(project, str) else None


# ---------------------------------------------------------------------------
# Module-level helpers — extracted to reduce handler cognitive complexity
# ---------------------------------------------------------------------------


def _extract_basic_auth_token(auth_header: str | None) -> str | None:
    """Parse ``Authorization: Basic`` header, return username (token)."""
    if not auth_header or not auth_header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
    except Exception:
        return None
    if ":" not in decoded:
        return None
    username, _password = decoded.split(":", 1)
    return username or None


def _parse_content_length(header: str | None) -> tuple[int, str | None]:
    """Validate a Content-Length header value.

    Returns ``(length, None)`` on success or ``(0, error_message)`` on failure.
    """
    if not header:
        return 0, None
    try:
        length = int(header)
        if length < 0:
            raise ValueError("negative")
    except ValueError:
        return 0, "Invalid Content-Length"
    return length, None


def _build_cgi_env(
    base_path: Path,
    path_info: str,
    query_string: str,
    method: str,
    content_type: str,
    protocol: str,
    content_length: int,
) -> dict[str, str]:
    """Build the CGI environment for ``git http-backend``."""
    env = {
        "GIT_PROJECT_ROOT": str(base_path),
        "GIT_HTTP_EXPORT_ALL": "1",
        "PATH_INFO": path_info,
        "QUERY_STRING": query_string,
        "REQUEST_METHOD": method,
        "CONTENT_TYPE": content_type,
        "SERVER_PROTOCOL": protocol,
        "REMOTE_USER": "token",
        # Defense in depth: disable hooks
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/dev/null",
        "GIT_CONFIG_COUNT": "1",
    }
    if content_length:
        env["CONTENT_LENGTH"] = str(content_length)
    return env


def _stream_request_body(rfile: object, stdin: object, remaining: int) -> None:
    """Stream *remaining* bytes from *rfile* to CGI *stdin*."""
    if remaining <= 0:
        return
    try:
        while remaining > 0:
            chunk = rfile.read(min(remaining, 8192))
            if not chunk:
                break
            stdin.write(chunk)
            remaining -= len(chunk)
    except BrokenPipeError:
        pass  # CGI process closed stdin early


def _parse_cgi_headers(stdout: object) -> tuple[int, list[tuple[str, str]]]:
    """Read CGI response headers from *stdout*.

    Returns ``(status_code, [(header_name, header_value), ...])``.
    """
    status_code = 200
    headers: list[tuple[str, str]] = []
    while True:
        line = stdout.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        header_line = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if header_line.startswith("Status:"):
            try:
                status_code = int(header_line.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif ":" in header_line:
            key, val = header_line.split(":", 1)
            headers.append((key.strip(), val.strip()))
    return status_code, headers


def _stream_response_body(stdout: object, wfile: object) -> None:
    """Stream CGI response body from *stdout* to *wfile*."""
    while True:
        chunk = stdout.read(8192)
        if not chunk:
            break
        wfile.write(chunk)


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------


def _make_handler_class(base_path: Path, token_store: TokenStore) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to the given base_path and token_store."""

    class GateRequestHandler(BaseHTTPRequestHandler):
        """Handle smart-HTTP git requests with token authentication."""

        server_version = "terok-gate/1.0"

        def do_GET(self) -> None:
            """Handle GET requests (info/refs discovery)."""
            self._handle()

        def do_POST(self) -> None:
            """Handle POST requests (upload-pack, receive-pack)."""
            self._handle()

        def _handle(self) -> None:
            """Route, authenticate, and delegate to CGI."""
            path, query_string = self._split_path()

            m = _ROUTE.match(path)
            if not m:
                self.send_error(404, "Not Found")
                return

            repo = m.group("repo")
            path_info = f"/{repo}{m.group('path')}"

            token = _extract_basic_auth_token(self.headers.get("Authorization"))
            if token is None:
                self._send_auth_required()
                return

            project_id = token_store.validate(token)
            if project_id is None or repo != f"{project_id}.git":
                self.send_error(403, "Forbidden")
                return

            self._run_cgi(path_info, query_string)

        def _split_path(self) -> tuple[str, str]:
            """Split request path into path and query string."""
            if "?" in self.path:
                return self.path.split("?", 1)
            return self.path, ""

        def _send_auth_required(self) -> None:
            """Send a 401 response with WWW-Authenticate header."""
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="terok gate"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Authentication required\n")

        def _run_cgi(self, path_info: str, query_string: str) -> None:
            """Execute ``git http-backend`` and stream the response."""
            content_length, err = _parse_content_length(self.headers.get("Content-Length"))
            if err:
                self.send_error(400, err)
                return

            cgi_env = _build_cgi_env(
                base_path,
                path_info,
                query_string,
                self.command,
                self.headers.get("Content-Type", ""),
                self.request_version,
                content_length,
            )

            try:
                proc = subprocess.Popen(
                    ["git", "http-backend"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=cgi_env,
                )
            except FileNotFoundError:
                self.send_error(500, "git not found")
                return
            except OSError:
                self.send_error(500, "git http-backend unavailable")
                return

            _stream_request_body(self.rfile, proc.stdin, content_length)
            proc.stdin.close()

            status_code, headers = _parse_cgi_headers(proc.stdout)
            self.send_response(status_code)
            for key, val in headers:
                self.send_header(key, val)
            self.end_headers()

            _stream_response_body(proc.stdout, self.wfile)

            try:
                proc.wait(timeout=_CGI_WAIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        def log_message(self, _format: str, *args: object) -> None:
            """Suppress default stderr logging."""

    return GateRequestHandler


# ---------------------------------------------------------------------------
# Threading HTTP server
# ---------------------------------------------------------------------------


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request in a new thread."""

    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Inetd mode: handle one request on an inherited socket
# ---------------------------------------------------------------------------


def _serve_inetd(base_path: Path, token_store: TokenStore) -> None:
    """Handle a single HTTP request on the inherited socket (fd 0), then exit."""
    handler_class = _make_handler_class(base_path, token_store)

    # systemd Accept=yes passes the connected socket as fd 0 (stdin)
    conn = socket.fromfd(0, socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Wrap in a minimal server context for BaseHTTPRequestHandler
        rfile = conn.makefile("rb", buffering=0)
        wfile = conn.makefile("wb", buffering=0)

        handler = handler_class.__new__(handler_class)
        handler.request = conn
        handler.client_address = conn.getpeername()
        handler.server = type("FakeServer", (), {"server_name": "localhost", "server_port": 0})()
        handler.rfile = rfile
        handler.wfile = wfile
        handler.raw_requestline = rfile.readline(65537)
        if handler.raw_requestline and handler.parse_request():
            method = getattr(handler, f"do_{handler.command}", None)
            if method:
                method()
            else:
                handler.send_error(501, "Unsupported method")
        wfile.flush()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Daemon mode: bind, fork, accept loop
# ---------------------------------------------------------------------------


def _serve_daemon(
    base_path: Path, token_store: TokenStore, port: int, pid_file: Path | None
) -> None:
    """Bind socket, fork, write PID file, run accept loop in child."""
    handler_class = _make_handler_class(base_path, token_store)
    server = _ThreadingHTTPServer(("127.0.0.1", port), handler_class)

    pid = os.fork()
    if pid > 0:
        # Parent: write child PID and exit
        if pid_file:
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(pid))
        sys.exit(0)

    # Child: detach
    os.setsid()
    # Redirect stdio to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server.serve_forever()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI args and run the gate server in the selected mode."""
    parser = argparse.ArgumentParser(
        prog="terok-gate",
        description="HTTP gate server for git repos with token authentication",
    )
    parser.add_argument("--base-path", required=True, help="Root directory for git repos")
    parser.add_argument("--token-file", required=True, help="Path to tokens.json")
    parser.add_argument("--port", type=int, default=9418, help="Listen port (daemon mode)")
    parser.add_argument("--inetd", action="store_true", help="Handle one request on fd 0, exit")
    parser.add_argument("--detach", action="store_true", help="Fork and run as daemon")
    parser.add_argument("--pid-file", default=None, help="PID file path (daemon mode)")

    args = parser.parse_args()
    base_path = Path(args.base_path)
    token_store = TokenStore(Path(args.token_file))

    if args.inetd:
        _serve_inetd(base_path, token_store)
    elif args.detach:
        pid_file = Path(args.pid_file) if args.pid_file else None
        _serve_daemon(base_path, token_store, args.port, pid_file)
    else:
        parser.error("One of --inetd or --detach is required")
