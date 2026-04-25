# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Verdict + decision modal for the first-run / re-run host setup flow.

Renders the current :class:`terok_sandbox.SetupVerdict` with a
contextual blurb and a Run / Skip choice — no subprocess plumbing here.
The actual ``terok setup`` invocation rides on top of
:class:`~terok.tui.worker_log_screen.WorkerLogScreen`, pushed by the
TUI's first-run flow worker after this screen dismisses with
:data:`SetupOutcome.SHOULD_RUN`.

The verdict probe (``terok_sandbox.needs_setup``) is the same one
``terok task run`` enforces in
:func:`terok.cli.commands.task._setup_verdict_or_exit` — so a verdict
of ``OK`` short-circuits with a banner instead of nudging the user
toward a slow re-run, and a ``STALE_AFTER_DOWNGRADE`` refuses outright
with the same wording the CLI uses.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator

from terok_sandbox import SetupVerdict, needs_setup
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class SetupOutcome(enum.Enum):
    """User's decision on :class:`SetupScreen`.

    The screen does *not* run setup itself; it only collects intent.
    Translating the outcome to host-state changes is the caller's job:

    - ``SHOULD_RUN`` — user clicked Run; caller should push
      :class:`~terok.tui.worker_log_screen.WorkerLogScreen` with
      ``["terok", "setup"]``.
    - ``SKIPPED`` — user dismissed before running; the caller leaves
      the host alone.
    - ``REFUSED`` — verdict is ``STALE_AFTER_DOWNGRADE``; the only
      available exit, mirroring the CLI exit-4 contract.
    - ``CANCELLED`` — Esc on the pre-run screen; treated as a soft
      skip but distinguishable for telemetry.
    """

    SHOULD_RUN = "should_run"
    SKIPPED = "skipped"
    REFUSED = "refused"
    CANCELLED = "cancelled"


_VERDICT_HEADLINE: dict[SetupVerdict, str] = {
    SetupVerdict.OK: "Host services are already set up — re-running is safe but optional.",
    SetupVerdict.FIRST_RUN: "First run detected — host services have not been initialised yet.",
    SetupVerdict.STALE_AFTER_UPDATE: (
        "Package versions changed since the last setup — re-run to apply."
    ),
    SetupVerdict.STAMP_CORRUPT: "Setup stamp is unreadable — re-run setup to refresh it.",
    SetupVerdict.STALE_AFTER_DOWNGRADE: (
        "Downgrade detected — terok refuses to run until the stamp is reconciled."
    ),
}


class SetupScreen(ModalScreen[SetupOutcome]):
    """Modal that surfaces the current setup verdict and asks whether to run it.

    Two button layouts:

    1. *Healthy / non-OK verdict* — Skip + Run buttons.  Clicking Run
       dismisses with :data:`SetupOutcome.SHOULD_RUN` so the parent
       can push the :class:`WorkerLogScreen` that owns the actual
       subprocess streaming.
    2. *Downgrade verdict* — Run is hidden; the only exit dismisses
       with :data:`SetupOutcome.REFUSED`.  The CLI refuses with exit
       code 4 here; this mirrors that contract by *not* offering an
       option that would make the contract meaningless.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    CSS = """
    SetupScreen {
        align: center middle;
    }

    #setup-dialog {
        width: 80;
        max-width: 100%;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #setup-headline {
        height: auto;
        margin-bottom: 1;
    }

    #setup-blurb {
        color: $text-muted;
        height: auto;
        margin-bottom: 1;
    }

    #setup-buttons {
        height: auto;
        align-horizontal: right;
    }

    #setup-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, verdict: SetupVerdict | None = None) -> None:
        """Build the screen with an optional pre-fetched verdict.

        Verdict probing is normally done on the main thread before the
        screen is pushed (so the caller can decide whether to show it
        at all).  Falling back to a fresh probe inside the screen
        keeps direct invocations from the command palette working
        when no caller pre-fetched it.
        """
        super().__init__()
        self._verdict = verdict if verdict is not None else needs_setup()

    # ── Layout ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Build the dialog: headline, blurb, action buttons."""
        dialog = Vertical(id="setup-dialog")
        dialog.border_title = "Terok host setup"
        with dialog:
            yield Static(_VERDICT_HEADLINE[self._verdict], id="setup-headline")
            yield Label(self._blurb_for(self._verdict), id="setup-blurb")
            with Horizontal(id="setup-buttons"):
                yield from self._buttons_for(self._verdict)

    @staticmethod
    def _blurb_for(verdict: SetupVerdict) -> str:
        """Return the per-verdict explanation rendered below the headline."""
        if verdict is SetupVerdict.STALE_AFTER_DOWNGRADE:
            return (
                "Older code may not read newer state correctly.  Either "
                "re-upgrade terok, or remove the setup stamp at your own "
                "risk and re-run setup manually from a shell."
            )
        return (
            "Installs the sandbox stack (shield + vault + gate + clearance) "
            "and the XDG desktop entry for terok-tui.  Idempotent — safe to "
            "re-run.  Image builds are deferred to first task run."
        )

    @staticmethod
    def _buttons_for(verdict: SetupVerdict) -> Iterator[Button]:
        """Render the button row, refusing the run on a downgrade."""
        if verdict is SetupVerdict.STALE_AFTER_DOWNGRADE:
            yield Button("Refused", id="setup-refused", variant="error", disabled=True)
            yield Button("Close", id="setup-close", variant="default")
            return
        yield Button("Skip", id="setup-skip", variant="default")
        yield Button("Run setup", id="setup-run", variant="primary")

    # ── Actions ─────────────────────────────────────────────────────────

    def action_close(self) -> None:
        """Esc dismisses with :data:`SetupOutcome.CANCELLED` (or REFUSED on a downgrade)."""
        self.dismiss(self._cancel_outcome())

    def _cancel_outcome(self) -> SetupOutcome:
        """Pick the right outcome for an Esc / Close press given the verdict."""
        if self._verdict is SetupVerdict.STALE_AFTER_DOWNGRADE:
            return SetupOutcome.REFUSED
        return SetupOutcome.CANCELLED

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route the three button IDs to their dismissal outcomes."""
        match event.button.id:
            case "setup-run":
                self.dismiss(SetupOutcome.SHOULD_RUN)
            case "setup-skip":
                self.dismiss(SetupOutcome.SKIPPED)
            case "setup-close":
                self.dismiss(self._cancel_outcome())
