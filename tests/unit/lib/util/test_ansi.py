# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ANSI helpers — focused on the OSC 8 hyperlink escape.

The colour helpers (``blue``, ``green`` …) are trivial wrappers around
[`color`][]; their only logic is the on/off gate.  The hyperlink
helper is the new, less-obvious one — it produces an OSC 8 sequence
with a stable ``id=`` so terminals stitch wrapped link segments back
into one clickable hyperlink.
"""

from terok.lib.util.ansi import blue, hyperlink


def test_hyperlink_disabled_returns_text_unchanged() -> None:
    """When *enabled* is False the helper is a pass-through — no escapes leak."""
    assert hyperlink("toad URL", "http://example.com/", enabled=False) == "toad URL"


def test_hyperlink_emits_osc8_with_id_and_url() -> None:
    """Enabled output wraps text in the canonical OSC 8 sequence with an id."""
    out = hyperlink("click me", "http://example.com/x?y=1", enabled=True)
    # The opening sequence carries an ``id=`` so wrap-split segments stitch.
    assert out.startswith("\x1b]8;id=")
    assert ";http://example.com/x?y=1\x1b\\" in out
    assert out.endswith("\x1b]8;;\x1b\\")
    # The visible text sits between the wrappers, untouched.
    assert "click me" in out


def test_hyperlink_id_is_stable_for_same_url() -> None:
    """Two calls with the same URL produce the same id — so wrap segments link."""
    a = hyperlink("part 1", "http://example.com/", enabled=True)
    b = hyperlink("part 2", "http://example.com/", enabled=True)
    # Both sequences must share the id prefix, otherwise the terminal would
    # treat the wrapped halves as two separate links.
    id_a = a.split(";", 2)[1]
    id_b = b.split(";", 2)[1]
    assert id_a == id_b


def test_hyperlink_composes_with_color() -> None:
    """OSC 8 wraps *outside* the SGR escape — the colour reset doesn't break the link."""
    url = "http://example.com/"
    out = hyperlink(blue("toad URL", True), url, enabled=True)
    # The OSC 8 brackets sit at the outermost edges; the SGR colour
    # codes nest inside them — that's the only ordering terminals
    # accept for a coloured-clickable link.
    assert out.startswith("\x1b]8;id=")
    assert out.endswith("\x1b]8;;\x1b\\")
    assert "\x1b[34m" in out  # blue
    assert "\x1b[0m" in out  # reset
