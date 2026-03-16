# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for launch and restart workflows."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.testnet import EXAMPLE_UPSTREAM_URL, LOCALHOST, localhost_url

from ..helpers import TerokIntegrationEnv, write_fake_podman

pytestmark = pytest.mark.needs_host_features

PROJECT_ID = "demo"
TASK_ID = "1"
CLI_CONTAINER = f"{PROJECT_ID}-cli-{TASK_ID}"
TOAD_CONTAINER = f"{PROJECT_ID}-toad-{TASK_ID}"
WEB_PORT = 7860
TOAD_PORT = 8080

PROJECT_CONFIG = f"""
project:
  id: {PROJECT_ID}
  security_class: online
git:
  upstream_url: {EXAMPLE_UPSTREAM_URL}
"""

GLOBAL_CONFIG = f"""
shield:
  bypass_firewall_no_protection: true
ui:
  base_port: {WEB_PORT}
"""


def _configure_fake_runtime(
    terok_env: TerokIntegrationEnv,
    tmp_path: Path,
) -> tuple[Path, dict[str, str]]:
    """Install a fake podman shim and a config that bypasses real shield hooks."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "fake-podman-state.json"
    write_fake_podman(bin_dir, state_path)

    config_dir = terok_env.xdg_config_home / "terok"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text(GLOBAL_CONFIG.strip() + "\n", encoding="utf-8")

    path = os.environ.get("PATH", "")
    return state_path, {"PATH": f"{bin_dir}{os.pathsep}{path}" if path else str(bin_dir)}


def _load_fake_podman_state(state_path: Path) -> dict[str, Any]:
    """Return the recorded fake-podman state."""
    return json.loads(state_path.read_text(encoding="utf-8"))


def _container_args(state: dict[str, Any], name: str) -> list[str]:
    """Return the recorded `podman run` arguments for `name`."""
    return list(state["containers"][name]["args"])


def _env_entries(args: list[str]) -> set[str]:
    """Return the `-e KEY=VALUE` entries from a recorded podman argv."""
    return {args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "-e"}


class TestLaunchWorkflows:
    """Verify host-only task start/restart flows through the real CLI."""

    def test_task_start_cli_launches_container(
        self, terok_env: TerokIntegrationEnv, tmp_path: Path
    ) -> None:
        """`task start` should create a task and launch the CLI container."""
        terok_env.write_project(PROJECT_ID, PROJECT_CONFIG)
        state_path, extra_env = _configure_fake_runtime(terok_env, tmp_path)

        result = terok_env.run_cli(
            "task",
            "start",
            PROJECT_ID,
            "--name",
            "Fix Login Bug",
            extra_env=extra_env,
        )

        meta = yaml.safe_load(
            terok_env.task_meta_path(PROJECT_ID, TASK_ID).read_text(encoding="utf-8")
        )
        state = _load_fake_podman_state(state_path)
        args = _container_args(state, CLI_CONTAINER)

        assert "Created task 1 (fix-login-bug)" in result.stdout
        assert "CLI container is running in the background." in result.stdout
        assert "Login with: terokctl login demo 1" in result.stdout
        assert state["containers"][CLI_CONTAINER]["status"] == "running"
        assert state["containers"][CLI_CONTAINER]["marker"] == "__CLI_READY__"
        assert "--name" in args and args[args.index("--name") + 1] == CLI_CONTAINER
        assert f"{PROJECT_ID}:l2-cli" in args
        assert "-p" not in args
        assert meta["mode"] == "cli"
        assert meta["name"] == "fix-login-bug"
        assert meta["unrestricted"] is True

    def test_task_start_toad_launches_browser_tui(
        self, terok_env: TerokIntegrationEnv, tmp_path: Path
    ) -> None:
        """`task start --toad` should launch the served Toad workflow."""
        terok_env.write_project(PROJECT_ID, PROJECT_CONFIG)
        state_path, extra_env = _configure_fake_runtime(terok_env, tmp_path)

        result = terok_env.run_cli(
            "task",
            "start",
            PROJECT_ID,
            "--toad",
            extra_env=extra_env,
        )

        meta = yaml.safe_load(
            terok_env.task_meta_path(PROJECT_ID, TASK_ID).read_text(encoding="utf-8")
        )
        state = _load_fake_podman_state(state_path)
        args = _container_args(state, TOAD_CONTAINER)

        assert "Toad is serving." in result.stdout
        assert localhost_url(WEB_PORT) in result.stdout
        assert state["containers"][TOAD_CONTAINER]["marker"] == "Serving http://0.0.0.0:8080"
        assert args[args.index("-p") + 1] == f"{LOCALHOST}:{WEB_PORT}:{TOAD_PORT}"
        assert f"{PROJECT_ID}:l2-cli" in args
        assert "toad --serve -H 0.0.0.0 -p 8080" in args[-1]
        assert f"--public-url {localhost_url(WEB_PORT).rstrip('/')}" in args[-1]
        assert meta["mode"] == "toad"
        assert meta["web_port"] == WEB_PORT
        assert meta["unrestricted"] is True

    def test_task_restart_starts_existing_stopped_container(
        self, terok_env: TerokIntegrationEnv, tmp_path: Path
    ) -> None:
        """`task restart` should use `podman start` for an existing stopped container."""
        terok_env.write_project(PROJECT_ID, PROJECT_CONFIG)
        state_path, extra_env = _configure_fake_runtime(terok_env, tmp_path)

        terok_env.run_cli("task", "start", PROJECT_ID, extra_env=extra_env)

        state = _load_fake_podman_state(state_path)
        state["containers"][CLI_CONTAINER]["status"] = "exited"
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

        result = terok_env.run_cli("task", "restart", PROJECT_ID, TASK_ID, extra_env=extra_env)
        restarted = _load_fake_podman_state(state_path)

        assert "Restarting task demo/1 (cli)..." in result.stdout
        assert "Restarted task 1:" in result.stdout
        assert "Login with: terokctl login demo 1" in result.stdout
        assert restarted["containers"][CLI_CONTAINER]["status"] == "running"
        assert any(command == ["start", CLI_CONTAINER] for command in restarted["commands"])
