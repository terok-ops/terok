# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Streaming, dot-padded check reporter for ``terok sickbay`` and friends.

Replaces the collect-then-print-all pattern that made long diagnostic
runs look hung.  Each check emits its label eagerly (so the user sees
work is happening), runs, then stamps the same line with ``ok`` /
``WARN`` / ``ERROR``.

Grouped checks (e.g. one "Credential files" heading covering seven
per-agent credential probes) show the heading eagerly and collapse the
members into a single summary when every member passes — the detail
lines only appear when something fails.

Worst-status aggregation is built in: ``reporter.worst_status`` follows
the ``ok < info < warn < error`` ladder across every emission, so the
CLI can pick its exit code off a single object.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

#: Default label column — dots pad the label up to this width so ``ok``
#: or ``ERROR`` always lands at the same column on clean output.
DEFAULT_LABEL_WIDTH = 60

#: Human-friendly markers for each severity.
STATUS_MARKERS = {
    "ok": "ok",
    "info": "info",
    "warn": "WARN",
    "error": "ERROR",
}

#: Severity ordering for "worst status wins" aggregation.  Higher = worse.
_SEVERITY_RANK = {"ok": 0, "info": 1, "warn": 2, "error": 3}


def _worse(a: str, b: str) -> str:
    """Return the more severe of two severities (unknown → treat as ``ok``)."""
    return a if _SEVERITY_RANK.get(a, 0) >= _SEVERITY_RANK.get(b, 0) else b


class CheckReporter:
    """Print check progress line-by-line with aligned ``ok``/``WARN``/``ERROR`` markers.

    Use :meth:`emit` for one-shot checks whose label is known up front.
    Use :meth:`begin` + :meth:`end` when the detail is computed between
    showing the label and showing the verdict — the two halves land on
    the same terminal line.  Use :meth:`group` to batch a category of
    checks under a single heading.

    Writes go through an injectable *stream* so tests can capture output
    without touching stdout.
    """

    def __init__(self, *, width: int = DEFAULT_LABEL_WIDTH, stream=None) -> None:
        self._width = width
        self._stream = stream if stream is not None else sys.stdout
        self._worst = "ok"

    @property
    def worst_status(self) -> str:
        """The most severe status seen so far (``ok`` if nothing emitted yet)."""
        return self._worst

    # ------------------------------------------------------------------
    # Streaming primitives
    # ------------------------------------------------------------------

    def begin(self, label: str) -> None:
        """Emit ``  <label> ....`` without a trailing newline and flush.

        The caller is expected to follow with :meth:`end` on the same
        logical check — the status marker and detail land on the same
        visible line.
        """
        self._stream.write(f"  {label} {self._dots(label)} ")
        self._stream.flush()

    def end(self, status: str, detail: str) -> None:
        """Close the currently-open line started by :meth:`begin`.

        Updates :attr:`worst_status`.  ``detail`` is wrapped in
        parentheses — leave it empty for a bare marker.
        """
        self._worst = _worse(self._worst, status)
        marker = STATUS_MARKERS.get(status, status)
        if detail:
            self._stream.write(f"{marker} ({detail})\n")
        else:
            self._stream.write(f"{marker}\n")
        self._stream.flush()

    def emit(self, status: str, label: str, detail: str) -> None:
        """Shortcut for a check whose result is already known: ``begin`` then ``end``."""
        self.begin(label)
        self.end(status, detail)

    # ------------------------------------------------------------------
    # Grouped checks
    # ------------------------------------------------------------------

    @contextmanager
    def group(self, label: str) -> Iterator[_GroupContext]:
        """Emit a single heading line covering several related checks.

        The heading is printed eagerly (so the user knows work is
        happening); members run silently via ``ctx.add(status, detail)``
        or ``ctx.track(status, label, detail)``; on exit the reporter
        prints either ``ok (N checks)`` when every member passed or
        ``WARN/ERROR (...)`` followed by an indented list of every
        non-ok member.

        Example::

            with reporter.group("Credential files") as g:
                for check in credential_checks:
                    status, label, detail = run(check)
                    g.track(status, label, detail)
        """
        self._stream.write(f"  {label} {self._dots(label)} ")
        self._stream.flush()
        ctx = _GroupContext()
        try:
            yield ctx
        finally:
            self._close_group(ctx)

    def _close_group(self, ctx: _GroupContext) -> None:
        results = ctx.results
        if not results:
            # Empty group — defensively print a "skipped" marker so the
            # dangling line never stays open.
            self._stream.write("ok (0 checks)\n")
            self._stream.flush()
            return

        statuses = [s for s, _, _ in results]
        worst = statuses[0]
        for s in statuses[1:]:
            worst = _worse(worst, s)
        self._worst = _worse(self._worst, worst)

        if worst == "ok":
            self._stream.write(f"ok ({len(results)} checks)\n")
            self._stream.flush()
            return

        # Non-ok branch: summary counts in severity order, then detail
        # lines for every non-ok member.  Format is intentionally plain;
        # polish can come in a follow-up once we've lived with it.
        counts = []
        for sev in ("error", "warn", "info", "ok"):
            n = sum(1 for s in statuses if s == sev)
            if n:
                counts.append(f"{n} {sev}")
        marker = STATUS_MARKERS.get(worst, worst)
        self._stream.write(f"{marker} ({', '.join(counts)})\n")
        for status, _label, detail in results:
            if status != "ok":
                tag = STATUS_MARKERS.get(status, status)
                self._stream.write(f"    {tag}: {detail}\n")
        self._stream.flush()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dots(self, label: str) -> str:
        """Return the dot-run that pads *label* up to the target column."""
        # "  {label} {dots} " — two leading spaces, one space either side of
        # the dots, so the column after the dots is at (width + 4).  Keep a
        # three-dot minimum for labels that already exceed the width.
        return "." * max(3, self._width - len(label))


class _GroupContext:
    """Collector handed to the caller inside :meth:`CheckReporter.group`.

    Not part of the public API — callers receive it via the context
    manager and only use ``add`` / ``track``.
    """

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def add(self, status: str, detail: str) -> None:
        """Record a member result without a separate member label."""
        self.results.append((status, "", detail))

    def track(self, status: str, label: str, detail: str) -> None:
        """Record a member result, preserving the member's own label for failure listings."""
        self.results.append((status, label, detail))
