# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for :class:`WorkerLogScreen` — the reusable subprocess-streaming modal.

Drives the screen via Textual's ``Pilot`` harness — spawns real
(short-lived) subprocesses rather than mocking ``asyncio.subprocess``
so the fd / pipe plumbing gets exercised end-to-end.
"""

from __future__ import annotations

import sys

import pytest
from textual.app import App
from textual.widgets import Button, RichLog

from terok.tui.worker_log_screen import WorkerLogScreen, WorkerResult

_SENTINEL_PENDING = object()


class _WorkerHost(App):
    """Minimal app that pushes a :class:`WorkerLogScreen` and stashes the result."""

    def __init__(self, argv: list[str]) -> None:
        super().__init__()
        self._argv = argv
        self.result: object = _SENTINEL_PENDING

    def on_mount(self) -> None:
        self.push_screen(WorkerLogScreen(self._argv, title="test"), self._capture)

    def _capture(self, result: object) -> None:
        self.result = result


async def _wait_until_close_enabled(pilot, timeout_ticks: int = 200) -> None:
    """Pump the event loop until the Close button enables (subprocess finished).

    ``Pilot.pause()`` processes one iteration of the Textual tick; 200
    * default pause (few ms each) gives us ~2–4 s of real wait, enough
    for even a cold Python startup + an echo.
    """
    for _ in range(timeout_ticks):
        await pilot.pause()
        button = pilot.app.screen.query_one("#worker-log-close", Button)
        if not button.disabled:
            return
    raise AssertionError("Close button never enabled — subprocess stuck or screen wedged")


# ── Happy path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_exit_streams_output_and_marks_done() -> None:
    """A clean subprocess completes, enables Close with ``success`` variant, captures stdout."""
    argv = [sys.executable, "-c", "print('hello from worker'); print('line two')"]
    app = _WorkerHost(argv)
    async with app.run_test() as pilot:
        await _wait_until_close_enabled(pilot)
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        # Both printed lines should have made it into the log widget.
        log = screen.query_one("#worker-log-output", RichLog)
        rendered = "\n".join(str(line) for line in log.lines)
        assert "hello from worker" in rendered
        assert "line two" in rendered
        # Close is green and relabelled "Done" when the run succeeded.
        button = screen.query_one("#worker-log-close", Button)
        assert button.variant == "success"
        assert str(button.label) == "Done"
        # Click Close and confirm the screen dismisses with exit 0.
        await pilot.click("#worker-log-close")
        await pilot.pause()
    assert isinstance(app.result, WorkerResult)
    assert app.result.exit_code == 0
    assert app.result.ok is True


@pytest.mark.asyncio
async def test_nonzero_exit_surfaces_exit_code_and_warns() -> None:
    """A subprocess that exits non-zero reports the code and styles Close as ``warning``."""
    argv = [sys.executable, "-c", "import sys; print('oops'); sys.exit(7)"]
    app = _WorkerHost(argv)
    async with app.run_test() as pilot:
        await _wait_until_close_enabled(pilot)
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        log = screen.query_one("#worker-log-output", RichLog)
        rendered = "\n".join(str(line) for line in log.lines)
        assert "oops" in rendered
        assert "exited with code 7" in rendered
        button = screen.query_one("#worker-log-close", Button)
        assert button.variant == "warning"
        await pilot.click("#worker-log-close")
        await pilot.pause()
    assert isinstance(app.result, WorkerResult)
    assert app.result.exit_code == 7
    assert app.result.ok is False


# ── Exec failure ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_executable_surfaces_in_log_with_exit_127() -> None:
    """Command-not-found is caught and printed — the modal doesn't freeze silently."""
    argv = ["/nonexistent/command/terok-never-existed", "--help"]
    app = _WorkerHost(argv)
    async with app.run_test() as pilot:
        await _wait_until_close_enabled(pilot)
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        log = screen.query_one("#worker-log-output", RichLog)
        rendered = "\n".join(str(line) for line in log.lines)
        assert "failed to launch" in rendered
        # 127 matches the shell convention for "command not found".
        await pilot.click("#worker-log-close")
        await pilot.pause()
    assert isinstance(app.result, WorkerResult)
    assert app.result.exit_code == 127
    assert app.result.ok is False


# ── Escape binding ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_escape_before_completion_is_ignored() -> None:
    """Escape mid-run can't dismiss — Close-button enablement is the only exit.

    Prevents an accidental Escape from killing the view of a long-
    running ``podman build`` / ``terok setup`` before it finishes.
    """
    # A subprocess that takes a moment — long enough to send Escape to.
    argv = [sys.executable, "-c", "import time; time.sleep(0.3); print('done')"]
    app = _WorkerHost(argv)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Subprocess is in flight; Escape action runs but should be a no-op.
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        screen.action_maybe_cancel()
        await pilot.pause()
        # The modal is still up, the Close button still disabled.
        assert isinstance(pilot.app.screen, WorkerLogScreen)
        assert pilot.app.screen.query_one("#worker-log-close", Button).disabled is True
        # Wait for natural completion, then dismiss.
        await _wait_until_close_enabled(pilot)
        await pilot.click("#worker-log-close")
        await pilot.pause()
    assert isinstance(app.result, WorkerResult)
    assert app.result.ok is True


# ── WorkerResult sanity ───────────────────────────────────────────────


def test_worker_result_ok_flag_matches_exit_zero() -> None:
    """``WorkerResult.ok`` is syntactic sugar for ``exit_code == 0``."""
    assert WorkerResult(exit_code=0).ok is True
    assert WorkerResult(exit_code=1).ok is False
    assert WorkerResult(exit_code=127).ok is False
    # Dismissed-before-completion sentinel is explicitly not ok.
    assert WorkerResult(exit_code=None).ok is False
