# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for TUI upstream polling logic."""

# Mock dependencies before importing app module
import sys
import time
from unittest import TestCase, main, mock

sys.modules["textual"] = mock.MagicMock()
sys.modules["textual.app"] = mock.MagicMock()
sys.modules["textual.widgets"] = mock.MagicMock()
sys.modules["textual.containers"] = mock.MagicMock()
sys.modules["textual.message"] = mock.MagicMock()
sys.modules["yaml"] = mock.MagicMock()

from terok.lib.security.git_gate import GateStalenessInfo
from test_utils import make_staleness_info


class MockProject:
    """Mock Project for testing."""

    def __init__(
        self,
        project_id: str = "test-proj",
        security_class: str = "gatekeeping",
        upstream_polling_enabled: bool = True,
        upstream_polling_interval_minutes: int = 5,
        auto_sync_enabled: bool = False,
        auto_sync_branches: list[str] | None = None,
    ) -> None:
        self.id = project_id
        self.security_class = security_class
        self.upstream_polling_enabled = upstream_polling_enabled
        self.upstream_polling_interval_minutes = upstream_polling_interval_minutes
        self.auto_sync_enabled = auto_sync_enabled
        self.auto_sync_branches = auto_sync_branches or []

        # Mock gate_path
        self.gate_path = mock.MagicMock()
        self.gate_path.exists.return_value = True


class PollingStateTests(TestCase):
    """Tests for polling state management logic (without full TUI)."""

    def test_staleness_notification_only_once(self):
        """Test that staleness notification only fires once per stale state."""
        # Simulate the notification logic from _on_staleness_updated
        last_notified_stale = False
        notifications = []

        def on_staleness_updated(staleness: GateStalenessInfo) -> None:
            nonlocal last_notified_stale

            if staleness.error:
                pass  # Don't change state on errors
            elif staleness.is_stale and not last_notified_stale:
                notifications.append(f"Gate is behind on {staleness.branch}")
                last_notified_stale = True
            elif not staleness.is_stale:
                last_notified_stale = False

        # First stale notification
        stale1 = make_staleness_info(commits_behind=3)
        on_staleness_updated(stale1)
        self.assertEqual(len(notifications), 1)
        self.assertTrue(last_notified_stale)

        # Second stale poll - should NOT notify again
        stale2 = make_staleness_info(upstream_head="ccc", commits_behind=5)
        on_staleness_updated(stale2)
        self.assertEqual(len(notifications), 1)  # Still 1

        # Up to date - should reset flag
        up_to_date = make_staleness_info(
            gate_head="ccc", upstream_head="ccc", is_stale=False, commits_behind=0
        )
        on_staleness_updated(up_to_date)
        self.assertEqual(len(notifications), 1)
        self.assertFalse(last_notified_stale)

        # Stale again - should notify
        stale3 = make_staleness_info(gate_head="ccc", upstream_head="ddd")
        on_staleness_updated(stale3)
        self.assertEqual(len(notifications), 2)

    def test_error_preserves_notification_state(self):
        """Test that errors don't reset the notification state."""
        last_notified_stale = True  # Was stale before

        def on_staleness_updated(staleness: GateStalenessInfo) -> None:
            nonlocal last_notified_stale

            if staleness.error:
                pass  # Preserve state
            elif staleness.is_stale and not last_notified_stale:
                last_notified_stale = True
            elif not staleness.is_stale:
                last_notified_stale = False

        # Error occurs - should preserve stale state
        error_result = make_staleness_info(
            upstream_head=None,
            is_stale=False,
            commits_behind=None,
            commits_ahead=None,
            error="Could not reach upstream",
        )
        on_staleness_updated(error_result)
        self.assertTrue(last_notified_stale)  # Preserved

    def test_auto_sync_cooldown(self):
        """Test that auto-sync respects cooldown period per project."""
        cooldown_dict = {}
        sync_calls = []

        def maybe_auto_sync(project_id: str) -> None:
            now = time.time()
            cooldown_until = cooldown_dict.get(project_id, 0)
            if now < cooldown_until:
                return  # Cooldown active

            # Set 5 minute cooldown for this project
            cooldown_dict[project_id] = now + 300
            sync_calls.append(project_id)

        # First sync should work
        maybe_auto_sync("proj1")
        self.assertEqual(len(sync_calls), 1)
        self.assertEqual(sync_calls[0], "proj1")

        # Second sync within cooldown should be skipped
        maybe_auto_sync("proj1")
        self.assertEqual(len(sync_calls), 1)

        # Different project should work (no shared cooldown)
        maybe_auto_sync("proj2")
        self.assertEqual(len(sync_calls), 2)
        self.assertEqual(sync_calls[1], "proj2")

        # After cooldown expires for proj1
        cooldown_dict["proj1"] = time.time() - 1  # Expired
        maybe_auto_sync("proj1")
        self.assertEqual(len(sync_calls), 3)
        self.assertEqual(sync_calls[2], "proj1")

    def test_only_reset_flag_when_up_to_date(self):
        """Test notification flag only resets when confirmed up-to-date."""
        last_notified_stale = True

        def sync_completed(staleness: GateStalenessInfo) -> None:
            nonlocal last_notified_stale
            # Only reset if actually up-to-date with no error
            if not staleness.is_stale and not staleness.error:
                last_notified_stale = False

        # Sync completed but still stale
        still_stale = make_staleness_info()
        sync_completed(still_stale)
        self.assertTrue(last_notified_stale)  # Not reset

        # Sync completed with error
        error_result = make_staleness_info(
            upstream_head=None,
            is_stale=False,
            commits_behind=None,
            commits_ahead=None,
            error="Network error",
        )
        sync_completed(error_result)
        self.assertTrue(last_notified_stale)  # Not reset

        # Sync completed and up-to-date
        up_to_date = make_staleness_info(
            gate_head="bbb", upstream_head="bbb", is_stale=False, commits_behind=0
        )
        sync_completed(up_to_date)
        self.assertFalse(last_notified_stale)  # Reset

    def test_project_switch_invalidates_poll(self):
        """Test that poll results are discarded if project changed."""
        current_project_id = "proj1"
        staleness_info = None

        def on_poll_complete(poll_project_id: str, staleness: GateStalenessInfo) -> None:
            nonlocal staleness_info
            # Validate project hasn't changed
            if poll_project_id != current_project_id:
                return  # Discard result
            staleness_info = staleness

        # Poll started for proj1
        poll_project = "proj1"

        # User switches to proj2 before poll completes
        current_project_id = "proj2"

        # Poll completes with proj1 result
        result = make_staleness_info(commits_behind=3)
        on_poll_complete(poll_project, result)

        # Result should be discarded
        self.assertIsNone(staleness_info)


