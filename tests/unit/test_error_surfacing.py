# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for error-surfacing utilities and all error-handling branches.

Covers logging_utils, config._load_validated, config._resolve_path,
project_state, image_cleanup, ports, and environment warning paths.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terok.lib.core import config as cfg
from terok.lib.util.logging_utils import LOG_FILENAME, _log, _log_debug, log_warning, warn_user

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

    @pytest.fixture(autouse=True)
    def _isolate_log(self, tmp_path: Path) -> None:
        """Redirect log writes so tests never touch the real state dir."""
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            yield

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
        import io

        broken = io.StringIO()
        broken.write = lambda _: (_ for _ in ()).throw(OSError("broken pipe"))
        with patch("terok.lib.util.logging_utils.sys") as mock_sys:
            mock_sys.stderr = broken
            # Must not raise
            warn_user("test", "should not crash")

    def test_also_logs_to_file(self, tmp_path: Path) -> None:
        """warn_user also writes a WARNING line to the log file."""
        warn_user("gate", "Connection refused.")
        log_file = tmp_path / LOG_FILENAME
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
        """log_warning() writes a WARNING-level line to core_state_dir()/terok.log."""
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            log_warning("disk almost full")
        log_file = tmp_path / LOG_FILENAME
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "[WARNING] disk almost full" in content

    def test_log_debug_writes_debug_level(self, tmp_path: Path) -> None:
        """_log_debug() writes a DEBUG-level line."""
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            _log_debug("resolved path /foo")
        content = (tmp_path / LOG_FILENAME).read_text(encoding="utf-8")
        assert "[DEBUG] resolved path /foo" in content

    def test_log_appends_multiple_lines(self, tmp_path: Path) -> None:
        """Successive calls append; they do not overwrite."""
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            log_warning("first")
            log_warning("second")
        lines = (tmp_path / LOG_FILENAME).read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert "first" in lines[0]
        assert "second" in lines[1]

    def test_log_creates_parent_dirs(self, tmp_path: Path) -> None:
        """_log creates parent directories if they do not exist."""
        nested = tmp_path / "deep" / "nested"
        with patch("terok.lib.core.paths.core_state_dir", return_value=nested):
            _log("hello", level="INFO")
        assert (nested / LOG_FILENAME).exists()

    def test_log_never_raises_on_io_error(self) -> None:
        """Exception safety: if core_state_dir() raises, _log swallows it."""
        with patch(
            "terok.lib.core.paths.core_state_dir",
            side_effect=OSError("permission denied"),
        ):
            # Must not raise
            _log("should not crash")

    def test_log_warning_never_raises_on_io_error(self) -> None:
        """Exception safety for the convenience wrapper."""
        with patch(
            "terok.lib.core.paths.core_state_dir",
            side_effect=RuntimeError("boom"),
        ):
            log_warning("should not crash")

    def test_log_debug_never_raises_on_io_error(self) -> None:
        """Exception safety for the debug convenience wrapper."""
        with patch(
            "terok.lib.core.paths.core_state_dir",
            side_effect=RuntimeError("boom"),
        ):
            _log_debug("should not crash")

    def test_log_line_contains_timestamp(self, tmp_path: Path) -> None:
        """Each log line starts with a bracketed timestamp."""
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            log_warning("ts check")
        line = (tmp_path / LOG_FILENAME).read_text(encoding="utf-8").strip()
        # Format: [YYYY-MM-DD HH:MM:SS] [WARNING] ts check
        assert line.startswith("[")
        assert "] [WARNING] ts check" in line


# ===========================================================================
# _load_validated() error paths
# ===========================================================================


