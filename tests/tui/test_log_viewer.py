# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI log viewer screen and formatters."""

import json
from unittest import mock

import pytest
from tui_test_helpers import import_log_viewer


def make_tui_formatter(**kwargs: object) -> object:
    """Build the structured TUI formatter under test."""
    return import_log_viewer()._TuiLogFormatter(**kwargs)


def make_plain_text_formatter() -> object:
    """Build the plain-text fallback formatter under test."""
    return import_log_viewer()._PlainTextTuiFormatter()


def make_log_viewer_screen(
    *, mode: str = "cli", follow: bool = True, provider: str | None = None
) -> object:
    """Build a LogViewerScreen with captured posted output."""
    mod = import_log_viewer()
    ref = mod.TaskContainerRef(
        project_id="p",
        task_id="1",
        mode=mode,
        container_name="p-cli-1",
        provider=provider,
    )
    screen = mod.LogViewerScreen(ref, follow=follow)
    screen._posted = []
    screen._post_text = lambda text: screen._posted.append(text)
    screen._update_footer_static = lambda: None
    return screen


def make_mock_stdout(data: bytes) -> mock.MagicMock:
    """Create a stdout-like mock that serves buffered bytes via ``read1``."""
    import io

    buf = io.BytesIO(data)
    stdout = mock.MagicMock(wraps=buf)
    stdout.read1 = buf.read1 if hasattr(buf, "read1") else buf.read
    stdout.read = buf.read
    stdout.fileno = mock.MagicMock(return_value=99)
    return stdout


class TestTuiLogFormatter:
    """Tests for _TuiLogFormatter (Rich Text output, no Textual stubs needed)."""

    def test_system_init_blue_text(self) -> None:
        fmt = make_tui_formatter()
        line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc123"})
        result = fmt.feed_line(line)
        assert len(result) == 1
        assert "abc123" in str(result[0])
        assert result[0].style.color.name == "blue"

    def test_system_init_with_model_and_tools(self) -> None:
        fmt = make_tui_formatter()
        line = json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "s1",
                "model": "claude-4",
                "tools": ["a", "b"],
            }
        )
        result = fmt.feed_line(line)
        assert len(result) == 1
        text = str(result[0])
        assert "model=claude-4" in text
        assert "2 tools available" in text

    def test_assistant_text_block_streaming(self) -> None:
        fmt = make_tui_formatter(streaming=True)
        # Start text block
        start = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "text"},
            }
        )
        result = fmt.feed_line(start)
        assert result == []

        # Delta with text
        delta = json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello world"},
            }
        )
        result = fmt.feed_line(delta)
        assert result == []

        # Stop
        stop = json.dumps({"type": "content_block_stop"})
        result = fmt.feed_line(stop)
        assert len(result) == 1
        assert str(result[0]) == "Hello world"

    def test_tool_use_block_streaming(self) -> None:
        fmt = make_tui_formatter(streaming=True)
        # Start tool_use block
        start = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read"},
            }
        )
        result = fmt.feed_line(start)
        assert len(result) == 1
        assert "[tool] Read" in str(result[0])
        assert result[0].style.color.name == "blue"

        # Input delta
        delta = json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"file": "foo.py"}'},
            }
        )
        result = fmt.feed_line(delta)
        assert result == []

        # Stop — should produce yellow tool input
        stop = json.dumps({"type": "content_block_stop"})
        result = fmt.feed_line(stop)
        assert len(result) == 1
        assert "file" in str(result[0])
        assert result[0].style.color.name == "yellow"

    def test_coalesced_assistant_non_streaming(self) -> None:
        fmt = make_tui_formatter(streaming=False)
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I will help you."},
                        {"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}},
                    ],
                },
            }
        )
        result = fmt.feed_line(line)
        assert len(result) == 3  # text + tool label + tool input
        assert str(result[0]) == "I will help you."
        assert "[tool] Bash" in str(result[1])
        assert "cmd" in str(result[2])

    def test_user_tool_result_green(self) -> None:
        fmt = make_tui_formatter()
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_abc12345",
                            "content": "Success!",
                            "is_error": False,
                        }
                    ],
                },
            }
        )
        result = fmt.feed_line(line)
        assert len(result) >= 1
        assert "[tool_result]" in str(result[0])
        assert result[0].style.color.name == "green"

    def test_user_tool_error_red(self) -> None:
        fmt = make_tui_formatter()
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_err12345",
                            "content": "File not found",
                            "is_error": True,
                        }
                    ],
                },
            }
        )
        result = fmt.feed_line(line)
        assert len(result) >= 1
        assert "[tool_error]" in str(result[0])
        assert result[0].style.color.name == "red"

    def test_result_summary_on_finish(self) -> None:
        fmt = make_tui_formatter()
        line = json.dumps(
            {
                "type": "result",
                "cost_usd": 0.0123,
                "duration_ms": 5000,
                "num_turns": 3,
                "is_error": False,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )
        result = fmt.feed_line(line)
        assert result == []

        finish_result = fmt.finish()
        assert len(finish_result) == 1
        text = str(finish_result[0])
        assert "[result]" in text
        assert "turns=3" in text
        assert "cost=$0.0123" in text
        assert "duration=5.0s" in text
        assert "tokens=100in/50out" in text
        assert finish_result[0].style.color.name == "yellow"

    def test_malformed_json_passthrough(self) -> None:
        fmt = make_tui_formatter()
        result = fmt.feed_line("this is not JSON at all")
        assert len(result) == 1
        assert str(result[0]) == "this is not JSON at all"
        # No style (default) — Rich uses empty string for unstyled Text
        assert result[0].style in ("", None)

    @pytest.mark.parametrize("line", ["", "   ", "\n"])
    def test_empty_line_skipped(self, line: str) -> None:
        assert make_tui_formatter().feed_line(line) == []

    def test_long_result_truncated(self) -> None:
        fmt = make_tui_formatter()
        long_text = "x" * 600
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": long_text,
                        }
                    ],
                },
            }
        )
        result = fmt.feed_line(line)
        # Find the content text (second element after label)
        content_texts = [str(t) for t in result]
        joined = " ".join(content_texts)
        assert "..." in joined
        # Should be truncated to 500 chars
        for t in result:
            text = str(t)
            if text.startswith("  x"):
                # Content line: "  " + truncated text
                assert len(text) <= 502  # "  " + 497 + "..."

    def test_streaming_events_ignored_when_non_streaming(self) -> None:
        fmt = make_tui_formatter(streaming=False)
        start = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "text"},
            }
        )
        result = fmt.feed_line(start)
        assert result == []

    def test_finish_flushes_text_block(self) -> None:
        fmt = make_tui_formatter(streaming=True)
        # Start a text block
        fmt.feed_line(
            json.dumps(
                {
                    "type": "content_block_start",
                    "content_block": {"type": "text"},
                }
            )
        )
        # Delta without stop
        fmt.feed_line(
            json.dumps(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "partial"},
                }
            )
        )
        # Finish should flush
        result = fmt.finish()
        assert len(result) == 1
        assert str(result[0]) == "partial"

    def test_tool_result_with_list_content(self) -> None:
        fmt = make_tui_formatter()
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": [
                                {"type": "text", "text": "part1"},
                                {"type": "text", "text": "part2"},
                            ],
                        }
                    ],
                },
            }
        )
        result = fmt.feed_line(line)
        joined = " ".join(str(t) for t in result)
        assert "part1 part2" in joined

    def test_tool_input_truncates_long_values(self) -> None:
        fmt = make_tui_formatter(streaming=False)
        long_val = "v" * 250
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"content": long_val}},
                    ],
                },
            }
        )
        result = fmt.feed_line(line)
        input_texts = [str(t) for t in result if "content:" in str(t)]
        assert len(input_texts) > 0
        assert "..." in input_texts[0]


