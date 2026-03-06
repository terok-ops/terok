# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the completions CLI subcommand."""

import os
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from terok.cli.commands.completions import (
    _detect_shell,
    _install_completions,
    is_completion_installed,
)


class DetectShellTests(unittest.TestCase):
    """Tests for _detect_shell()."""

    def test_detects_bash(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            self.assertEqual(_detect_shell(), "bash")

    def test_detects_zsh(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"SHELL": "/usr/bin/zsh"}):
            self.assertEqual(_detect_shell(), "zsh")

    def test_detects_fish(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"SHELL": "/usr/bin/fish"}):
            self.assertEqual(_detect_shell(), "fish")

    def test_unknown_shell_exits(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"SHELL": "/bin/tcsh"}):
            with self.assertRaises(SystemExit):
                _detect_shell()

    def test_missing_shell_var_exits(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                _detect_shell()


class InstallCompletionsTests(unittest.TestCase):
    """Tests for _install_completions()."""

    @unittest.mock.patch("terok.cli.commands.completions.shellcode", return_value="# completion")
    def test_writes_to_target(self, _mock_sc: unittest.mock.MagicMock) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "nested" / "dir" / "terokctl"
            targets = {"bash": target, "zsh": Path("/unused"), "fish": Path("/unused")}
            with (
                unittest.mock.patch("terok.cli.commands.completions._INSTALL_TARGETS", targets),
                redirect_stdout(StringIO()) as out,
            ):
                _install_completions("bash")
            self.assertTrue(target.is_file())
            self.assertIn("# completion", target.read_text(encoding="utf-8"))
            self.assertIn(str(target), out.getvalue())

    @unittest.mock.patch("terok.cli.commands.completions.shellcode", return_value="# comp")
    @unittest.mock.patch("terok.cli.commands.completions._detect_shell", return_value="fish")
    def test_auto_detects_shell(
        self,
        mock_detect: unittest.mock.MagicMock,
        _mock_sc: unittest.mock.MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "terokctl.fish"
            targets = {"bash": Path("/unused"), "zsh": Path("/unused"), "fish": target}
            with (
                unittest.mock.patch("terok.cli.commands.completions._INSTALL_TARGETS", targets),
                redirect_stdout(StringIO()),
            ):
                _install_completions(None)
            mock_detect.assert_called_once()
            self.assertTrue(target.is_file())


class IsCompletionInstalledTests(unittest.TestCase):
    """Tests for is_completion_installed()."""

    def _patch_all_empty(self) -> unittest.mock._patch_dict:
        """Return a context manager that empties all search directories."""
        # We need to stack multiple patches; use a helper
        return unittest.mock.patch.multiple(
            "terok.cli.commands.completions",
            _BASH_COMPLETION_DIRS=(),
            _ZSH_COMPLETION_DIRS=(),
            _FISH_COMPLETION_DIRS=(),
            _SHELL_RC_FILES=(),
        )

    def test_returns_false_when_nothing_found(self) -> None:
        with self._patch_all_empty():
            self.assertFalse(is_completion_installed())

    def test_detects_bash_autoload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "terokctl").write_text("# comp", encoding="utf-8")
            with unittest.mock.patch.multiple(
                "terok.cli.commands.completions",
                _BASH_COMPLETION_DIRS=(Path(td),),
                _ZSH_COMPLETION_DIRS=(),
                _FISH_COMPLETION_DIRS=(),
                _SHELL_RC_FILES=(),
            ):
                self.assertTrue(is_completion_installed())

    def test_detects_zsh_autoload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "_terokctl").write_text("# comp", encoding="utf-8")
            with unittest.mock.patch.multiple(
                "terok.cli.commands.completions",
                _BASH_COMPLETION_DIRS=(),
                _ZSH_COMPLETION_DIRS=(Path(td),),
                _FISH_COMPLETION_DIRS=(),
                _SHELL_RC_FILES=(),
            ):
                self.assertTrue(is_completion_installed())

    def test_detects_fish_autoload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "terokctl.fish").write_text("# comp", encoding="utf-8")
            with unittest.mock.patch.multiple(
                "terok.cli.commands.completions",
                _BASH_COMPLETION_DIRS=(),
                _ZSH_COMPLETION_DIRS=(),
                _FISH_COMPLETION_DIRS=(Path(td),),
                _SHELL_RC_FILES=(),
            ):
                self.assertTrue(is_completion_installed())

    def test_detects_rc_marker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rc = Path(td) / ".bashrc"
            rc.write_text("# register-python-argcomplete terokctl\n", encoding="utf-8")
            with unittest.mock.patch.multiple(
                "terok.cli.commands.completions",
                _BASH_COMPLETION_DIRS=(),
                _ZSH_COMPLETION_DIRS=(),
                _FISH_COMPLETION_DIRS=(),
                _SHELL_RC_FILES=(rc,),
            ):
                self.assertTrue(is_completion_installed())
