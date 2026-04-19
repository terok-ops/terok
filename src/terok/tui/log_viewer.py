# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""In-app log viewer screen for the TUI.

Provides a full-page ``LogViewerScreen`` backed by Textual's ``RichLog``
widget that renders formatted, color-coded container log output inline —
replacing the previous approach of launching an external terminal.

The ``_TuiLogFormatter`` mirrors the state machine in
``terok.lib.domain.log_format.ClaudeStreamJsonFormatter`` but
produces Rich ``Text`` objects instead of ANSI ``print()`` calls.
"""

from __future__ import annotations

import json
import select
import subprocess
import threading
from dataclasses import dataclass
from enum import Enum, auto

from rich.style import Style
from rich.text import Text
from terok_executor import AgentRunner
from textual import screen
from textual.app import ComposeResult
from textual.widgets import RichLog, Static

from .screens import _modal_binding

try:  # pragma: no cover - optional import for test stubs
    from textual.css.query import NoMatches
except Exception:  # pragma: no cover - textual may be a stub module
    NoMatches = Exception  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Style constants (match CLI color scheme)
# ---------------------------------------------------------------------------

_STYLE_SYSTEM = Style(color="blue")
_STYLE_TOOL = Style(color="blue")
_STYLE_TOOL_INPUT = Style(color="yellow")
_STYLE_RESULT_OK = Style(color="green")
_STYLE_RESULT_ERR = Style(color="red", bold=True)
_STYLE_SUMMARY = Style(color="yellow")


# ---------------------------------------------------------------------------
# TUI log formatter (Rich Text output)
# ---------------------------------------------------------------------------


class _StreamState(Enum):
    """Tracks which streaming content block the formatter is currently inside."""

    IDLE = auto()
    TEXT_BLOCK = auto()
    TOOL_USE_BLOCK = auto()


class _TuiLogFormatter:
    """Parse Claude stream-json NDJSON into Rich ``Text`` objects.

    Same state machine as ``ClaudeStreamJsonFormatter`` in ``log_format.py``
    but outputs ``Text`` objects for ``RichLog.write()`` instead of ANSI
    ``print()`` calls.
    """

    def __init__(self, *, streaming: bool = True) -> None:
        """Initialize the formatter.

        Args:
            streaming: When True, handle incremental ``content_block_*`` events
                       (follow mode).  When False, only handle complete messages.
        """
        self._streaming = streaming
        self._state = _StreamState.IDLE
        self._tool_input_buf: list[str] = []
        self._current_tool_name: str = ""
        self._text_buf: list[str] = []
        self._result: dict[str, object] | None = None

    def feed_line(self, line: str) -> list[Text]:
        """Process one log line and return zero or more ``Text`` objects."""
        if not line.strip():
            return []
        stripped = line.strip()
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            # Not JSON — pass through as plain text
            return [Text(line.rstrip("\r\n"))]

        msg_type = data.get("type", "")

        if msg_type == "system":
            return self._handle_system(data)
        if msg_type == "assistant":
            return self._handle_assistant(data)
        if msg_type == "user":
            return self._handle_user(data)
        if msg_type == "result":
            return self._handle_result(data)
        if self._streaming and msg_type == "content_block_start":
            return self._handle_block_start(data)
        if self._streaming and msg_type == "content_block_delta":
            return self._handle_block_delta(data)
        if self._streaming and msg_type == "content_block_stop":
            return self._handle_block_stop(data)
        return []

    def finish(self) -> list[Text]:
        """Flush any in-progress block and return summary if available."""
        out: list[Text] = []
        if self._state == _StreamState.TEXT_BLOCK and self._text_buf:
            out.append(Text("".join(self._text_buf)))
            self._text_buf.clear()
        elif self._state == _StreamState.TOOL_USE_BLOCK:
            accumulated = "".join(self._tool_input_buf)
            if accumulated:
                out.append(Text(f"  {accumulated}", style=_STYLE_TOOL_INPUT))
            self._tool_input_buf.clear()
        self._state = _StreamState.IDLE

        if self._result:
            out.extend(self._format_result_summary())
        return out

    # -- message handlers --

    def _handle_system(self, data: dict) -> list[Text]:
        """Format a ``system`` event (e.g. session init with model and tool count)."""
        subtype = data.get("subtype", "")
        if subtype == "init":
            session_id = data.get("session_id", "")
            tools = data.get("tools", [])
            model = data.get("model", "")
            parts = [f"Session: {session_id}"] if session_id else []
            if model:
                parts.append(f"model={model}")
            if tools:
                parts.append(f"{len(tools)} tools available")
            if parts:
                return [Text(f"[system] {', '.join(parts)}", style=_STYLE_SYSTEM)]
        return []

    def _handle_assistant(self, data: dict) -> list[Text]:
        """Format a complete ``assistant`` message (text blocks and tool-use blocks)."""
        out: list[Text] = []
        message = data.get("message", {})
        content = message.get("content", [])
        for block in content:
            block_type = block.get("type", "")
            if block_type == "text":
                text = block.get("text", "")
                if text.strip():
                    out.append(Text(text))
            elif block_type == "tool_use":
                name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                out.append(Text(f"[tool] {name}", style=_STYLE_TOOL))
                out.extend(self._format_tool_input(tool_input))
        return out

    def _handle_user(self, data: dict) -> list[Text]:
        """Format a ``user`` message, extracting tool results and error status."""
        out: list[Text] = []
        message = data.get("message", {})
        content = message.get("content", [])
        for block in content:
            block_type = block.get("type", "")
            if block_type == "tool_result":
                tool_id = block.get("tool_use_id", "")
                result_content = block.get("content", "")
                is_error = block.get("is_error", False)
                label = "[tool_error]" if is_error else "[tool_result]"
                style = _STYLE_RESULT_ERR if is_error else _STYLE_RESULT_OK
                if isinstance(result_content, str):
                    text = result_content
                elif isinstance(result_content, list):
                    text = " ".join(
                        b.get("text", "") for b in result_content if b.get("type") == "text"
                    )
                else:
                    text = str(result_content)
                # Truncate long results for readability
                if len(text) > 500:
                    text = text[:497] + "..."
                if tool_id:
                    out.append(Text(f"{label} ({tool_id[:8]}...)", style=style))
                else:
                    out.append(Text(label, style=style))
                if text.strip():
                    out.append(Text(f"  {text}"))
        return out

    def _handle_result(self, data: dict) -> list[Text]:
        """Stash a ``result`` event for display in ``finish()``."""
        self._result = data
        return []

    # -- streaming event handlers --

    def _handle_block_start(self, data: dict) -> list[Text]:
        """Begin a new streaming content block (text or tool-use)."""
        content_block = data.get("content_block", {})
        block_type = content_block.get("type", "")
        if block_type == "text":
            self._state = _StreamState.TEXT_BLOCK
            self._text_buf.clear()
        elif block_type == "tool_use":
            self._state = _StreamState.TOOL_USE_BLOCK
            self._current_tool_name = content_block.get("name", "unknown")
            self._tool_input_buf.clear()
            return [Text(f"[tool] {self._current_tool_name}", style=_STYLE_TOOL)]
        return []

    def _handle_block_delta(self, data: dict) -> list[Text]:
        """Accumulate incremental text or tool-input JSON from a streaming delta."""
        delta = data.get("delta", {})
        delta_type = delta.get("type", "")
        if self._state == _StreamState.TEXT_BLOCK and delta_type == "text_delta":
            text = delta.get("text", "")
            if text:
                self._text_buf.append(text)
        elif self._state == _StreamState.TOOL_USE_BLOCK and delta_type == "input_json_delta":
            partial = delta.get("partial_json", "")
            if partial:
                self._tool_input_buf.append(partial)
        return []

    def _handle_block_stop(self, _data: dict) -> list[Text]:
        """Finalize the current streaming block and emit buffered content."""
        out: list[Text] = []
        if self._state == _StreamState.TEXT_BLOCK:
            text = "".join(self._text_buf)
            if text:
                out.append(Text(text))
            self._text_buf.clear()
        elif self._state == _StreamState.TOOL_USE_BLOCK:
            accumulated = "".join(self._tool_input_buf)
            if accumulated:
                try:
                    parsed = json.loads(accumulated)
                    out.extend(self._format_tool_input(parsed))
                except (json.JSONDecodeError, ValueError):
                    out.append(Text(f"  {accumulated}", style=_STYLE_TOOL_INPUT))
            self._tool_input_buf.clear()
        self._state = _StreamState.IDLE
        return out

    # -- helpers --

    def _format_tool_input(self, tool_input: dict[str, object] | str) -> list[Text]:
        """Render tool-use input as key-value lines, truncating long values."""
        out: list[Text] = []
        if isinstance(tool_input, dict):
            for k, v in tool_input.items():
                val_str = str(v)
                if len(val_str) > 200:
                    val_str = val_str[:197] + "..."
                out.append(Text(f"  {k}: {val_str}", style=_STYLE_TOOL_INPUT))
        elif tool_input:
            out.append(Text(f"  {tool_input}", style=_STYLE_TOOL_INPUT))
        return out

    def _format_result_summary(self) -> list[Text]:
        """Build a one-line summary showing cost, duration, tokens, and error status."""
        data = self._result
        if not data:
            return []
        cost_usd = data.get("cost_usd")
        duration_ms = data.get("duration_ms")
        is_error = data.get("is_error", False)
        num_turns = data.get("num_turns")
        usage = data.get("usage", {})

        parts: list[str] = []
        if is_error:
            parts.append("FAILED")
        if num_turns is not None:
            parts.append(f"turns={num_turns}")
        if cost_usd is not None:
            parts.append(f"cost=${cost_usd:.4f}")
        if duration_ms is not None:
            secs = duration_ms / 1000
            parts.append(f"duration={secs:.1f}s")
        if usage:
            inp = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            if inp or out_tok:
                parts.append(f"tokens={inp}in/{out_tok}out")

        if parts:
            summary = ", ".join(parts)
            return [Text(f"[result] {summary}", style=_STYLE_SUMMARY)]
        return []


# ---------------------------------------------------------------------------
# Plain text formatter (non-autopilot modes)
# ---------------------------------------------------------------------------


class _PlainTextTuiFormatter:
    """Pass-through formatter that wraps each non-empty line in a ``Text``."""

    def feed_line(self, line: str) -> list[Text]:
        """Wrap a non-empty log line in a plain ``Text`` object."""
        if not line.strip():
            return []
        return [Text(line.rstrip("\r\n"))]

    def finish(self) -> list[Text]:
        """No-op; plain text mode has no buffered state to flush."""
        return []


# ---------------------------------------------------------------------------
# LogViewerScreen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskContainerRef:
    """Identifies a task's container for log viewing."""

    project_id: str
    task_id: str
    mode: str
    container_name: str
    provider: str | None = None


