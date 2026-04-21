# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for :mod:`terok.lib.util.check_reporter`.

Covers the three things this helper actually has to get right for
``terok sickbay`` to behave: each line streams (no buffering until the
end), the dot padding lands at a fixed column, and the worst-status
aggregate follows the ``ok < info < warn < error`` ladder.  Grouped
output collapses to a one-liner on the all-ok path and expands into
indented failure detail when anything under the heading is non-ok.
"""

from __future__ import annotations

import io

import pytest

from terok.lib.util.check_reporter import (
    DEFAULT_LABEL_WIDTH,
    STATUS_MARKERS,
    CheckReporter,
    _worse,
)


@pytest.fixture
def buf() -> io.StringIO:
    """Capture reporter output without touching real stdout."""
    return io.StringIO()


@pytest.fixture
def reporter(buf: io.StringIO) -> CheckReporter:
    """Reporter writing into the captured buffer at default width."""
    return CheckReporter(stream=buf)


class TestWorstStatus:
    """Severity ordering used to drive the sickbay exit code."""

    @pytest.mark.parametrize(
        ("current", "new", "expected"),
        [
            ("ok", "ok", "ok"),
            ("ok", "info", "info"),
            ("info", "warn", "warn"),
            ("warn", "error", "error"),
            ("error", "ok", "error"),
            ("error", "warn", "error"),
            ("warn", "ok", "warn"),
        ],
    )
    def test_severity_ladder(self, current: str, new: str, expected: str) -> None:
        assert _worse(current, new) == expected

    def test_unknown_severity_treated_as_ok(self) -> None:
        """Unknown severity strings shouldn't upgrade a known one."""
        assert _worse("warn", "mystery") == "warn"


class TestEmit:
    """One-shot ``emit`` path — the common case for global checks."""

    def test_streams_label_before_status(self, buf: io.StringIO) -> None:
        """``begin`` flushes the label before the check runs."""
        reporter = CheckReporter(stream=buf)
        reporter.begin("Gate server")
        mid = buf.getvalue()
        # The label + dots must be on the buffer *before* end() runs.
        assert "Gate server" in mid
        assert "\n" not in mid  # still on an open line

        reporter.end("ok", "listening")
        final = buf.getvalue()
        assert final.endswith("ok (listening)\n")
        assert final.count("\n") == 1

    def test_marker_lands_after_dot_padding(self, buf: io.StringIO) -> None:
        """At default width, the status word starts at a predictable column."""
        reporter = CheckReporter(stream=buf)
        reporter.emit("ok", "Vault", "ready")
        out = buf.getvalue()
        # "  Vault " + dots-to-width + " ok (ready)\n" — the ok marker is
        # separated by exactly one space from the last dot.
        assert out.startswith("  Vault ")
        assert " ok (ready)\n" in out

    def test_empty_detail_omits_parens(self, buf: io.StringIO) -> None:
        reporter = CheckReporter(stream=buf)
        reporter.emit("ok", "Silent check", "")
        assert buf.getvalue().rstrip().endswith(" ok")

    def test_worst_status_updates(self, reporter: CheckReporter) -> None:
        reporter.emit("ok", "a", "")
        assert reporter.worst_status == "ok"
        reporter.emit("warn", "b", "")
        assert reporter.worst_status == "warn"
        reporter.emit("ok", "c", "")
        assert reporter.worst_status == "warn"  # doesn't downgrade
        reporter.emit("error", "d", "")
        assert reporter.worst_status == "error"


class TestDotPadding:
    """Label padding keeps status markers aligned on clean output."""

    def test_short_label_padded_to_width(self, buf: io.StringIO) -> None:
        """Short labels get enough dots to hit the configured column."""
        reporter = CheckReporter(stream=buf, width=40)
        reporter.emit("ok", "X", "done")
        line = buf.getvalue()
        # "  X " + dots + " ok (done)\n" — column count up to the space
        # before "ok" should equal 2 + label_len + 1 + dot_count + 1.
        assert line.index("ok (done)") == 2 + 1 + 1 + (40 - 1) + 1

    def test_overlong_label_gets_minimum_dots(self, buf: io.StringIO) -> None:
        """Labels longer than the width still render — with three dots."""
        reporter = CheckReporter(stream=buf, width=10)
        reporter.emit("ok", "A long label that exceeds width", "ok")
        line = buf.getvalue()
        # Minimum three dots, not a negative multiplier.
        assert " ... " in line
        assert line.rstrip().endswith(" ok (ok)")

    def test_default_width_constant(self) -> None:
        """The default width is the documented 60 columns."""
        assert DEFAULT_LABEL_WIDTH == 60


