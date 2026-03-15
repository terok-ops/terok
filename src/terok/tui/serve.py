# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Serve the Terok TUI as a web application via textual-serve."""

import argparse
import sys

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 8566


def _valid_port(value: str) -> int:
    """Validate that *value* is a valid TCP port number (1–65535)."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port value: {value!r} (must be an integer)")
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError(
            f"invalid port value: {value!r} (must be between 1 and 65535)"
        )
    return port


def main() -> None:
    """Launch the Terok TUI as a web application.

    Uses textual-serve to expose the TUI over HTTP/WebSocket so it can
    be accessed from a browser.  Accepts ``--host`` and ``--port`` to
    override the default listen address.
    """
    try:
        from textual_serve.server import Server
    except ModuleNotFoundError as exc:
        if exc.name in ("textual_serve", "textual_serve.server"):
            print(
                "terok-web requires the 'textual-serve' package.\n"
                "Install it with: pip install textual-serve",
                file=sys.stderr,
            )
            sys.exit(1)
        raise

    parser = argparse.ArgumentParser(
        prog="terok-web",
        description="Serve the Terok TUI as a web application",
    )
    parser.add_argument(
        "--host",
        default=_DEFAULT_HOST,
        help=f"Host to bind to (default: {_DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=_valid_port,
        default=_DEFAULT_PORT,
        help=f"Port to listen on (default: {_DEFAULT_PORT})",
    )
    args = parser.parse_args()

    server = Server("terok", host=args.host, port=args.port)
    server.serve()


if __name__ == "__main__":
    main()
