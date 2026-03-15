# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for SSH bootstrap workflows."""

from __future__ import annotations

import stat

import pytest

from ..conftest import ssh_keygen_missing
from ..helpers import TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features

PROJECT_TEMPLATE = """
project:
  id: {project_id}
  security_class: gatekeeping
git:
  upstream_url: https://example.com/{project_id}.git
"""

CUSTOM_HOST_DIR_TEMPLATE = """
project:
  id: {project_id}
  security_class: gatekeeping
git:
  upstream_url: https://example.com/{project_id}.git
ssh:
  host_dir: {host_dir}
"""


class TestSshInit:
    """Verify SSH bootstrap behavior through the real CLI."""

    @ssh_keygen_missing
    def test_ssh_init_creates_default_keypair_and_config(
        self, terok_env: TerokIntegrationEnv
    ) -> None:
        """``ssh-init`` writes the default per-project env mount directory."""
        terok_env.write_project(
            "demo",
            PROJECT_TEMPLATE.format(project_id="demo"),
        )

        first = terok_env.run_cli("ssh-init", "demo")
        second = terok_env.run_cli("ssh-init", "demo")

        ssh_dir = terok_env.envs_base_dir / "_ssh-config-demo"
        private_key = ssh_dir / "id_ed25519_demo"
        public_key = ssh_dir / "id_ed25519_demo.pub"
        config_path = ssh_dir / "config"

        assert "SSH directory initialized:" in first.stdout
        assert "SSH directory initialized:" in second.stdout
        assert ssh_dir.is_dir()
        assert private_key.is_file()
        assert public_key.is_file()
        assert config_path.is_file()
        assert stat.S_IMODE(ssh_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
        assert stat.S_IMODE(public_key.stat().st_mode) == 0o644
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o644
        assert "IdentityFile ~/.ssh/id_ed25519_demo" in config_path.read_text(encoding="utf-8")

    @ssh_keygen_missing
    def test_ssh_init_respects_custom_host_dir_and_key_name(
        self, terok_env: TerokIntegrationEnv
    ) -> None:
        """``ssh-init`` respects explicit ``ssh.host_dir`` and ``--key-name``."""
        custom_dir = terok_env.base_dir / "custom-ssh"
        terok_env.write_project(
            "custom",
            CUSTOM_HOST_DIR_TEMPLATE.format(project_id="custom", host_dir=custom_dir),
        )

        result = terok_env.run_cli("ssh-init", "custom", "--key-name", "id_custom")

        assert "SSH directory initialized:" in result.stdout
        assert (custom_dir / "id_custom").is_file()
        assert (custom_dir / "id_custom.pub").is_file()
        config_text = (custom_dir / "config").read_text(encoding="utf-8")
        assert "IdentityFile ~/.ssh/id_custom" in config_text
