# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI log viewer screen and formatters."""

import json
from unittest import TestCase, main, mock

from tui_test_helpers import import_log_viewer


class TuiLogFormatterTests(TestCase):
    """Tests for _TuiLogFormatter (Rich Text output, no Textual stubs needed)."""

    def _make_formatter(self, **kwargs):
        mod = import_log_viewer()
        return mod._TuiLogFormatter(**kwargs)

    def test_system_init_blue_text(self) -> None:
        fmt = self._make_formatter()
        line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc123"})
        result = fmt.feed_line(line)
        self.assertEqual(len(result), 1)
        self.assertIn("abc123", str(result[0]))
        self.assertEqual(result[0].style.color.name, "blue")

    def test_system_init_with_model_and_tools(self) -> None:
        fmt = self._make_formatter()
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
        self.assertEqual(len(result), 1)
        text = str(result[0])
        self.assertIn("model=claude-4", text)
        self.assertIn("2 tools available", text)

    def test_assistant_text_block_streaming(self) -> None:
        fmt = self._make_formatter(streaming=True)
        # Start text block
        start = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "text"},
            }
        )
        result = fmt.feed_line(start)
        self.assertEqual(result, [])

        # Delta with text
        delta = json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello world"},
            }
        )
        result = fmt.feed_line(delta)
        self.assertEqual(result, [])

        # Stop
        stop = json.dumps({"type": "content_block_stop"})
        result = fmt.feed_line(stop)
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0]), "Hello world")

    def test_tool_use_block_streaming(self) -> None:
        fmt = self._make_formatter(streaming=True)
        # Start tool_use block
        start = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read"},
            }
        )
        result = fmt.feed_line(start)
        self.assertEqual(len(result), 1)
        self.assertIn("[tool] Read", str(result[0]))
        self.assertEqual(result[0].style.color.name, "blue")

        # Input delta
        delta = json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"file": "foo.py"}'},
            }
        )
        result = fmt.feed_line(delta)
        self.assertEqual(result, [])

        # Stop — should produce yellow tool input
        stop = json.dumps({"type": "content_block_stop"})
        result = fmt.feed_line(stop)
        self.assertEqual(len(result), 1)
        self.assertIn("file", str(result[0]))
        self.assertEqual(result[0].style.color.name, "yellow")

    def test_coalesced_assistant_non_streaming(self) -> None:
        fmt = self._make_formatter(streaming=False)
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
        self.assertEqual(len(result), 3)  # text + tool label + tool input
        self.assertEqual(str(result[0]), "I will help you.")
        self.assertIn("[tool] Bash", str(result[1]))
        self.assertIn("cmd", str(result[2]))

    def test_user_tool_result_green(self) -> None:
        fmt = self._make_formatter()
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
        self.assertTrue(len(result) >= 1)
        self.assertIn("[tool_result]", str(result[0]))
        self.assertEqual(result[0].style.color.name, "green")

    def test_user_tool_error_red(self) -> None:
        fmt = self._make_formatter()
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
        self.assertTrue(len(result) >= 1)
        self.assertIn("[tool_error]", str(result[0]))
        self.assertEqual(result[0].style.color.name, "red")

    def test_result_summary_on_finish(self) -> None:
        fmt = self._make_formatter()
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
        self.assertEqual(result, [])

        finish_result = fmt.finish()
        self.assertEqual(len(finish_result), 1)
        text = str(finish_result[0])
        self.assertIn("[result]", text)
        self.assertIn("turns=3", text)
        self.assertIn("cost=$0.0123", text)
        self.assertIn("duration=5.0s", text)
        self.assertIn("tokens=100in/50out", text)
        self.assertEqual(finish_result[0].style.color.name, "yellow")

    def test_malformed_json_passthrough(self) -> None:
        fmt = self._make_formatter()
        result = fmt.feed_line("this is not JSON at all")
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0]), "this is not JSON at all")
        # No style (default) — Rich uses empty string for unstyled Text
        self.assertIn(result[0].style, ("", None))

    def test_empty_line_skipped(self) -> None:
        fmt = self._make_formatter()
        self.assertEqual(fmt.feed_line(""), [])
        self.assertEqual(fmt.feed_line("   "), [])
        self.assertEqual(fmt.feed_line("\n"), [])

    def test_long_result_truncated(self) -> None:
        fmt = self._make_formatter()
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
        self.assertIn("...", joined)
        # Should be truncated to 500 chars
        for t in result:
            text = str(t)
            if text.startswith("  x"):
                # Content line: "  " + truncated text
                self.assertLessEqual(len(text), 502)  # "  " + 497 + "..."

    def test_streaming_events_ignored_when_non_streaming(self) -> None:
        fmt = self._make_formatter(streaming=False)
        start = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "text"},
            }
        )
        result = fmt.feed_line(start)
        self.assertEqual(result, [])

    def test_finish_flushes_text_block(self) -> None:
        fmt = self._make_formatter(streaming=True)
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
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0]), "partial")

    def test_tool_result_with_list_content(self) -> None:
        fmt = self._make_formatter()
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
        self.assertIn("part1 part2", joined)

    def test_tool_input_truncates_long_values(self) -> None:
        fmt = self._make_formatter(streaming=False)
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
        self.assertTrue(len(input_texts) > 0)
        self.assertIn("...", input_texts[0])