class TestPlainTextTuiFormatter:
    """Tests for _PlainTextTuiFormatter."""

    def test_plain_text_passthrough(self) -> None:
        fmt = make_plain_text_formatter()
        result = fmt.feed_line("hello world")
        assert len(result) == 1
        assert str(result[0]) == "hello world"

    @pytest.mark.parametrize("line", ["", "  "])
    def test_plain_text_empty_line(self, line: str) -> None:
        assert make_plain_text_formatter().feed_line(line) == []

    def test_plain_text_strips_trailing_newline(self) -> None:
        fmt = make_plain_text_formatter()
        result = fmt.feed_line("hello\n")
        assert str(result[0]) == "hello"

    def test_plain_text_finish_returns_empty(self) -> None:
        assert make_plain_text_formatter().finish() == []


class TestLogViewerScreenConstruction:
    """Tests for LogViewerScreen construction (with Textual stubs)."""

    def test_construction_follow_mode(self) -> None:
        mod = import_log_viewer()
        ref = mod.TaskContainerRef(
            project_id="proj1",
            task_id="42",
            mode="run",
            container_name="proj1-run-42",
        )
        screen = mod.LogViewerScreen(ref, follow=True)
        assert screen.project_id == "proj1"
        assert screen.task_id == "42"
        assert screen.mode == "run"
        assert screen.container_name == "proj1-run-42"
        assert screen.follow

    def test_construction_static_mode(self) -> None:
        mod = import_log_viewer()
        ref = mod.TaskContainerRef(
            project_id="proj1",
            task_id="7",
            mode="cli",
            container_name="proj1-cli-7",
        )
        screen = mod.LogViewerScreen(ref, follow=False)
        assert not screen.follow
        assert screen.mode == "cli"

    def test_construction_default_follow(self) -> None:
        mod = import_log_viewer()
        ref = mod.TaskContainerRef(
            project_id="p",
            task_id="1",
            mode="run",
            container_name="p-run-1",
        )
        screen = mod.LogViewerScreen(ref)
        assert screen.follow

    def test_stop_event_initialized(self) -> None:
        mod = import_log_viewer()
        ref = mod.TaskContainerRef(
            project_id="p",
            task_id="1",
            mode="run",
            container_name="p-run-1",
        )
        screen = mod.LogViewerScreen(ref)
        assert not screen._stop_event.is_set()
        assert screen._process is None


