# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok.clearance.notifier.app`` — desktop-popup bridge entry point."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from terok.clearance.notifier import app as _app
from terok.clearance.notifier.app import _teardown, run_notifier


class TestRunNotifier:
    """``run_notifier`` brings up the subscriber, waits, and tears it down."""

    @pytest.mark.asyncio
    async def test_happy_path_connects_waits_then_teardown(self) -> None:
        """SIGTERM returns → clean teardown, no SystemExit.

        ``ClearanceClient`` now auto-reconnects on hub drops, so the
        notifier's own race against stream death is gone — the only
        thing ``run_notifier`` waits for is the shutdown signal.
        """
        notifier = AsyncMock()
        subscriber = AsyncMock()
        with (
            patch.object(_app, "configure_logging"),
            patch.object(_app, "create_notifier", AsyncMock(return_value=notifier)),
            patch.object(_app, "EventSubscriber", return_value=subscriber),
            patch.object(_app, "IdentityResolver"),
            patch.object(_app, "wait_for_shutdown_signal", AsyncMock()),
        ):
            await run_notifier()  # returns normally
        subscriber.start.assert_awaited_once()
        subscriber.stop.assert_awaited_once()
        notifier.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_subscriber_start_failure_exits_after_disconnect(self) -> None:
        """If ``subscriber.start()`` raises, the notifier is disconnected and we SystemExit."""
        notifier = AsyncMock()
        subscriber = AsyncMock()
        subscriber.start.side_effect = OSError("hub unreachable")
        with (
            patch.object(_app, "configure_logging"),
            patch.object(_app, "create_notifier", AsyncMock(return_value=notifier)),
            patch.object(_app, "EventSubscriber", return_value=subscriber),
            patch.object(_app, "IdentityResolver"),
        ):
            with pytest.raises(SystemExit) as excinfo:
                await run_notifier()
        assert excinfo.value.code == 1
        notifier.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_failure_during_error_path_is_swallowed(self) -> None:
        """A flaky session bus at error-time can't escape past the SystemExit(1)."""
        notifier = AsyncMock()
        notifier.disconnect.side_effect = RuntimeError("bus gone")
        subscriber = AsyncMock()
        subscriber.start.side_effect = OSError("hub unreachable")
        with (
            patch.object(_app, "configure_logging"),
            patch.object(_app, "create_notifier", AsyncMock(return_value=notifier)),
            patch.object(_app, "EventSubscriber", return_value=subscriber),
            patch.object(_app, "IdentityResolver"),
        ):
            with pytest.raises(SystemExit):
                await run_notifier()


class TestTeardown:
    """Cleanup helper — runs each step under its own per-step timeout."""

    @pytest.mark.asyncio
    async def test_stops_subscriber_then_notifier(self) -> None:
        subscriber = AsyncMock()
        notifier = AsyncMock()
        await _teardown(subscriber, notifier)
        subscriber.stop.assert_awaited_once()
        notifier.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step_failure_does_not_skip_remaining_steps(self) -> None:
        """A stuck subscriber can't leak the notifier connection."""
        subscriber = AsyncMock()
        subscriber.stop.side_effect = RuntimeError("stream wedged")
        notifier = AsyncMock()
        # Must not raise — teardown logs and carries on.
        await _teardown(subscriber, notifier)
        notifier.disconnect.assert_awaited_once()
