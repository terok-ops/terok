# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for error-surfacing utilities: warn_user, log_warning, _log_debug, and config error paths.

Covers the new ``logging_utils`` module and the ``_load_validated()`` error-handling
branches in ``config.py`` that surface silent diagnostic failures to operators.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.core import config as cfg
from terok.lib.util.logging_utils import _log, _log_debug, log_warning, warn_user

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def write_config(tmp_path: Path, content: str) -> Path:
    """Write a temporary config file and return its path."""
    path = tmp_path / "config.yml"
    path.write_text(content, encoding="utf-8")
    return path


# ===========================================================================
# warn_user
# ===========================================================================


class TestWarnUser:
    """Tests for ``warn_user(component, message)``."""

    def test_prints_structured_warning_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Output follows ``Warning [component]: message`` format on stderr."""
        warn_user("config", "Something went wrong.")
        captured = capsys.readouterr()
        assert captured.err == "Warning [config]: Something went wrong.\n"
        assert captured.out == ""

    def test_custom_component_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Component name is embedded in the output."""
        warn_user("shield", "Firewall unreachable.")
        assert "Warning [shield]: Firewall unreachable." in capsys.readouterr().err

    def test_never_raises_on_stderr_failure(self) -> None:
        """Exception safety: writing to stderr fails silently."""
        with patch("terok.lib.util.logging_utils.sys") as mock_sys:
            mock_sys.stderr.write.side_effect = OSError("broken pipe")
            # Must not raise
            warn_user("test", "should not crash")

    def test_also_logs_to_file(self, tmp_path: Path) -> None:
        """warn_user also writes a WARNING line to the log file."""
        with patch("terok.lib.core.paths.state_root", return_value=tmp_path):
            warn_user("gate", "Connection refused.")
        log_file = tmp_path / "terok.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "[WARNING]" in content
        assert "[gate] Connection refused." in content


# ===========================================================================
# log_warning / _log_debug / _log
# ===========================================================================


class TestLogFunctions:
    """Tests for file-based logging utilities."""

    def test_log_warning_writes_warning_level(self, tmp_path: Path) -> None:
        """log_warning() writes a WARNING-level line to state_root()/terok.log."""
        with patch("terok.lib.core.paths.state_root", return_value=tmp_path):
            log_warning("disk almost full")
        log_file = tmp_path / "terok.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "[WARNING] disk almost full" in content

    def test_log_debug_writes_debug_level(self, tmp_path: Path) -> None:
        """_log_debug() writes a DEBUG-level line."""
        with patch("terok.lib.core.paths.state_root", return_value=tmp_path):
            _log_debug("resolved path /foo")
        content = (tmp_path / "terok.log").read_text(encoding="utf-8")
        assert "[DEBUG] resolved path /foo" in content

    def test_log_appends_multiple_lines(self, tmp_path: Path) -> None:
        """Successive calls append; they do not overwrite."""
        with patch("terok.lib.core.paths.state_root", return_value=tmp_path):
            log_warning("first")
            log_warning("second")
        lines = (tmp_path / "terok.log").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert "first" in lines[0]
        assert "second" in lines[1]

    def test_log_creates_parent_dirs(self, tmp_path: Path) -> None:
        """_log creates parent directories if they do not exist."""
        nested = tmp_path / "deep" / "nested"
        with patch("terok.lib.core.paths.state_root", return_value=nested):
            _log("hello", level="INFO")
        assert (nested / "terok.log").exists()

    def test_log_never_raises_on_io_error(self) -> None:
        """Exception safety: if state_root() raises, _log swallows it."""
        with patch(
            "terok.lib.core.paths.state_root",
            side_effect=OSError("permission denied"),
        ):
            # Must not raise
            _log("should not crash")

    def test_log_warning_never_raises_on_io_error(self) -> None:
        """Exception safety for the convenience wrapper."""
        with patch(
            "terok.lib.core.paths.state_root",
            side_effect=RuntimeError("boom"),
        ):
            log_warning("should not crash")

    def test_log_debug_never_raises_on_io_error(self) -> None:
        """Exception safety for the debug convenience wrapper."""
        with patch(
            "terok.lib.core.paths.state_root",
            side_effect=RuntimeError("boom"),
        ):
            _log_debug("should not crash")

    def test_log_line_contains_timestamp(self, tmp_path: Path) -> None:
        """Each log line starts with a bracketed timestamp."""
        with patch("terok.lib.core.paths.state_root", return_value=tmp_path):
            log_warning("ts check")
        line = (tmp_path / "terok.log").read_text(encoding="utf-8").strip()
        # Format: [YYYY-MM-DD HH:MM:SS] [WARNING] ts check
        assert line.startswith("[")
        assert "] [WARNING] ts check" in line


# ===========================================================================
# _load_validated() error paths
# ===========================================================================


class TestLoadValidatedErrorPaths:
    """Tests for ``_load_validated()`` error handling in config.py."""

    def test_malformed_yaml_warns_and_returns_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Malformed YAML triggers a stderr warning and returns defaults."""
        bad_file = write_config(tmp_path, "not: {valid: yaml: broken")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
        result = cfg._load_validated()
        captured = capsys.readouterr()
        assert "Warning [config]:" in captured.err
        assert "Malformed YAML" in captured.err
        assert str(bad_file) in captured.err
        # Returns defaults
        assert result.ui.base_port == 7860
        assert result.gate_server.port == 9418

    def test_invalid_schema_warns_with_field_errors_and_returns_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Schema validation error shows field-level errors on stderr."""
        bad_file = write_config(tmp_path, "ui:\n  base_port: not-a-number\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
        result = cfg._load_validated()
        captured = capsys.readouterr()
        assert "Warning [config]:" in captured.err
        assert "Invalid config" in captured.err
        assert "base_port" in captured.err
        # Returns defaults
        assert result.ui.base_port == 7860

    def test_unknown_key_warns_with_field_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """extra=forbid catches typos and surfaces them via warn_user."""
        bad_file = write_config(tmp_path, "uii:\n  base_port: 7860\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
        result = cfg._load_validated()
        captured = capsys.readouterr()
        assert "Warning [config]:" in captured.err
        assert "Invalid config" in captured.err
        # Returns defaults
        assert result.ui.base_port == 7860

    def test_missing_file_returns_defaults_no_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Missing config file returns defaults silently (no warning)."""
        missing = tmp_path / "nonexistent.yml"
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(missing))
        result = cfg._load_validated()
        captured = capsys.readouterr()
        assert captured.err == ""
        assert result.ui.base_port == 7860

    def test_unreadable_file_warns_and_returns_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """OSError (e.g. permission denied) triggers a warning."""
        cfg_file = write_config(tmp_path, "ui:\n  base_port: 9000\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(cfg_file))
        # Patch read_text on the Path object to simulate permission error
        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            result = cfg._load_validated()
        captured = capsys.readouterr()
        assert "Warning [config]:" in captured.err
        assert "Cannot read" in captured.err
        assert result.ui.base_port == 7860

    def test_valid_config_no_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Valid config produces no warnings on stderr."""
        good_file = write_config(tmp_path, "ui:\n  base_port: 9000\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(good_file))
        result = cfg._load_validated()
        captured = capsys.readouterr()
        assert captured.err == ""
        assert result.ui.base_port == 9000