class TestStreamLogs:
    """Tests for LogViewerScreen._stream_logs (binary I/O with manual line splitting)."""

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_streams_lines_from_process(self, mock_select, mock_popen):
        """Lines produced by the subprocess are posted via _post_text."""
        screen = make_log_viewer_screen()

        data = b"line one\nline two\nline three\n"
        stdout = make_mock_stdout(data)

        proc = mock.MagicMock()
        proc.stdout = stdout
        # First poll: None (running), then 0 (exited) for all subsequent calls
        proc.poll = mock.MagicMock(side_effect=[None, 0, 0, 0])
        proc.returncode = 0
        mock_popen.return_value = proc

        # select returns ready on first call, then process exits on next poll
        mock_select.return_value = ([stdout], [], [])

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        assert "line one" in texts
        assert "line two" in texts
        assert "line three" in texts

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_drains_remaining_on_process_exit(self, mock_select, mock_popen):
        """When the process exits, remaining buffered data is drained."""
        screen = make_log_viewer_screen()

        # Process exits immediately with data still in pipe
        stdout = make_mock_stdout(b"drained line\n")

        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)  # Already exited
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        assert "drained line" in texts

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_trailing_partial_line_flushed(self, mock_select, mock_popen):
        """A trailing line without newline is still processed."""
        screen = make_log_viewer_screen()

        stdout = make_mock_stdout(b"complete\npartial")

        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        assert "complete" in texts
        assert "partial" in texts

    @mock.patch("subprocess.Popen")
    def test_stop_event_skips_drain(self, mock_popen):
        """When stop_event is set, the drain loop is skipped."""
        screen = make_log_viewer_screen()

        stdout = make_mock_stdout(b"line1\nline2\nline3\n")

        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        # Pre-set the stop event so the while loop exits immediately
        screen._stop_event.set()

        screen._stream_logs()

        # Nothing should be posted (stop_event was set before entering the loop)
        content_texts = [str(t) for t in screen._posted if "Log stream ended" not in str(t)]
        assert content_texts == []

    @mock.patch("subprocess.Popen", side_effect=FileNotFoundError)
    def test_podman_not_found(self, mock_popen):
        """FileNotFoundError is caught and an error message is posted."""
        screen = make_log_viewer_screen()

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        assert any("podman not found" in t for t in texts)

    @mock.patch("subprocess.Popen", side_effect=OSError("connection refused"))
    def test_oserror_on_launch(self, mock_popen):
        """OSError on Popen is caught and an error message is posted."""
        screen = make_log_viewer_screen()

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        assert any("connection refused" in t for t in texts)

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_oserror_during_read_breaks_loop(self, mock_select, mock_popen):
        """OSError during select/read breaks the loop cleanly."""
        screen = make_log_viewer_screen()

        stdout = mock.MagicMock()
        stdout.read = mock.MagicMock(return_value=b"")

        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=None)
        proc.returncode = -1
        mock_popen.return_value = proc

        mock_select.side_effect = OSError("broken pipe")

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        assert any("Log stream ended" in t for t in texts)

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_uses_claude_formatter_for_run_mode(self, mock_select, mock_popen):
        """Run mode with claude provider uses the structured formatter."""
        screen = make_log_viewer_screen(mode="run", provider="claude")

        log_line = json.dumps(
            {"type": "system", "subtype": "init", "session_id": "sess1", "model": "claude-4"}
        )
        stdout = make_mock_stdout(log_line.encode() + b"\n")

        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        assert any("sess1" in t for t in texts)
        assert any("claude-4" in t for t in texts)

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_follow_flag_in_command(self, mock_select, mock_popen):
        """Follow mode appends -f to the podman logs command."""
        screen = make_log_viewer_screen(follow=True)

        stdout = make_mock_stdout(b"")
        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        cmd = mock_popen.call_args[0][0]
        assert "-f" in cmd

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_no_follow_flag_when_static(self, mock_select, mock_popen):
        """Static mode (follow=False) does not include -f."""
        screen = make_log_viewer_screen(follow=False)

        stdout = make_mock_stdout(b"")
        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        cmd = mock_popen.call_args[0][0]
        assert "-f" not in cmd

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_process_terminated_in_finally(self, mock_select, mock_popen):
        """If the process is still running when an exception occurs, it gets terminated."""
        screen = make_log_viewer_screen()

        stdout = mock.MagicMock()
        # Make read1 raise to trigger the except→break path
        stdout.read1 = mock.MagicMock(side_effect=ValueError("closed"))

        proc = mock.MagicMock()
        proc.stdout = stdout
        # Process still running
        proc.poll = mock.MagicMock(return_value=None)
        proc.returncode = None
        proc.wait = mock.MagicMock()
        mock_popen.return_value = proc

        # select says data is ready, then read1 raises ValueError
        mock_select.return_value = ([stdout], [], [])

        screen._stream_logs()

        proc.terminate.assert_called_once()