class PlainTextTuiFormatterTests(TestCase):
    """Tests for _PlainTextTuiFormatter."""

    def _make_formatter(self):
        mod = import_log_viewer()
        return mod._PlainTextTuiFormatter()

    def test_plain_text_passthrough(self) -> None:
        fmt = self._make_formatter()
        result = fmt.feed_line("hello world")
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0]), "hello world")

    def test_plain_text_empty_line(self) -> None:
        fmt = self._make_formatter()
        self.assertEqual(fmt.feed_line(""), [])
        self.assertEqual(fmt.feed_line("  "), [])

    def test_plain_text_strips_trailing_newline(self) -> None:
        fmt = self._make_formatter()
        result = fmt.feed_line("hello\n")
        self.assertEqual(str(result[0]), "hello")

    def test_plain_text_finish_returns_empty(self) -> None:
        fmt = self._make_formatter()
        self.assertEqual(fmt.finish(), [])


class LogViewerScreenConstructionTests(TestCase):
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
        self.assertEqual(screen.project_id, "proj1")
        self.assertEqual(screen.task_id, "42")
        self.assertEqual(screen.mode, "run")
        self.assertEqual(screen.container_name, "proj1-run-42")
        self.assertTrue(screen.follow)

    def test_construction_static_mode(self) -> None:
        mod = import_log_viewer()
        ref = mod.TaskContainerRef(
            project_id="proj1",
            task_id="7",
            mode="cli",
            container_name="proj1-cli-7",
        )
        screen = mod.LogViewerScreen(ref, follow=False)
        self.assertFalse(screen.follow)
        self.assertEqual(screen.mode, "cli")

    def test_construction_default_follow(self) -> None:
        mod = import_log_viewer()
        ref = mod.TaskContainerRef(
            project_id="p",
            task_id="1",
            mode="run",
            container_name="p-run-1",
        )
        screen = mod.LogViewerScreen(ref)
        self.assertTrue(screen.follow)

    def test_stop_event_initialized(self) -> None:
        mod = import_log_viewer()
        ref = mod.TaskContainerRef(
            project_id="p",
            task_id="1",
            mode="run",
            container_name="p-run-1",
        )
        screen = mod.LogViewerScreen(ref)
        self.assertFalse(screen._stop_event.is_set())
        self.assertIsNone(screen._process)


