# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.core import config as cfg


class ConfigTests(unittest.TestCase):
    def test_global_config_search_paths_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yml"
            with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(cfg_path)}):
                paths = cfg.global_config_search_paths()
                self.assertEqual(paths, [cfg_path.expanduser().resolve()])

    def test_global_config_path_prefers_xdg(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            xdg = Path(td)
            config_file = xdg / "terok" / "config.yml"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text("ui:\n  base_port: 7000\n", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg)}, clear=False):
                path = cfg.global_config_path()
                self.assertEqual(path, config_file.resolve())

    def test_state_root_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"TEROK_STATE_DIR": td}):
                self.assertEqual(cfg.state_root(), Path(td).resolve())

    def test_state_root_config_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yml"
            state_dir = Path(td) / "state"
            cfg_path.write_text(f"paths:\n  state_root: {state_dir}\n", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(cfg_path)}):
                self.assertEqual(cfg.state_root(), state_dir.resolve())

    def test_user_projects_root_config_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yml"
            projects_dir = Path(td) / "projects"
            cfg_path.write_text(f"paths:\n  user_projects_root: {projects_dir}\n", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(cfg_path)}):
                self.assertEqual(cfg.user_projects_root(), projects_dir.resolve())

    def test_ui_and_envs_values_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yml"
            envs_dir = Path(td) / "envs"
            cfg_path.write_text(
                f"ui:\n  base_port: 8123\nenvs:\n  base_dir: {envs_dir}\n",
                encoding="utf-8",
            )
            with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(cfg_path)}):
                self.assertEqual(cfg.get_ui_base_port(), 8123)
                self.assertEqual(cfg.get_envs_base_dir(), envs_dir.resolve())

    def test_tui_default_tmux_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yml"
            cfg_path.write_text("tui:\n  default_tmux: true\n", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(cfg_path)}):
                self.assertTrue(cfg.get_tui_default_tmux())

    def test_tui_default_tmux_default_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yml"
            cfg_path.write_text("", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(cfg_path)}):
                self.assertFalse(cfg.get_tui_default_tmux())

    def test_tui_default_tmux_explicit_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.yml"
            cfg_path.write_text("tui:\n  default_tmux: false\n", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(cfg_path)}):
                self.assertFalse(cfg.get_tui_default_tmux())

    def test_experimental_default_false(self) -> None:
        cfg.set_experimental(False)
        self.assertFalse(cfg.is_experimental())

    def test_experimental_set_true(self) -> None:
        cfg.set_experimental(True)
        try:
            self.assertTrue(cfg.is_experimental())
        finally:
            cfg.set_experimental(False)

    def test_experimental_roundtrip(self) -> None:
        cfg.set_experimental(True)
        self.assertTrue(cfg.is_experimental())
        cfg.set_experimental(False)
        self.assertFalse(cfg.is_experimental())
