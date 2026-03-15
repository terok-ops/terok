# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the blablatoad wrapper script and Dockerfile integration."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from terok.lib.containers.docker import generate_dockerfiles
from terok.lib.core.config import build_root
from tests.test_utils import project_env

REPO_ROOT = Path(__file__).resolve().parents[3]


def blablatoad_script_path() -> Path:
    """Return the path to the blablatoad wrapper script."""
    return REPO_ROOT / "src" / "terok" / "resources" / "scripts" / "blablatoad"


def load_blablatoad_module() -> ModuleType:
    """Load the wrapper script as a Python module."""
    script_path = blablatoad_script_path()
    loader = importlib.machinery.SourceFileLoader("blablatoad", str(script_path))
    spec = importlib.util.spec_from_file_location("blablatoad", script_path, loader=loader)
    if spec is None:
        raise ImportError(f"Could not load spec from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["blablatoad"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def blablatoad_module() -> Iterator[ModuleType]:
    """Load the blablatoad script as an isolated module for one test."""
    sys.modules.pop("blablatoad", None)
    mod = load_blablatoad_module()
    yield mod
    sys.modules.pop("blablatoad", None)


# -- Script validity ----------------------------------------------------------


class TestBlablatoadScript:
    """Tests for the blablatoad wrapper script itself."""

    def test_script_is_valid_python(self) -> None:
        """Verify that the script is syntactically valid Python."""
        ast.parse(blablatoad_script_path().read_text(encoding="utf-8"))

    def test_script_has_shebang(self) -> None:
        """Verify the script has a proper Python shebang."""
        content = blablatoad_script_path().read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env python3")


# -- Theme management --------------------------------------------------------


class TestSetToadTheme:
    """Tests for _set_toad_theme."""

    def test_creates_config_from_scratch(self, blablatoad_module) -> None:
        """Creates toad config when none exists."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad" / "toad.json"
            with patch.object(blablatoad_module, "TOAD_CONFIG", config_path):
                blablatoad_module._set_toad_theme("dracula")
            result = json.loads(config_path.read_text())
            assert result["ui"]["theme"] == "dracula"

    def test_updates_existing_config(self, blablatoad_module) -> None:
        """Updates theme in existing config, preserving other keys."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad.json"
            config_path.write_text(json.dumps({"ui": {"theme": "monokai"}, "shell": {"x": 1}}))
            with patch.object(blablatoad_module, "TOAD_CONFIG", config_path):
                blablatoad_module._set_toad_theme("dracula")
            result = json.loads(config_path.read_text())
            assert result["ui"]["theme"] == "dracula"
            assert result["shell"]["x"] == 1

    def test_recovers_from_malformed_json(self, blablatoad_module) -> None:
        """Recovers gracefully when config contains invalid JSON."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad.json"
            config_path.write_text("{not valid json!!!")
            with patch.object(blablatoad_module, "TOAD_CONFIG", config_path):
                blablatoad_module._set_toad_theme("dracula")
            result = json.loads(config_path.read_text())
            assert result["ui"]["theme"] == "dracula"

    def test_replaces_non_dict_ui(self, blablatoad_module) -> None:
        """Replaces non-dict ui value while preserving other keys."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad.json"
            config_path.write_text(json.dumps({"ui": "not-a-dict", "shell": {"x": 1}}))
            with patch.object(blablatoad_module, "TOAD_CONFIG", config_path):
                blablatoad_module._set_toad_theme("dracula")
            result = json.loads(config_path.read_text())
            assert result["ui"] == {"theme": "dracula"}
            assert result["shell"]["x"] == 1

    def test_skips_write_when_already_set(self, blablatoad_module) -> None:
        """Does not rewrite config when theme already matches."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad.json"
            config_path.write_text(json.dumps({"ui": {"theme": "dracula"}}))
            mtime = config_path.stat().st_mtime
            with patch.object(blablatoad_module, "TOAD_CONFIG", config_path):
                blablatoad_module._set_toad_theme("dracula")
            assert config_path.stat().st_mtime == mtime


# -- Entry point --------------------------------------------------------------


class TestMain:
    """Tests for the blablatoad main entry point."""

    def test_exits_when_toad_missing(self, blablatoad_module) -> None:
        """Exits with error when toad is not on PATH."""
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit, match="1"):
                blablatoad_module.main()

    def test_execs_toad_acp_blablador(self, blablatoad_module) -> None:
        """Calls os.execvp with toad acp blablador-acp."""
        with (
            patch("shutil.which", return_value="/usr/bin/toad"),
            patch.object(blablatoad_module, "_set_toad_theme") as mock_theme,
            patch("os.execvp") as mock_exec,
            patch("sys.argv", ["blablatoad"]),
        ):
            blablatoad_module.main()
        mock_theme.assert_called_once_with("dracula")
        mock_exec.assert_called_once_with(
            "/usr/bin/toad", ["/usr/bin/toad", "acp", "blablador-acp"]
        )

    def test_forwards_extra_args(self, blablatoad_module) -> None:
        """Extra CLI args are forwarded to toad."""
        with (
            patch("shutil.which", return_value="/usr/bin/toad"),
            patch.object(blablatoad_module, "_set_toad_theme"),
            patch("os.execvp") as mock_exec,
            patch("sys.argv", ["blablatoad", "--title", "Test"]),
        ):
            blablatoad_module.main()
        mock_exec.assert_called_once_with(
            "/usr/bin/toad", ["/usr/bin/toad", "acp", "blablador-acp", "--title", "Test"]
        )


# -- Dockerfile integration ---------------------------------------------------


class TestBlablatoadDockerfile:
    """Tests for blablatoad integration in the L1 CLI Dockerfile."""

    def test_l1_cli_has_blablatoad(self) -> None:
        """Verify that the L1 CLI Dockerfile copies the blablatoad script."""
        yaml_text = (
            "project:\n"
            "  id: proj_blablatoad_test\n"
            "git:\n"
            "  upstream_url: https://example.com/repo.git\n"
            "  default_branch: main\n"
        )
        with project_env(yaml_text, project_id="proj_blablatoad_test"):
            generate_dockerfiles("proj_blablatoad_test")
            content = (build_root() / "proj_blablatoad_test" / "L1.cli.Dockerfile").read_text()
            assert "COPY scripts/blablatoad /usr/local/bin/blablatoad" in content

    def test_l1_cli_blablatoad_script_staged(self) -> None:
        """Verify the blablatoad script is staged in the build context."""
        yaml_text = (
            "project:\n"
            "  id: proj_blablatoad_staged\n"
            "git:\n"
            "  upstream_url: https://example.com/repo.git\n"
            "  default_branch: main\n"
        )
        with project_env(yaml_text, project_id="proj_blablatoad_staged"):
            generate_dockerfiles("proj_blablatoad_staged")
            scripts_dir = build_root() / "proj_blablatoad_staged" / "scripts"
            assert (scripts_dir / "blablatoad").is_file()