class LogViewerScreen(screen.Screen[None]):
    """Full-page log viewer with formatted, color-coded output."""

    BINDINGS = [
        _modal_binding("escape", "dismiss_screen", "Back"),
        _modal_binding("q", "dismiss_screen", "Back"),
        _modal_binding("f", "dismiss_screen", "Back"),
    ]

    CSS = """
    LogViewerScreen {
        layout: vertical;
        background: $background;
    }

    #log-header {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    #log-view {
        height: 1fr;
    }

    #log-footer {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        ref: TaskContainerRef,
        *,
        follow: bool = True,
    ) -> None:
        """Create a log viewer for a container.

        Args:
            ref: Task container reference (project, task, mode, container name, provider).
            follow: If True, stream logs in real-time with auto-scroll.
        """
        super().__init__()
        self.project_id = ref.project_id
        self.task_id = ref.task_id
        self.mode = ref.mode
        self.container_name = ref.container_name
        self.follow = follow
        self.provider = ref.provider
        self._stop_event = threading.Event()
        self._process: subprocess.Popen | None = None

    def compose(self) -> ComposeResult:
        """Build the header, RichLog body, and keybinding footer."""
        yield Static(
            f" Task {self.task_id} ({self.mode}) | {self.container_name}",
            id="log-header",
        )
        yield RichLog(auto_scroll=self.follow, id="log-view")
        yield Static(" \\[Esc/q/f] Back", id="log-footer")

    def on_mount(self) -> None:
        """Start the background log-streaming worker when the screen is mounted."""
        self.run_worker(self._stream_logs, thread=True, group="log-stream")

    def _stream_logs(self) -> None:
        """Worker thread: stream podman logs through the formatter.

        Uses binary I/O with ``read1()`` and manual line splitting to avoid
        the buffering mismatch between ``select()`` on the raw fd and
        Python's ``TextIOWrapper``/``BufferedReader`` internal buffer, which
        could cause lines to get stuck unread in the Python buffer.
        """
        formatter: _TuiLogFormatter | _PlainTextTuiFormatter
        if self.mode == "run" and (self.provider or "claude") == "claude":
            formatter = _TuiLogFormatter(streaming=self.follow)
        else:
            formatter = _PlainTextTuiFormatter()

        try:
            self._process = AgentRunner().stream_logs_process(
                self.container_name, follow=self.follow, merge_stderr=True
            )
        except FileNotFoundError:
            self._post_text(Text("Error: podman not found", style=_STYLE_RESULT_ERR))
            return
        except OSError as e:
            self._post_text(Text(f"Error launching podman: {e}", style=_STYLE_RESULT_ERR))
            return

        try:
            stdout = self._process.stdout
            if stdout is None:
                return

            buf = b""
            while not self._stop_event.is_set():
                if self._process.poll() is not None:
                    # Process exited — drain remaining output
                    remaining = stdout.read()
                    if remaining:
                        buf += remaining
                    break

                try:
                    ready, _, _ = select.select([stdout], [], [], 0.2)
                    if not ready:
                        continue
                    chunk = stdout.read1(4096) if hasattr(stdout, "read1") else stdout.read(4096)
                    if not chunk:
                        continue
                    buf += chunk
                except (OSError, ValueError):
                    break

                # Process complete lines
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.decode("utf-8", errors="replace")
                    texts = formatter.feed_line(line)
                    for t in texts:
                        self._post_text(t)

            # Process any remaining complete lines in the buffer
            while b"\n" in buf:
                if self._stop_event.is_set():
                    break
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace")
                texts = formatter.feed_line(line)
                for t in texts:
                    self._post_text(t)

            # Flush any trailing partial line
            if buf and not self._stop_event.is_set():
                line = buf.decode("utf-8", errors="replace")
                if line.strip():
                    texts = formatter.feed_line(line)
                    for t in texts:
                        self._post_text(t)

            # Finish (summary, etc.)
            for t in formatter.finish():
                self._post_text(t)
        finally:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()

        # Post status after stream ends
        exit_code = self._process.returncode if self._process else None
        status_msg = f"--- Log stream ended (exit code: {exit_code}) ---"
        self._post_text(Text(status_msg, style=_STYLE_SYSTEM))
        self._update_footer_static()

    def _post_text(self, text: Text) -> None:
        """Thread-safe write to the RichLog widget."""
        self.app.call_from_thread(self._write_to_log, text)

    def _write_to_log(self, text: Text) -> None:
        """Append a ``Text`` object to the RichLog widget (must run on main thread)."""
        try:
            log_widget = self.query_one("#log-view", RichLog)
            log_widget.write(text)
        except NoMatches:
            pass  # Screen may have been dismissed

    def _update_footer_static(self) -> None:
        """Update footer to reflect STATIC mode after stream ends."""

        def _update() -> None:
            """Switch footer text and disable auto-scroll on the main thread."""
            try:
                footer = self.query_one("#log-footer", Static)
                footer.update(" \\[Esc/q/f] Back  \\[STREAM ENDED]")
                log_widget = self.query_one("#log-view", RichLog)
                log_widget.auto_scroll = False
            except NoMatches:
                pass

        self.app.call_from_thread(_update)

    # -- cleanup --

    def _cleanup_process(self) -> None:
        """Signal the worker to stop and terminate the subprocess if running."""
        self._stop_event.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    # -- actions --

    def action_dismiss_screen(self) -> None:
        """Stop the log stream and dismiss this screen."""
        self._cleanup_process()
        self.dismiss(None)

    def on_unmount(self) -> None:
        """Ensure the subprocess is terminated when the screen is removed."""
        self._cleanup_process()
