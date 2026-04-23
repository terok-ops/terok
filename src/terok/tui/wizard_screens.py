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
import io
from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, RichLog, Static, TextArea

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
        height: auto;
        max-height: 90%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #wizard-form-scroll {
        height: auto;
        max-height: 32;
    }

    .wizard-field {
        margin-bottom: 1;
        height: auto;
    }

    .wizard-help {
        color: $text-muted;
    }

    .wizard-error {
        color: $error;
    }

    #wizard-form-buttons {
        height: auto;
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

    def __init__(self) -> None:
        """Build a fresh wizard form — no prefill."""
        super().__init__()
        self._errors: dict[str, Label] = {}

    def compose(self) -> ComposeResult:
        """Lay out one widget per question, plus footer buttons."""
        dialog = Vertical(id="wizard-form-dialog")
        dialog.border_title = "New project"
        with dialog, VerticalScroll(id="wizard-form-scroll"):
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
        match q.kind:
            case "choice":
                yield from self._choice_widget(q)
            case "text":
                yield Input(placeholder=q.placeholder, id=self._widget_id(q))
            case "editor":
                yield TextArea(id=self._widget_id(q), language=None)
        err = Label("", classes="wizard-error", id=self._error_id(q))
        self._errors[q.key] = err
        yield err

    def _choice_widget(self, q: Question) -> ComposeResult:
        """Render a ``RadioSet`` for a choice question.

        ``RadioButton.value=True`` is set on the first option so the
        form is never in an "initially no selection" state — matches
        the CLI where the user *must* pick something.
        """
        with RadioSet(id=self._widget_id(q)):
            for i, (slug, label) in enumerate(q.choices):
                yield RadioButton(label, value=(i == 0), name=slug)

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


class ProjectReviewScreen(ModalScreen["str | None"]):
    """Show the rendered ``project.yml`` in an editable ``TextArea``.

    Dismisses with the (possibly edited) YAML string when the user
    confirms, or ``None`` on cancel / back.  The TUI equivalent of the
    CLI wizard's "Edit configuration file before setup? [Y/n]" step,
    but inline instead of suspending to ``$EDITOR`` — textual-serve
    cannot do the latter.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
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
        """Dismiss with ``None`` — caller may re-open the form screen."""
        self.dismiss(None)

    @on(Button.Pressed, "#wizard-review-back")
    def _on_back(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#wizard-review-init")
    def _on_init(self) -> None:
        yaml_text = self.query_one("#wizard-review-yaml", TextArea).text
        self.dismiss(yaml_text)


# ── Step 3: initialize project (ssh-init → generate → build → gate) ──


class InitProgressScreen(ModalScreen[bool]):
    """Run ``cmd_project_init``'s four steps as a background worker.

    Dismisses ``True`` on success, ``False`` on failure or cancellation.
    The SSH-key registration pause in :func:`maybe_pause_for_ssh_key_registration`
    is replaced by a mid-wizard continue button that gates the next
    step — no blocking ``input()`` in a Textual worker.
    """

    BINDINGS = [
        Binding("escape", "maybe_cancel", "Close"),
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
        self._ok = False

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
            # Hidden until ssh-init runs + upstream is SSH-scheme
            with Vertical(id="wizard-init-ssh-key"):
                yield Label("SSH public key — register this on your git remote:")
                yield TextArea("", id="wizard-init-ssh-pubkey", read_only=True)
                with Horizontal():
                    yield Button(
                        "I've registered the key — continue",
                        id="wizard-init-ssh-continue",
                        variant="primary",
                    )
            yield RichLog(id="wizard-init-log", markup=True, wrap=True)
            with Horizontal(id="wizard-init-buttons"):
                yield Button("Close", id="wizard-init-close", variant="default", disabled=True)

    async def on_mount(self) -> None:
        """Persist the reviewed YAML then kick off the background worker."""
        log = self.query_one("#wizard-init-log", RichLog)
        log.write(f"[dim]Writing project.yml for {self._project_id}…[/]")
        write_project_yaml(self._project_id, self._rendered_yaml, overwrite=True)
        self._run_init()

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

    @work(exclusive=True)
    async def _run_init(self) -> None:
        """Drive the four init steps, updating UI between each."""
        import asyncio

        from terok.lib.core.projects import load_project
        from terok.lib.domain.facade import (
            build_images,
            generate_dockerfiles,
            make_git_gate,
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

            if _needs_key_registration(self._project_id):
                self.query_one("#wizard-init-ssh-pubkey", TextArea).text = result["public_line"]
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

            # Step 3: Build
            self._mark("build", "running", "this can take several minutes")
            log.write(
                "[dim]Running image build in the background — podman's output goes to the "
                "terminal that launched terok, not this pane.[/]"
            )
            await asyncio.to_thread(build_images, self._project_id)
            self._mark("build", "done")

            # Step 4: Gate sync — load_project to read gate_enabled
            project = load_project(self._project_id)
            if not project.gate_enabled:
                self._mark("gate", "skipped", "gate.enabled: false")
                log.write("[dim]Gate disabled in project.yml — skipping gate-sync.[/]")
            else:
                self._mark("gate", "running")
                res = await asyncio.to_thread(lambda: make_git_gate(project).sync())
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

            self._ok = True
            log.write(f"[green]Project '{self._project_id}' is ready.[/]")
        except Exception as exc:
            log.write(f"[red]Error: {exc}[/]")
            # Mark the currently-running step failed (whichever one raised).
            for key in self._STEP_KEYS:
                widget = self.query_one(f"#{self._step_id(key)}", Static)
                if "⋯" in str(widget.renderable):
                    self._mark(key, "failed", str(exc))
                    break
        finally:
            self.query_one("#wizard-init-close", Button).disabled = False
            self.query_one("#wizard-init-close", Button).variant = (
                "success" if self._ok else "warning"
            )
            self.query_one("#wizard-init-close", Button).label = "Done" if self._ok else "Close"

    # ── Button handlers ───────────────────────────────────────────────

    @on(Button.Pressed, "#wizard-init-ssh-continue")
    def _on_ssh_continue(self) -> None:
        if self._ssh_continue is not None:
            self._ssh_continue.set()

    @on(Button.Pressed, "#wizard-init-close")
    def _on_close(self) -> None:
        self.dismiss(self._ok)

    def action_maybe_cancel(self) -> None:
        """Escape only dismisses once the worker has finished."""
        if not self.query_one("#wizard-init-close", Button).disabled:
            self.dismiss(self._ok)


# ── Helpers ───────────────────────────────────────────────────────────


def _needs_key_registration(project_id: str) -> bool:
    """Return True when the project's upstream is an SSH URL and thus needs a deploy-key pause."""
    from terok_sandbox import is_ssh_url

    from ..lib.core.projects import load_project

    try:
        project = load_project(project_id)
    except SystemExit:
        return False
    return bool(project.upstream_url) and is_ssh_url(project.upstream_url)


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
