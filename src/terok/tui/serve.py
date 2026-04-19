# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""HTTP gateway that serves terok-tui to the owning user's browser.

An aiohttp Basic-auth middleware is injected into ``textual-serve``'s
``Server._make_app`` so every route — including the WebSocket upgrade —
is gated by a password the user picks and the browser remembers for the
origin.  The password is persisted scrypt-hashed in
``~/.config/terok/serve.password`` so it survives reboots; on first
launch a random one is minted and printed once.
"""

from __future__ import annotations

import argparse
import errno
import getpass
import hashlib
import hmac
import os
import re
import secrets
import stat
import sys
from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path
from typing import TYPE_CHECKING

from ..lib.core.paths import config_root
from ..lib.util.ansi import red, supports_color
from ..lib.util.net import url_host

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiohttp import web
    from textual_serve.server import Server


_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 8566
_AUTH_USER = "terok"
_AUTH_REALM = "terok-tui"

# N=2**14 · r=8 · p=1 → ≈30 ms per verify on modern hardware; the KDF
# cost *is* the online-guessing rate limit.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_PREFIX = "scrypt"


# ── Entry point ─────────────────────────────────────────────────────────


def main() -> None:
    """Run the terok-web server, or update the stored password and exit."""
    args = _argparser().parse_args()
    path = _password_path()

    if args.set_password:
        # Changing the password doesn't need textual-serve installed.
        _set_password(path)
        return

    _require_textual_serve()
    _warn_if_cleartext_basic_auth(args.host, args.public_url)
    stored_hash = _bootstrap_password(path)
    server = _build_server("terok-tui", args.host, args.port, args.public_url, stored_hash)
    display_url = args.public_url or f"http://{url_host(args.host)}:{args.port}/"
    print(
        f"terok-web: serving at {display_url} (user '{_AUTH_USER}', hash in {path})",
        file=sys.stderr,
    )
    server.serve()


# ── Password lifecycle ──────────────────────────────────────────────────


def _set_password(path: Path) -> None:
    """Replace the stored password record with a user-chosen one."""
    _save_password(path, _prompt_password())
    print(f"terok-web: password updated in {path}", file=sys.stderr)


def _bootstrap_password(path: Path) -> str:
    """Stored scrypt record for the serve password, minting one on first run."""
    existing = _load_password_record(path)
    if existing is not None:
        return existing
    fresh = secrets.token_urlsafe(16)
    record = _save_password(path, fresh)
    _print_first_run_banner(fresh)
    return record


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _print_first_run_banner(password: str) -> None:
    """Print the first-launch credentials framed in ASCII with red values.

    Only the username and password *values* are coloured; the frame and
    labels stay plain so the callout is legible on any terminal, and the
    frame width is computed from visible (ANSI-stripped) line length so
    the borders line up when colours are active.
    """
    color = supports_color()
    lines = [
        "terok-web: no password set — generated a random one.",
        "Copy it now; it will not be shown again.",
        "Run 'terok-web --set-password' to set your own.",
        "",
        f"Username: {red(_AUTH_USER, color)}",
        f"Password: {red(password, color)}",
    ]
    width = max(len(_ANSI_RE.sub("", line)) for line in lines)
    bar = "+" + "-" * (width + 2) + "+"
    print(bar, file=sys.stderr)
    for line in lines:
        padding = " " * (width - len(_ANSI_RE.sub("", line)))
        print(f"| {line}{padding} |", file=sys.stderr)
    print(bar, file=sys.stderr)


# ── HTTP gate ───────────────────────────────────────────────────────────


def _build_server(
    command: str, host: str, port: int, public_url: str | None, stored_hash: str
) -> Server:
    """A textual-serve :class:`Server` with Basic-auth middleware already attached.

    We monkey-patch ``_make_app`` on the instance rather than subclassing
    so the upstream call shape stays unchanged.  Breaks only if
    textual-serve renames that seam — guarded at import time.
    """
    from textual_serve.server import Server

    mw = _basic_auth_middleware(stored_hash)
    server = Server(command, host=host, port=port, public_url=public_url)
    original_make_app = server._make_app

    async def _make_app_with_auth() -> web.Application:
        app = await original_make_app()
        # Insert at position 0 so Basic auth is the outermost guard: any
        # middleware textual-serve installs runs *inside* the gate, not
        # before it.
        app.middlewares.insert(0, mw)
        return app

    server._make_app = _make_app_with_auth
    return server


def _basic_auth_middleware(stored_hash: str) -> Callable[..., Awaitable[web.StreamResponse]]:
    """Basic-auth gate that verifies the user's password against *stored_hash*.

    Browsers cache Basic credentials for the origin, so the prompt fires
    at most once per session.  Each request still pays a full scrypt
    verify — deliberate rate limit against online guessing.
    """
    from aiohttp import web

    challenge = {"WWW-Authenticate": f'Basic realm="{_AUTH_REALM}"'}
    user_prefix = f"{_AUTH_USER}:".encode()

    @web.middleware
    async def mw(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        header = request.headers.get("Authorization", "")
        scheme, _, payload = header.partition(" ")
        if scheme.lower() == "basic":
            try:
                decoded = b64decode(payload.encode(), validate=True)
            except ValueError:
                decoded = b""
            if decoded.startswith(user_prefix):
                candidate = decoded[len(user_prefix) :].decode("utf-8", errors="replace")
                if _verify_password(candidate, stored_hash):
                    return await handler(request)
        return web.Response(status=401, headers=challenge, text="Unauthorized")

    return mw


# ── scrypt hashing ──────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    """A serialised scrypt record for *password* — ``scrypt$N$r$p$salt$hash``.

    The record is self-describing so parameter upgrades are detectable
    on verify and re-hashes can happen lazily later.
    """
    salt = secrets.token_bytes(16)
    hashed = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN
    )
    return "$".join(
        (
            _SCRYPT_PREFIX,
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            urlsafe_b64encode(salt).decode().rstrip("="),
            urlsafe_b64encode(hashed).decode().rstrip("="),
        )
    )


def _verify_password(candidate: str, stored: str) -> bool:
    """True iff *candidate* hashes to the stored scrypt record."""
    try:
        prefix, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if prefix != _SCRYPT_PREFIX:
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        expected = urlsafe_b64decode(hash_b64 + "=" * (-len(hash_b64) % 4))
        got = hashlib.scrypt(candidate.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


# ── Password file I/O ───────────────────────────────────────────────────


def _save_password(path: Path, password: str) -> str:
    """Hash *password* and write the record to *path* (0600); return it.

    The open deliberately omits ``O_TRUNC`` — truncating before the
    ownership check would silently destroy someone else's file if the
    uid check were to fail.  We only call ``ftruncate`` after verifying
    the file is ours.
    """
    record = _hash_password(password)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SystemExit(f"Refusing to write {path}: it is a symlink.") from exc
        raise
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise SystemExit(f"Refusing to write {path}: not owned by current uid.")
        # O_CREAT's mode is ignored on an existing file; fchmod tightens
        # a prior loose mode before we write.
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, 0)
        os.write(fd, (record + "\n").encode())
    finally:
        os.close(fd)
    return record


def _load_password_record(path: Path) -> str | None:
    """Stored scrypt record at *path*, or ``None`` when no file exists.

    Refuses a symlink, a file not owned by the current user, or loose
    permissions — any of these would let a local peer leak or swap
    credentials on a shared host.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SystemExit(f"Refusing to read {path}: it is a symlink.") from exc
        raise
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid():
            raise SystemExit(
                f"Refusing to read {path}: owned by uid {st.st_uid}, not {os.getuid()}."
            )
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise SystemExit(
                f"Refusing to read {path}: mode {oct(stat.S_IMODE(st.st_mode))}, expected 0600."
            )
        return os.read(fd, 4096).decode().strip() or None
    finally:
        os.close(fd)


