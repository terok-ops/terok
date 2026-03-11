# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.core.projects import load_project
from terok.lib.security.ssh import SSHManager
from test_utils import mock_git_config, write_project


class SshTests(unittest.TestCase):
    def test_init_project_ssh_uses_existing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"
            ssh_dir = base / "ssh"
            config_root.mkdir(parents=True, exist_ok=True)
            ssh_dir.mkdir(parents=True, exist_ok=True)

            project_id = "proj5"
            write_project(
                config_root,
                project_id,
                f"""\nproject:\n  id: {project_id}\nssh:\n  host_dir: {ssh_dir}\n""".lstrip(),
            )

            key_name = "id_test"
            (ssh_dir / key_name).write_text("dummy", encoding="utf-8")
            (ssh_dir / f"{key_name}.pub").write_text("dummy", encoding="utf-8")

            with (
                unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_DIR": str(config_root)}),
                mock_git_config(),
                unittest.mock.patch("terok.lib.security.ssh.subprocess.run") as run_mock,
            ):
                result = SSHManager(load_project(project_id)).init(key_name=key_name)

                run_mock.assert_not_called()
                cfg_path = Path(result["config_path"])
                self.assertTrue(cfg_path.is_file())
                cfg_text = cfg_path.read_text(encoding="utf-8")
                self.assertIn(f"IdentityFile ~/.ssh/{key_name}", cfg_text)

    def test_init_project_ssh_without_key_name_does_not_print_default_warning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"
            ssh_dir = base / "ssh"
            config_root.mkdir(parents=True, exist_ok=True)
            ssh_dir.mkdir(parents=True, exist_ok=True)

            project_id = "proj6"
            write_project(
                config_root,
                project_id,
                f"""\nproject:\n  id: {project_id}\nssh:\n  host_dir: {ssh_dir}\n""".lstrip(),
            )

            key_name = f"id_ed25519_{project_id}"
            (ssh_dir / key_name).write_text("dummy", encoding="utf-8")
            (ssh_dir / f"{key_name}.pub").write_text("dummy", encoding="utf-8")

            with (
                unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_DIR": str(config_root)}),
                mock_git_config(),
                unittest.mock.patch("terok.lib.security.ssh.subprocess.run") as run_mock,
                unittest.mock.patch("builtins.print") as print_mock,
            ):
                SSHManager(load_project(project_id)).init()

                run_mock.assert_not_called()
                printed_lines = [
                    " ".join(str(part) for part in call.args) for call in print_mock.call_args_list
                ]
                self.assertFalse(
                    any("does not define ssh.key_name" in line for line in printed_lines),
                    "Unexpected default-key warning was printed",
                )
