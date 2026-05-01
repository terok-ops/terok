# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Hanging-indent wrapping for prefix-aligned TUI labels.

Wrapping itself is delegated to :func:`textwrap.wrap` (which already breaks
on hyphens and folds overlong dashless words). This helper exists only to
stitch together a cell-aware *prefix* (which may contain wide characters
like emoji that would throw off ``textwrap``'s char-count accounting), the
wrapped body, and an optional trailing *suffix*.
"""

from __future__ import annotations

import textwrap

from rich.cells import cell_len


def wrap_with_hanging_indent(prefix: str, body: str, suffix: str, width: int) -> str:
    """Render ``prefix + body + suffix`` with continuation lines hanging-indented.

    *body* wraps with :func:`textwrap.wrap` (so dashes are break points and
    overlong segments fold by character). Continuation lines are prepended
    with spaces aligning to the *cell* width of *prefix*, so they sit
    underneath the start of *body* even when *prefix* contains emoji.

    *suffix* attaches to the last body line if it fits; otherwise it lands
    on its own continuation line with the leading space stripped.

    *width* ≤ 0, or a *prefix* already wider than *width*, disables wrapping
    and returns the inputs concatenated verbatim.
    """
    full = f"{prefix}{body}{suffix}"
    if width <= 0 or cell_len(full) <= width:
        return full

    indent = cell_len(prefix)
    avail = width - indent
    if avail <= 0:
        return full

    # textwrap counts in chars; it lines up with cell width as long as *body*
    # is ASCII (terok task names are).
    lines = textwrap.wrap(body, width=avail) or [""]
    if suffix:
        if cell_len(lines[-1]) + cell_len(suffix) <= avail:
            lines[-1] += suffix
        else:
            lines.append(suffix.lstrip())

    pad = " " * indent
    return prefix + ("\n" + pad).join(lines)
