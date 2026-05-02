# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI clearance screen and CLI/TUI integration."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any
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

        from terok_clearance import Notification

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

        from terok_clearance import Notification

        n = Notification(
            nid=2,
            summary="Blocked",
            body="c1",
            actions=[("allow", "Allow")],
            replaces_id=0,
            timeout_ms=0,
        )
        screen._on_notify(n)
        msg = screen.post_message.call_args[0][0]
        assert msg.actions == [("allow", "Allow")]

    def test_on_notify_replaces_id(self) -> None:
        """_on_notify forwards replaces_id for verdict updates."""
        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()

        from terok_clearance import Notification

        n = Notification(
            nid=1, summary="Allowed", body="", actions=[], replaces_id=1, timeout_ms=5000
        )
        screen._on_notify(n)
        msg = screen.post_message.call_args[0][0]
        assert msg.replaces_id == 1

    def test_callback_notifier_wired_to_on_notify(self) -> None:
        """CallbackNotifier's on_notify hook invokes _on_notify."""
        from terok_clearance import CallbackNotifier

        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()
        notifier = CallbackNotifier(on_notify=screen._on_notify)
        asyncio.run(notifier.notify("Test", "Body"))
        screen.post_message.assert_called_once()


class TestLifecycleBridge:
    """Container lifecycle signals land on the event log, not the pending list."""

    def test_on_container_started_posts_lifecycle_message(self) -> None:
        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()
        screen._on_container_started("abc123")
        screen.post_message.assert_called_once()
        msg = screen.post_message.call_args[0][0]
        assert msg.event == "started"
        assert msg.container == "abc123"
        assert msg.reason == ""

    def test_on_container_exited_posts_lifecycle_message(self) -> None:
        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()
        screen._on_container_exited("abc123", "poststop")
        msg = screen.post_message.call_args[0][0]
        assert msg.event == "exited"
        assert msg.container == "abc123"
        assert msg.reason == "poststop"

    def test_callback_notifier_wires_lifecycle_hooks(self) -> None:
        """CallbackNotifier forwards both lifecycle hooks back into the screen."""
        from terok_clearance import CallbackNotifier

        mod = _import_clearance()
        screen = mod.ClearanceScreen()
        screen.post_message = mock.Mock()
        notifier = CallbackNotifier(
            on_container_started=screen._on_container_started,
            on_container_exited=screen._on_container_exited,
        )
        notifier.on_container_started("abc123")
        notifier.on_container_exited("abc123", "poststop")
        assert screen.post_message.call_count == 2


class TestRenderNotification:
    """``_render_notification`` is a one-liner that joins ``summary`` + subscriber body.

    The subscriber's body already does dossier-aware identity rendering —
    ``Task: project/task · name`` for orchestrator-managed containers,
    ``Container: <slug>`` otherwise — so the TUI must not recompose its
    own identity string.  An earlier iteration here built ``Container:
    {name} ({id})`` from the typed kwargs and silently diverged from the
    desktop popup.  These tests pin the no-divergence contract: whatever
    the subscriber wrote in ``body`` is what the operator sees.
    """

    def test_passes_subscriber_body_through_verbatim(self) -> None:
        """The dossier-aware body the subscriber composed lands in the log unchanged."""
        mod = _import_clearance()
        msg = mod._NotificationPosted(
            nid=1,
            summary="Blocked: seznam.cz:80",
            body="Task: terok/abc · diligent-octopus\nProtocol: TCP",
            actions=[("allow", "Allow"), ("deny", "Deny")],
            replaces_id=0,
            container_id="fa0905d97a1c",
            container_name="terok-cli-abc",
            project="terok",
            task_id="abc",
            task_name="diligent-octopus",
        )
        assert (
            mod._render_notification(msg)
            == "Blocked: seznam.cz:80  Task: terok/abc · diligent-octopus\nProtocol: TCP"
        )

    def test_bare_container_body_passes_through_too(self) -> None:
        """Standalone container path: the subscriber's bare-name body reaches the log as-is."""
        mod = _import_clearance()
        msg = mod._NotificationPosted(
            nid=1,
            summary="Blocked: 1.2.3.4:443",
            body="Container: fa0905d97a1c\nProtocol: TCP",
            actions=[("allow", "Allow")],
            replaces_id=0,
            container_id="fa0905d97a1c",
            container_name="",
        )
        assert (
            mod._render_notification(msg)
            == "Blocked: 1.2.3.4:443  Container: fa0905d97a1c\nProtocol: TCP"
        )


