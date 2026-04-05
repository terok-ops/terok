# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""In-TUI clearance screen for live D-Bus shield verdict handling.

Provides a ``ClearanceScreen`` backed by ``terok_dbus.CallbackNotifier``
plugged into ``terok_dbus.EventSubscriber``.  The subscriber handles the
full signal-to-verdict cycle; the callback notifier bridges D-Bus events
into Textual messages so the screen can render blocked connections and
route operator Allow/Deny actions back through D-Bus.

The screen listens on the whole session bus — all containers' events are
shown, with the container name displayed prominently on every row.

Dual use:

* **Embedded** — pushed as a screen inside ``terok-tui``.
* **Standalone** — ``terok clearance`` launches ``ClearanceApp``, a
  minimal Textual app containing only this screen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rich.style import Style
from rich.text import Text
from textual import screen
from textual.app import App, ComposeResult
from textual.message import Message
from textual.widgets import ListItem, ListView, RichLog, Static

from .screens import _modal_binding

try:  # pragma: no cover - optional import for test stubs
    from textual.css.query import NoMatches
except Exception:  # pragma: no cover - textual may be a stub module
    NoMatches = Exception  # type: ignore[assignment,misc]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_STYLE_BLOCKED = Style(color="yellow", bold=True)
_STYLE_ALLOWED = Style(color="green")
_STYLE_DENIED = Style(color="red")
_STYLE_INFO = Style(color="blue")
_STYLE_ERROR = Style(color="red", bold=True)

# ---------------------------------------------------------------------------
# Internal messages
# ---------------------------------------------------------------------------


@dataclass
class _PendingRequest:
    """A blocked connection awaiting operator verdict."""

    nid: int
    summary: str
    body: str


class _NotificationPosted(Message):
    """Posted when ``CallbackNotifier`` fires its ``on_notify`` hook."""

    def __init__(self, nid: int, summary: str, body: str, actions: list, replaces_id: int) -> None:
        """Store notification fields for the screen handler."""
        super().__init__()
        self.nid = nid
        self.summary = summary
        self.body = body
        self.actions = actions
        self.replaces_id = replaces_id


# ---------------------------------------------------------------------------
# ClearanceScreen
# ---------------------------------------------------------------------------


