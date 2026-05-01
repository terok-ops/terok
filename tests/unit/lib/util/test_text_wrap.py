# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the hanging-indent wrapper.

The interesting cases are the *boundaries* between "fits on one line",
"wraps body", and "suffix overflows onto its own line" — plus the
cell-aware accounting that keeps wide-character prefixes from confusing
``textwrap.wrap``.
"""

import pytest

from terok.lib.util.text_wrap import wrap_with_hanging_indent

# Width 13 (cells): "tk-001 " (7) + "🤖" (2) + " " (1) + "✅" (2) + " " (1).
EMOJI_PREFIX = "tk-001 \U0001f916 ✅ "
EMOJI_PREFIX_CELLS = 13


@pytest.mark.parametrize("width", [-1, 0])
def test_nonpositive_width_disables_wrapping(width: int) -> None:
    """Width ≤ 0 is a "do not wrap" signal — return the inputs verbatim."""
    out = wrap_with_hanging_indent("P: ", "a-b-c", " [x]", width)
    assert out == "P: a-b-c [x]"


def test_short_input_returns_one_line() -> None:
    """A label that already fits in *width* is left untouched."""
    out = wrap_with_hanging_indent("Name:      ", "short", "", 80)
    assert out == "Name:      short"
    assert "\n" not in out


def test_prefix_wider_than_width_returns_concatenated() -> None:
    """When the prefix alone fills the width, there is nothing to wrap into."""
    out = wrap_with_hanging_indent("very-long-prefix: ", "body", "", 5)
    assert out == "very-long-prefix: body"


def test_dash_break_with_hanging_indent() -> None:
    """Body breaks at dash boundaries; continuations align with body start."""
    out = wrap_with_hanging_indent(EMOJI_PREFIX, "my-very-long-task-branch-name", "", 30)
    pad = " " * EMOJI_PREFIX_CELLS
    assert out == f"{EMOJI_PREFIX}my-very-long-\n{pad}task-branch-name"


def test_dashless_long_word_falls_back_to_char_fold() -> None:
    """A dashless body wider than *avail* still wraps — char-by-char."""
    out = wrap_with_hanging_indent("Name:      ", "verylongtasknamewithoutdashes", "", 30)
    assert out == "Name:      verylongtasknamewit\n           houtdashes"


def test_suffix_attaches_when_it_fits_on_last_line() -> None:
    """The suffix rides along on the last body line if there's room for it."""
    out = wrap_with_hanging_indent(EMOJI_PREFIX, "my-very-long-task-branch-name", " [w=run]", 40)
    last_line = out.splitlines()[-1]
    assert last_line.endswith("[w=run]")


def test_suffix_overflows_to_new_line_with_leading_space_stripped() -> None:
    """When the suffix can't fit, it lands on its own line with no leading gap."""
    out = wrap_with_hanging_indent(
        EMOJI_PREFIX, "my-very-long-task-branch-name", " [work=running]", 30
    )
    pad = " " * EMOJI_PREFIX_CELLS
    last_line = out.splitlines()[-1]
    # Suffix sits at the indent column with the leading space stripped.
    assert last_line == f"{pad}[work=running]"


def test_empty_body_emits_just_prefix_and_suffix() -> None:
    """Empty bodies are accepted — the helper still returns a single line."""
    assert wrap_with_hanging_indent("Name:      ", "", "", 30) == "Name:      "


def test_emoji_prefix_indent_uses_cells_not_chars() -> None:
    """Continuation indent matches the *cell* width of the prefix, not its len()."""
    out = wrap_with_hanging_indent(EMOJI_PREFIX, "a-b-c-d-e-f-g-h", "", 20)
    # Prefix is 13 cells, body avail is 7 cells. Continuation indent = 13 spaces.
    second_line = out.splitlines()[1]
    assert second_line.startswith(" " * EMOJI_PREFIX_CELLS)
    # And exactly that many leading spaces — no over- or under-pad.
    assert second_line[EMOJI_PREFIX_CELLS] != " "


def test_every_continuation_line_gets_the_indent() -> None:
    """Three or more wrapped lines all share the same hanging indent."""
    out = wrap_with_hanging_indent(EMOJI_PREFIX, "a-b-c-d-e-f-g-h-i-j-k", "", 18)
    pad = " " * EMOJI_PREFIX_CELLS
    lines = out.splitlines()
    assert len(lines) >= 3
    for cont in lines[1:]:
        assert cont.startswith(pad)