class TestLoadValidatedErrorPaths:
    """Tests for ``_load_validated()`` error handling in config.py."""

    @pytest.fixture(autouse=True)
    def _isolate_log(self, tmp_path: Path) -> None:
        """Redirect log writes to tmp_path so tests never touch the real state dir."""
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            yield

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
        assert result.gate_server.port is None
        assert result.tui.default_tmux is False

    def test_invalid_schema_warns_with_field_errors_and_returns_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Schema validation error shows field-level errors on stderr."""
        bad_file = write_config(tmp_path, "gate_server:\n  port: not-a-number\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
        result = cfg._load_validated()
        captured = capsys.readouterr()
        assert "Warning [config]:" in captured.err
        assert "Invalid config" in captured.err
        assert "port" in captured.err
        # Returns defaults
        assert result.gate_server.port is None

    def test_unknown_key_warns_with_field_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """extra=forbid catches typos and surfaces them via warn_user."""
        bad_file = write_config(tmp_path, "tuii:\n  default_tmux: true\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
        result = cfg._load_validated()
        captured = capsys.readouterr()
        assert "Warning [config]:" in captured.err
        assert "Invalid config" in captured.err
        # Returns defaults
        assert result.tui.default_tmux is False

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
        assert result.gate_server.port is None

    def test_unreadable_file_warns_and_returns_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """OSError (e.g. permission denied) triggers a warning."""
        cfg_file = write_config(tmp_path, "gate_server:\n  port: 9000\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(cfg_file))
        # Patch read_text on the Path object to simulate permission error
        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            result = cfg._load_validated()
        captured = capsys.readouterr()
        assert "Warning [config]:" in captured.err
        assert "Cannot read" in captured.err
        assert result.gate_server.port is None

    def test_valid_config_no_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Valid config produces no warnings on stderr."""
        good_file = write_config(tmp_path, "gate_server:\n  port: 9000\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(good_file))
        result = cfg._load_validated()
        captured = capsys.readouterr()
        assert captured.err == ""
        assert result.gate_server.port == 9000


# ===========================================================================
# _resolve_path() error paths
# ===========================================================================


class TestResolvePathFallback:
    """Tests for ``_resolve_path()`` config key lookup failure logging."""

    @pytest.fixture(autouse=True)
    def _isolate_log(self, tmp_path: Path) -> None:
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            yield

    def test_value_error_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ValueError during config key lookup falls back to default path."""
        # Write a config file with a null path value that triggers fallback
        bad_file = write_config(tmp_path, "paths:\n  build_dir: null\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
        # _resolve_path should fall back to default without raising
        result = cfg.build_dir()
        assert result.is_absolute()

    def test_yaml_error_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """YAMLError during config key lookup falls back to default path."""
        bad_file = write_config(tmp_path, ": [broken")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
        result = cfg.build_dir()
        assert result.is_absolute()


# ===========================================================================
# project_state.py — template comparison and gate commit failures
# ===========================================================================


class TestProjectStateWarnings:
    """Cover the exception-handling branches in get_project_state()."""

    @pytest.fixture(autouse=True)
    def _isolate_log(self, tmp_path: Path) -> None:
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            yield

    def test_template_comparison_failure_logged(self, tmp_path: Path) -> None:
        """Template comparison exception is caught and logged."""
        from terok.lib.domain.project_state import get_project_state

        mock_project = MagicMock()
        mock_project.id = "test-proj"
        mock_project.security_class = "online"
        mock_project.ssh_host_dir = None
        mock_project.gate_path = tmp_path / "nonexistent-gate"

        # Build dir with Dockerfiles so has_dockerfiles=True
        stage = tmp_path / "build" / "test-proj"
        stage.mkdir(parents=True)
        for name in ("L0.Dockerfile", "L1.cli.Dockerfile", "L2.Dockerfile"):
            (stage / name).write_text("FROM scratch\n")

        with (
            patch("terok.lib.domain.project_state.load_project", return_value=mock_project),
            patch("terok.lib.domain.project_state.build_dir", return_value=tmp_path / "build"),
            patch(
                "terok.lib.orchestration.image.render_all_dockerfiles",
                side_effect=RuntimeError("template broken"),
            ),
            patch("subprocess.run", side_effect=FileNotFoundError("no podman")),
            patch("terok.lib.util.logging_utils.log_warning") as mock_warn,
            patch("terok.lib.domain.project_state._scope_has_vault_key", return_value=False),
        ):
            get_project_state("test-proj")

        assert any("Template comparison failed" in str(c) for c in mock_warn.call_args_list)

    def test_gate_commit_failure_logged(self, tmp_path: Path) -> None:
        """Gate commit lookup exception is caught and logged."""
        from terok.lib.domain.project_state import get_project_state

        mock_project = MagicMock()
        mock_project.id = "test-proj"
        mock_project.security_class = "online"
        mock_project.ssh_host_dir = None
        gate_dir = tmp_path / "gate"
        gate_dir.mkdir()
        mock_project.gate_path = gate_dir

        def broken_commit(_pid: str) -> None:
            raise RuntimeError("git broken")

        with (
            patch("terok.lib.domain.project_state.load_project", return_value=mock_project),
            patch("terok.lib.domain.project_state.build_dir", return_value=tmp_path / "build"),
            patch("subprocess.run", side_effect=FileNotFoundError("no podman")),
            patch("terok.lib.util.logging_utils.log_warning") as mock_warn,
            patch("terok.lib.domain.project_state._scope_has_vault_key", return_value=False),
        ):
            get_project_state("test-proj", gate_commit_provider=broken_commit)

        assert any("Gate commit lookup failed" in str(c) for c in mock_warn.call_args_list)


# ===========================================================================
# image_cleanup.py — project discovery failure
# ===========================================================================


class TestImageCleanupWarning:
    """Cover _known_project_ids() exception logging."""

    @pytest.fixture(autouse=True)
    def _isolate_log(self, tmp_path: Path) -> None:
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            yield

    def test_project_discovery_failure_logged(self) -> None:
        """Exception in list_projects() is caught and logged."""
        from terok.lib.domain.image_cleanup import _known_project_ids

        with (
            patch(
                "terok.lib.domain.image_cleanup.list_projects",
                side_effect=RuntimeError("config broken"),
            ),
            patch("terok.lib.util.logging_utils.log_warning") as mock_warn,
        ):
            result = _known_project_ids()

        assert result is None
        mock_warn.assert_called_once()
        assert "Project discovery failed" in mock_warn.call_args[0][0]


# ===========================================================================
# environment.py — SSH key loading and gate fallback
# ===========================================================================


class TestEnvironmentWarnings:
    """Cover SSH key loading and gate server fallback warnings."""

    @pytest.fixture(autouse=True)
    def _isolate_log(self, tmp_path: Path) -> None:
        with patch("terok.lib.core.paths.core_state_dir", return_value=tmp_path):
            yield

    def test_gate_fallback_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Gate server unreachable triggers an informational warning on stderr."""
        from terok.lib.orchestration.environment import _security_mode_env_and_volumes

        mock_project = MagicMock()
        mock_project.security_class = "online"
        mock_project.id = "test-proj"
        mock_project.upstream_url = "https://example.com/repo.git"
        mock_project.default_branch = "main"
        mock_project.expose_external_remote = False
        # gate_path must be a real Path that .exists() works on
        gate_path = MagicMock()
        gate_path.exists.return_value = True
        gate_path.name = "test-proj.git"
        mock_project.gate_path = gate_path

        with (
            patch(
                "terok.lib.orchestration.environment.get_gate_base_path",
                return_value=Path("/fake/gate"),
            ),
            patch(
                "terok.lib.orchestration.environment.ensure_server_reachable",
                side_effect=SystemExit("unreachable"),
            ),
        ):
            env, _vols = _security_mode_env_and_volumes(mock_project, "task-1", MagicMock())

        err = capsys.readouterr().err
        assert "Gate server unreachable" in err
        assert "online mode" in err.lower()
