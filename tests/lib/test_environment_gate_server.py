# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for environment.py gate server integration."""

import unittest
import unittest.mock
from pathlib import Path

from terok.lib.containers.environment import _security_mode_env_and_volumes
from terok.lib.core.projects import load_project
from test_utils import mock_git_config, project_env

_GATEKEEPING_YAML = """\
project:
  id: gk-proj
  security_class: gatekeeping
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""

_ONLINE_YAML = """\
project:
  id: online-proj
  security_class: online
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""


class TestGatekeepingMode(unittest.TestCase):
    """Gatekeeping mode produces http:// URLs with token auth, no volume mounts for gate."""

    @unittest.mock.patch(
        "terok.lib.security.gate_tokens.create_token",
        return_value="deadbeef" * 4,
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_server.get_server_status",
        return_value=unittest.mock.Mock(running=True),
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_server._get_port",
        return_value=9418,
    )
    def test_gatekeeping_uses_http_url_with_token(self, *_mocks: unittest.mock.Mock) -> None:
        with (
            mock_git_config(),
            project_env(_GATEKEEPING_YAML, project_id="gk-proj", with_gate=True) as ctx,
        ):
            project = load_project("gk-proj")
            env, volumes = _security_mode_env_and_volumes(project, Path(ctx.base / "ssh"), "1")

        expected_token = "deadbeef" * 4
        self.assertEqual(
            env["CODE_REPO"],
            f"http://{expected_token}@host.containers.internal:9418/gk-proj.git",
        )
        # No gate volume mount
        gate_mounts = [v for v in volumes if "git-gate" in v or "gate" in v.split(":")[0]]
        self.assertEqual(gate_mounts, [])
        self.assertEqual(env["GIT_BRANCH"], "main")

    def test_gatekeeping_missing_gate_raises(self) -> None:
        with (
            mock_git_config(),
            project_env(_GATEKEEPING_YAML, project_id="gk-proj", with_gate=False),
        ):
            project = load_project("gk-proj")
            with self.assertRaises(SystemExit) as ctx:
                _security_mode_env_and_volumes(project, Path("/tmp/ssh"), "1")
            self.assertIn("gate-sync", str(ctx.exception))

    @unittest.mock.patch(
        "terok.lib.security.gate_server.get_server_status",
        return_value=unittest.mock.Mock(running=False),
    )
    @unittest.mock.patch("terok.lib.security.gate_server.is_systemd_available", return_value=False)
    @unittest.mock.patch(
        "terok.lib.security.gate_server._get_port",
        return_value=9418,
    )
    def test_gatekeeping_server_not_running_raises(self, *_mocks: unittest.mock.Mock) -> None:
        with (
            mock_git_config(),
            project_env(_GATEKEEPING_YAML, project_id="gk-proj", with_gate=True),
        ):
            project = load_project("gk-proj")
            with self.assertRaises(SystemExit) as ctx:
                _security_mode_env_and_volumes(project, Path("/tmp/ssh"), "1")
            self.assertIn("Gate server", str(ctx.exception))


class TestOnlineMode(unittest.TestCase):
    """Online mode produces http:// CLONE_FROM with token when gate exists."""

    @unittest.mock.patch(
        "terok.lib.security.gate_tokens.create_token",
        return_value="cafebabe" * 4,
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_server.get_server_status",
        return_value=unittest.mock.Mock(running=True),
    )
    @unittest.mock.patch(
        "terok.lib.security.gate_server._get_port",
        return_value=9418,
    )
    def test_online_with_gate_uses_http_url_with_token(self, *_mocks: unittest.mock.Mock) -> None:
        with (
            mock_git_config(),
            project_env(_ONLINE_YAML, project_id="online-proj", with_gate=True) as ctx,
        ):
            project = load_project("online-proj")
            env, volumes = _security_mode_env_and_volumes(project, Path(ctx.base / "ssh"), "1")

        expected_token = "cafebabe" * 4
        self.assertEqual(
            env["CLONE_FROM"],
            f"http://{expected_token}@host.containers.internal:9418/online-proj.git",
        )
        self.assertEqual(env["CODE_REPO"], "https://example.com/repo.git")
        # No gate volume mount
        gate_mounts = [v for v in volumes if "git-gate" in v or "gate" in v.split(":")[0]]
        self.assertEqual(gate_mounts, [])

    @unittest.mock.patch(
        "terok.lib.security.gate_server.get_server_status",
        return_value=unittest.mock.Mock(running=False),
    )
    @unittest.mock.patch("terok.lib.security.gate_server.is_systemd_available", return_value=False)
    def test_online_gate_server_down_skips_clone_from(self, *_mocks: unittest.mock.Mock) -> None:
        """Online mode with gate repo but server down falls back gracefully."""
        with (
            mock_git_config(),
            project_env(_ONLINE_YAML, project_id="online-proj", with_gate=True) as ctx,
        ):
            project = load_project("online-proj")
            env, _volumes = _security_mode_env_and_volumes(project, Path(ctx.base / "ssh"), "1")

        self.assertNotIn("CLONE_FROM", env)
        self.assertEqual(env["CODE_REPO"], "https://example.com/repo.git")

    def test_online_without_gate_no_clone_from(self) -> None:
        with (
            mock_git_config(),
            project_env(_ONLINE_YAML, project_id="online-proj", with_gate=False) as ctx,
        ):
            project = load_project("online-proj")
            env, _ = _security_mode_env_and_volumes(project, Path(ctx.base / "ssh"), "1")

        self.assertNotIn("CLONE_FROM", env)
        self.assertEqual(env["CODE_REPO"], "https://example.com/repo.git")
