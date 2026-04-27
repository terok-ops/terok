# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Panic button widget with arm-then-fire safety mechanism.

The button sits in the main layout but is excluded from the Tab focus chain
(``can_focus = False``).  A single click arms it (visual change + 5-second
auto-disarm timer); a second click fires the emergency panic sequence.
"""

from textual.message import Message
from textual.widgets import Static

_LABEL_IDLE = "PANIC"
_LABEL_ARMED = "PRESS AGAIN TO PANIC"
_DISARM_SECONDS = 5.0


class PanicButton(Static):
    """Clickable emergency button with arm-then-fire two-phase activation."""

    can_focus = False

    DEFAULT_CSS = """
    PanicButton {
        height: 3;
        margin-top: 1;
        content-align: center middle;
        text-align: center;
        background: $error;
        color: white;
        text-style: bold;
    }

    PanicButton.armed {
        background: darkred;
        text-style: bold reverse;
    }
    """

    class Fired(Message):
        """Posted when the arm-then-fire sequence completes."""

    def __init__(self, **kwargs: object) -> None:
        """Initialize in idle (disarmed) state."""
        super().__init__(**kwargs)
        self._armed = False
        self._disarm_timer = None

    def on_mount(self) -> None:
        """Set the idle label once the widget is mounted."""
        self.update(_LABEL_IDLE)

    def arm(self) -> None:
        """Transition from idle to armed state with auto-disarm timer."""
        if self._armed:
            return
        self._armed = True
        self.add_class("armed")
        self.update(_LABEL_ARMED)
        self._disarm_timer = self.set_timer(_DISARM_SECONDS, self.disarm)

    def disarm(self) -> None:
        """Return to idle state, cancelling any pending timer."""
        if not self._armed:
            return
        self._armed = False
        self.remove_class("armed")
        self.update(_LABEL_IDLE)
        if self._disarm_timer is not None:
            self._disarm_timer.stop()
            self._disarm_timer = None

    def fire(self) -> None:
        """Complete the arm-then-fire sequence: disarm and emit [`Fired`][]."""
        self.disarm()
        self.post_message(self.Fired())

    def on_click(self) -> None:
        """Handle mouse clicks: arm on first click, fire on second."""
        if self._armed:
            self.fire()
        else:
            self.arm()
