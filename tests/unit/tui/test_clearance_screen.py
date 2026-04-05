# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI clearance screen and CLI/TUI integration."""

from __future__ import annotations

import argparse
import asyncio
from unittest import mock

from terok.cli.commands.clearance import dispatch, register
from tests.unit.tui.tui_test_helpers import (
    _import_with_stubs,
    import_app,
    import_screens,
    make_key_event,
)


def _import_clearance():
    """Import clearance_screen module with Textual stubs."""
    return _import_with_stubs(None, "terok.tui.clearance_screen")[0]


# ---------------------------------------------------------------------------
# CallbackNotifier integration (on_notify bridge)
# ---------------------------------------------------------------------------


class TestNotifyBridge:
    """Tests for the ClearanceScreen._on_notify → post_message bridge."""

    def test_on_notify_posts_message(self) -> None:
        """_on_notify posts a _NotificationPosted message to the screen."""
        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()

        from terok_dbus import Notification

        n = Notification(nid=1, summary="S", body="B", actions=[], replaces_id=0, timeout_ms=-1)
        screen._on_notify(n)
        screen.post_message.assert_called_once()
        msg = screen.post_message.call_args[0][0]
        assert msg.nid == 1
        assert msg.summary == "S"
        assert msg.body == "B"

    def test_on_notify_with_actions(self) -> None:
        """_on_notify preserves action tuples."""
        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()

        from terok_dbus import Notification

        n = Notification(
            nid=2,
            summary="Blocked",
            body="c1",
            actions=[("accept", "Allow")],
            replaces_id=0,
            timeout_ms=0,
        )
        screen._on_notify(n)
        msg = screen.post_message.call_args[0][0]
        assert msg.actions == [("accept", "Allow")]

    def test_on_notify_replaces_id(self) -> None:
        """_on_notify forwards replaces_id for verdict updates."""
        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()

        from terok_dbus import Notification

        n = Notification(
            nid=1, summary="Allowed", body="", actions=[], replaces_id=1, timeout_ms=5000
        )
        screen._on_notify(n)
        msg = screen.post_message.call_args[0][0]
        assert msg.replaces_id == 1

    def test_callback_notifier_wired_to_on_notify(self) -> None:
        """CallbackNotifier's on_notify hook invokes _on_notify."""
        from terok_dbus import CallbackNotifier

        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()
        notifier = CallbackNotifier(on_notify=screen._on_notify)
        asyncio.run(notifier.notify("Test", "Body"))
        screen.post_message.assert_called_once()


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestClearanceCLI:
    """Tests for the terok clearance CLI command."""

    def test_register_creates_subparser(self) -> None:
        """The clearance subcommand registers without error."""
        parser = argparse.ArgumentParser()
        register(parser.add_subparsers(dest="cmd"))
        args = parser.parse_args(["clearance"])
        assert args.cmd == "clearance"

    def test_dispatch_returns_false_for_other_commands(self) -> None:
        """Dispatch ignores non-clearance commands."""
        assert not dispatch(argparse.Namespace(cmd="project"))

    def test_dispatch_returns_true_for_clearance(self) -> None:
        """Dispatch launches the clearance app for cmd=clearance."""
        with mock.patch("terok.tui.clearance_screen.main"):
            assert dispatch(argparse.Namespace(cmd="clearance"))


# ---------------------------------------------------------------------------
# TUI integration
# ---------------------------------------------------------------------------


class TestClearanceTUIIntegration:
    """Tests for clearance wiring into the existing TUI."""

    def test_task_action_handlers_includes_show_clearance(self) -> None:
        """TASK_ACTION_HANDLERS maps show_clearance to the correct method."""
        app_mod, _ = import_app()
        assert "show_clearance" in app_mod.TASK_ACTION_HANDLERS
        assert app_mod.TASK_ACTION_HANDLERS["show_clearance"] == "action_show_clearance"

    def test_task_details_shift_c_dismisses_show_clearance(self) -> None:
        """Pressing C on TaskDetailsScreen dismisses with show_clearance."""
        screens, widgets = import_screens()
        task = widgets.TaskMeta(
            task_id="1", mode="cli", workspace="/w", web_port=None, container_state="running"
        )
        screen = screens.TaskDetailsScreen(task=task, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        screen.on_key(make_key_event("C"))
        screen.dismiss.assert_called_once_with("show_clearance")

    def test_task_details_shift_c_noop_without_tasks(self) -> None:
        """Pressing C without tasks does nothing."""
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        screen.on_key(make_key_event("C"))
        screen.dismiss.assert_not_called()
