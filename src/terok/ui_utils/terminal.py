# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Terminal ANSI formatting helpers.

Core color functions (``supports_color``, ``color``, ``yellow``, ``blue``,
``green``, ``red``) are defined in ``terok.lib.util.ansi`` so that
service-layer modules can use them without a cross-layer dependency.
This module re-exports them and adds higher-level helpers.
"""

from terok.lib.util.ansi import (  # noqa: F401  -- re-exports
    blue,
    bold,
    color,
    green,
    red,
    supports_color,
    yellow,
)


def yes_no(value: bool, enabled: bool) -> str:
    """Return green ``"yes"`` or red ``"no"`` based on *value* when *enabled*."""
    return color("yes" if value else "no", "32" if value else "31", enabled)


def violet(text: str, enabled: bool) -> str:
    """Return *text* in violet (ANSI 35) when *enabled*."""
    return color(text, "35", enabled)


def gray(text: str, enabled: bool) -> str:
    """Return *text* in gray (ANSI 90) when *enabled*."""
    return color(text, "90", enabled)
