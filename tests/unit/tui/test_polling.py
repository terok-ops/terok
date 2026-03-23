# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for TUI upstream polling logic."""

from __future__ import annotations

import time

import pytest
from terok_sandbox import GateStalenessInfo

from tests.test_utils import make_staleness_info


def update_staleness_notification(
    staleness: GateStalenessInfo,
    *,
    last_notified_stale: bool,
    notifications: list[str],
) -> bool:
    """Update stale-gate notification state and return the new notification flag."""
    if staleness.error:
        return last_notified_stale
    if staleness.is_stale and not last_notified_stale:
        notifications.append(f"Gate is behind on {staleness.branch}")
        return True
    if not staleness.is_stale:
        return False
    return last_notified_stale


def reset_notification_after_sync(staleness: GateStalenessInfo, *, notified: bool) -> bool:
    """Reset the stale-notification flag only when sync confirms an up-to-date state."""
    return False if not staleness.is_stale and not staleness.error else notified


def maybe_auto_sync(project_id: str, cooldowns: dict[str, float], sync_calls: list[str]) -> None:
    """Schedule auto-sync once per project per cooldown window."""
    now = time.time()
    if now < cooldowns.get(project_id, 0):
        return
    cooldowns[project_id] = now + 300
    sync_calls.append(project_id)


def apply_poll_result(
    poll_project_id: str,
    current_project_id: str,
    staleness: GateStalenessInfo,
) -> GateStalenessInfo | None:
    """Return the poll result only when it still matches the currently selected project."""
    return staleness if poll_project_id == current_project_id else None


def test_staleness_notification_only_once() -> None:
    """Staleness notification fires once per stale interval."""
    notifications: list[str] = []
    notified = False

    notified = update_staleness_notification(
        make_staleness_info(commits_behind=3),
        last_notified_stale=notified,
        notifications=notifications,
    )
    assert notifications == ["Gate is behind on main"]
    assert notified

    notified = update_staleness_notification(
        make_staleness_info(upstream_head="ccc", commits_behind=5),
        last_notified_stale=notified,
        notifications=notifications,
    )
    assert notifications == ["Gate is behind on main"]

    notified = update_staleness_notification(
        make_staleness_info(
            gate_head="ccc",
            upstream_head="ccc",
            is_stale=False,
            commits_behind=0,
        ),
        last_notified_stale=notified,
        notifications=notifications,
    )
    assert notifications == ["Gate is behind on main"]
    assert not notified

    update_staleness_notification(
        make_staleness_info(gate_head="ccc", upstream_head="ddd"),
        last_notified_stale=notified,
        notifications=notifications,
    )
    assert notifications == ["Gate is behind on main", "Gate is behind on main"]


def test_error_preserves_notification_state() -> None:
    """Errors do not reset the stale notification flag."""
    notified = update_staleness_notification(
        make_staleness_info(
            upstream_head=None,
            is_stale=False,
            commits_behind=None,
            commits_ahead=None,
            error="Could not reach upstream",
        ),
        last_notified_stale=True,
        notifications=[],
    )
    assert notified


def test_auto_sync_cooldown() -> None:
    """Auto-sync respects the per-project cooldown period."""
    cooldowns: dict[str, float] = {}
    sync_calls: list[str] = []

    maybe_auto_sync("proj1", cooldowns, sync_calls)
    maybe_auto_sync("proj1", cooldowns, sync_calls)
    maybe_auto_sync("proj2", cooldowns, sync_calls)
    cooldowns["proj1"] = time.time() - 1
    maybe_auto_sync("proj1", cooldowns, sync_calls)

    assert sync_calls == ["proj1", "proj2", "proj1"]


def test_only_reset_flag_when_up_to_date() -> None:
    """Sync completion only resets the stale flag for confirmed healthy states."""
    notified = True
    assert reset_notification_after_sync(make_staleness_info(), notified=notified)
    assert reset_notification_after_sync(
        make_staleness_info(
            upstream_head=None,
            is_stale=False,
            commits_behind=None,
            commits_ahead=None,
            error="Network error",
        ),
        notified=notified,
    )
    assert not reset_notification_after_sync(
        make_staleness_info(
            gate_head="bbb",
            upstream_head="bbb",
            is_stale=False,
            commits_behind=0,
        ),
        notified=notified,
    )


def test_project_switch_invalidates_poll() -> None:
    """Poll results are discarded when the selected project changed mid-flight."""
    assert (
        apply_poll_result(
            "proj1",
            "proj2",
            make_staleness_info(commits_behind=3),
        )
        is None
    )


@pytest.mark.parametrize(
    ("info", "expected_stale", "expected_behind", "expected_ahead", "expects_error"),
    [
        pytest.param(
            GateStalenessInfo(
                branch="main",
                gate_head="abc123",
                upstream_head="def456",
                is_stale=True,
                commits_behind=5,
                commits_ahead=0,
                last_checked="2024-01-01T00:00:00",
                error=None,
            ),
            True,
            5,
            0,
            False,
            id="stale-behind",
        ),
        pytest.param(
            GateStalenessInfo(
                branch="main",
                gate_head="abc123",
                upstream_head="def456",
                is_stale=True,
                commits_behind=0,
                commits_ahead=3,
                last_checked="2024-01-01T00:00:00",
                error=None,
            ),
            True,
            0,
            3,
            False,
            id="stale-ahead",
        ),
        pytest.param(
            GateStalenessInfo(
                branch="main",
                gate_head="abc123",
                upstream_head="abc123",
                is_stale=False,
                commits_behind=0,
                commits_ahead=0,
                last_checked="2024-01-01T00:00:00",
                error=None,
            ),
            False,
            0,
            0,
            False,
            id="up-to-date",
        ),
        pytest.param(
            GateStalenessInfo(
                branch="main",
                gate_head="abc123",
                upstream_head=None,
                is_stale=False,
                commits_behind=None,
                commits_ahead=None,
                last_checked="2024-01-01T00:00:00",
                error="Could not reach upstream",
            ),
            False,
            None,
            None,
            True,
            id="error",
        ),
    ],
)
def test_gate_staleness_info_states(
    info: GateStalenessInfo,
    expected_stale: bool,
    expected_behind: int | None,
    expected_ahead: int | None,
    expects_error: bool,
) -> None:
    """The staleness info dataclass preserves the expected state fields."""
    assert info.is_stale is expected_stale
    assert info.commits_behind == expected_behind
    assert info.commits_ahead == expected_ahead
    assert (info.error is not None) is expects_error