class TestGroupHappyPath:
    """All-ok groups collapse to a single summary line."""

    def test_all_ok_prints_single_summary(self, buf: io.StringIO) -> None:
        reporter = CheckReporter(stream=buf)
        with reporter.group("Credential files") as g:
            g.track("ok", "Credential file (claude)", "no credential file")
            g.track("ok", "Credential file (codex)", "no credential file")
            g.track("ok", "Credential file (gh)", "no credential file")
        out = buf.getvalue()
        # Exactly one newline — the summary line.
        assert out.count("\n") == 1
        assert "Credential files" in out
        assert "ok (3 checks)" in out

    def test_ok_group_does_not_expand_members(self, buf: io.StringIO) -> None:
        """Member details never appear on the all-ok path."""
        reporter = CheckReporter(stream=buf)
        with reporter.group("Bridges") as g:
            g.track("ok", "Bridge A", "alive")
            g.track("ok", "Bridge B", "alive")
        out = buf.getvalue()
        assert "alive" not in out  # details suppressed
        assert "Bridge A" not in out  # member labels suppressed

    def test_empty_group_still_closes_the_line(self, buf: io.StringIO) -> None:
        """A group with no members must terminate its open line."""
        reporter = CheckReporter(stream=buf)
        with reporter.group("Nothing to see"):
            pass
        out = buf.getvalue()
        assert out.endswith("\n")
        assert "0 checks" in out

    def test_group_promotes_worst_status(self, reporter: CheckReporter) -> None:
        with reporter.group("Group") as g:
            g.track("ok", "a", "")
            g.track("ok", "b", "")
        assert reporter.worst_status == "ok"


class TestGroupFailurePath:
    """Non-ok groups expand member failures under the heading."""

    def test_warn_member_expands_into_bullet(self, buf: io.StringIO) -> None:
        reporter = CheckReporter(stream=buf)
        with reporter.group("Phantom tokens") as g:
            g.track("ok", "Phantom token (GH_TOKEN)", "GH_TOKEN: phantom (gh)")
            g.track("warn", "Phantom token (SONAR_TOKEN)", "SONAR_TOKEN: not set")
            g.track("ok", "Phantom token (OPENAI_API_KEY)", "OPENAI_API_KEY: phantom (codex)")
        out = buf.getvalue()
        # Summary line mentions the counts; detail only for the failure.
        assert "WARN" in out
        assert "2 ok" in out and "1 warn" in out
        assert "SONAR_TOKEN: not set" in out
        # Ok members stay collapsed — their details never leak.
        assert "GH_TOKEN: phantom" not in out

    def test_error_member_summary_uses_error_marker(self, buf: io.StringIO) -> None:
        reporter = CheckReporter(stream=buf)
        with reporter.group("Credential files") as g:
            g.track("ok", "Credential file (codex)", "clean")
            g.track("error", "Credential file (claude)", "real API key detected")
        out = buf.getvalue()
        # Summary header carries the worst marker (error), even with only
        # one erroring member.
        assert "ERROR (1 error, 1 ok)" in out
        assert "real API key detected" in out
        assert reporter.worst_status == "error"

    def test_failure_lines_are_indented(self, buf: io.StringIO) -> None:
        reporter = CheckReporter(stream=buf)
        with reporter.group("Base URLs") as g:
            g.track("warn", "Base URL (ANTHROPIC_BASE_URL)", "not set — vault bypass possible")
        lines = buf.getvalue().splitlines()
        # First line is the heading; second is the indented detail.
        assert lines[0].lstrip().startswith("Base URLs")
        assert lines[1].startswith("    ")
        assert "not set" in lines[1]


class TestStatusMarkersConstant:
    """Public marker table exists for callers that want to mimic the format."""

    def test_markers_contain_expected_keys(self) -> None:
        assert set(STATUS_MARKERS) == {"ok", "info", "warn", "error"}
        assert STATUS_MARKERS["warn"] == "WARN"
        assert STATUS_MARKERS["error"] == "ERROR"