class GateStalenessInfoTests(TestCase):
    """Tests for GateStalenessInfo dataclass."""

    def test_stale_state(self):
        """Test stale state attributes."""
        info = GateStalenessInfo(
            branch="main",
            gate_head="abc123",
            upstream_head="def456",
            is_stale=True,
            commits_behind=5,
            commits_ahead=0,
            last_checked="2024-01-01T00:00:00",
            error=None,
        )
        self.assertTrue(info.is_stale)
        self.assertEqual(info.commits_behind, 5)
        self.assertIsNone(info.error)

    def test_stale_ahead_state(self):
        """Test stale state when gate is ahead of upstream."""
        info = GateStalenessInfo(
            branch="main",
            gate_head="abc123",
            upstream_head="def456",
            is_stale=True,
            commits_behind=0,
            commits_ahead=3,
            last_checked="2024-01-01T00:00:00",
            error=None,
        )
        self.assertTrue(info.is_stale)
        self.assertEqual(info.commits_ahead, 3)
        self.assertEqual(info.commits_behind, 0)
        self.assertIsNone(info.error)

    def test_up_to_date_state(self):
        """Test up-to-date state attributes."""
        info = GateStalenessInfo(
            branch="main",
            gate_head="abc123",
            upstream_head="abc123",
            is_stale=False,
            commits_behind=0,
            commits_ahead=0,
            last_checked="2024-01-01T00:00:00",
            error=None,
        )
        self.assertFalse(info.is_stale)
        self.assertEqual(info.commits_behind, 0)

    def test_error_state(self):
        """Test error state attributes."""
        info = GateStalenessInfo(
            branch="main",
            gate_head="abc123",
            upstream_head=None,
            is_stale=False,
            commits_behind=None,
            commits_ahead=None,
            last_checked="2024-01-01T00:00:00",
            error="Could not reach upstream",
        )
        self.assertFalse(info.is_stale)
        self.assertIsNotNone(info.error)


if __name__ == "__main__":
    main()
