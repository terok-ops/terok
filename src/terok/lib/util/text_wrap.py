# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Hanging-indent wrapping for prefix-aligned labels.

Used by the TUI to wrap long task names at dash boundaries (and char
boundaries when a single segment overflows), keeping continuation lines
indented to where the body started after a fixed-width prefix.
"""

from __future__ import annotations

from rich.cells import cell_len


def _wrap_at_dashes(text: str, width: int) -> list[str]:
    """Split *text* into lines of at most *width* cells, breaking at dashes.

    Dashes stay attached to the segment on their left so a wrap point looks
    like "foo-\\nbar" rather than "foo\\n-bar". Segments longer than *width*
    fall back to character-folding.
    """
    if width <= 0 or cell_len(text) <= width:
        return [text]

    # Pair every segment with its cell width to keep this loop O(n).
    segments: list[tuple[str, int]] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch == "-":
            segments.append((buf, cell_len(buf)))
            buf = ""
    if buf:
        segments.append((buf, cell_len(buf)))

    lines: list[tuple[str, int]] = []
    line, line_w = "", 0
    for seg, seg_w in segments:
        if not line or line_w + seg_w <= width:
            line += seg
            line_w += seg_w
        else:
            lines.append((line, line_w))
            line, line_w = seg, seg_w
    if line:
        lines.append((line, line_w))

    folded: list[str] = []
    for ln, ln_w in lines:
        while ln_w > width:
            cut, taken = "", 0
            for ch in ln:
                w = cell_len(ch)
                if taken + w > width:
                    break
                cut += ch
                taken += w
            folded.append(cut)
            ln = ln[len(cut) :]
            ln_w -= taken
        folded.append(ln)
    return folded


def wrap_with_hanging_indent(prefix: str, body: str, suffix: str, width: int) -> str:
    """Render ``prefix + body + suffix`` with continuation lines hanging-indented.

    *body* wraps at dash boundaries (or anywhere, if a dashless segment is
    still too wide) so that each output line fits in *width* cells. Wrapped
    lines are prepended with spaces aligning to the cell width of *prefix*,
    so continuations sit underneath the start of *body*.

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

    body_lines = _wrap_at_dashes(body, avail)
    if suffix:
        last = body_lines[-1]
        if cell_len(last) + cell_len(suffix) <= avail:
            body_lines[-1] = last + suffix
        else:
            body_lines.append(suffix.lstrip())

    pad = " " * indent
    return prefix + body_lines[0] + "".join(f"\n{pad}{ln}" for ln in body_lines[1:])
