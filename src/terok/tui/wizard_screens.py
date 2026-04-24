# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Textual modal screens for the new-project wizard.

Three screens drive the flow:

1. :class:`WizardFormScreen` — one form that collects every
   :data:`terok.lib.domain.wizards.new_project.QUESTIONS` answer at
   once.  Shares vocabulary + validation with the CLI wizard.
2. :class:`ProjectReviewScreen` — shows the rendered ``project.yml`` in
   a ``TextArea`` for last-minute edits before commit.
3. :class:`InitProgressScreen` — runs ssh-init → generate → build →
   gate-sync as a background worker, updating a per-step status list
   and surfacing the public key when the user needs to register it.

Each screen is its own ``ModalScreen`` and dismisses with a result;
the orchestration lives on the main app
(:class:`terok.tui.app.TerokTUI._launch_project_wizard`).

No ``app.suspend()`` — this replaces the CLI-subprocess path that
textual-serve cannot support.
"""

from __future__ import annotations

import contextlib
import enum
import io
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, RichLog, Static, TextArea

from ..lib.domain.facade import project_needs_key_registration
from ..lib.domain.wizards.new_project import (
    QUESTIONS,
    Question,
    validate_answer,
    write_project_yaml,
)

# ── Step 1: the form ──────────────────────────────────────────────────


class WizardFormScreen(ModalScreen["dict[str, str] | None"]):
    """First wizard screen — one form with every question, validated on submit.

    Dismisses with the collected values dict (keys = ``Question.key``)
    or ``None`` on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    WizardFormScreen {
        align: center middle;
    }

    #wizard-form-dialog {
        width: 80;
        height: 90%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #wizard-form-scroll {
        height: 1fr;
    }

    .wizard-field {
        margin-bottom: 1;
        height: auto;
    }

    .wizard-help {
        color: $text-muted;
        height: auto;
    }

    .wizard-error {
        color: $error;
        height: auto;
    }

    #wizard-form-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #wizard-form-buttons Button {
        margin-left: 1;
    }

    RadioSet {
        border: round $primary-darken-2;
        padding: 0 1;
    }

    TextArea {
        height: 8;
    }
    """

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        """Build a fresh wizard form, optionally pre-filled with previous answers.

        *initial* is the dict returned by a prior run of this screen —
        passed back in when the user clicks "Back" on the review screen
        so their typing survives the round trip.  Keys not in *initial*
        fall back to "first choice selected" / empty string.
        """
        super().__init__()
        self._errors: dict[str, Label] = {}
        self._initial: dict[str, str] = dict(initial) if initial else {}

    def compose(self) -> ComposeResult:
        """Lay out one widget per question, plus footer buttons.

        Form fields live in a scrollable pane; the button row is pinned
        below them but still inside the dialog border so both scroll
        area and buttons render inside the modal, not alongside it.
        """
        dialog = Vertical(id="wizard-form-dialog")
        dialog.border_title = "New project"
        with dialog:
            with VerticalScroll(id="wizard-form-scroll"):
                for q in QUESTIONS:
                    yield from self._field(q)
            with Horizontal(id="wizard-form-buttons"):
                yield Button("Cancel", id="wizard-form-cancel", variant="default")
                yield Button("Create", id="wizard-form-create", variant="primary")

    def _field(self, q: Question) -> ComposeResult:
        """Render the label, help, input widget, and error slot for *q*."""
        label = f"{q.prompt}" + ("" if q.required else "  (optional)")
        yield Label(label, classes="wizard-field")
        if q.help:
            yield Label(q.help, classes="wizard-help")
        preset = self._initial.get(q.key, "")
        match q.kind:
            case "choice":
                yield from self._choice_widget(q, selected_slug=preset)
            case "text":
                yield Input(value=preset, placeholder=q.placeholder, id=self._widget_id(q))
            case "editor":
                yield TextArea(preset, id=self._widget_id(q), language=None)
        err = Label("", classes="wizard-error", id=self._error_id(q))
        self._errors[q.key] = err
        yield err

    def _choice_widget(self, q: Question, *, selected_slug: str = "") -> ComposeResult:
        """Render a ``RadioSet`` for a choice question, optionally preselecting *selected_slug*.

        When the prefill slug matches one of the choices it takes
        precedence; otherwise the first option is preselected — the
        form is never in an "initially no selection" state, matching
        the CLI's "pick a number" semantics.
        """
        valid_slugs = {slug for slug, _ in q.choices}
        preset_slug = selected_slug if selected_slug in valid_slugs else ""
        with RadioSet(id=self._widget_id(q)):
            for i, (slug, label) in enumerate(q.choices):
                selected = (slug == preset_slug) if preset_slug else (i == 0)
                yield RadioButton(label, value=selected, name=slug)

    @staticmethod
    def _widget_id(q: Question) -> str:
        return f"wizard-field-{q.key}"

    @staticmethod
    def _error_id(q: Question) -> str:
        return f"wizard-error-{q.key}"

    # ── Actions ────────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        """Dismiss without collecting values."""
        self.dismiss(None)

    @on(Button.Pressed, "#wizard-form-cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#wizard-form-create")
    def _on_create(self) -> None:
        """Validate every field; on success dismiss with the collected dict."""
        values: dict[str, str] = {}
        any_error = False
        for q in QUESTIONS:
            raw = self._read_raw(q)
            value, error = validate_answer(q, raw)
            if error is None:
                values[q.key] = value
                self._errors[q.key].update("")
            else:
                self._errors[q.key].update(error)
                any_error = True
        if not any_error:
            self.dismiss(values)

    def _read_raw(self, q: Question) -> str:
        """Pull the current raw string out of the widget for *q*."""
        widget_id = f"#{self._widget_id(q)}"
        match q.kind:
            case "choice":
                rs = self.query_one(widget_id, RadioSet)
                pressed = rs.pressed_button
                # ``RadioSet`` always has a pressed button because we
                # initialised the first one pressed; name holds the slug.
                return pressed.name if pressed is not None else ""
            case "text":
                return self.query_one(widget_id, Input).value
            case "editor":
                return self.query_one(widget_id, TextArea).text


# ── Step 2: review rendered YAML ──────────────────────────────────────


#: Sentinel returned by :class:`ProjectReviewScreen` when the user clicks
#: "Back" — distinguishes "go back to the form, keep my answers" from
#: "cancel the whole wizard".  A distinct object lets the caller use
#: ``is REVIEW_BACK`` for the three-way branch without overloading the
#: string/None type.
REVIEW_BACK: object = object()


class ProjectReviewScreen(ModalScreen["str | object | None"]):
    """Show the rendered ``project.yml`` in an editable ``TextArea``.

    Dismisses with one of three results:

    - The (possibly edited) YAML string → user clicked "Initialize".
    - :data:`REVIEW_BACK` → user clicked "Back"; caller should re-open
      the form with the previous answers as prefill.
    - ``None`` → user hit Escape; wizard is abandoned.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ProjectReviewScreen {
        align: center middle;
    }

    #wizard-review-dialog {
        width: 90;
        height: 80%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #wizard-review-yaml {
        height: 1fr;
        margin-bottom: 1;
    }

    #wizard-review-buttons {
        height: auto;
        align-horizontal: right;
    }

    #wizard-review-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, project_id: str, rendered: str) -> None:
        """Create the review screen with the initial rendered YAML text."""
        super().__init__()
        self._project_id = project_id
        self._rendered = rendered

    def compose(self) -> ComposeResult:
        """Build the editable YAML pane and Back/Initialize buttons."""
        dialog = Vertical(id="wizard-review-dialog")
        dialog.border_title = f"Review project.yml — {self._project_id}"
        with dialog:
            yield TextArea.code_editor(
                self._rendered,
                language="yaml",
                id="wizard-review-yaml",
            )
            with Horizontal(id="wizard-review-buttons"):
                yield Button("Back", id="wizard-review-back", variant="default")
                yield Button("Initialize project", id="wizard-review-init", variant="primary")

    def action_cancel(self) -> None:
        """Escape abandons the wizard — dismiss with ``None``."""
        self.dismiss(None)

    @on(Button.Pressed, "#wizard-review-back")
    def _on_back(self) -> None:
        """Back returns to the form with the previous answers preserved."""
        self.dismiss(REVIEW_BACK)

    @on(Button.Pressed, "#wizard-review-init")
    def _on_init(self) -> None:
        yaml_text = self.query_one("#wizard-review-yaml", TextArea).text
        self.dismiss(yaml_text)