class StreamLogsTests(TestCase):
    """Tests for LogViewerScreen._stream_logs (binary I/O with manual line splitting)."""

    def _make_screen(self, mode="cli", follow=True, provider=None):
        mod = import_log_viewer()
        ref = mod.TaskContainerRef(
            project_id="p",
            task_id="1",
            mode=mode,
            container_name="p-cli-1",
            provider=provider,
        )
        screen = mod.LogViewerScreen(ref, follow=follow)
        # Collect posted Text objects instead of calling into Textual
        screen._posted: list[object] = []
        screen._post_text = lambda t: screen._posted.append(t)
        screen._update_footer_static = lambda: None
        return screen

    def _make_mock_stdout(self, data: bytes):
        """Create a mock stdout that returns data via read1(), then empty."""
        import io

        buf = io.BytesIO(data)
        # Wrap so it has read1 like a real BufferedReader
        stdout = mock.MagicMock(wraps=buf)
        stdout.read1 = buf.read1 if hasattr(buf, "read1") else buf.read
        stdout.read = buf.read
        stdout.fileno = mock.MagicMock(return_value=99)
        return stdout

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_streams_lines_from_process(self, mock_select, mock_popen):
        """Lines produced by the subprocess are posted via _post_text."""
        screen = self._make_screen()

        data = b"line one\nline two\nline three\n"
        stdout = self._make_mock_stdout(data)

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
        self.assertIn("line one", texts)
        self.assertIn("line two", texts)
        self.assertIn("line three", texts)

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_drains_remaining_on_process_exit(self, mock_select, mock_popen):
        """When the process exits, remaining buffered data is drained."""
        screen = self._make_screen()

        # Process exits immediately with data still in pipe
        stdout = self._make_mock_stdout(b"drained line\n")

        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)  # Already exited
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        self.assertIn("drained line", texts)

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_trailing_partial_line_flushed(self, mock_select, mock_popen):
        """A trailing line without newline is still processed."""
        screen = self._make_screen()

        stdout = self._make_mock_stdout(b"complete\npartial")

        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        self.assertIn("complete", texts)
        self.assertIn("partial", texts)

    @mock.patch("subprocess.Popen")
    def test_stop_event_skips_drain(self, mock_popen):
        """When stop_event is set, the drain loop is skipped."""
        screen = self._make_screen()

        stdout = self._make_mock_stdout(b"line1\nline2\nline3\n")

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
        self.assertEqual(content_texts, [])

    @mock.patch("subprocess.Popen", side_effect=FileNotFoundError)
    def test_podman_not_found(self, mock_popen):
        """FileNotFoundError is caught and an error message is posted."""
        screen = self._make_screen()

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        self.assertTrue(any("podman not found" in t for t in texts))

    @mock.patch("subprocess.Popen", side_effect=OSError("connection refused"))
    def test_oserror_on_launch(self, mock_popen):
        """OSError on Popen is caught and an error message is posted."""
        screen = self._make_screen()

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        self.assertTrue(any("connection refused" in t for t in texts))

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_oserror_during_read_breaks_loop(self, mock_select, mock_popen):
        """OSError during select/read breaks the loop cleanly."""
        screen = self._make_screen()

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
        self.assertTrue(any("Log stream ended" in t for t in texts))

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_uses_claude_formatter_for_run_mode(self, mock_select, mock_popen):
        """Run mode with claude provider uses the structured formatter."""
        screen = self._make_screen(mode="run", provider="claude")

        log_line = json.dumps(
            {"type": "system", "subtype": "init", "session_id": "sess1", "model": "claude-4"}
        )
        stdout = self._make_mock_stdout(log_line.encode() + b"\n")

        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        texts = [str(t) for t in screen._posted]
        self.assertTrue(any("sess1" in t for t in texts))
        self.assertTrue(any("claude-4" in t for t in texts))

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_follow_flag_in_command(self, mock_select, mock_popen):
        """Follow mode appends -f to the podman logs command."""
        screen = self._make_screen(follow=True)

        stdout = self._make_mock_stdout(b"")
        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        cmd = mock_popen.call_args[0][0]
        self.assertIn("-f", cmd)

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_no_follow_flag_when_static(self, mock_select, mock_popen):
        """Static mode (follow=False) does not include -f."""
        screen = self._make_screen(follow=False)

        stdout = self._make_mock_stdout(b"")
        proc = mock.MagicMock()
        proc.stdout = stdout
        proc.poll = mock.MagicMock(return_value=0)
        proc.returncode = 0
        mock_popen.return_value = proc

        screen._stream_logs()

        cmd = mock_popen.call_args[0][0]
        self.assertNotIn("-f", cmd)

    @mock.patch("subprocess.Popen")
    @mock.patch("select.select")
    def test_process_terminated_in_finally(self, mock_select, mock_popen):
        """If the process is still running when an exception occurs, it gets terminated."""
        screen = self._make_screen()

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


if __name__ == "__main__":
    main()
