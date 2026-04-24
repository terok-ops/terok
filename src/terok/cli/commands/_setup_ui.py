# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared terminal output primitives for ``setup.py`` / ``uninstall.py``.

Both commands render stage lines with a 17-char label column and a
coloured ``ok`` / ``FAIL`` / ``WARN`` marker.  Keeping the palette
wrappers and the column-width convention here prevents drift — if the
padding ever changes, both ends of the install cycle must update in
lockstep.
"""

from __future__ import annotations

from functools import cache

from ...lib.util.ansi import (
    bold as _ansi_bold,
    green as _ansi_green,
    red as _ansi_red,
    supports_color,
    yellow as _ansi_yellow,
)

# The ``supports_color()`` verdict is stable for a process lifetime
# (NO_COLOR / FORCE_COLOR / isatty() don't change mid-run), so every
# caller can render through colour-aware helpers that resolve the flag
# once.  Tests force a deterministic verdict by clearing the cache.


@cache
def _colour_on() -> bool:
    """Memoised ``supports_color()`` — stable per process."""
    return supports_color()


def _bold(text: str) -> str:
    """Return *text* in bold when the terminal supports colour."""
    return _ansi_bold(text, _colour_on())


def _green(text: str) -> str:
    """Return *text* in green when the terminal supports colour."""
    return _ansi_green(text, _colour_on())


def _red(text: str) -> str:
    """Return *text* in red when the terminal supports colour."""
    return _ansi_red(text, _colour_on())


def _yellow(text: str) -> str:
    """Return *text* in yellow when the terminal supports colour."""
    return _ansi_yellow(text, _colour_on())


def _status_label(ok: bool) -> str:
    """Return a coloured ``ok`` / ``FAIL`` marker."""
    return _green("ok") if ok else _red("FAIL")


def _warn_label() -> str:
    """Return a coloured ``WARN`` marker."""
    return _yellow("WARN")


def _stage_begin(label: str) -> None:
    """Write ``'  <label>'`` (padded to the status column) and flush.

    Long-running stages print the label up-front so the operator sees
    *which* stage is currently grinding — without progressive output
    the whole block looks frozen during a slow ``systemctl restart`` or
    a network round-trip.  The matching terminator is the regular
    ``print(...)`` that writes the status suffix and newline.
    """
    # Column width is load-bearing: setup and uninstall both align
    # their status markers at this offset, so the two commands read
    # as one continuous log when run back-to-back.  Recompute when a
    # new phase ships with a label longer than the current widest.
    print(f"  {label:<21}", end="", flush=True)