# ── Step 3: initialize project (ssh-init → generate → build → gate) ──


class InitOutcome(enum.Enum):
    """Result of :class:`InitProgressScreen` — four distinct states.

    ``SUCCESS`` and ``FAILED`` are the obvious outcomes; ``DECLINED``
    covers the case where the user deliberately chose not to overwrite
    an existing ``project.yml``, and ``CANCELLED`` covers Esc-out
    mid-run.  The caller needs all four to render the right follow-up
    notification: a decline or a cancellation is a benign no-op, not
    an error.
    """

    SUCCESS = "success"
    FAILED = "failed"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class InitProgressScreen(ModalScreen[InitOutcome]):
    """Run ``cmd_project_init``'s four steps as a background worker.

    Dismisses with one of the :class:`InitOutcome` values.  The SSH-key
    registration pause in :func:`maybe_pause_for_ssh_key_registration`
    is replaced by a mid-wizard continue button that gates the next
    step — no blocking ``input()`` in a Textual worker.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    InitProgressScreen {
        align: center middle;
    }

    #wizard-init-dialog {
        width: 90;
        height: 80%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #wizard-init-steps {
        height: auto;
        margin-bottom: 1;
    }

    .wizard-init-step {
        height: auto;
    }

    #wizard-init-log {
        height: 1fr;
        border: round $primary-darken-2;
        margin-bottom: 1;
    }

    #wizard-init-ssh-key {
        height: auto;
        border: round $warning;
        background: $surface;
        padding: 1;
        margin-bottom: 1;
        display: none;
    }

    .wizard-init-ssh-spacer {
        height: 1;
    }

    #wizard-init-ssh-pubkey {
        color: $accent;
        height: auto;
    }

    #wizard-init-ssh-fingerprint {
        color: $text-muted;
        height: auto;
    }

    #wizard-init-buttons {
        height: auto;
        align-horizontal: right;
    }

    #wizard-init-buttons Button {
        margin-left: 1;
    }
    """

    #: Ordered list of step keys (must match ``_run_steps`` implementation).
    _STEP_KEYS: tuple[str, ...] = ("ssh", "generate", "build", "gate")
    _STEP_LABELS: dict[str, str] = {
        "ssh": "SSH key",
        "generate": "Dockerfiles",
        "build": "Build images",
        "gate": "Gate sync",
    }

    def __init__(self, project_id: str, rendered_yaml: str) -> None:
        """Create the init screen with the project ID and final YAML text."""
        super().__init__()
        self._project_id = project_id
        self._rendered_yaml = rendered_yaml
        self._ssh_continue: Any = None  # an asyncio.Event, set when user clicks continue
        # Default pessimistic — the worker flips this to SUCCESS on a clean
        # finish, DECLINED when the user opts out of overwriting, and leaves
        # it on FAILED when a step raises.
        self._outcome: InitOutcome = InitOutcome.FAILED

    def compose(self) -> ComposeResult:
        """Build the per-step status list, log pane, and buttons."""
        dialog = Vertical(id="wizard-init-dialog")
        dialog.border_title = f"Initializing project — {self._project_id}"
        with dialog:
            with Vertical(id="wizard-init-steps"):
                for key in self._STEP_KEYS:
                    yield Static(
                        self._step_text(key, "pending"),
                        classes="wizard-init-step",
                        id=self._step_id(key),
                    )
            # Hidden until ssh-init runs + upstream is SSH-scheme.  A
            # ``Static`` (rather than ``TextArea``) renders selectable
            # plain text without the editor's line-number gutter or
            # cursor — copying from the terminal's mouse works cleanly,
            # and the "Copy" button bypasses that path entirely via the
            # shared clipboard helper.  The fingerprint is shown
            # alongside the key so the user can verify what GitHub etc.
            # will display *after* they paste the key — the comparison
            # has to be possible with both halves on screen at once.
            with Vertical(id="wizard-init-ssh-key"):
                yield Label("SSH public key — register this on your git remote:")
                yield Static("", classes="wizard-init-ssh-spacer")
                yield Static("", id="wizard-init-ssh-pubkey")
                yield Static("", classes="wizard-init-ssh-spacer")
                yield Static("", id="wizard-init-ssh-fingerprint")
                with Horizontal(id="wizard-init-ssh-buttons"):
                    yield Button(
                        "Copy",
                        id="wizard-init-ssh-copy",
                        variant="default",
                    )
                    yield Button(
                        "I've registered the key — continue",
                        id="wizard-init-ssh-continue",
                        variant="primary",
                    )
            yield RichLog(id="wizard-init-log", markup=True, wrap=True)
            with Horizontal(id="wizard-init-buttons"):
                yield Button("Close", id="wizard-init-close", variant="default", disabled=True)

    async def on_mount(self) -> None:
        """Confirm overwrite when needed, persist the YAML, then run init.

        When a ``project.yml`` already exists for this project ID, the
        TUI mirrors the CLI's overwrite prompt via a modal confirm.
        The heavy lifting (confirm → write → worker) happens in a
        ``@work`` coroutine so ``push_screen_wait`` has the worker
        context it needs.  A write failure is rendered into the log
        pane and the Close button is re-enabled; ``_run_init`` never
        runs on a partial-write state.
        """
        self._run_init_with_confirm()

    # Distinct ``group`` from the outer wizard flow is essential:
    # ``@work(exclusive=True)`` cancels every running worker sharing the
    # same group, so inheriting the default "default" group would have
    # the init worker cancel its own parent the moment it started —
    # symptom: click "Initialize" on the review screen, the init modal
    # pops off immediately, and the wizard silently completes with no
    # project created.  Keep these two groups isolated.
    @work(exclusive=True, group="wizard-init", exit_on_error=False)
    async def _run_init_with_confirm(self) -> None:
        """Top-level coroutine that sequences overwrite-confirm → write → run.

        The outer try/except is a safety net: any unexpected exception
        from confirm/write/run is logged and the Close button is
        enabled, so a user is never stuck in front of a frozen modal
        wondering why nothing is happening.
        """
        log = self.query_one("#wizard-init-log", RichLog)
        try:
            existing = self._existing_project_yaml_path()
            if existing is not None:
                if not await self._confirm_overwrite(existing):
                    log.write(
                        "[yellow]Keeping existing project.yml — nothing written, "
                        "nothing initialised.[/]"
                    )
                    # A declined overwrite is the user's deliberate choice —
                    # not a failure — so the screen dismisses with a distinct
                    # outcome the caller can branch on.
                    self._outcome = InitOutcome.DECLINED
                    return

            log.write(f"[dim]Writing project.yml for {self._project_id}…[/]")
            try:
                write_project_yaml(self._project_id, self._rendered_yaml, overwrite=True)
            except (OSError, SystemExit) as exc:
                log.write(f"[red]Failed to write project.yml: {exc}[/]")
                return
            await self._run_init()
        except Exception as exc:  # noqa: BLE001 — modal must never freeze silently
            log.write(f"[red]Unexpected wizard error: {exc}[/]")
        finally:
            self._finish_with_close_button()

    def _existing_project_yaml_path(self) -> Path | None:
        """Return the on-disk ``project.yml`` path if it already exists, else None."""
        from ..lib.core.config import user_projects_dir

        candidate = user_projects_dir() / self._project_id / "project.yml"
        return candidate if candidate.is_file() else None

    async def _confirm_overwrite(self, path: Path) -> bool:
        """Show the shared ``ConfirmDestructiveScreen`` for a project.yml overwrite."""
        from .screens import ConfirmDestructiveScreen

        return bool(
            await self.app.push_screen_wait(
                ConfirmDestructiveScreen(
                    message=(
                        f"A configuration for project '{self._project_id}' already "
                        f"exists at:\n\n{path}\n\nOverwrite with the reviewed content?"
                    ),
                    title=f"Overwrite project.yml — {self._project_id}",
                    confirm_label="Overwrite",
                )
            )
        )

    def _finish_with_close_button(self) -> None:
        """Enable the Close button after the screen reaches a terminal state.

        Called from ``on_mount`` when the write fails, from the decline
        branch in ``_run_init_with_confirm``, and from the worker's
        ``finally`` block.  Button label/variant mirror the outcome so
        a declined overwrite doesn't masquerade as a failed init.

        When the user cancels via Esc the worker's ``finally`` still
        runs after the screen has been dismissed — the Close button
        has already been torn down, so a failed ``query_one`` is
        expected and ignored rather than surfaced as a worker error.
        """
        if self._outcome is InitOutcome.CANCELLED:
            return
        variant_for = {
            InitOutcome.SUCCESS: "success",
            InitOutcome.FAILED: "warning",
            InitOutcome.DECLINED: "default",
        }
        label_for = {
            InitOutcome.SUCCESS: "Done",
            InitOutcome.FAILED: "Close",
            InitOutcome.DECLINED: "Close",
        }
        try:
            button = self.query_one("#wizard-init-close", Button)
        except NoMatches:
            return
        button.disabled = False
        button.variant = variant_for[self._outcome]
        button.label = label_for[self._outcome]

    # ── Step status helpers ───────────────────────────────────────────

    def _step_text(self, key: str, status: str, detail: str = "") -> str:
        """Render one step's label with a status badge."""
        badges = {
            "pending": "[dim]•[/]",
            "running": "[yellow]⋯[/]",
            "done": "[green]✓[/]",
            "failed": "[red]✗[/]",
            "skipped": "[dim]–[/]",
        }
        extra = f" — {detail}" if detail else ""
        return f"{badges.get(status, '•')} {self._STEP_LABELS[key]}{extra}"

    @staticmethod
    def _step_id(key: str) -> str:
        return f"wizard-init-step-{key}"

    def _mark(self, key: str, status: str, detail: str = "") -> None:
        self.query_one(f"#{self._step_id(key)}", Static).update(
            self._step_text(key, status, detail)
        )

    # ── Background worker ─────────────────────────────────────────────

    async def _run_init(self) -> None:
        """Drive the four init steps, updating UI between each.

        Runs inside the ``@work`` context established by
        :meth:`_run_init_with_confirm` — no extra decorator needed;
        nesting workers confuses Textual's exclusivity tracking.
        """
        import asyncio

        from terok.lib.core.projects import load_project
        from terok.lib.domain.facade import (
            generate_dockerfiles,
            provision_ssh_key,
            summarize_ssh_init,
        )

        log = self.query_one("#wizard-init-log", RichLog)
        self._ssh_continue = asyncio.Event()

        try:
            # Step 1: SSH
            self._mark("ssh", "running")
            result = await asyncio.to_thread(provision_ssh_key, self._project_id)
            self._mark("ssh", "done", f"key id {result['key_id']}")
            log.write(f"[green]✓[/] SSH key minted: {result['comment']}")

            if project_needs_key_registration(self._project_id):
                self.query_one("#wizard-init-ssh-pubkey", Static).update(result["public_line"])
                # Show the fingerprint beside the key so the user can
                # check it matches what their remote (e.g. GitHub) shows
                # *after* pasting — by then the pubkey is already gone
                # from that page and only the SHA256 digest remains.
                self.query_one("#wizard-init-ssh-fingerprint", Static).update(
                    f"Fingerprint: {result['fingerprint']}  ·  Comment: {result['comment']}"
                )
                # Stash for the Copy button handler — Static doesn't keep
                # the raw text the way TextArea.text does.
                self._ssh_pub_line = result["public_line"]
                self.query_one("#wizard-init-ssh-key").styles.display = "block"
                log.write(
                    "[yellow]Register the public key on the git remote, then click Continue.[/]"
                )
                await self._ssh_continue.wait()
                self.query_one("#wizard-init-ssh-key").styles.display = "none"

            # Silent stdout capture — the facade's summarise/print lines
            # land in the log instead of the terminal.  podman subprocess
            # output is lost (it writes to a real fd); future work: a
            # reusable log-tailer widget (see issue #473).
            with _log_capture(log):
                summarize_ssh_init(result)

            # Step 2: Dockerfiles
            self._mark("generate", "running")
            with _log_capture(log):
                await asyncio.to_thread(generate_dockerfiles, self._project_id)
            self._mark("generate", "done")

            # Step 3: Build.  ``build_images`` invokes ``podman build``
            # subprocesses that inherit the caller's stdout/stderr file
            # descriptors; left alone, their raw output *corrupts the
            # TUI frame* (colour codes, cursor moves, et al.).  We run
            # the whole build in a fresh Python subprocess whose fds
            # are captured by ``subprocess.run`` — parent-side fds stay
            # untouched no matter what the build crashes on.  A proper
            # log-tailer widget that streams subprocess output into
            # this pane is tracked in issue #473.
            self._mark("build", "running", "this can take several minutes")
            log.write(
                "[dim]Running image build in a subprocess (output suppressed to keep "
                "the TUI intact; watch `podman images`, or see #473 for the planned "
                "log-tailer widget).[/]"
            )
            await asyncio.to_thread(_build_in_subprocess, self._project_id)
            self._mark("build", "done")

            # Step 4: Gate sync — load_project to read gate_enabled.
            # Sync itself runs in a subprocess for the same reason as
            # the build: ``git clone --mirror`` and friends inherit
            # stdout/stderr and would otherwise overwrite the TUI frame.
            project = load_project(self._project_id)
            if not project.gate_enabled:
                self._mark("gate", "skipped", "gate.enabled: false")
                log.write("[dim]Gate disabled in project.yml — skipping gate-sync.[/]")
            else:
                self._mark("gate", "running")
                res = await asyncio.to_thread(_gate_sync_in_subprocess, self._project_id)
                if res["success"]:
                    upstream_hint = (
                        f"upstream {res['upstream_url']}"
                        if res.get("upstream_url")
                        else "local-only bare repo"
                    )
                    self._mark("gate", "done", upstream_hint)
                else:
                    errors = ", ".join(res.get("errors", []))
                    self._mark("gate", "failed", errors)
                    raise RuntimeError(f"Gate sync failed: {errors}")

            self._outcome = InitOutcome.SUCCESS
            log.write(f"[green]Project '{self._project_id}' is ready.[/]")
        except (Exception, SystemExit) as exc:
            # Many facade calls (``load_project``, etc.) signal user-
            # friendly errors with ``SystemExit`` which does *not*
            # inherit from ``Exception``; widening the catch keeps
            # those messages inside the log pane instead of bubbling
            # out of the worker and vanishing silently.
            log.write(f"[red]Error: {exc}[/]")
            # Mark the currently-running step failed (whichever one raised).
            for key in self._STEP_KEYS:
                widget = self.query_one(f"#{self._step_id(key)}", Static)
                if "⋯" in str(widget.renderable):
                    self._mark(key, "failed", str(exc))
                    break
        # Close-button enabling is consolidated in the outer
        # ``_run_init_with_confirm`` finally — keeping it there prevents
        # the button from flashing enabled then disabled between the
        # two coroutines.

    # ── Button handlers ───────────────────────────────────────────────

    @on(Button.Pressed, "#wizard-init-ssh-continue")
    def _on_ssh_continue(self) -> None:
        if self._ssh_continue is not None:
            self._ssh_continue.set()

    @on(Button.Pressed, "#wizard-init-ssh-copy")
    async def _on_ssh_copy(self) -> None:
        """Copy the SSH public key to the system clipboard via the shared helper.

        Runs the helper off the UI thread via :func:`asyncio.to_thread`:
        the clipboard call has its own internal timeout, but the whole
        point of the async hop is that a slow helper (think a laggy
        Wayland compositor, or a misbehaving clipboard daemon) can't
        freeze the event loop even for those few seconds.
        """
        import asyncio

        from .clipboard import copy_to_clipboard_detailed

        key = getattr(self, "_ssh_pub_line", "")
        if not key:
            return
        result = await asyncio.to_thread(copy_to_clipboard_detailed, key)
        if result.ok:
            self.notify("SSH public key copied to clipboard.")
        else:
            hint = result.hint or result.error or "no clipboard helper found"
            self.notify(
                f"Copy failed: {hint}",
                severity="warning",
                timeout=10,
            )

    @on(Button.Pressed, "#wizard-init-close")
    def _on_close(self) -> None:
        self.dismiss(self._outcome)

    def action_cancel(self) -> None:
        """Esc aborts the wizard at any point in its lifecycle.

        The Close button's ``disabled`` flag is the authoritative
        "worker finished" signal — ``_outcome`` defaults to FAILED at
        ``__init__`` time and only gets rewritten on success/decline
        paths, so reading it to detect completion would falsely
        classify an in-flight run as already-failed.

        Three cases:

        * **Worker finished** (Close button enabled) — the stored
          outcome is SUCCESS/FAILED/DECLINED; dismiss with it so the
          caller's match arms render the correct notification.
        * **Worker paused on the SSH-key "continue" gate** — the
          worker is awaiting an :class:`asyncio.Event`; cancelling
          raises ``CancelledError`` out of that ``await`` and the
          worker unwinds cleanly.
        * **Worker actively running a step** (provision, build,
          gate-sync) — those steps are ``asyncio.to_thread`` calls;
          cancelling the Task drops the awaiter but the OS thread
          keeps running to completion.  That's unavoidable on CPython
          (threads aren't forcibly cancellable), and the work ends up
          in a transient state the caller may need to clean up later —
          but the UI is freed immediately, which is the point.
        """
        try:
            close_button = self.query_one("#wizard-init-close", Button)
        except NoMatches:
            # Screen already torn down by a prior dismiss — nothing to do.
            return
        if not close_button.disabled:
            self.dismiss(self._outcome)
            return

        # Cancel the in-flight worker explicitly rather than relying on
        # dismiss() to tear it down; the worker's ``finally`` touches
        # widgets (``_finish_with_close_button``) and a concurrent
        # dismiss would race those queries against screen teardown.
        self.workers.cancel_group(self, "wizard-init")
        self._outcome = InitOutcome.CANCELLED
        self.dismiss(self._outcome)


