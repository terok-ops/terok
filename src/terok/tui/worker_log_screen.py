# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Reusable modal that streams a child process's output into a RichLog.

Retires the ``app.suspend()`` + "run it in the underlying terminal"
pattern for long-running shell-outs (podman build, git clone --mirror,
terok setup, etc.) so they work identically over ``terok-web`` /
textual-serve where there's no terminal to suspend *to*.

Scope for this first cut (tracked in
[terok-ai/terok#473](https://github.com/terok-ai/terok/issues/473)):

- Run an arbitrary ``argv`` as a subprocess via
  [`asyncio.create_subprocess_exec`][].
- Stream ``stdout`` + ``stderr`` (merged) line by line into a
  ``RichLog``.
- Dismiss with [`WorkerResult`][] carrying the exit code; a
  ``Close`` button enables when the process finishes and is coloured
  by success / failure.
- ``Hide`` button and "running tasks" drawer are **not** in this
  PR — the modal blocks until the subprocess exits.  Adding them
  later is additive (the worker structure is already decoupled via
  ``@work``).

Not in scope:

- Python-level callables running in a thread.  Kept intentionally
  out of the first cut because fd-1 / fd-2 redirection for a
  whole-process capture risks interfering with Textual's own output.
  Callers with Python work should wrap it in a ``python -c`` argv
  (same pattern ``wizard_screens._run_isolated`` already uses).
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RichLog


@dataclass(frozen=True)
class WorkerResult:
    """Outcome of a [`WorkerLogScreen`][] run.

    ``exit_code`` is the subprocess's exit status (0 on success,
    non-zero on failure).  A screen dismissed before the subprocess
    finished resolves to ``exit_code=None`` — distinguishable from
    an exit-0 completion.
    """

    exit_code: int | None
    """Subprocess exit status, or ``None`` if the screen was dismissed early."""

    @property
    def ok(self) -> bool:
        """``True`` when the subprocess completed with a zero exit code."""
        return self.exit_code == 0


class WorkerLogScreen(ModalScreen[WorkerResult]):
    """Modal that runs *argv* as a subprocess and streams its output live.

    Example::

        result = await app.push_screen_wait(
            WorkerLogScreen(
                ["terok", "setup"],
                title="Running terok setup…",
            )
        )
        if result.ok:
            ...

    The widget is deliberately generic — callers that want to run a
    Python function inline should shell out with ``[sys.executable,
    "-c", "..."]`` (the same isolation pattern the wizard's build +
    gate-sync steps already use for TUI-frame hygiene).
    """

    BINDINGS = [
        Binding("escape", "maybe_cancel", "Close"),
    ]

    CSS = """
    WorkerLogScreen {
        align: center middle;
    }

    #worker-log-dialog {
        width: 90%;
        height: 80%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #worker-log-command {
        color: $text-muted;
        margin-bottom: 1;
        height: auto;
    }

    #worker-log-output {
        height: 1fr;
        border: round $primary-darken-2;
        margin-bottom: 1;
    }

    #worker-log-buttons {
        height: 3;
        align-horizontal: right;
    }

    #worker-log-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        argv: list[str],
        *,
        title: str = "Running…",
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        """Create the modal for an about-to-run subprocess.

        Args:
            argv: The command to run.  Passed straight to
                [`asyncio.create_subprocess_exec`][] — no shell.
            title: Border-title text shown above the log pane.
            env: Optional environment override.  ``None`` inherits
                the parent's env (the common case).
            cwd: Optional working directory.  ``None`` inherits.
        """
        super().__init__()
        self._argv = list(argv)
        self._title = title
        self._env = env
        self._cwd = cwd
        # Default pessimistic — the worker flips this to the real code on clean
        # exit; an escape-key dismissal mid-run leaves it None so the caller
        # can tell "user bailed out" from "process exited 0".
        self._result: WorkerResult = WorkerResult(exit_code=None)
        self._proc: asyncio.subprocess.Process | None = None

    def compose(self) -> ComposeResult:
        """Lay out the log pane and single-action button row."""
        dialog = Vertical(id="worker-log-dialog")
        dialog.border_title = self._title
        with dialog:
            # Render argv via shlex.join so the operator knows which
            # command is producing the output they're about to watch.
            yield Label(f"$ {shlex.join(self._argv)}", id="worker-log-command")
            yield RichLog(id="worker-log-output", markup=False, wrap=True)
            with Horizontal(id="worker-log-buttons"):
                yield Button(
                    "Close",
                    id="worker-log-close",
                    variant="default",
                    disabled=True,
                )

    async def on_mount(self) -> None:
        """Kick off the subprocess as soon as the screen is on stage."""
        self._run_subprocess()

    # ── Worker ────────────────────────────────────────────────────────

    @work(exclusive=False, group="worker-log", exit_on_error=False)
    async def _run_subprocess(self) -> None:
        """Drive the subprocess lifecycle: spawn, stream, finalise.

        The outer try/except is a safety net: an exec failure (command
        not on PATH, permission denied, cwd missing) needs to surface
        as a visible line rather than freezing the modal silently.
        """
        log = self.query_one("#worker-log-output", RichLog)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._env,
                cwd=self._cwd,
            )
        except (OSError, FileNotFoundError) as exc:
            log.write(f"[failed to launch] {exc}")
            self._result = WorkerResult(exit_code=127)
            self._finish_with_close_button()
            return

        assert self._proc.stdout is not None
        async for raw in self._proc.stdout:
            # Strip only the trailing newline — keep whitespace-preserving
            # indentation on each line (podman build's output uses it
            # for STEP 1/n / STEP 2/n markers).
            log.write(raw.decode(errors="replace").rstrip("\n"))

        exit_code = await self._proc.wait()
        self._result = WorkerResult(exit_code=exit_code)
        log.write("")
        if exit_code == 0:
            log.write(f"[✓] {self._argv[0]} exited 0")
        else:
            log.write(f"[✗] {self._argv[0]} exited with code {exit_code}")
        self._finish_with_close_button()

    def _finish_with_close_button(self) -> None:
        """Enable the Close button and colour it by the outcome."""
        button = self.query_one("#worker-log-close", Button)
        button.disabled = False
        if self._result.ok:
            button.label = "Done"
            button.variant = "success"
        else:
            button.label = "Close"
            button.variant = "warning"

    # ── Actions ───────────────────────────────────────────────────────

    def action_maybe_cancel(self) -> None:
        """Escape only dismisses once the worker has finished.

        Mid-run Escape is deliberately ignored — an in-flight
        ``podman build`` or ``terok setup`` spanning minutes shouldn't
        be killable by an accidental keypress.  A future ``Cancel``
        button (with explicit SIGTERM + confirm) is the place for
        abort semantics.
        """
        if not self.query_one("#worker-log-close", Button).disabled:
            self.dismiss(self._result)

    @on(Button.Pressed, "#worker-log-close")
    def _on_close(self) -> None:
        """User-driven dismiss — passes the worker's result back to the caller."""
        self.dismiss(self._result)
