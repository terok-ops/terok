# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for TUI upstream polling logic."""

import time

from terok.lib.security.git_gate import GateStalenessInfo
from test_utils import make_staleness_info


class TestPollingState:
    """Tests for polling state management logic (without full TUI)."""

    def test_staleness_notification_only_once(self) -> None:
        """Test that staleness notification only fires once per stale state."""
        last_notified_stale = False
        notifications: list[str] = []

        def on_staleness_updated(staleness: GateStalenessInfo) -> None:
            nonlocal last_notified_stale

            if staleness.error:
                return
            if staleness.is_stale and not last_notified_stale:
                notifications.append(f"Gate is behind on {staleness.branch}")
                last_notified_stale = True
            elif not staleness.is_stale:
                last_notified_stale = False

        on_staleness_updated(make_staleness_info(commits_behind=3))
        assert len(notifications) == 1
        assert last_notified_stale

        on_staleness_updated(make_staleness_info(upstream_head="ccc", commits_behind=5))
        assert len(notifications) == 1

        on_staleness_updated(
            make_staleness_info(
                gate_head="ccc",
                upstream_head="ccc",
                is_stale=False,
                commits_behind=0,
            )
        )
        assert len(notifications) == 1
        assert not last_notified_stale

        on_staleness_updated(make_staleness_info(gate_head="ccc", upstream_head="ddd"))
        assert len(notifications) == 2

    def test_error_preserves_notification_state(self) -> None:
        """Test that errors don't reset the notification state."""
        last_notified_stale = True

        def on_staleness_updated(staleness: GateStalenessInfo) -> None:
            nonlocal last_notified_stale

            if staleness.error:
                return
            if staleness.is_stale and not last_notified_stale:
                last_notified_stale = True
            elif not staleness.is_stale:
                last_notified_stale = False

        on_staleness_updated(
            make_staleness_info(
                upstream_head=None,
                is_stale=False,
                commits_behind=None,
                commits_ahead=None,
                error="Could not reach upstream",
            )
        )
        assert last_notified_stale

    def test_auto_sync_cooldown(self) -> None:
        """Test that auto-sync respects cooldown period per project."""
        cooldown_dict: dict[str, float] = {}
        sync_calls: list[str] = []

        def maybe_auto_sync(project_id: str) -> None:
            now = time.time()
            cooldown_until = cooldown_dict.get(project_id, 0)
            if now < cooldown_until:
                return

            cooldown_dict[project_id] = now + 300
            sync_calls.append(project_id)

        maybe_auto_sync("proj1")
        assert sync_calls == ["proj1"]

        maybe_auto_sync("proj1")
        assert sync_calls == ["proj1"]

        maybe_auto_sync("proj2")
        assert sync_calls == ["proj1", "proj2"]

        cooldown_dict["proj1"] = time.time() - 1
        maybe_auto_sync("proj1")
        assert sync_calls == ["proj1", "proj2", "proj1"]

    def test_only_reset_flag_when_up_to_date(self) -> None:
        """Test notification flag only resets when confirmed up-to-date."""
        last_notified_stale = True

        def sync_completed(staleness: GateStalenessInfo) -> None:
            nonlocal last_notified_stale
            if not staleness.is_stale and not staleness.error:
                last_notified_stale = False

        sync_completed(make_staleness_info())
        assert last_notified_stale

        sync_completed(
            make_staleness_info(
                upstream_head=None,
                is_stale=False,
                commits_behind=None,
                commits_ahead=None,
                error="Network error",
            )
        )
        assert last_notified_stale

        sync_completed(
            make_staleness_info(
                gate_head="bbb",
                upstream_head="bbb",
                is_stale=False,
                commits_behind=0,
            )
        )
        assert not last_notified_stale

    def test_project_switch_invalidates_poll(self) -> None:
        """Test that poll results are discarded if project changed."""
        current_project_id = "proj1"
        staleness_info = None

        def on_poll_complete(poll_project_id: str, staleness: GateStalenessInfo) -> None:
            nonlocal staleness_info
            if poll_project_id != current_project_id:
                return
            staleness_info = staleness

        poll_project = "proj1"
        current_project_id = "proj2"
        on_poll_complete(poll_project, make_staleness_info(commits_behind=3))

        assert staleness_info is None


class TestGateStalenessInfo:
    """Tests for GateStalenessInfo dataclass."""

    def test_stale_state(self) -> None:
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
        assert info.is_stale
        assert info.commits_behind == 5
        assert info.error is None

    def test_stale_ahead_state(self) -> None:
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
        assert info.is_stale
        assert info.commits_ahead == 3
        assert info.commits_behind == 0
        assert info.error is None

    def test_up_to_date_state(self) -> None:
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
        assert not info.is_stale
        assert info.commits_behind == 0

    def test_error_state(self) -> None:
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
        assert not info.is_stale
        assert info.error is not None