# ── Helpers ───────────────────────────────────────────────────────────


@contextlib.contextmanager
def _log_capture(log: RichLog):
    """Redirect Python-level stdout into *log* for the duration of the block.

    Python-level ``print()`` calls from the facade land in the log
    pane.  Subprocess output (``podman build``) is not captured — those
    writes go through the kernel file descriptor, not through
    ``sys.stdout``.  A richer log-tailer widget that also captures
    subprocess streams is tracked in issue #473.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            yield
        finally:
            text = buffer.getvalue().rstrip()
            if text:
                log.write(text)


def _run_isolated(child_body: str, *, label: str) -> None:
    """Run *child_body* as a ``python -c`` subprocess with captured fds.

    Several init steps shell out to long-running commands (``podman
    build``, ``git clone --mirror``) that inherit the caller's
    stdout/stderr file descriptors.  Left alone, their raw output
    *corrupts the TUI frame* — colour codes, cursor moves, and progress
    bars land directly on the terminal underneath Textual.  Running the
    whole step in a child Python process moves those fds one layer
    away: ``capture_output=True`` swallows them, and the parent's
    terminal stays pristine regardless of what the step prints or
    crashes with.

    *label* is the human-friendly step name used in the error message.
    Any non-zero exit propagates as :class:`RuntimeError` carrying the
    last few KiB of the child's combined output, which the wizard's
    worker already logs as the failed step's detail.

    Proper streaming (subprocess output → ``RichLog`` via a reader
    thread) is tracked in issue #473 — this is the minimum-viable
    isolation, not the final UX.
    """
    import subprocess  # noqa: S404 — launching a known python interpreter with fixed argv
    import sys

    result = subprocess.run(  # noqa: S603 — argv is sys.executable + -c + terok code we control
        [sys.executable, "-c", child_body],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        snippet = "\n".join(tail[-20:]) if tail else "(no output)"
        raise RuntimeError(f"{label} exited with code {result.returncode}:\n{snippet}")


def _build_in_subprocess(project_id: str) -> None:
    """Run :func:`build_images` in a child Python process."""
    # Literal repr keeps project_id safely escaped into the -c body.
    child_body = f"from terok.lib.domain.facade import build_images; build_images({project_id!r})"
    _run_isolated(child_body, label="build_images")


def _gate_sync_in_subprocess(project_id: str) -> dict[str, Any]:
    """Run the gate sync in a child Python process and return its result dict.

    The result dict is serialised to a tempfile (rather than captured
    stdout) so any ``git`` progress noise can't be mistaken for the
    payload.  The tempfile is cleaned up regardless of subprocess
    outcome.
    """
    import json
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".json", prefix="terok-gate-sync-", delete=False) as f:
        result_path = Path(f.name)
    try:
        child_body = (
            "import json\n"
            "from terok.lib.core.projects import load_project\n"
            "from terok.lib.domain.facade import make_git_gate\n"
            f"_project = load_project({project_id!r})\n"
            "_result = make_git_gate(_project).sync()\n"
            f"json.dump(_result, open({str(result_path)!r}, 'w'))\n"
        )
        _run_isolated(child_body, label="gate sync")
        return json.loads(result_path.read_text(encoding="utf-8"))
    finally:
        result_path.unlink(missing_ok=True)
