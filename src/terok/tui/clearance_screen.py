# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""In-TUI clearance screen for live D-Bus shield verdict handling.

Provides a ``ClearanceScreen`` backed by ``terok_clearance.CallbackNotifier``
plugged into ``terok_clearance.EventSubscriber``.  The subscriber handles the
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

import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from terok_clearance import Notification

from rich.style import Style
from rich.text import Text
from textual import events, screen
from textual.app import App, ComposeResult
from textual.message import Message
from textual.widgets import Footer, ListItem, ListView, RichLog, Static

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

_ID_PENDING = "#pending-list"
_ID_EVENT_LOG = "#event-log"

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

    def __init__(
        self,
        nid: int,
        summary: str,
        body: str,
        actions: list[tuple[str, str]],
        replaces_id: int,
        container_id: str = "",
        container_name: str = "",
        project: str = "",
        task_id: str = "",
        task_name: str = "",
    ) -> None:
        """Store notification fields for the screen handler."""
        super().__init__()
        self.nid = nid
        self.summary = summary
        self.body = body
        self.actions = actions
        self.replaces_id = replaces_id
        self.container_id = container_id
        self.container_name = container_name
        self.project = project
        self.task_id = task_id
        self.task_name = task_name


class _LifecyclePosted(Message):
    """Posted when ``CallbackNotifier`` fires a ``ContainerStarted``/``Exited`` hook.

    Rendered in the scrolling event log below the pending list — lifecycle
    events are purely informational and don't belong in the verdict queue.
    """

    def __init__(self, event: str, container: str, reason: str = "") -> None:
        """Store the event kind (``started``/``exited``) and its args."""
        super().__init__()
        self.event = event
        self.container = container
        self.reason = reason


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _render_notification(message: _NotificationPosted) -> str:
    """Format a notification for the TUI log + pending list.

    The subscriber's body already does dossier-aware identity rendering
    via ``_identity_label`` (``project/task · name`` or fall back to the
    bare container slug) — the TUI just concatenates the title and body
    so every consumer of the same event renders identically.  An earlier
    iteration here recomposed ``Container: {name} ({id})`` from the
    notifier's typed kwargs and silently diverged from the desktop
    popup; one renderer, one source of truth, no divergence.
    """
    return f"{message.summary}  {message.body}"


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
    """

    def __init__(self) -> None:
        """Initialise clearance screen state."""
        super().__init__()
        self._notifier: Any = None  # CallbackNotifier
        self._subscriber: Any = None  # EventSubscriber
        self._pending: dict[int, _PendingRequest] = {}

    def _on_notify(self, notification: Notification) -> None:
        """Bridge ``CallbackNotifier`` hook into a Textual message."""
        self.post_message(
            _NotificationPosted(
                nid=notification.nid,
                summary=notification.summary,
                body=notification.body,
                actions=notification.actions,
                replaces_id=notification.replaces_id,
                container_id=notification.container_id,
                container_name=notification.container_name,
                project=notification.project,
                task_id=notification.task_id,
                task_name=notification.task_name,
            )
        )

    def _on_container_started(self, container: str) -> None:
        """Bridge ``ContainerStarted`` into a Textual message for the event log."""
        self.post_message(_LifecyclePosted(event="started", container=container))

    def _on_container_exited(self, container: str, reason: str) -> None:
        """Bridge ``ContainerExited`` into a Textual message for the event log."""
        self.post_message(_LifecyclePosted(event="exited", container=container, reason=reason))

    def compose(self) -> ComposeResult:
        """Build header, pending list, event log.

        The footer is *not* composed here: when this screen is pushed
        inside the host ``terok-tui`` app its parent ``Footer`` already
        renders the active screen's bindings, and doubling up would
        produce two footer bars.  The standalone [`ClearanceApp`][terok.tui.clearance_screen.ClearanceApp]
        composes its own ``Footer`` so the bindings still show.
        """
        yield Static(" Shield Clearance", id="clearance-header")
        pending = ListView(id="pending-list")
        pending.border_title = "Pending (0)"
        yield pending
        yield RichLog(auto_scroll=True, max_lines=1000, id="event-log")

    async def on_mount(self) -> None:
        """Connect to the clearance hub and start the event subscriber."""
        log = self.query_one(_ID_EVENT_LOG, RichLog)
        try:
            from terok_clearance import CallbackNotifier, EventSubscriber

            self._notifier = CallbackNotifier(
                on_notify=self._on_notify,
                on_container_started=self._on_container_started,
                on_container_exited=self._on_container_exited,
            )
            # Identity resolution is no longer a TUI concern: the shield
            # reader resolves the orchestrator dossier at emit time and
            # ships it on every event, so the subscriber just reads it.
            self._subscriber = EventSubscriber(self._notifier)
            await self._subscriber.start()
            log.write(Text("Connected to clearance hub...", style=_STYLE_INFO))
        except Exception as exc:
            _log.debug("clearance hub connection failed: %s", exc)
            log.write(Text(f"clearance hub unavailable: {exc}", style=_STYLE_ERROR))
            if self._subscriber:
                try:
                    await self._subscriber.stop()
                except Exception:
                    _log.debug("Failed to stop subscriber during error cleanup", exc_info=True)
            if self._notifier:
                try:
                    await self._notifier.disconnect()
                except Exception:
                    _log.debug("Failed to disconnect notifier during error cleanup", exc_info=True)
            self._notifier = None
            self._subscriber = None

    def on_app_focus(self, _event: events.AppFocus) -> None:
        """Cut short any reconnect back-off when the operator refocuses."""
        if self._subscriber is not None:
            with contextlib.suppress(Exception):
                self._subscriber.poke_reconnect()

    async def on_unmount(self) -> None:
        """Stop the subscriber and release resources."""
        try:
            if self._subscriber:
                await self._subscriber.stop()
        except Exception as exc:
            _log.debug("Failed to stop EventSubscriber: %s", exc)
        finally:
            if self._notifier:
                try:
                    await self._notifier.disconnect()
                except Exception as exc:
                    _log.debug("Failed to disconnect CallbackNotifier: %s", exc)

    # -- message handler --

    def on__notification_posted(self, message: _NotificationPosted) -> None:
        """Handle notifications from the CallbackNotifier."""
        try:
            log = self.query_one(_ID_EVENT_LOG, RichLog)
            pending_list = self.query_one(_ID_PENDING, ListView)
        except NoMatches:
            return

        rendered = _render_notification(message)
        if message.replaces_id and message.replaces_id in self._pending:
            # Verdict applied — remove from pending, log result
            del self._pending[message.replaces_id]
            self._remove_pending_item(message.replaces_id)
            style = _STYLE_ALLOWED if "Allowed" in message.summary else _STYLE_DENIED
            log.write(Text(rendered, style=style))
        elif message.actions:
            # New blocked connection — add to pending
            req = _PendingRequest(nid=message.nid, summary=message.summary, body=message.body)
            self._pending[message.nid] = req
            label = Static(rendered, markup=False)
            item = ListItem(label)
            item.clearance_nid = message.nid  # type: ignore[attr-defined]
            pending_list.append(item)
            # The style alone communicates "this is a block" — drop the redundant
            # "BLOCKED  " prefix that used to double up with the "Blocked:" title.
            log.write(Text(rendered, style=_STYLE_BLOCKED))
        else:
            # Informational (e.g. verdict details)
            log.write(Text(rendered, style=_STYLE_INFO))

        pending_list.border_title = f"Pending ({len(self._pending)})"

    def on__lifecycle_posted(self, message: _LifecyclePosted) -> None:
        """Render container-lifecycle events in the scrolling log."""
        try:
            log = self.query_one(_ID_EVENT_LOG, RichLog)
        except NoMatches:
            return
        if message.event == "started":
            log.write(Text(f"Container connected: {message.container}", style=_STYLE_INFO))
        else:
            tail = f" ({message.reason})" if message.reason else ""
            log.write(Text(f"Container gone: {message.container}{tail}", style=_STYLE_INFO))

    def _remove_pending_item(self, nid: int) -> None:
        """Remove the ``ListItem`` tagged with the given notification ID."""
        try:
            pending_list = self.query_one(_ID_PENDING, ListView)
        except NoMatches:
            return
        for idx in range(len(pending_list)):
            item = pending_list.children[idx]
            if getattr(item, "clearance_nid", None) == nid:
                item.remove()
                break

    # -- actions --

    def action_allow_selected(self) -> None:
        """Send an ``allow`` verdict for the highlighted pending request."""
        self._send_verdict("allow")

    def action_deny_selected(self) -> None:
        """Send a ``deny`` verdict for the highlighted pending request."""
        self._send_verdict("deny")

    def _send_verdict(self, action: str) -> None:
        """Invoke the notifier callback for the currently highlighted item."""
        if not self._notifier:
            return
        try:
            pending_list = self.query_one(_ID_PENDING, ListView)
        except NoMatches:
            return
        item = pending_list.highlighted_child
        if item is None:
            self.app.notify("No pending request selected.")
            return
        nid = getattr(item, "clearance_nid", None)
        if nid is None or nid not in self._pending:
            return
        try:
            self._notifier.invoke_action(nid, action)
        except Exception as exc:
            _log.debug("Failed to send %s verdict for %s: %s", action, nid, exc)
            self.app.notify(f"Failed to send verdict: {exc}")

    def action_dismiss_screen(self) -> None:
        """Close the clearance screen."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Standalone app
# ---------------------------------------------------------------------------


class ClearanceApp(App):
    """Minimal Textual app containing only the ClearanceScreen.

    The app-level ``Footer`` auto-renders the pushed screen's bindings, so
    operators see ``a Allow  x Deny  Esc Back`` without us maintaining a
    hand-written hint string.  The command palette (``^p``) is disabled —
    this tool's surface is four verdict keys, and a palette prompt would
    just confuse.
    """

    TITLE = "terok clearance"
    ENABLE_COMMAND_PALETTE = False

    def compose(self) -> ComposeResult:
        """Pair an app-level ``Footer`` with the pushed clearance screen."""
        yield Footer()

    def on_mount(self) -> None:
        """Push the clearance screen on startup."""
        self.push_screen(ClearanceScreen(), callback=lambda _: self.exit())


def main() -> None:
    """Entry point for ``terok clearance`` standalone command."""
    ClearanceApp().run()
