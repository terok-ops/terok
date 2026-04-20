# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for launch and restart workflows."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from terok.lib.util.yaml import load as yaml_load
from tests.test_utils import assert_task_id
from tests.testnet import EXAMPLE_UPSTREAM_URL, LOCALHOST, localhost_url

from ..helpers import TerokIntegrationEnv, write_fake_podman

pytestmark = pytest.mark.needs_host_features

PROJECT_ID = "demo"
TOAD_PORT = 8080

PROJECT_CONFIG = f"""
project:
  id: {PROJECT_ID}
  security_class: online
git:
  upstream_url: {EXAMPLE_UPSTREAM_URL}
"""

GLOBAL_CONFIG = """
shield:
  bypass_firewall_no_protection: true
vault:
  bypass_no_secret_protection: true
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


def _extract_task_id(stdout: str) -> str:
    """Extract the task ID from 'Created task <id> ...' output."""
    match = re.search(r"Created task ([ghjkmnp-tv-z][0-9][0-9a-hjkmnp-tv-z]{3})", stdout)
    assert match, f"Could not extract task ID from: {stdout!r}"
    return match.group(1)


def _extract_web_port(stdout: str) -> int:
    """Extract the dynamically allocated web port from '- URL:  http://...:PORT/' output."""
    match = re.search(r"URL:\s+http://[\d.]+:(\d+)/", stdout)
    assert match, f"Could not extract web port from: {stdout!r}"
    return int(match.group(1))


class TestLaunchWorkflows:
    """Verify host-only task run/restart flows through the real CLI."""

    def test_task_run_cli_launches_container(
        self, terok_env: TerokIntegrationEnv, tmp_path: Path
    ) -> None:
        """`task run` (default --mode cli) should create a task and launch the CLI container."""
        terok_env.write_project(PROJECT_ID, PROJECT_CONFIG)
        state_path, extra_env = _configure_fake_runtime(terok_env, tmp_path)

        result = terok_env.run_cli(
            "task",
            "run",
            PROJECT_ID,
            "--name",
            "Fix Login Bug",
            extra_env=extra_env,
        )

        tid = _extract_task_id(result.stdout)
        assert_task_id(tid)
        cli_container = f"{PROJECT_ID}-cli-{tid}"
        meta = yaml_load(terok_env.task_meta_path(PROJECT_ID, tid).read_text(encoding="utf-8"))
        state = _load_fake_podman_state(state_path)
        args = _container_args(state, cli_container)

        assert f"Created task {tid} (fix-login-bug)" in result.stdout
        assert "CLI container is running in the background." in result.stdout
        assert f"Login with: terok login demo {tid}" in result.stdout
        assert state["containers"][cli_container]["status"] == "running"
        assert state["containers"][cli_container]["marker"] == "__CLI_READY__"
        assert "--name" in args and args[args.index("--name") + 1] == cli_container
        assert f"{PROJECT_ID}:l2-cli" in args
        assert "-p" not in args
        assert meta["mode"] == "cli"
        assert meta["name"] == "fix-login-bug"
        assert meta["unrestricted"] is True

    def test_task_run_toad_launches_browser_tui(
        self, terok_env: TerokIntegrationEnv, tmp_path: Path
    ) -> None:
        """`task run --mode toad` should launch the served Toad workflow."""
        terok_env.write_project(PROJECT_ID, PROJECT_CONFIG)
        state_path, extra_env = _configure_fake_runtime(terok_env, tmp_path)

        result = terok_env.run_cli(
            "task",
            "run",
            PROJECT_ID,
            "--mode",
            "toad",
            extra_env=extra_env,
        )

        tid = _extract_task_id(result.stdout)
        web_port = _extract_web_port(result.stdout)
        toad_container = f"{PROJECT_ID}-toad-{tid}"
        meta = yaml_load(terok_env.task_meta_path(PROJECT_ID, tid).read_text(encoding="utf-8"))
        state = _load_fake_podman_state(state_path)
        args = _container_args(state, toad_container)

        assert "Toad is serving." in result.stdout
        assert state["containers"][toad_container]["marker"] == "TEROK_READY"
        assert args[args.index("-p") + 1] == f"{LOCALHOST}:{web_port}:{TOAD_PORT}"
        assert f"{PROJECT_ID}:l2-cli" in args
        # terok-toad-entry (in-container supervisor) starts Caddy + toad;
        # terok only forwards the public URL now.
        assert "terok-toad-entry" in args[-1]
        assert f"--public-url {localhost_url(web_port).rstrip('/')}" in args[-1]
        assert meta["mode"] == "toad"
        assert meta["web_port"] == web_port
        assert isinstance(meta.get("web_token"), str) and meta["web_token"]
        assert meta["unrestricted"] is True
        # The printed URL seeds Caddy's cookie on first hit — it must
        # carry the same token we persisted in task metadata.
        assert f"{localhost_url(web_port)}?token={meta['web_token']}" in result.stdout

    def test_task_restart_starts_existing_stopped_container(
        self, terok_env: TerokIntegrationEnv, tmp_path: Path
    ) -> None:
        """`task restart` should use `podman start` for an existing stopped container."""
        terok_env.write_project(PROJECT_ID, PROJECT_CONFIG)
        state_path, extra_env = _configure_fake_runtime(terok_env, tmp_path)

        start_result = terok_env.run_cli("task", "run", PROJECT_ID, extra_env=extra_env)
        tid = _extract_task_id(start_result.stdout)
        cli_container = f"{PROJECT_ID}-cli-{tid}"

        state = _load_fake_podman_state(state_path)
        state["containers"][cli_container]["status"] = "exited"
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

        result = terok_env.run_cli("task", "restart", PROJECT_ID, tid, extra_env=extra_env)
        restarted = _load_fake_podman_state(state_path)

        assert f"Restarting task demo/{tid} (cli)..." in result.stdout
        assert f"Restarted task {tid}:" in result.stdout
        assert f"Login with: terok login demo {tid}" in result.stdout
        assert restarted["containers"][cli_container]["status"] == "running"
        assert any(command == ["start", cli_container] for command in restarted["commands"])