class TestOnNotificationPosted:
    """``on__notification_posted`` routes new blocks vs verdict updates vs info."""

    def _screen_with_mocked_queries(self, mod: Any) -> tuple[Any, mock.Mock, mock.Mock]:
        """Return a screen whose query_one returns (log, pending_list) mocks.

        Unknown selectors are a test error — a production-code typo must
        fail loudly rather than silently receive ``pending_list`` and
        appear to work.
        """
        screen = mod.ClearanceScreen()
        log = mock.Mock()
        pending_list = mock.Mock()
        pending_list.border_title = ""

        def _query_one(sel: str, *_args: Any, **_kwargs: Any) -> mock.Mock:
            if sel == "#event-log":
                return log
            if sel == "#pending-list":
                return pending_list
            raise AssertionError(f"unexpected selector: {sel!r}")

        screen.query_one = mock.Mock(side_effect=_query_one)
        return screen, log, pending_list

    def test_new_block_writes_to_log_and_pending_list(self) -> None:
        """A notification with actions queues onto the pending list and logs once."""
        mod = _import_clearance()
        screen, log, pending_list = self._screen_with_mocked_queries(mod)
        msg = mod._NotificationPosted(
            nid=42,
            summary="Blocked: seznam.cz:80",
            body="Container: my-task\nProtocol: TCP",
            actions=[("allow", "Allow"), ("deny", "Deny")],
            replaces_id=0,
            container_id="fa0905d97a1c",
            container_name="my-task",
        )
        screen.on__notification_posted(msg)
        assert 42 in screen._pending
        pending_list.append.assert_called_once()
        log.write.assert_called_once()
        assert "Pending (1)" in pending_list.border_title

    def test_verdict_applied_clears_pending_and_logs(self) -> None:
        """A notification with ``replaces_id`` matching a pending entry resolves it."""
        mod = _import_clearance()
        screen, log, pending_list = self._screen_with_mocked_queries(mod)
        screen._pending[42] = mod._PendingRequest(nid=42, summary="s", body="b")
        screen._remove_pending_item = mock.Mock()
        msg = mod._NotificationPosted(
            nid=42,
            summary="Allowed: seznam.cz",
            body="Container: my-task",
            actions=[],
            replaces_id=42,
            container_id="fa0905d97a1c",
            container_name="my-task",
        )
        screen.on__notification_posted(msg)
        assert 42 not in screen._pending
        screen._remove_pending_item.assert_called_once_with(42)
        log.write.assert_called_once()

    def test_informational_message_only_logs(self) -> None:
        """No actions + no replaces_id → informational line in the log only."""
        mod = _import_clearance()
        screen, log, pending_list = self._screen_with_mocked_queries(mod)
        msg = mod._NotificationPosted(
            nid=1, summary="Info", body="Details", actions=[], replaces_id=0
        )
        screen.on__notification_posted(msg)
        pending_list.append.assert_not_called()
        log.write.assert_called_once()


class TestClearanceAppFooter:
    """Standalone app composes a Footer and disables the command palette."""

    def test_compose_yields_footer(self) -> None:
        """``ClearanceApp.compose`` yields a Footer widget for the bindings bar."""
        mod = _import_clearance()
        app = mod.ClearanceApp()
        yielded = list(app.compose())
        assert len(yielded) == 1
        # Class name compared by string because the module imports against
        # Textual stubs (see tests/unit/tui/tui_test_helpers.py).
        assert type(yielded[0]).__name__ == "Footer"

    def test_command_palette_disabled(self) -> None:
        """The clearance app turns off the ``^p`` palette binding entirely."""
        mod = _import_clearance()
        assert mod.ClearanceApp.ENABLE_COMMAND_PALETTE is False


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

    def test_dispatch_execs_terok_clearance(self) -> None:
        """Dispatch execs the terok-clearance entry point."""
        with mock.patch("os.execlp") as execlp:
            dispatch(argparse.Namespace(cmd="clearance"))
            execlp.assert_called_once_with("terok-clearance", "terok-clearance")


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