class ClearanceScreen(screen.Screen[None]):
    """Full-page screen for live D-Bus shield clearance verdicts."""

    BINDINGS = [
        _modal_binding("escape", "dismiss_screen", "Back"),
        _modal_binding("q", "dismiss_screen", "Back"),
        _modal_binding("a", "allow_selected", "Allow"),
        _modal_binding("x", "deny_selected", "Deny"),
    ]

    CSS = """
    ClearanceScreen {
        layout: vertical;
        background: $background;
    }
    #clearance-header {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    #pending-list {
        height: auto;
        max-height: 40%;
        border: round $primary;
        border-title-align: right;
        background: $surface;
    }
    #event-log {
        height: 1fr;
    }
    #clearance-footer {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        """Initialise clearance screen state."""
        super().__init__()
        self._notifier: Any = None  # CallbackNotifier
        self._subscriber: Any = None  # EventSubscriber
        self._pending: dict[int, _PendingRequest] = {}

    def _on_notify(self, notification: Any) -> None:
        """Bridge ``CallbackNotifier`` hook into a Textual message."""
        self.post_message(
            _NotificationPosted(
                nid=notification.nid,
                summary=notification.summary,
                body=notification.body,
                actions=notification.actions,
                replaces_id=notification.replaces_id,
            )
        )

    def compose(self) -> ComposeResult:
        """Build header, pending list, event log, and footer."""
        yield Static(" Shield Clearance", id="clearance-header")
        pending = ListView(id="pending-list")
        pending.border_title = "Pending (0)"
        yield pending
        yield RichLog(auto_scroll=True, id="event-log")
        yield Static(
            " \\[a] Allow  \\[x] Deny  \\[Esc/q] Back",
            id="clearance-footer",
        )

    async def on_mount(self) -> None:
        """Connect to the D-Bus session bus and start the event subscriber."""
        from terok_dbus import CallbackNotifier, EventSubscriber

        self._notifier = CallbackNotifier(on_notify=self._on_notify)
        log = self.query_one("#event-log", RichLog)
        try:
            self._subscriber = EventSubscriber(self._notifier)
            await self._subscriber.start()
            log.write(Text("Listening on session bus...", style=_STYLE_INFO))
        except Exception as exc:
            _log.debug("D-Bus connection failed: %s", exc)
            log.write(Text(f"D-Bus unavailable: {exc}", style=_STYLE_ERROR))
            self._subscriber = None

    async def on_unmount(self) -> None:
        """Stop the subscriber and release resources."""
        if self._subscriber:
            await self._subscriber.stop()
        if self._notifier:
            await self._notifier.disconnect()

    # -- message handler --

    def on__notification_posted(self, message: _NotificationPosted) -> None:
        """Handle notifications from the CallbackNotifier."""
        try:
            log = self.query_one("#event-log", RichLog)
            pending_list = self.query_one("#pending-list", ListView)
        except NoMatches:
            return

        if message.replaces_id and message.replaces_id in self._pending:
            # Verdict applied — remove from pending, log result
            del self._pending[message.replaces_id]
            self._remove_pending_item(message.replaces_id)
            style = _STYLE_ALLOWED if "Allowed" in message.summary else _STYLE_DENIED
            log.write(Text(f"{message.summary}  {message.body}", style=style))
        elif message.actions:
            # New blocked connection — add to pending
            req = _PendingRequest(nid=message.nid, summary=message.summary, body=message.body)
            self._pending[message.nid] = req
            label = Static(f"[{message.nid}]  {message.summary}  {message.body}")
            item = ListItem(label)
            item.clearance_nid = message.nid  # type: ignore[attr-defined]
            pending_list.append(item)
            log.write(Text(f"BLOCKED  {message.summary}  {message.body}", style=_STYLE_BLOCKED))
        else:
            # Informational (e.g. verdict details)
            log.write(Text(f"{message.summary}  {message.body}", style=_STYLE_INFO))

        pending_list.border_title = f"Pending ({len(self._pending)})"

    def _remove_pending_item(self, nid: int) -> None:
        """Remove the ``ListItem`` tagged with the given notification ID."""
        try:
            pending_list = self.query_one("#pending-list", ListView)
        except NoMatches:
            return
        for idx in range(len(pending_list)):
            item = pending_list.children[idx]
            if getattr(item, "clearance_nid", None) == nid:
                item.remove()
                break

    # -- actions --

    def action_allow_selected(self) -> None:
        """Send an ``accept`` verdict for the highlighted pending request."""
        self._send_verdict("accept")

    def action_deny_selected(self) -> None:
        """Send a ``deny`` verdict for the highlighted pending request."""
        self._send_verdict("deny")

    def _send_verdict(self, action: str) -> None:
        """Invoke the notifier callback for the currently highlighted item."""
        if not self._notifier:
            return
        try:
            pending_list = self.query_one("#pending-list", ListView)
        except NoMatches:
            return
        item = pending_list.highlighted_child
        if item is None:
            self.app.notify("No pending request selected.")
            return
        nid = getattr(item, "clearance_nid", None)
        if nid is None or nid not in self._pending:
            return
        self._notifier.invoke_action(nid, action)

    def action_dismiss_screen(self) -> None:
        """Close the clearance screen."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Standalone app
# ---------------------------------------------------------------------------


class ClearanceApp(App):
    """Minimal Textual app containing only the ClearanceScreen."""

    TITLE = "terok clearance"

    def on_mount(self) -> None:
        """Push the clearance screen on startup."""
        self.push_screen(ClearanceScreen())


def main() -> None:
    """Entry point for ``terok clearance`` standalone command."""
    ClearanceApp().run()
