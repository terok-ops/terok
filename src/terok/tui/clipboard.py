# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""System clipboard integration for the TUI."""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

#: Hard cap on a single clipboard-helper invocation.  wl-copy in
#: particular forks a daemon that holds the X/Wayland selection alive
#: after the foreground process exits; if we ever ended up waiting for
#: that daemon to close its pipes we'd block the UI loop forever.  Three
#: seconds is comfortably more than any real clipboard write needs and
#: small enough that a stuck helper surfaces as an error, not a hang.
_CLIPBOARD_TIMEOUT_SEC = 3.0


@dataclass(frozen=True)
class ClipboardHelperStatus:
    """Result of probing the system for available clipboard helpers."""

    available: tuple[str, ...]
    hint: str | None = None


@dataclass(frozen=True)
class ClipboardCopyResult:
    """Outcome of a clipboard copy attempt."""

    ok: bool
    method: str | None = None
    error: str | None = None
    hint: str | None = None


def _clipboard_install_hint() -> str:
    """Return a platform-specific hint for installing a clipboard helper."""
    if sys.platform == "darwin":
        return ""

    # Wayland vs X11 is fuzzy; provide a useful Ubuntu/Debian hint.
    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY")
    )
    x11 = os.environ.get("XDG_SESSION_TYPE") == "x11" or bool(os.environ.get("DISPLAY"))

    if wayland and not x11:
        return "Install wl-clipboard: sudo apt install wl-clipboard"
    if x11 and not wayland:
        return "Install xclip or xsel: sudo apt install xclip"
    return "Install wl-clipboard (Wayland) or xclip/xsel (X11)"


def _clipboard_candidates() -> list[tuple[str, list[str]]]:
    """Return an ordered list of (name, command) clipboard helper candidates."""
    candidates: list[tuple[str, list[str]]] = []

    if sys.platform == "darwin":
        candidates.append(("pbcopy", ["pbcopy"]))
        return candidates
    if os.name == "nt":
        candidates.append(("clip", ["clip"]))
        return candidates

    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY")
    )
    x11 = os.environ.get("XDG_SESSION_TYPE") == "x11" or bool(os.environ.get("DISPLAY"))

    if wayland:
        candidates.append(("wl-copy", ["wl-copy", "--type", "text/plain"]))
    if x11:
        candidates.append(("xclip", ["xclip", "-selection", "clipboard"]))
        candidates.append(("xsel", ["xsel", "--clipboard", "--input"]))

    if not candidates:
        candidates.extend(
            [
                ("wl-copy", ["wl-copy", "--type", "text/plain"]),
                ("xclip", ["xclip", "-selection", "clipboard"]),
                ("xsel", ["xsel", "--clipboard", "--input"]),
            ]
        )

    return candidates


def get_clipboard_helper_status() -> ClipboardHelperStatus:
    """Return which clipboard helpers are available on this machine."""

    candidates = _clipboard_candidates()
    available = tuple(name for name, cmd in candidates if shutil.which(cmd[0]))
    if available:
        return ClipboardHelperStatus(available=available)

    hint = _clipboard_install_hint()
    return ClipboardHelperStatus(available=(), hint=hint or None)


def copy_to_clipboard_detailed(text: str) -> ClipboardCopyResult:
    """Copy text to the system clipboard and return a detailed result.

    Prefers native OS clipboard helpers when available. On Linux, users may
    need to install a helper (for example, ``wl-clipboard`` on Wayland or
    ``xclip``/``xsel`` on X11).

    Args:
        text: The text to copy to the system clipboard. If this is an empty
            string, the function will not invoke any clipboard helper and will
            return a failure result.

    Returns:
        ClipboardCopyResult: A dataclass describing the outcome:

            * ``ok``: ``True`` if the text was successfully written to the
              clipboard using one of the available helpers; ``False`` if all
              helpers failed or no helper was available, or if ``text`` was
              empty.
            * ``method``: The name of the clipboard helper that succeeded
              (for example, ``"pbcopy"``, ``"wl-copy"``, or ``"xclip"``) when
              ``ok`` is ``True``. ``None`` if no helper was run or all helpers
              failed.
            * ``error``: A human-readable error message describing why the
              copy failed when ``ok`` is ``False``. This is ``"Nothing to copy."``
              when ``text`` is empty, ``"No clipboard helper found on PATH."``
              when no helper is available, or the last recorded helper failure
              message when all helpers fail.
            * ``hint``: An optional hint string with guidance on how to enable
              clipboard support on the current platform (for example, a command
              to install a missing helper). Populated only when **no** helper
              was found on the system; ``None`` on success and also ``None``
              when helpers were available but all of them failed at runtime —
              in that case the user already has a helper installed, so
              ``error`` describes what actually went wrong.

    Examples:
        Basic usage with boolean check::

            result = copy_to_clipboard_detailed("hello world")
            if result.ok:
                print(f"Copied to clipboard using {result.method}")
            else:
                print(f"Copy failed: {result.error}")
                if result.hint:
                    print(result.hint)

        Handling the case where no clipboard helper is installed::

            result = copy_to_clipboard_detailed("some text")
            if not result.ok and result.error == "No clipboard helper found on PATH.":
                # result.hint may contain a command to install a suitable helper.
                print(result.hint or "Install a clipboard helper for your system.")

        Handling an empty string (nothing to copy)::

            result = copy_to_clipboard_detailed("")
            assert not result.ok
            assert result.error == "Nothing to copy."
    """
    if not text:
        return ClipboardCopyResult(ok=False, error="Nothing to copy.")

    candidates = _clipboard_candidates()
    available = [(name, cmd) for name, cmd in candidates if shutil.which(cmd[0])]
    if not available:
        hint = _clipboard_install_hint()
        return ClipboardCopyResult(
            ok=False, error="No clipboard helper found on PATH.", hint=hint or None
        )

    errors: list[str] = []
    for name, cmd in available:
        try:
            # Why ``stdout=DEVNULL`` instead of ``capture_output=True``:
            # wl-copy forks a long-lived daemon that inherits the parent's
            # stdout/stderr to keep the Wayland selection alive.  When
            # Python captures stdout via a pipe, ``subprocess.run`` waits
            # for EOF on that pipe — which the daemon never closes — and
            # the call hangs forever, freezing the caller's event loop.
            # Giving the helper ``/dev/null`` as stdout breaks the pipe
            # dependency entirely; stderr stays on a pipe so we can still
            # surface a real error message on failure.  The timeout is
            # defence-in-depth for helpers we haven't anticipated.
            subprocess.run(
                cmd,
                input=text,
                check=True,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=_CLIPBOARD_TIMEOUT_SEC,
            )
            return ClipboardCopyResult(ok=True, method=name)
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip()
            errors.append(f"{name} failed" + (f": {detail}" if detail else ""))
        except subprocess.TimeoutExpired:
            errors.append(f"{name} timed out after {_CLIPBOARD_TIMEOUT_SEC:g}s")
        except Exception as e:
            errors.append(f"{name} error: {e}")

    # Helpers *were* available but all of them failed at runtime — a
    # broken Wayland socket, a misconfigured compositor, an xclip
    # segfault, etc.  Suggesting ``apt install wl-clipboard`` here would
    # be actively misleading; the user already has the helper.  Leave
    # hint None so the caller surfaces the actual per-helper error.
    return ClipboardCopyResult(ok=False, error=errors[-1] if errors else "Clipboard copy failed.")