def _prompt_password() -> str:
    """A non-empty password read (hidden on a TTY, one line from stdin otherwise)."""
    if sys.stdin.isatty():
        pw = getpass.getpass("New terok-web password: ")
        if not pw:
            raise SystemExit("Password must not be empty.")
        if getpass.getpass("Confirm: ") != pw:
            raise SystemExit("Passwords did not match.")
        return pw
    pw = sys.stdin.readline().rstrip("\n")
    if not pw:
        raise SystemExit("Password must not be empty.")
    return pw


# ── Wiring ──────────────────────────────────────────────────────────────


def _password_path() -> Path:
    """Path of the scrypt-hashed serve password (creating its config dir if needed)."""
    root = config_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / "serve.password"


def _argparser() -> argparse.ArgumentParser:
    """Argparser for ``terok-web``."""
    parser = argparse.ArgumentParser(
        prog="terok-web",
        description="Serve the Terok TUI as a web application",
    )
    parser.add_argument(
        "--host", default=_DEFAULT_HOST, help=f"Host to bind to (default: {_DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port",
        type=_valid_port,
        default=_DEFAULT_PORT,
        help=f"Port to listen on (default: {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--public-url",
        default=None,
        help="Public URL for browser-facing links and WebSocket connections "
        "(e.g. http://myhost:8566). Required when serving to LAN or "
        "behind a reverse proxy. If omitted, derived from --host and --port.",
    )
    parser.add_argument(
        "--set-password",
        action="store_true",
        help="Prompt for a new Basic-auth password, store its scrypt hash, and exit.",
    )
    return parser


def _valid_port(value: str) -> int:
    """A TCP port number in the range 1–65535."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port value: {value!r} (must be an integer)")
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError(
            f"invalid port value: {value!r} (must be between 1 and 65535)"
        )
    return port


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _warn_if_cleartext_basic_auth(host: str, public_url: str | None) -> None:
    """Warn when Basic-auth credentials would travel the network in cleartext.

    The only genuinely safe case is a loopback bind — anyone who can
    read ``lo`` already owns the UID we run as.  A non-loopback bind
    exposes the socket to every client on the segment; an
    ``https://…`` advertisement via ``--public-url`` doesn't change
    that, because the unencrypted port is still reachable directly.
    The fix for that setup is ``--host 127.0.0.1`` + a reverse proxy,
    not a URL string, so we warn loudly and keep going.
    """
    if host in _LOOPBACK_HOSTS:
        return
    detail = (
        " (note: https:// --public-url advertises the reverse-proxy URL,"
        " but the raw listener on this host is still plaintext)"
        if public_url and public_url.lower().startswith("https://")
        else ""
    )
    print(
        f"terok-web: WARNING — binding non-loopback means Basic-auth credentials "
        f"travel the wire in cleartext.{detail}",
        file=sys.stderr,
    )


def _require_textual_serve() -> None:
    """Exit early if textual-serve is missing or its ``_make_app`` seam is gone."""
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
    if not hasattr(Server, "_make_app"):
        print(
            "Unsupported textual-serve version: Server._make_app is missing.  "
            "terok pins the upstream seam used to inject basic-auth middleware.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
